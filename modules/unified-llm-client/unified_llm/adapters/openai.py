"""OpenAI Responses API adapter (Spec §7.3-7.8).

Wraps the openai SDK's AsyncOpenAI client to implement the
ProviderAdapter interface with request/response/error/stream translation.

CRITICAL: This adapter uses the OpenAI **Responses API** (/v1/responses),
NOT the Chat Completions API. This means client.responses.create().
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import openai

from collections.abc import AsyncIterator

from unified_llm import errors
from unified_llm.adapters._openai_strict_schema import (
    make_openai_strict_schema as _make_strict_schema,
)
from unified_llm.types import (
    ContentKind,
    ContentPart,
    FinishReason,
    Message,
    RateLimitInfo,
    Request,
    Response,
    Role,
    StreamEvent,
    StreamEventType,
    Tool,
    ToolCall,
    ToolCallData,
    ToolChoice,
    Usage,
)


def _serialize_raw(obj: Any) -> dict[str, Any] | None:
    """Defensively serialize a provider SDK response to a JSON-serializable dict.

    Tries, in order:
    1. Already a dict — return as-is.
    2. Pydantic model_dump() — OpenAI SDK objects are pydantic v2 BaseModel.
    3. to_dict() — fallback for other SDK styles.
    4. vars() — for SimpleNamespace and plain objects.
    5. Fallback sentinel {"_unserializable": repr(obj)}.

    Returns None only if *obj* is None.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        try:
            result = model_dump()
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    try:
        d = vars(obj)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"_unserializable": repr(obj)}


def _parse_ratelimit_headers(headers: Any) -> RateLimitInfo | None:
    """Parse x-ratelimit-* HTTP headers into a RateLimitInfo.

    Supports both integer fields and the reset timestamp, which providers
    encode as either an ISO-8601 string or a Go-style duration
    (e.g. ``"6m5.128s"``, ``"1m"``, ``"100ms"``).

    Returns None when no recognised rate-limit header is present (so
    callers can skip setting Response.rate_limit entirely).
    """

    def _int(key: str) -> int | None:
        v = headers.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    def _reset_dt(key: str) -> datetime | None:
        v = headers.get(key)
        if not v:
            return None
        # ISO-8601 path (some providers send a timestamp)
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            pass
        # Go-style duration path: "6m5.128s", "1m30s", "5s", "100ms"
        m = re.fullmatch(
            r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?",
            v.strip(),
        )
        if m and any(g is not None for g in m.groups()):
            hours = int(m.group(1) or 0)
            minutes = int(m.group(2) or 0)
            seconds = float(m.group(3) or 0)
            millis = int(m.group(4) or 0)
            delta = timedelta(
                hours=hours, minutes=minutes, seconds=seconds, milliseconds=millis
            )
            return datetime.now(timezone.utc) + delta
        return None

    limit_req = _int("x-ratelimit-limit-requests")
    remaining_req = _int("x-ratelimit-remaining-requests")
    limit_tok = _int("x-ratelimit-limit-tokens")
    remaining_tok = _int("x-ratelimit-remaining-tokens")
    reset_at = _reset_dt("x-ratelimit-reset-requests")

    if all(
        v is None
        for v in [limit_req, remaining_req, limit_tok, remaining_tok, reset_at]
    ):
        return None

    return RateLimitInfo(
        requests_limit=limit_req,
        requests_remaining=remaining_req,
        tokens_limit=limit_tok,
        tokens_remaining=remaining_tok,
        reset_at=reset_at,
    )


