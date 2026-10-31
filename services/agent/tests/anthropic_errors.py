"""Anthropic's answers as the API sends them, for tests that must make no live call.

The shape is Anthropic's error format (docs: API errors): a JSON body
{"type": "error", "error": {"type": ..., "message": ...}, "request_id": ...}, a request-id header,
and retry-after on a 429. The messages are Anthropic's own wording where Anthropic publishes one;
402 billing_error has no published example, so its message is a stand-in. None was captured from
this account: that would take a live call.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

import httpx2

# How long Claude stays off after the answer: for this call only, until the agent restarts (the
# key), or until the month ends (UTC; spent() reads the recorded outcome).
Stop = Literal["call", "restart", "month"]
REQUEST_ID = "req_011CTRIAGECOPILOT00000000"


@dataclass(frozen=True)
class Answer:
    name: str
    status: int
    kind: str
    message: str
    headers: dict[str, str] = field(default_factory=dict[str, str])

    def body(self) -> dict[str, Any]:
        error = {"type": self.kind, "message": self.message}
        return {"type": "error", "error": error, "request_id": REQUEST_ID}

    def response(self) -> httpx2.Response:
        return httpx2.Response(
            self.status, json=self.body(), headers={"request-id": REQUEST_ID, **self.headers}
        )


CREDIT_BALANCE = Answer(
    "credit balance too low",
    400,
    "invalid_request_error",
    "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing "
    "to upgrade or purchase credits.",
)
USAGE_LIMITS = Answer(
    "spend limit reached",
    400,
    "invalid_request_error",
    "You have reached your specified API usage limits. You will regain access on 2026-12-01 at "
    "00:00 UTC.",
)
BILLING = Answer("billing error", 402, "billing_error", "There is a problem with your billing.")
INVALID_KEY = Answer("invalid key", 401, "authentication_error", "invalid x-api-key")
NO_PERMISSION = Answer(
    "key without permission",
    403,
    "permission_error",
    "Your API key does not have permission to use the specified resource.",
)
BUSY = "This request would exceed the rate limit for your organization of 50 requests per minute."
RATE_LIMITED = Answer(
    "rate limited", 429, "rate_limit_error", BUSY, {"retry-after": "7", "x-should-retry": "true"}
)
RATE_LIMITED_BARE = Answer("rate limited, no retry-after", 429, "rate_limit_error", BUSY)
OVERLOADED = Answer("overloaded", 529, "overloaded_error", "Overloaded", {"x-should-retry": "true"})
SERVER_ERROR = Answer(
    "server error", 500, "api_error", "Internal server error", {"x-should-retry": "true"}
)
BAD_REQUEST = Answer(
    "bad request",
    400,
    "invalid_request_error",
    'messages: roles must alternate between "user" and "assistant"',
)
NO_MODEL = Answer("model not found", 404, "not_found_error", "model: claude-haiku-5-5")
TOO_LARGE = Answer(
    "request too large",
    413,
    "request_too_large",
    "Request exceeds the maximum allowed number of bytes.",
)

# Every answer, with the outcome recorded for Claude's call and how long Claude stays off.
ANSWERS: tuple[tuple[Answer, str, Stop], ...] = (
    (CREDIT_BALANCE, "credits_exhausted", "month"),
    (USAGE_LIMITS, "credits_exhausted", "month"),
    (BILLING, "credits_exhausted", "month"),
    (INVALID_KEY, "error", "restart"),
    (NO_PERMISSION, "error", "restart"),
    (RATE_LIMITED, "rate_limited", "call"),
    (RATE_LIMITED_BARE, "rate_limited", "call"),
    (OVERLOADED, "error", "call"),
    (SERVER_ERROR, "error", "call"),
    (BAD_REQUEST, "error", "call"),
    (NO_MODEL, "error", "call"),
    (TOO_LARGE, "error", "call"),
)

# A 200 whose model declined: Claude's stop reason "refusal", billed for its input.
REFUSAL: dict[str, Any] = {
    "id": "msg_01TRIAGECOPILOT",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-5-5",
    "content": [],
    "stop_reason": "refusal",
    "stop_sequence": None,
    "stop_details": {"type": "refusal", "category": "cyber", "explanation": None},
    "usage": {
        "input_tokens": 300,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 2_500,
    },
}
