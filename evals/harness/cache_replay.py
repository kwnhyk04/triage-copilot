"""Prompt caching on the recorded Claude runs: what each investigation reads from the cache and
writes to it, with and without the breakpoint on the last message of a turn.

    uv run python evals/harness/cache_replay.py     (make eval-cache)

No model is called and no stack is needed. Each all-Claude run in evals/reports/runs/ goes back
through the job's own `history` and the real AnthropicProvider, turn by turn, into PromptCache: a
fake Messages API that keeps Anthropic's published cache rules and takes each prompt's size from
the recorded call. With message breakpoints ignored it must give every call its recorded reads
and writes, or the pass stops: that holds the fake to the real API's answers. It writes
evals/reports/<date>-cache-replay.md, with every investigation before and after.
"""

import asyncio
import hashlib
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NamedTuple

import eval as harness
import httpx2
import score

from agent.model import Message, ToolSpec
from agent.prompt import FORCED, REPORT_TOOL, SYSTEM, CallRow, brief, history
from agent.providers import AnthropicProvider
from agent.steps import StepRecordedV2, TokenUsage

Json = dict[str, Any]
# The positions a breakpoint checks for an earlier entry, itself included.
LOOKBACK = 20
# Stands in for the run's tools: the fake compares their bytes and never counts them.
TOOLS = [ToolSpec(REPORT_TOOL, "The run's tools.", {"type": "object", "properties": {}})]
REPLY: Json = {
    "id": "msg_replay",
    "type": "message",
    "role": "assistant",
    "model": "replay",
    "content": [{"type": "tool_use", "id": "toolu_replay", "name": REPORT_TOOL, "input": {}}],
    "stop_reason": "tool_use",
    "stop_sequence": None,
}


def unmarked(block: Json) -> bytes:
    """A block's bytes without its marker, which moves on every turn and invalidates nothing."""
    return json.dumps({k: v for k, v in block.items() if k != "cache_control"}).encode()


class PromptCache:
    """Anthropic's cache as published, on an httpx2 transport. The tools and system prompt are one
    entry, and tool_choice keys the messages after them. A request reads the longest entry its
    prefix matches, at most LOOKBACK blocks behind its breakpoint, and writes the rest up to it.
    Each message of the agent's holds one block, and its breakpoint sits on the last one."""

    def __init__(self, system_tokens: int, *, messages: bool = True, flips: bool = True) -> None:
        self.system_tokens = system_tokens
        # False ignores message breakpoints, as the agent sent none before them.
        self.messages = messages
        # False has the forced report keep its tool_choice, and so a breakpoint.
        self.flips = flips
        self.entries: dict[str, int] = {}
        # The next request's size, from its recorded call.
        self.prompt = 0

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        body: Json = json.loads(request.content)
        prefix = hashlib.sha256(json.dumps(body["tools"]).encode())
        for block in body["system"]:
            prefix.update(unmarked(block))
        system = prefix.hexdigest()
        read = self.entries.get(system, 0)
        written = self.system_tokens - read
        self.entries[system] = self.system_tokens
        if self.flips:
            prefix.update(json.dumps(body["tool_choice"]).encode())
        blocks = [block for message in body["messages"] for block in message["content"]]
        keys: list[str] = []
        for block in blocks:
            prefix.update(unmarked(block))
            keys.append(prefix.hexdigest())
        if self.messages and ("cache_control" in blocks[-1] or not self.flips):
            hits = (self.entries[key] for key in reversed(keys[-LOOKBACK:]) if key in self.entries)
            read = next(hits, read)
            written = self.prompt - read
            self.entries[keys[-1]] = self.prompt
        usage = {
            "input_tokens": self.prompt - read - written,
            "output_tokens": 0,
            "cache_read_input_tokens": read,
            "cache_creation_input_tokens": written,
        }
        return httpx2.Response(200, json=REPLY | {"usage": usage})


