"""Provider layer types: the investigation job makes every model call through a Model.

agent.llm.Router is the real one (Gemini, NVIDIA NIM, OpenRouter, Ollama); ScriptedModel plays
fixed actions in tests. Every turn states its token budget, and a Model must keep within it: the
input under `TurnBudget.limits`' bound and the output under its max output tokens.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from agent.steps import StepRecordedV2, TokenUsage
from py_clients.generated.messages.report_recorded_v1_schema import Report

__all__ = ["Report"]

RootCause = Literal[
    "bad_deploy",
    "config_change",
    "dependency_failure",
    "resource_exhaustion",
    "traffic_surge",
    "database",
    "infrastructure",
    "certificate_expiry",
    "unknown",
]
Outcome = Literal["ok", "rate_limited", "quota_exhausted", "error"]
# Framing a provider adds around each message and tool definition, in tokens.
MESSAGE_OVERHEAD = 16
PROMPT_OVERHEAD = 256


class ToolRequest(BaseModel):
    """One read-only tool call the model asks for."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    args: dict[str, Any]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class HypothesisDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root_cause_class: RootCause
    service: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description=(
                "The component where the cause lives, often not the alerting service: the "
                "database, the DNS server, the workload on a shared node, the failing dependency."
            ),
        ),
    ]
    confidence: Annotated[float, Field(ge=0, le=1)]
    summary: Annotated[str, Field(min_length=1, max_length=500)]
    evidence_ids: Annotated[list[str], Field(max_length=20)]


class ReportDraft(BaseModel):
    """What the model submits through submit_report; the loop checks and completes it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    hypotheses: Annotated[list[HypothesisDraft], Field(max_length=5)]
    missing_evidence: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=500)]], Field(max_length=20)
    ] = []


@dataclass(frozen=True)
class Usage:
    input: int = 0
    # Thinking tokens included: providers bill them as output.
    output: int = 0
    cached: int = 0
    thoughts: int = 0


NO_USAGE = Usage()


@dataclass(frozen=True)
class ModelCall:
    """One request to a provider, recorded in agent.model_calls."""

    role: str
    provider: str
    model: str
    outcome: Outcome
    usage: Usage = Usage()
    input_bound: int | None = None
    max_output_tokens: int | None = None
    latency_ms: int = 0
    error: str | None = None


@dataclass(frozen=True)
class ModelTurn:
    action: ToolRequest | ReportDraft
    tokens: TokenUsage
    calls: Sequence[ModelCall] = ()
    # Opaque data the provider needs back with this call, such as a Gemini thought signature.
    provider_state: str | None = None
    # The model that answered: the next turn's input bound builds on its exact token count.
    model: str = "scripted"


class ModelError(Exception):
    """A failed model call. `calls` are the requests made, which the job records either way."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        retryable: bool = True,
        usage: Usage = NO_USAGE,
        calls: Sequence[ModelCall] = (),
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.retryable = retryable
        self.usage = usage
        self.calls = calls


class RateLimited(ModelError):
    """A 429 for requests or tokens per minute."""


class QuotaExhausted(ModelError):
    """The model's requests for the day are spent, by the limiter's count or the provider's."""


class BudgetRefused(Exception):
    """The turn's input alone would leave too few of the investigation's tokens for its output."""

    def __init__(self, calls: Sequence[ModelCall] = ()) -> None:
        super().__init__("the turn's input alone would leave too few tokens for its output")
        self.calls = calls


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCallPart:
    id: str
    name: str
    args: dict[str, Any]
    provider_state: str | None = None


@dataclass(frozen=True)
class ToolResultPart:
    id: str
    name: str
    content: str


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str | None = None
    call: ToolCallPart | None = None
    result: ToolResultPart | None = None


@dataclass(frozen=True)
class TurnBudget:
    """The hard token cap for one model call.

    `bound` is an upper bound on the input: the exact token count the same model reported for the
    last turn plus the UTF-8 bytes of what came after it (no tokenizer makes more tokens than
    bytes), or the bytes of the whole prompt when another model answered last. The output gets
    what is left, up to `output_cap`; with less than `min_output` left the turn is refused.
    """

    remaining: int
    prompt_bytes: int
    delta_bytes: int
    messages: int
    new_messages: int
    output_cap: int
    min_output: int
    last_model: str | None = None
    last_input: int = 0
    last_output: int = 0

    def bound(self, model: str) -> int:
        if model == self.last_model:
            new = self.delta_bytes + MESSAGE_OVERHEAD * self.new_messages
            return self.last_input + self.last_output + new
        return self.prompt_bytes + MESSAGE_OVERHEAD * self.messages + PROMPT_OVERHEAD

    def limits(self, model: str) -> tuple[int, int] | None:
        """(input bound, max output tokens), or None when the turn would not fit."""
        bound = self.bound(model)
        max_output = min(self.output_cap, self.remaining - bound)
        return None if max_output < self.min_output else (bound, max_output)


@dataclass(frozen=True)
class TurnRequest:
    steps: Sequence[StepRecordedV2]
    system: str
    messages: Sequence[Message]
    tools: Sequence[ToolSpec]
    budget: TurnBudget
    # None allows every tool; the forced final report allows submit_report only.
    allowed: tuple[str, ...] | None = None


class Model(Protocol):
    async def turn(self, request: TurnRequest) -> ModelTurn: ...

    def available(self, conn: Connection) -> bool:
        """Whether a turn can be answered now: false while the daily quota it needs is spent."""
        ...


def is_model_turn(step: StepRecordedV2) -> bool:
    """Model turns always record their tokens; tool results never do."""
    return step.tokens is not None


class ScriptedModel:
    """Plays fixed actions in order.

    It counts the model turns already recorded, so a resumed investigation goes on with the next
    action, as a real model goes on from the history. Like a real provider it stays within the
    turn's budget: input at most the bound, output at most the max output tokens. Asked for a
    forced report, it plays its next report, or fails when its script has none left.
    """

    def __init__(
        self, actions: Sequence[ToolRequest | ReportDraft], tokens: TokenUsage | None = None
    ) -> None:
        self.actions = list(actions)
        self.tokens = tokens or TokenUsage(input=0, output=0)

    async def turn(self, request: TurnRequest) -> ModelTurn:
        limits = request.budget.limits("scripted")
        if limits is None:
            raise BudgetRefused
        bound, max_output = limits
        done = sum(is_model_turn(step) for step in request.steps)
        action: ToolRequest | ReportDraft | None = self.actions[done]
        if request.allowed is not None and not isinstance(action, ReportDraft):
            reports = (a for a in self.actions[done:] if isinstance(a, ReportDraft))
            action = next(reports, None)
        if action is None:
            raise ModelError("the script has no report left", retryable=False)
        tokens = TokenUsage(
            input=min(self.tokens.input, bound), output=min(self.tokens.output, max_output)
        )
        usage = Usage(input=tokens.input, output=tokens.output)
        call = ModelCall("primary", "scripted", "scripted", "ok", usage, bound, max_output)
        return ModelTurn(action=action, tokens=tokens, calls=[call])

    def available(self, conn: Connection) -> bool:
        return True