class OpenAIAdapter:
    """OpenAI Responses API adapter."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        timeout: float | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if api_key is not None:
            kwargs["api_key"] = api_key
        if base_url is not None:
            kwargs["base_url"] = base_url
        if organization is not None:
            kwargs["organization"] = organization
        if project is not None:
            kwargs["project"] = project
        if timeout is not None:
            kwargs["timeout"] = timeout
        self._client = openai.AsyncOpenAI(**kwargs)

    @property
    def name(self) -> str:
        return "openai"

    # ------------------------------------------------------------------
    # Task 31: complete() Integration
    # ------------------------------------------------------------------

    async def complete(self, request: Request) -> Response:
        """Send a request, block until done, return full Response."""
        try:
            kwargs = self._translate_request(request)
            # ULM-5/ULM-6: use with_raw_response to access HTTP headers for
            # rate-limit info while still getting the parsed SDK object.
            raw_http = await self._client.responses.with_raw_response.create(**kwargs)
            raw = raw_http.parse()
            headers = raw_http.headers
            response = self._translate_response(raw)
            response.raw = _serialize_raw(raw)
            rate_limit = _parse_ratelimit_headers(headers)
            if rate_limit is not None:
                response.rate_limit = rate_limit
            return response
        except (openai.APIError, openai.APIConnectionError) as e:
            raise self._translate_error(e) from e

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        """Send a request, return async iterator of StreamEvent."""
        kwargs = self._translate_request(request)
        kwargs["stream"] = True

        try:
            raw_stream = await self._client.responses.create(**kwargs)
        except (openai.APIError, openai.APIConnectionError) as e:
            raise self._translate_error(e) from e

        model = request.model
        response_id = ""

        # Per-block state
        text_started = False
        current_tool_id: str = ""
        current_tool_name: str = ""
        tool_args_parts: list[str] = []
        tool_call_started = False

        try:
            async for event in raw_stream:
                event_type = getattr(event, "type", "")

                if event_type == "response.created":
                    resp_obj = getattr(event, "response", None)
                    if resp_obj:
                        response_id = getattr(resp_obj, "id", "")
                    yield StreamEvent(type=StreamEventType.STREAM_START)

                elif event_type == "response.output_text.delta":
                    if not text_started:
                        yield StreamEvent(type=StreamEventType.TEXT_START)
                        text_started = True
                    delta_text = getattr(event, "delta", "")
                    yield StreamEvent(type=StreamEventType.TEXT_DELTA, delta=delta_text)

                elif event_type == "response.output_text.done":
                    if text_started:
                        yield StreamEvent(type=StreamEventType.TEXT_END)
                        text_started = False

                elif event_type == "response.function_call_arguments.delta":
                    if not tool_call_started:
                        current_tool_id = getattr(event, "call_id", "") or getattr(
                            event, "item_id", ""
                        )
                        current_tool_name = getattr(event, "name", "")
                        tool_args_parts = []
                        tool_call_started = True
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_START,
                            tool_call=ToolCall(
                                id=current_tool_id,
                                name=current_tool_name,
                                arguments={},
                            ),
                        )
                    delta_args = getattr(event, "delta", "")
                    tool_args_parts.append(delta_args)
                    yield StreamEvent(
                        type=StreamEventType.TOOL_CALL_DELTA,
                        delta=delta_args,
                    )

                elif event_type == "response.function_call_arguments.done":
                    args_str = getattr(event, "arguments", "") or "".join(
                        tool_args_parts
                    )
                    try:
                        args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        args = {}
                    yield StreamEvent(
                        type=StreamEventType.TOOL_CALL_END,
                        tool_call=ToolCall(
                            id=current_tool_id,
                            name=current_tool_name,
                            arguments=args,
                            raw_arguments=args_str or None,
                        ),
                    )
                    tool_call_started = False

                elif event_type == "response.output_item.done":
                    # Output item completed — may carry function_call info
                    item = getattr(event, "item", None)
                    if item:
                        item_type = getattr(item, "type", "")
                        if item_type == "function_call" and not tool_call_started:
                            # Complete function call in one shot (non-streamed)
                            fc_id = getattr(item, "call_id", "") or getattr(
                                item, "id", ""
                            )
                            fc_name = getattr(item, "name", "")
                            fc_args_str = getattr(item, "arguments", "")
                            try:
                                fc_args = json.loads(fc_args_str) if fc_args_str else {}
                            except json.JSONDecodeError:
                                fc_args = {}
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL_START,
                                tool_call=ToolCall(
                                    id=fc_id, name=fc_name, arguments={}
                                ),
                            )
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL_END,
                                tool_call=ToolCall(
                                    id=fc_id,
                                    name=fc_name,
                                    arguments=fc_args,
                                    raw_arguments=fc_args_str or None,
                                ),
                            )

                elif event_type == "response.completed":
                    resp_obj = getattr(event, "response", None)
                    usage = self._extract_usage(resp_obj)
                    finish_reason = self._extract_finish_reason(resp_obj)

                    yield StreamEvent(
                        type=StreamEventType.FINISH,
                        finish_reason=finish_reason,
                        usage=usage,
                        response=Response(
                            id=response_id,
                            model=model,
                            provider="openai",
                            message=Message(role=Role.ASSISTANT, content=[]),
                            finish_reason=finish_reason,
                            usage=usage,
                        ),
                    )
        except (openai.APIError, openai.APIConnectionError) as e:
            raise self._translate_error(e) from e

    async def close(self) -> None:
        """Release resources."""
        await self._client.close()

    async def initialize(self) -> None:
        """Validate configuration on startup."""

    async def list_models(self) -> list[str]:
        """Return the live list of model ids served by this OpenAI client.

        Uses the same ``self._client`` (``AsyncOpenAI``) instance used for
        ``complete()`` and ``stream()``, so the returned ids are in the same
        namespace as what the adapter passes to ``responses.create(model=...)``.
        This is the foundation of the id-seam guarantee: the lister IS the
        generator.

        Returns the first page (openai serves all current models on one page).
        """
        page = await self._client.models.list()
        return [m.id for m in page.data]

    def supports_tool_choice(self, mode: str) -> bool:
        """Check if a particular tool choice mode is supported."""
        return mode in ("auto", "none", "required", "named")

    # ------------------------------------------------------------------
    # Task 28: Request Translation (Responses API)
    # ------------------------------------------------------------------

    def _translate_request(self, request: Request) -> dict[str, Any]:
        """Convert unified Request → OpenAI Responses API kwargs."""
        instructions_parts: list[str] = []
        input_items: list[dict[str, Any]] = []

        for msg in request.messages:
            if msg.role in (Role.SYSTEM, Role.DEVELOPER):
                # Extract to instructions parameter
                for part in msg.content:
                    if part.kind == ContentKind.TEXT and part.text:
                        instructions_parts.append(part.text)
            elif msg.role == Role.USER:
                content = self._translate_user_content(msg.content)
                input_items.append(
                    {"type": "message", "role": "user", "content": content}
                )
            elif msg.role == Role.ASSISTANT:
                # Assistant messages + any tool calls they contain
                self._translate_assistant_to_input(msg.content, input_items)
            elif msg.role == Role.TOOL:
                # Tool results as top-level function_call_output items
                self._translate_tool_results_to_input(msg.content, input_items)

        kwargs: dict[str, Any] = {
            "model": request.model,
            "input": input_items,
        }

        if instructions_parts:
            kwargs["instructions"] = "\n\n".join(instructions_parts)

        # Tools
        if request.tools:
            kwargs["tools"] = self._translate_tools(request.tools)

        # Tool choice
        if request.tool_choice:
            kwargs["tool_choice"] = self._translate_tool_choice(request.tool_choice)

        # Generation params
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.top_p is not None:
            kwargs["top_p"] = request.top_p
        if request.max_tokens is not None:
            kwargs["max_output_tokens"] = request.max_tokens
        if request.stop_sequences:
            kwargs["stop"] = request.stop_sequences

        # Response format (structured output via Responses API text.format)
        if request.response_format:
            fmt = request.response_format
            if fmt.type == "json_schema" and fmt.json_schema:
                if fmt.strict:
                    # ULM-16: OpenAI strict mode requires additionalProperties:false
                    # and required=[all property keys] on EVERY object node.
                    # A standard schema with optional fields (in properties but
                    # not required) causes a 400 at runtime.  Transform a deep
                    # copy of the user's schema to satisfy strict mode without
                    # mutating the caller's dict.  Originally-optional fields
                    # become nullable (type includes "null") so the model can
                    # signal "absent" as null rather than omitting the key.
                    schema = _make_strict_schema(fmt.json_schema)
                else:
                    schema = dict(fmt.json_schema)
                kwargs["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": schema.get("title", "response"),
                        "strict": fmt.strict,
                        "schema": schema,
                    }
                }
            elif fmt.type == "json":
                kwargs["text"] = {"format": {"type": "json_object"}}

        # Reasoning effort (for o-series models)
        if request.reasoning_effort is not None:
            kwargs["reasoning"] = {"effort": request.reasoning_effort}

        # Provider options escape hatch
        if request.provider_options and "openai" in request.provider_options:
            opts = request.provider_options["openai"]
            for k, v in opts.items():
                kwargs[k] = v

        # GPT-6.2 migration guide, "Update API and model parameters":
        # https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6.2-astra
        # Validate the FINAL wire request, including provider_options. Do not
        # silently discard explicit settings or rewrite effort/history. Exact
        # known IDs only: unknown/future models remain provider pass-through
        # (unified-llm spec §2.9); Responses is already our native API (§2.7).
        # extra_body is an SDK escape hatch whose keys override normal body
        # fields. Check that effective body too, without mutating either input.
        omit_type = getattr(openai, "Omit", ())
        body = {
            key: value
            for key, value in {**kwargs, **(kwargs.get("extra_body") or {})}.items()
            if not isinstance(value, omit_type)
        }
        if body.get("model") in ("gpt-6.2-sol", "gpt-6.2-astra"):
            for field in ("temperature", "top_p", "logprobs", "top_logprobs"):
                if body.get(field) is not None:
                    raise errors.ConfigurationError(
                        f"{body['model']} does not support {field}; omit it."
                    )
            if "message.output_text.logprobs" in (body.get("include") or []):
                raise errors.ConfigurationError(
                    f"{body['model']} does not support message.output_text.logprobs; "
                    "remove it from include."
                )
            reasoning = body.get("reasoning") or {}
            if reasoning.get("effort") in ("none", "minimal"):
                raise errors.ConfigurationError(
                    f"{body['model']} reasoning effort must not be none/minimal; "
                    "use low or medium, or omit reasoning for the provider default."
                )

        return kwargs

    def _translate_user_content(self, parts: list[ContentPart]) -> list[dict[str, Any]]:
        """Translate user content parts to Responses API format."""
        result: list[dict[str, Any]] = []
        for part in parts:
            if part.kind == ContentKind.TEXT and part.text is not None:
                result.append({"type": "input_text", "text": part.text})
            elif part.kind == ContentKind.IMAGE and part.image:
                if part.image.url:
                    result.append({"type": "input_image", "image_url": part.image.url})
                elif part.image.data:
                    media_type = part.image.media_type or "image/png"
                    b64 = base64.b64encode(part.image.data).decode()
                    result.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:{media_type};base64,{b64}",
                        }
                    )
            else:
                # ULM-10: No silent drops — fail loud for unsupported content kinds.
                raise errors.ConfigurationError(
                    f"OpenAI adapter: unsupported content kind {part.kind!r} in user "
                    "message. Only TEXT and IMAGE parts are supported for OpenAI user "
                    "messages. AUDIO and DOCUMENT parts are not yet implemented."
                )
        return result

    def _translate_assistant_to_input(
        self, parts: list[ContentPart], input_items: list[dict[str, Any]]
    ) -> None:
        """Translate assistant content parts to Responses API input items.

        Text parts go in a message item. Tool calls become top-level
        function_call items (Responses API convention).
        """
        text_content: list[dict[str, Any]] = []
        for part in parts:
            if part.kind == ContentKind.TEXT and part.text is not None:
                text_content.append({"type": "output_text", "text": part.text})
            elif part.kind == ContentKind.TOOL_CALL and part.tool_call:
                # First, flush any accumulated text as a message
                if text_content:
                    input_items.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": text_content,
                        }
                    )
                    text_content = []
                tc = part.tool_call
                arguments = (
                    json.dumps(tc.arguments)
                    if isinstance(tc.arguments, dict)
                    else tc.arguments
                )
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": tc.id,
                        "name": tc.name,
                        "arguments": arguments,
                    }
                )
            else:
                # ULM-10: No silent drops — fail loud for unsupported content kinds.
                raise errors.ConfigurationError(
                    f"OpenAI adapter: unsupported content kind {part.kind!r} in "
                    "assistant message. Supported: TEXT, TOOL_CALL. AUDIO and DOCUMENT "
                    "parts are not supported."
                )

        # Flush remaining text
        if text_content:
            input_items.append(
                {"type": "message", "role": "assistant", "content": text_content}
            )

    def _translate_tool_results_to_input(
        self, parts: list[ContentPart], input_items: list[dict[str, Any]]
    ) -> None:
        """Translate tool result content to function_call_output items."""
        for part in parts:
            if part.kind == ContentKind.TOOL_RESULT and part.tool_result:
                tr = part.tool_result
                output = (
                    tr.content
                    if isinstance(tr.content, str)
                    else json.dumps(tr.content)
                )
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": tr.tool_call_id,
                        "output": output,
                    }
                )

    def _translate_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        """Translate Tool definitions to OpenAI Responses API format."""
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in tools
            if isinstance(tool, Tool)
        ]

    def _translate_tool_choice(self, tool_choice: ToolChoice) -> Any:
        """Map unified ToolChoice to OpenAI format."""
        if tool_choice.mode == "auto":
            return "auto"
        elif tool_choice.mode == "none":
            return "none"
        elif tool_choice.mode == "required":
            return "required"
        elif tool_choice.mode == "named" and tool_choice.tool_name:
            return {"type": "function", "name": tool_choice.tool_name}
        return "auto"

    # ------------------------------------------------------------------
    # Task 29: Response Translation
    # ------------------------------------------------------------------

    def _translate_response(self, raw: Any) -> Response:
        """Convert OpenAI Responses API response → unified Response."""
        content_parts: list[ContentPart] = []

        output = getattr(raw, "output", []) or []
        for item in output:
            item_type = getattr(item, "type", "")
            if item_type == "message":
                for sub_content in getattr(item, "content", []):
                    sub_type = getattr(sub_content, "type", "")
                    if sub_type == "output_text":
                        content_parts.append(
                            ContentPart(
                                kind=ContentKind.TEXT,
                                text=getattr(sub_content, "text", ""),
                            )
                        )
            elif item_type == "function_call":
                fc_id = getattr(item, "call_id", "") or getattr(item, "id", "")
                fc_name = getattr(item, "name", "")
                fc_args_str = getattr(item, "arguments", "")
                try:
                    fc_args = json.loads(fc_args_str) if fc_args_str else {}
                except json.JSONDecodeError:
                    fc_args = {}
                content_parts.append(
                    ContentPart(
                        kind=ContentKind.TOOL_CALL,
                        tool_call=ToolCallData(
                            id=fc_id,
                            name=fc_name,
                            arguments=fc_args,
                            type="function",
                        ),
                    )
                )

        usage = self._extract_usage(raw)
        finish_reason = self._extract_finish_reason(raw)

        return Response(
            id=getattr(raw, "id", ""),
            model=getattr(raw, "model", ""),
            provider="openai",
            message=Message(role=Role.ASSISTANT, content=content_parts),
            finish_reason=finish_reason,
            usage=usage,
        )

    def _extract_finish_reason(self, raw: Any) -> FinishReason:
        """Extract finish reason from Responses API response."""
        if raw is None:
            return FinishReason(reason="other")

        status = getattr(raw, "status", "")
        # Check if there are function_call output items
        output = getattr(raw, "output", []) or []
        has_tool_calls = any(
            getattr(item, "type", "") == "function_call" for item in output
        )

        if has_tool_calls:
            return FinishReason(reason="tool_calls", raw=status)

        return self._map_finish_reason(status)

    def _map_finish_reason(self, status: str | None) -> FinishReason:
        """Map Responses API status to unified FinishReason."""
        mapping = {
            "completed": "stop",
            "incomplete": "length",
            "failed": "error",
        }
        reason = mapping.get(status or "", "other")
        return FinishReason(reason=reason, raw=status)

    def _extract_usage(self, raw: Any) -> Usage:
        """Extract usage from Responses API response."""
        if raw is None:
            return Usage(input_tokens=0, output_tokens=0, total_tokens=0)

        usage_obj = getattr(raw, "usage", None)
        if usage_obj is None:
            return Usage(input_tokens=0, output_tokens=0, total_tokens=0)

        input_tokens = getattr(usage_obj, "input_tokens", 0) or 0
        output_tokens = getattr(usage_obj, "output_tokens", 0) or 0
        total_tokens = getattr(usage_obj, "total_tokens", None)
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens

        # Reasoning tokens from output_tokens_details
        reasoning_tokens: int | None = None
        output_details = getattr(usage_obj, "output_tokens_details", None)
        if output_details:
            reasoning_tokens = getattr(output_details, "reasoning_tokens", None)

        # Cache read tokens from prompt_tokens_details (input_tokens_details)
        cache_read_tokens: int | None = None
        input_details = getattr(usage_obj, "input_tokens_details", None)
        if input_details:
            cache_read_tokens = getattr(input_details, "cached_tokens", None)

        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_read_tokens=cache_read_tokens,
        )

    # ------------------------------------------------------------------
    # Task 30: Error Translation
    # ------------------------------------------------------------------

    def _translate_error(self, error: Exception) -> errors.SDKError:
        """Map OpenAI SDK exception → unified error hierarchy."""
        if isinstance(error, openai.APITimeoutError):
            return errors.RequestTimeoutError(str(error), cause=error)

        if isinstance(error, openai.APIConnectionError):
            return errors.NetworkError(str(error), cause=error)

        if isinstance(error, openai.APIStatusError):
            status_code = error.status_code
            message = str(error)

            # Extract body / error code
            body = getattr(error, "body", None)
            raw = body if isinstance(body, dict) else None
            error_code = None
            if isinstance(body, dict):
                # OpenAI Responses API returns flat body: {message, type, code, param}
                # Chat Completions API nests under "error": {message, type, code}
                if "error" in body and isinstance(body["error"], dict):
                    err_obj = body["error"]
                    error_code = err_obj.get("code") or err_obj.get("type")
                else:
                    error_code = body.get("code") or body.get("type")

            # Extract Retry-After header
            retry_after: float | None = None
            response = getattr(error, "response", None)
            if response is not None and hasattr(response, "headers"):
                ra = response.headers.get("retry-after")
                if ra:
                    try:
                        retry_after = float(ra)
                    except ValueError:
                        pass

            return errors.error_from_status_code(
                status_code=status_code,
                message=message,
                provider="openai",
                error_code=error_code,
                raw=raw,
                retry_after=retry_after,
                cause=error,
            )

        # Generic fallback
        return errors.ProviderError(
            message=str(error),
            provider="openai",
            retryable=True,
            cause=error,
        )
