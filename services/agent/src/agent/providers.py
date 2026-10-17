"""Model providers behind one tool-calling interface, each returning the call and its usage.

Gemini goes through the google-genai SDK and Claude through the anthropic SDK; NVIDIA NIM,
OpenRouter and Ollama share one OpenAI-compatible client. All run on httpx2 clients with a
timeout, and none retries: agent.llm.Router and the job decide what happens after a failure.
"""

import base64
import json
import re
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast

import anthropic
import httpx2
from anthropic.types import (
    MessageParam,
    ToolChoiceParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlockParam,
)
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from agent.model import (
    CreditsExhausted,
    Message,
    ModelError,
    QuotaExhausted,
    RateLimited,
    ToolSpec,
    Usage,
)
from agent.settings import Role

REQUEST_TIMEOUT = 60.0
DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}
# Anthropic's 400 once a spend limit is reached ("You have reached your specified API usage
# limits"): like a 402 billing_error, nothing gets through until the month ends.
SPEND_LIMIT = re.compile(r"(spend|usage) limit", re.IGNORECASE)
# Gemini's documented value for a function call it did not sign, such as one replayed from
# another model's turn.
UNSIGNED = b"skip_thought_signature_validator"


@dataclass(frozen=True)
class ProviderTurn:
    tool: str
    args: dict[str, Any]
    provider_state: str | None
    usage: Usage


class Provider(Protocol):
    name: str
    model: str

    async def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        allowed: tuple[str, ...] | None,
        max_output_tokens: int,
    ) -> ProviderTurn:
        """One turn that must end in a call to one of the tools (only `allowed` ones if set)."""
        ...

    async def complete(self, system: str, text: str, max_output_tokens: int) -> tuple[str, Usage]:
        """Plain text in, plain text out."""
        ...


