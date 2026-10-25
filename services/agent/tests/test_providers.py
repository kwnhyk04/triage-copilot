"""The providers against recorded-shape responses: auth, request shape, usage and errors."""

import asyncio
import base64
import json
from typing import Any

import httpx2
import pytest

from agent.model import (
    CreditsExhausted,
    KeyRejected,
    Message,
    ModelError,
    ModelExhausted,
    QuotaExhausted,
    RateLimited,
    Refused,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from agent.providers import UNSIGNED, AnthropicProvider, GeminiProvider, OpenAICompatibleProvider

TOOLS = [ToolSpec("query_metrics", "Metrics.", {"type": "object", "properties": {}})]
HISTORY = [
    Message("user", text="Investigate."),
    Message("assistant", call=ToolCallPart("tc_0000000001", "query_metrics", {"query": "x"})),
]
GEMINI_REPLY = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {"name": "query_metrics", "args": {"query": "up"}},
                        "thoughtSignature": base64.b64encode(b"sig").decode(),
                    }
                ],
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 1200,
        "candidatesTokenCount": 30,
        "thoughtsTokenCount": 70,
        "cachedContentTokenCount": 1024,
    },
}


def recorder(
    status: int, body: Any, headers: dict[str, str] | None = None
) -> tuple[list[httpx2.Request], httpx2.AsyncClient]:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(status, json=body, headers=headers)

    return seen, httpx2.AsyncClient(transport=httpx2.MockTransport(handle))


def gemini(body: Any, status: int = 200) -> tuple[list[httpx2.Request], GeminiProvider]:
    seen, http = recorder(status, body)
    return seen, GeminiProvider("gemini-3.5-flash-lite", "AQ.right-key", http=http)


def test_gemini_sends_its_key_in_the_header_whatever_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "wrong-google-key")
    monkeypatch.setenv("GEMINI_API_KEY", "wrong-gemini-key")
    seen, provider = gemini(GEMINI_REPLY)

    turn = asyncio.run(provider.generate("system", HISTORY, TOOLS, None, 512))

    request = seen[0]
    assert request.headers["x-goog-api-key"] == "AQ.right-key"
    assert "key=" not in str(request.url)
    assert "AQ.right-key" not in str(request.url)
    signature = base64.b64decode(turn.provider_state or "")
    assert (turn.tool, turn.args, signature) == ("query_metrics", {"query": "up"}, b"sig")
    # Thinking counts as output; the cached prefix is reported.
    assert (turn.usage.input, turn.usage.output, turn.usage.cached) == (1200, 100, 1024)


def test_gemini_request_forces_a_tool_call_and_replays_unsigned_calls() -> None:
    seen, provider = gemini(GEMINI_REPLY)

    asyncio.run(provider.generate("system", HISTORY, TOOLS, ("submit_report",), 777))

    body = json.loads(seen[0].content)
    calling = body["toolConfig"]["functionCallingConfig"]
    assert (calling["mode"], calling["allowedFunctionNames"]) == ("ANY", ["submit_report"])
    assert body["generationConfig"]["maxOutputTokens"] == 777
    replayed = body["contents"][1]["parts"][0]
    assert base64.b64decode(replayed["thoughtSignature"]) == UNSIGNED


@pytest.mark.parametrize(
    ("quota_id", "error"),
    [
        ("GenerateRequestsPerDayPerProjectPerModel-FreeTier", QuotaExhausted),
        ("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", RateLimited),
    ],
)
def test_gemini_429_tells_a_spent_day_from_a_busy_minute(
    quota_id: str, error: type[ModelError]
) -> None:
    failure = {"@type": "type.googleapis.com/google.rpc.QuotaFailure"}
    retry = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}
    details = [failure | {"violations": [{"quotaId": quota_id}]}, retry]
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}
    _, provider = gemini(body, 429)

    with pytest.raises(error) as raised:
        asyncio.run(provider.generate("system", HISTORY, TOOLS, None, 512))

    assert raised.value.retry_after == 17.0