def turns(run: Json) -> Iterator[tuple[list[Message], tuple[str, ...] | None]]:
    """Each model turn of a recorded run, as the job asked it: its messages and allowed tools."""
    opening = brief(run["group_id"], datetime.fromisoformat(run["anchor"]))
    calls = {c["id"]: CallRow(c["name"], c["args"], c["error"], None) for c in run["tool_calls"]}
    steps: list[StepRecordedV2] = []
    for step in run["steps"]:
        tokens = None
        if step["tokens_input"] is not None:
            tokens = TokenUsage(input=step["tokens_input"], output=step["tokens_output"])
            messages = history(opening, steps, calls)
            if step["kind"] == "report" and run["report"]["partial"]:
                # Its last block is never marked, so its reason cannot change what is cached.
                yield [*messages, Message("user", text=FORCED)], (REPORT_TOOL,)
            else:
                yield messages, None
        steps.append(
            StepRecordedV2(
                kind=step["kind"],
                summary=step["summary"],
                tool_name=step["tool_name"],
                tool_call_id=step["tool_call_id"],
                result=step["result"],
                latency_ms=None,
                tokens=tokens,
            )
        )


async def replay(run: Json, cache: PromptCache) -> list[Json]:
    """The run's model calls, each model turn with the reads and writes `cache` gives it."""
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(cache.handle))
    provider = AnthropicProvider("replay", "replay", "https://replay.test", http)
    asked = turns(run)
    calls: list[Json] = []
    for call in run["model_calls"]:
        if call["role"] == "small" or call["outcome"] != "ok":
            calls.append(call)
            continue
        messages, allowed = next(asked)
        cache.prompt = call["input_tokens"]
        turn = await provider.generate(SYSTEM, messages, TOOLS, allowed, call["max_output_tokens"])
        usage = turn.usage
        calls.append(
            call | {"cached_tokens": usage.cached, "cache_write_tokens": usage.cache_writes}
        )
    return calls


class Tally(NamedTuple):
    reads: float
    writes: float
    uncached: float
    usd: float


@dataclass(frozen=True)
class Row:
    """One investigation: as recorded, with the breakpoint, and had a forced report kept it."""

    name: str
    scenario: str
    run: int
    calls: int
    forced: bool
    before: Tally
    after: Tally
    kept: Tally


def tally(calls: Sequence[Json], prices: Json) -> Tally:
    reads = sum(call["cached_tokens"] for call in calls)
    writes = sum(call["cache_write_tokens"] for call in calls)
    uncached = sum(call["input_tokens"] for call in calls) - reads - writes
    return Tally(reads, writes, uncached, score.cost(calls, prices)[0])


def measure(name: str, runs: Sequence[Json], prices: Json) -> list[Row]:
    """One pass, in the order it ran, on one cache per arm."""
    tokens = max(call["cached_tokens"] for run in runs for call in run["model_calls"])
    before, after = PromptCache(tokens, messages=False), PromptCache(tokens)
    kept = PromptCache(tokens, flips=False)
    rows: list[Row] = []
    for run in sorted(runs, key=lambda r: r["anchor"]):
        recorded = run["model_calls"]
        if asyncio.run(replay(run, before)) != recorded:
            sys.exit(f"{run['scenario']} run {run['run']}: the replay left its recorded usage")
        rows.append(
            Row(
                name,
                run["scenario"],
                run["run"],
                len(recorded),
                bool(run["report"]["partial"]),
                tally(recorded, prices),
                tally(asyncio.run(replay(run, after)), prices),
                tally(asyncio.run(replay(run, kept)), prices),
            )
        )
    return rows


def mean(rows: Sequence[Row], pick: Callable[[Row], Tally]) -> Tally:
    columns = zip(*map(pick, rows), strict=True)
    return Tally(*(sum(column) / len(rows) for column in columns))


