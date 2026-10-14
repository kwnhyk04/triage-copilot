"""Provider layer: the investigation job makes every model call through a Model.

Build step 6 adds the Gemini, NVIDIA NIM and Ollama providers. Until then the agent runs
ScriptedModel, which tests also use.
"""

from collections.abc import Sequence
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent.steps import StepRecordedV2, TokenUsage
from py_clients.generated.gateway import Hypothesis


class ToolRequest(BaseModel):
    """One read-only tool call the model asks for."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    args: dict[str, Any]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class Report(BaseModel):
    """The final report, structured so evals compare fields instead of prose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    hypotheses: list[Hypothesis]


class ModelTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: ToolRequest | Report
    tokens: TokenUsage


class ModelError(Exception):
    """A failed model call; retry_after is the provider's Retry-After in seconds, if it sent one."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Model(Protocol):
    async def turn(self, steps: Sequence[StepRecordedV2]) -> ModelTurn: ...


def is_model_turn(step: StepRecordedV2) -> bool:
    """Model turns always record their tokens; tool results never do."""
    return step.tokens is not None


class ScriptedModel:
    """Plays fixed actions in order.

    It counts the model turns already recorded, so a resumed investigation goes on with the next
    action, as a real model goes on from the history.
    """

    def __init__(
        self, actions: Sequence[ToolRequest | Report], tokens: TokenUsage | None = None
    ) -> None:
        self.actions = list(actions)
        self.tokens = tokens or TokenUsage(input=0, output=0)

    async def turn(self, steps: Sequence[StepRecordedV2]) -> ModelTurn:
        done = sum(is_model_turn(step) for step in steps)
        return ModelTurn(action=self.actions[done], tokens=self.tokens)