def make_provider(role: Role, http: httpx2.AsyncClient | None = None) -> Provider:
    if role.provider == "gemini":
        return GeminiProvider(role.model, role.api_key or "", base_url=role.base_url, http=http)
    base_url = role.base_url or DEFAULT_BASE_URLS[role.provider]
    if role.provider == "anthropic":
        return AnthropicProvider(role.model, role.api_key or "", base_url, http)
    return OpenAICompatibleProvider(role.provider, role.model, base_url, role.api_key, http)


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str | None = None,
        http: httpx2.AsyncClient | None = None,
    ) -> None:
        self.model = model
        options = types.HttpOptions(
            base_url=base_url,
            timeout=int(REQUEST_TIMEOUT * 1000),
            httpx_async_client=http or httpx2.AsyncClient(timeout=REQUEST_TIMEOUT),
        )
        # The explicit key wins over GOOGLE_API_KEY or GEMINI_API_KEY in the environment, and
        # the SDK sends it in the x-goog-api-key header, never in the URL.
        self._client = genai.Client(api_key=api_key, vertexai=False, http_options=options)

    async def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        allowed: tuple[str, ...] | None,
        max_output_tokens: int,
    ) -> ProviderTurn:
        declarations = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.parameters
            )
            for t in tools
        ]
        calling = types.FunctionCallingConfig(
            mode=types.FunctionCallingConfigMode.ANY,
            allowed_function_names=list(allowed) if allowed else None,
        )
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=declarations)],
            tool_config=types.ToolConfig(function_calling_config=calling),
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents: list[types.ContentUnion] = [_gemini_content(m) for m in messages]
        response = await self._generate(contents, config)
        usage = _gemini_usage(response.usage_metadata)
        candidate = response.candidates[0] if response.candidates else None
        parts = candidate.content.parts if candidate and candidate.content else None
        for part in parts or []:
            call = part.function_call
            if call is not None and call.name:
                signature = part.thought_signature
                state = base64.b64encode(signature).decode() if signature else None
                return ProviderTurn(call.name, dict(call.args or {}), state, usage)
        reason = candidate.finish_reason if candidate else None
        raise ModelError(f"gemini returned no tool call (finish reason {reason})", usage=usage)

    async def complete(self, system: str, text: str, max_output_tokens: int) -> tuple[str, Usage]:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        )
        user: list[types.ContentUnion] = [types.Content(role="user", parts=[types.Part(text=text)])]
        response = await self._generate(user, config)
        return response.text or "", _gemini_usage(response.usage_metadata)

    async def _generate(
        self, contents: list[types.ContentUnion], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        try:
            return await self._client.aio.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        except genai_errors.APIError as error:
            raise _gemini_error(error) from None


def _gemini_content(message: Message) -> types.Content:
    if message.call is not None:
        call = message.call
        signature = base64.b64decode(call.provider_state) if call.provider_state else UNSIGNED
        part = types.Part(
            function_call=types.FunctionCall(id=call.id, name=call.name, args=call.args),
            thought_signature=signature,
        )
        return types.Content(role="model", parts=[part])
    if message.result is not None:
        result = message.result
        response = types.FunctionResponse(
            id=result.id, name=result.name, response={"result": result.content}
        )
        return types.Content(role="user", parts=[types.Part(function_response=response)])
    return types.Content(role="user", parts=[types.Part(text=message.text or "")])


def _gemini_usage(meta: types.GenerateContentResponseUsageMetadata | None) -> Usage:
    if meta is None:
        return Usage()
    thoughts = meta.thoughts_token_count or 0
    return Usage(
        input=(meta.prompt_token_count or 0) + (meta.tool_use_prompt_token_count or 0),
        output=(meta.candidates_token_count or 0) + thoughts,
        cached=meta.cached_content_token_count or 0,
        thoughts=thoughts,
    )


class _GoogleDetail(BaseModel):
    retry_delay: str | None = Field(default=None, alias="retryDelay")


class _GoogleError(BaseModel):
    details: list[_GoogleDetail] = []


class _GoogleBody(BaseModel):
    """The parts of a Google API error body the agent reads."""

    error: _GoogleError = _GoogleError()


def _gemini_error(error: genai_errors.APIError) -> ModelError:
    code = error.code
    # The SDK leaves the parsed error body untyped.
    details: object = getattr(error, "details", None)
    if code == 429:
        retry_after = _retry_delay(details)
        # A per-day quota will not come back within this investigation's retries.
        if "PerDay" in json.dumps(details, default=str):
            return QuotaExhausted("gemini 429: daily quota spent", retry_after=retry_after)
        return RateLimited("gemini 429: rate limited", retry_after=retry_after)
    if code in (401, 403):
        return ModelError(f"gemini {code}: the API key was rejected", retryable=False)
    message = f"gemini {code} {error.status}: {error.message}"[:300]
    return ModelError(message, retryable=not 400 <= code < 500)


def _retry_delay(details: object) -> float | None:
    """RetryInfo's retryDelay ("17s") from a Google API error body, in seconds."""
    try:
        body = _GoogleBody.model_validate(details)
    except ValidationError:
        return None
    for detail in body.error.details:
        delay = detail.retry_delay or ""
        if delay.endswith("s") and delay[:-1].replace(".", "", 1).isdigit():
            return float(delay[:-1])
    return None


class AnthropicProvider:
    """Claude with native tool use. The system prompt and the tool definitions are cached: tools
    render before the system prompt, so its one breakpoint covers both."""

    name = "anthropic"

    def __init__(
        self, model: str, api_key: str, base_url: str, http: httpx2.AsyncClient | None = None
    ) -> None:
        self.model = model
        # The explicit key and URL win over ANTHROPIC_* variables in the environment, and SDK spans
        # never carry prompts, so no log line from a tool result reaches a span attribute.
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=REQUEST_TIMEOUT,
            max_retries=0,
            http_client=http,
            open_telemetry={"traces": {"content_mode": "metadata_only"}},
        )

    async def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        allowed: tuple[str, ...] | None,
        max_output_tokens: int,
    ) -> ProviderTurn:
        definitions: list[ToolParam] = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]
        # One forced call per turn, as the job runs one; Claude skips thinking on a forced call,
        # so no thinking block ever needs replaying.
        choice: ToolChoiceParam = (
            {"type": "tool", "name": allowed[0], "disable_parallel_tool_use": True}
            if allowed
            else {"type": "any", "disable_parallel_tool_use": True}
        )
        with _anthropic_errors():
            response = await self._client.messages.create(
                model=self.model,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[_anthropic_message(m) for m in messages],
                tools=definitions,
                tool_choice=choice,
                max_tokens=max_output_tokens,
            )
        usage = _anthropic_usage(response.usage)
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            message = f"anthropic declined the request (category {category})"
            raise ModelError(message, retryable=False, usage=usage)
        for block in response.content:
            if block.type == "tool_use":
                return ProviderTurn(block.name, dict(block.input), None, usage)
        reason = response.stop_reason
        raise ModelError(f"anthropic returned no tool call (stop reason {reason})", usage=usage)

    async def complete(self, system: str, text: str, max_output_tokens: int) -> tuple[str, Usage]:
        # A summary's few hundred output tokens leave no room for thinking.
        with _anthropic_errors():
            response = await self._client.messages.create(
                model=self.model,
                system=system,
                messages=[{"role": "user", "content": text}],
                max_tokens=max_output_tokens,
                thinking={"type": "disabled"},
            )
        reply = "".join(block.text for block in response.content if block.type == "text")
        return reply, _anthropic_usage(response.usage)