def summary(rows: Sequence[Row]) -> list[str]:
    """Means per investigation, one line a pass and one for them all."""
    head = "| Pass | Investigations | Forced reports | Reads before | Reads after |"
    head += " Writes before | Writes after | Uncached before | Uncached after |"
    head += " Cost before | Cost after | Saved | Cost had the forced report kept the cache |"
    lines = [head, "|" + " --- |" * 13]
    passes = sorted({row.name for row in rows})
    for name, group in [(p, [r for r in rows if r.name == p]) for p in passes] + [("All", rows)]:
        was, now = mean(group, lambda r: r.before), mean(group, lambda r: r.after)
        kept = mean(group, lambda r: r.kept)
        lines.append(
            f"| {name} | {len(group)} | {sum(r.forced for r in group)} | {was.reads:,.0f} |"
            f" {now.reads:,.0f} | {was.writes:,.0f} | {now.writes:,.0f} | {was.uncached:,.0f} |"
            f" {now.uncached:,.0f} | ${was.usd:.5f} | ${now.usd:.5f} |"
            f" {1 - now.usd / was.usd:.0%} | ${kept.usd:.5f} |"
        )
    return lines


def markdown(rows: Sequence[Row], day: str) -> str:
    """The report: means per pass, the method's limits, then every investigation."""
    head = "| Pass | Scenario | Run | Model calls | Forced report | Reads before | Reads after |"
    head += " Writes before | Writes after | Uncached before | Uncached after |"
    head += " Cost before | Cost after |"
    lines = [
        f"# Prompt cache replay, {day}",
        "",
        "What each recorded Claude investigation reads from the prompt cache and writes to it, in"
        " tokens, before and after the breakpoint on the last message of a turn. No model was"
        " called (`make eval-cache`, `evals/harness/cache_replay.py`).",
        "",
        "Before is the recorded usage. After sends each run's real conversation through the real"
        " provider to a fake that keeps Anthropic's published cache rules, with each prompt's size"
        " from the recorded call. With message breakpoints ignored the fake gave every recorded"
        " call its recorded reads and writes. The runs' decisions stay as recorded. The real API"
        " may count a few uncached tokens after the last breakpoint, which the fake does not.",
        "",
        "Costs are the whole investigation at `evals/pricing.json`'s rates, output included."
        " A forced report changes `tool_choice`, which ends the messages cache: it reads only the"
        " tools and system prompt, and writes nothing. The last column prices that one read.",
        "",
        "## Means per investigation",
        "",
        *summary(rows),
        "",
        "## Every investigation",
        "",
        head,
        "|" + " --- |" * 13,
    ]
    for row in rows:
        was, now = row.before, row.after
        lines.append(
            f"| {row.name} | {row.scenario} | {row.run} | {row.calls} |"
            f" {'yes' if row.forced else 'no'} | {was.reads:,.0f} | {now.reads:,.0f} |"
            f" {was.writes:,.0f} | {now.writes:,.0f} | {was.uncached:,.0f} | {now.uncached:,.0f} |"
            f" ${was.usd:.5f} | ${now.usd:.5f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    prices = harness.load(harness.PRICING)["models"]
    scenarios = harness.load(harness.SET / "manifest.json")["scenarios"]
    passes: defaultdict[str, list[Json]] = defaultdict(list)
    for path in harness.CACHE.glob("*.json"):
        run = harness.load(path)
        if {call["provider"] for call in run.get("model_calls", ())} == {"anthropic"}:
            split = scenarios[run["scenario"]]["split"]
            passes[f"{run['anchor'][:10]} {split}"].append(run)
    rows = [row for name, runs in sorted(passes.items()) for row in measure(name, runs, prices)]
    if not rows:
        sys.exit(f"no all-Claude run in {harness.CACHE}")
    day = datetime.now(UTC).date().isoformat()
    report = harness.REPORTS / f"{day}-cache-replay.md"
    report.write_text(markdown(rows, day), encoding="utf-8")
    print("\n".join([*summary(rows), f"report: {report}"]))  # noqa: T201 - CLI output


if __name__ == "__main__":
    main()