def openai(status: int, body: Any) -> tuple[list[httpx2.Request], OpenAICompatibleProvider]:
    seen, http = recorder(status, body)
    provider = OpenAICompatibleProvider("nvidia", "m", "https://nim.test/v1", "nvapi-key", http)
    return seen, provider


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_is_never_retried_on_any_provider(status: int) -> None:
    google = {"error": {"code": status, "status": "UNAUTHENTICATED", "message": "bad key"}}
    claude_body = {"type": "error", "error": {"type": "authentication_error", "message": "bad"}}
    providers = [gemini(google, status)[1], claude(status, claude_body)[1], openai(status, {})[1]]

    for provider in providers:
        with pytest.raises(KeyRejected) as raised:
            asyncio.run(provider.generate("system", HISTORY, TOOLS, None, 512))
        assert raised.value.retryable is False


def test_openai_compatible_request_and_reply() -> None:
    call = {"function": {"name": "query_metrics", "arguments": '{"query": "up"}'}}
    reply = {
        "choices": [{"message": {"content": None, "tool_calls": [call]}}],
        "usage": {
            "prompt_tokens": 900,
            "completion_tokens": 40,
            "completion_tokens_details": {"reasoning_tokens": 25},
        },
    }
    seen, provider = openai(200, reply)

    turn = asyncio.run(provider.generate("system", HISTORY, TOOLS, ("submit_report",), 300))

    request = seen[0]
    body = json.loads(request.content)
    assert request.headers["authorization"] == "Bearer nvapi-key"
    assert str(request.url) == "https://nim.test/v1/chat/completions"
    assert body["tool_choice"] == {"type": "function", "function": {"name": "submit_report"}}
    assert body["max_tokens"] == 300
    assert body["messages"][2]["tool_calls"][0]["id"] == "tc_0000000001"
    usage = (turn.usage.input, turn.usage.output, turn.usage.thoughts)
    assert (turn.tool, turn.args, usage) == ("query_metrics", {"query": "up"}, (900, 40, 25))


def test_openai_compatible_failures_keep_their_usage_and_retry_after() -> None:
    text_only = {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 50}}
    _, provider = openai(200, text_only)
    with pytest.raises(ModelError) as no_call:
        asyncio.run(provider.generate("system", HISTORY, TOOLS, None, 300))
    assert no_call.value.usage.input == 50

    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, headers={"retry-after": "9"}, json={})

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    limited = OpenAICompatibleProvider("nvidia", "m", "https://nim.test/v1", "k", http)
    with pytest.raises(RateLimited) as raised:
        asyncio.run(limited.generate("system", HISTORY, TOOLS, None, 300))
    assert raised.value.retry_after == 9.0


CLAUDE_REPLY: dict[str, Any] = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-5-5",
    "content": [
        {"type": "tool_use", "id": "toolu_01", "name": "query_metrics", "input": {"query": "up"}}
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 300,
        "output_tokens": 40,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 2_500,
    },
}


def claude(
    status: int, body: Any, headers: dict[str, str] | None = None
) -> tuple[list[httpx2.Request], AnthropicProvider]:
    seen, http = recorder(status, body, headers)
    provider = AnthropicProvider("claude-haiku-5-5", "sk-ant-right", "https://claude.test", http)
    return seen, provider


