"""The cache replay (cache_replay.py) on a made-up run: no model is called."""

import asyncio
from typing import Any

from cache_replay import PromptCache, replay

Json = dict[str, Any]
SYSTEM_TOKENS = 5_036
# Each turn's prompt: three tool calls, then the report.
PROMPTS = (5_300, 5_500, 6_000, 6_400)


def step(kind: str, call_id: str | None, tokens: int | None, result: str | None = None) -> Json:
    return {
        "kind": kind,
        "summary": "Look.",
        "tool_name": "query_metrics" if call_id else None,
        "tool_call_id": call_id,
        "result": result,
        "tokens_input": tokens,
        "tokens_output": None if tokens is None else 40,
    }


def recorded(group: str, *, partial: bool = False, result: str = "up 1") -> Json:
    """A run as evals/reports/runs/ holds it, with only what the replay reads."""
    ids = [f"tc_{n:010}" for n in range(len(PROMPTS) - 1)]
    steps: list[Json] = []
    for call_id, prompt in zip(ids, PROMPTS[:-1], strict=True):
        steps += [step("tool_call", call_id, prompt), step("tool_call", call_id, None, result)]
    call = {"role": "primary", "outcome": "ok", "output_tokens": 40, "max_output_tokens": 2_048}
    tool = {"name": "query_metrics", "args": {"query": "up"}, "error": None}
    return {
        "group_id": group,
        "anchor": "2026-11-01T06:00:00Z",
        "report": {"partial": partial},
        "steps": [*steps, step("report", None, PROMPTS[-1])],
        "tool_calls": [tool | {"id": call_id} for call_id in ids],
        "model_calls": [call | {"input_tokens": prompt} for prompt in PROMPTS],
    }


def cached(cache: PromptCache, run: Json) -> list[tuple[int, int]]:
    """Each turn's cache reads and writes."""
    calls = asyncio.run(replay(run, cache))
    return [(call["cached_tokens"], call["cache_write_tokens"]) for call in calls]


def test_each_turn_reads_the_last_one_and_writes_what_it_added() -> None:
    cache = PromptCache(SYSTEM_TOKENS)

    assert cached(cache, recorded("first")) == [
        (0, 5_300),
        (5_300, 200),
        (5_500, 500),
        (6_000, 400),
    ]
    # The next investigation shares only the tools and system prompt.
    assert cached(cache, recorded("second"))[0] == (5_036, 264)


def test_a_forced_report_reads_only_the_tools_and_system_prompt() -> None:
    turns = cached(PromptCache(SYSTEM_TOKENS), recorded("run", partial=True))

    assert turns[-1] == (5_036, 0)


def test_without_message_breakpoints_each_turn_reads_the_tools_and_system_prompt() -> None:
    """What the recorded runs show: the replay checks itself against them with this."""
    turns = cached(PromptCache(SYSTEM_TOKENS, messages=False), recorded("run", partial=True))

    assert turns == [(0, 5_036), (5_036, 0), (5_036, 0), (5_036, 0)]


def test_a_forced_report_that_kept_its_tool_choice_would_read_the_conversation() -> None:
    turns = cached(PromptCache(SYSTEM_TOKENS, flips=False), recorded("run", partial=True))

    assert turns[-1] == (6_000, 400)


def test_a_changed_message_loses_what_came_after_it() -> None:
    cache = PromptCache(SYSTEM_TOKENS)
    cached(cache, recorded("run"))

    assert cached(cache, recorded("run"))[1] == (5_500, 0)
    assert cached(cache, recorded("run", result="up 0"))[1] == (5_300, 200)