@contextmanager
def _anthropic_errors() -> Generator[None]:
    try:
        yield
    except anthropic.APIStatusError as error:
        raise _anthropic_error(error) from None


def _anthropic_message(message: Message) -> MessageParam:
    if message.call is not None:
        call = message.call
        use: ToolUseBlockParam = {
            "type": "tool_use",
            "id": call.id,
            "name": call.name,
            "input": call.args,
        }
        return {"role": "assistant", "content": [use]}
    if message.result is not None:
        result = message.result
        block: ToolResultBlockParam = {
            "type": "tool_result",
            "tool_use_id": result.id,
            "content": result.content,
        }
        return {"role": "user", "content": [block]}
    return {"role": "user", "content": message.text or ""}


def _anthropic_usage(usage: anthropic.types.Usage) -> Usage:
    """input_tokens counts only what follows the last cache breakpoint; the cap needs it all."""
    reads = usage.cache_read_input_tokens or 0
    writes = usage.cache_creation_input_tokens or 0
    return Usage(
        input=usage.input_tokens + reads + writes,
        output=usage.output_tokens,
        cached=reads,
        cache_writes=writes,
    )


def _anthropic_error(error: anthropic.APIStatusError) -> ModelError:
    """Credits gone (a 402 billing_error, a 400 naming a spend limit, or a 429 without
    retry-after) is CreditsExhausted: the router stops calling Claude until the month ends."""
    code = error.status_code
    detail = _api_message(error)
    if code == 402 or error.type == "billing_error" or (code == 400 and SPEND_LIMIT.search(detail)):
        return CreditsExhausted(f"anthropic {code}: credits or spend limit used up: {detail}"[:300])
    if code == 429:
        header = error.response.headers.get("retry-after")
        if header is None:
            return CreditsExhausted("anthropic 429 without retry-after: credits used up")
        delay = float(header) if header.replace(".", "", 1).isdigit() else None
        return RateLimited("anthropic 429: rate limited", retry_after=delay)
    if code in (401, 403):
        return ModelError(f"anthropic {code}: the API key was rejected", retryable=False)
    return ModelError(f"anthropic {code}: {detail}"[:300], retryable=code >= 500)


def _api_message(error: anthropic.APIStatusError) -> str:
    """The API's own message from the error body, else the SDK's."""
    body = error.body
    if isinstance(body, dict):
        inner = cast("dict[str, Any]", body).get("error")
        if isinstance(inner, dict):
            message = cast("dict[str, Any]", inner).get("message")
            if isinstance(message, str):
                return message
    return error.message


class _Function(BaseModel):
    name: str = ""
    arguments: str | None = None