def test_claude_sends_its_key_and_caches_the_system_prompt_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "wrong-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "wrong-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://wrong.test")
    seen, provider = claude(200, CLAUDE_REPLY)
    result = ToolResultPart("tc_0000000001", "query_metrics", "[tc_0000000001] up 1")
    history = [*HISTORY, Message("tool", result=result)]

    turn = asyncio.run(provider.generate("system", history, TOOLS, None, 512))

    request = seen[0]
    assert request.headers["x-api-key"] == "sk-ant-right"
    assert "authorization" not in request.headers
    assert str(request.url) == "https://claude.test/v1/messages"
    body = json.loads(request.content)
    cached = {"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    assert body["system"] == [cached]
    assert body["tools"] == [
        {"name": "query_metrics", "description": "Metrics.", "input_schema": schema}
    ]
    assert body["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}
    assert (body["model"], body["max_tokens"]) == ("claude-haiku-5-5", 512)
    use = {
        "type": "tool_use",
        "id": "tc_0000000001",
        "name": "query_metrics",
        "input": {"query": "x"},
    }
    back = {
        "type": "tool_result",
        "tool_use_id": "tc_0000000001",
        "content": "[tc_0000000001] up 1",
    }
    assert body["messages"][1:] == [
        {"role": "assistant", "content": [use]},
        {"role": "user", "content": [back]},
    ]
    assert (turn.tool, turn.args) == ("query_metrics", {"query": "up"})
    # input_tokens counts only what follows the cache breakpoint; the token cap needs it all.
    usage = (turn.usage.input, turn.usage.cached, turn.usage.cache_writes, turn.usage.output)
    assert usage == (2_800, 2_500, 0, 40)


def test_claude_forces_the_report_and_summarizes_without_thinking() -> None:
    seen, provider = claude(200, CLAUDE_REPLY)
    asyncio.run(provider.generate("system", HISTORY, TOOLS, ("submit_report",), 4_096))
    choice = json.loads(seen[0].content)["tool_choice"]
    assert choice == {"type": "tool", "name": "submit_report", "disable_parallel_tool_use": True}

    text = {"content": [{"type": "text", "text": "- 40 timeouts"}], "stop_reason": "end_turn"}
    seen, provider = claude(200, CLAUDE_REPLY | text)
    summary, usage = asyncio.run(provider.complete("Summarize.", "lines", 400))
    body = json.loads(seen[0].content)
    assert (body["thinking"], body["max_tokens"]) == ({"type": "disabled"}, 400)
    assert (summary, usage.input) == ("- 40 timeouts", 2_800)


LIMITS = "You have reached your specified API usage limits. You will regain access on 2026-11-01."
BUSY = "This request would exceed your organization's rate limit."


@pytest.mark.parametrize(
    ("status", "kind", "message", "headers", "error"),
    [
        # Credits or the spend limit stop every Anthropic model; a bare 429 stops only this one.
        (402, "billing_error", "Your credit balance is too low.", None, CreditsExhausted),
        (400, "invalid_request_error", LIMITS, None, CreditsExhausted),
        (429, "rate_limit_error", BUSY, None, ModelExhausted),
        (429, "rate_limit_error", BUSY, {"retry-after": "7"}, RateLimited),
        (400, "invalid_request_error", "messages: roles must alternate", None, ModelError),
        (529, "overloaded_error", "Overloaded", None, ModelError),
    ],
)
def test_claude_tells_credits_gone_from_a_model_stop_and_a_busy_minute(
    status: int, kind: str, message: str, headers: dict[str, str] | None, error: type[ModelError]
) -> None:
    body = {"type": "error", "error": {"type": kind, "message": message}}
    _, provider = claude(status, body, headers)

    with pytest.raises(ModelError) as raised:
        asyncio.run(provider.generate("system", HISTORY, TOOLS, None, 512))

    assert type(raised.value) is error
    assert raised.value.retry_after == (7.0 if headers else None)
    # Only an overloaded or failing server is worth asking again.
    assert raised.value.retryable is (error is not ModelError or status >= 500)


def test_a_claude_refusal_is_never_retried_in_a_turn_or_a_summary() -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": None}
    refused: dict[str, Any] = {"content": [], "stop_reason": "refusal", "stop_details": details}
    _, provider = claude(200, CLAUDE_REPLY | refused)

    with pytest.raises(Refused) as raised:
        asyncio.run(provider.generate("system", HISTORY, TOOLS, None, 512))

    assert raised.value.retryable is False
    assert raised.value.usage.input == 2_800
    assert "category cyber" in str(raised.value)
    _, provider = claude(200, CLAUDE_REPLY | refused)
    with pytest.raises(Refused):
        asyncio.run(provider.complete("Summarize.", "lines", 400))
