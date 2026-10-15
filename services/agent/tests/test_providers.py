"""Both providers against recorded-shape responses: auth, request shape, usage and errors."""

import asyncio
import base64
import json
from typing import Any

import httpx2
import pytest

from agent.model import Message, ModelError, QuotaExhausted, RateLimited, ToolCallPart, ToolSpec
from agent.providers import UNSIGNED, GeminiProvider, OpenAICompatibleProvider

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


def recorder(status: int, body: Any) -> tuple[list[httpx2.Request], httpx2.AsyncClient]:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(status, json=body)

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


def test_a_rejected_gemini_key_is_not_retried() -> None:
    body = {"error": {"code": 401, "status": "UNAUTHENTICATED", "message": "bad key"}}
    _, provider = gemini(body, 401)

    with pytest.raises(ModelError) as raised:
        asyncio.run(provider.generate("system", HISTORY, TOOLS, None, 512))

    assert raised.value.retryable is False


def openai(status: int, body: Any) -> tuple[list[httpx2.Request], OpenAICompatibleProvider]:
    seen, http = recorder(status, body)
    provider = OpenAICompatibleProvider("nvidia", "m", "https://nim.test/v1", "nvapi-key", http)
    return seen, provider


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