class _ToolCall(BaseModel):
    function: _Function


class _Reply(BaseModel):
    content: str | None = None
    tool_calls: list[_ToolCall] | None = None


class _Choice(BaseModel):
    message: _Reply


class _PromptDetails(BaseModel):
    cached_tokens: int | None = None


class _CompletionDetails(BaseModel):
    reasoning_tokens: int | None = None


class _Usage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    prompt_tokens_details: _PromptDetails | None = None
    completion_tokens_details: _CompletionDetails | None = None


class _Completion(BaseModel):
    """The parts of an OpenAI-compatible chat completion the agent reads."""

    choices: list[_Choice] = []
    usage: _Usage | None = None


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: str,
        model: str,
        base_url: str,
        api_key: str | None,
        http: httpx2.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = http or httpx2.AsyncClient(timeout=REQUEST_TIMEOUT)

    async def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        allowed: tuple[str, ...] | None,
        max_output_tokens: int,
    ) -> ProviderTurn:
        choice: str | dict[str, Any] = "required"
        if allowed:
            choice = {"type": "function", "function": {"name": allowed[0]}}
        functions = [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in tools
        ]
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *map(_openai_message, messages)],
            "tools": [{"type": "function", "function": f} for f in functions],
            "tool_choice": choice,
            "max_tokens": max_output_tokens,
        }
        completion = await self._post(body)
        usage = _openai_usage(completion.usage)
        reply = completion.choices[0].message if completion.choices else _Reply()
        calls = reply.tool_calls or []
        if not calls:
            raise ModelError(f"{self.name} returned no tool call", usage=usage)
        function = calls[0].function
        try:
            args: object = json.loads(function.arguments or "{}")
        except json.JSONDecodeError:
            message = f"{self.name} returned arguments that are not JSON"
            raise ModelError(message, usage=usage) from None
        if not isinstance(args, dict):
            raise ModelError(f"{self.name} returned arguments that are not an object", usage=usage)
        # A JSON object's keys are strings.
        return ProviderTurn(function.name, cast("dict[str, Any]", args), None, usage)

    async def complete(self, system: str, text: str, max_output_tokens: int) -> tuple[str, Usage]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
            "max_tokens": max_output_tokens,
        }
        completion = await self._post(body)
        reply = completion.choices[0].message if completion.choices else _Reply()
        return reply.content or "", _openai_usage(completion.usage)

    async def _post(self, body: dict[str, Any]) -> _Completion:
        response = await self._http.post(self._url, json=body, headers=self._headers)
        code = response.status_code
        if code == 429:
            retry_after = response.headers.get("retry-after", "")
            delay = float(retry_after) if retry_after.isdigit() else None
            raise RateLimited(f"{self.name} 429: rate limited", retry_after=delay)
        if code in (401, 403):
            raise ModelError(f"{self.name} {code}: the API key was rejected", retryable=False)
        if code >= 400:
            message = f"{self.name} {code}: {response.text}"[:300]
            raise ModelError(message, retryable=code >= 500)
        try:
            return _Completion.model_validate_json(response.content)
        except ValidationError:
            raise ModelError(f"{self.name} answered with an unexpected body") from None


def _openai_message(message: Message) -> dict[str, Any]:
    if message.call is not None:
        call = message.call
        function = {"name": call.name, "arguments": json.dumps(call.args)}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call.id, "type": "function", "function": function}],
        }
    if message.result is not None:
        result = message.result
        return {"role": "tool", "tool_call_id": result.id, "content": result.content}
    return {"role": "user", "content": message.text or ""}


def _openai_usage(usage: _Usage | None) -> Usage:
    if usage is None:
        return Usage()
    prompt = usage.prompt_tokens_details or _PromptDetails()
    completion = usage.completion_tokens_details or _CompletionDetails()
    return Usage(
        input=usage.prompt_tokens or 0,
        output=usage.completion_tokens or 0,
        cached=prompt.cached_tokens or 0,
        thoughts=completion.reasoning_tokens or 0,
    )
