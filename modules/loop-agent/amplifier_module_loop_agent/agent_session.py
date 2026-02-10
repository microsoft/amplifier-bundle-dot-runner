"""Core agentic loop for the coding agent.

Spec coverage: LOOP-001 through LOOP-023, STOP-001 through STOP-005,
ARCH-007, ARCH-008, EVENT-001 through EVENT-009, ERR-001 through ERR-013,
SHUT-001 through SHUT-009.

The AgentSession is the heart of the orchestrator. It holds conversation
state, dispatches tool calls, manages events, and enforces limits.
The core loop follows the spec's exact cadence:
    build request -> call LLM -> check tools -> execute -> repeat
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from amplifier_core.llm_errors import LLMError
from amplifier_core.message_models import (
    ChatRequest,
    ChatResponse,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolSpec,
)
from amplifier_core.models import ToolResult

from .config import SessionConfig
from .events import (
    AGENT_ASSISTANT_TEXT_END,
    AGENT_CONTEXT_WARNING,
    AGENT_ERROR,
    AGENT_LOOP_DETECTION,
    AGENT_SESSION_END,
    AGENT_SESSION_START,
    AGENT_STEERING_INJECTED,
    AGENT_TOOL_CALL_END,
    AGENT_TOOL_CALL_START,
    AGENT_TURN_LIMIT,
    AGENT_USER_INPUT,
    PROVIDER_ERROR,
    PROVIDER_REQUEST,
    PROVIDER_RESPONSE,
)
from .loop_detection import LoopDetector
from .messages import convert_history_to_messages
from .state import SessionState, SessionStateMachine
from .steering import FollowUpQueue, SteeringQueue
from .turns import (
    AssistantTurn,
    SessionHistory,
    SteeringTurn,
    ToolResultsTurn,
    UserTurn,
)

logger = logging.getLogger(__name__)


class AgentSession:
    """Manages a single coding agent session with the core agentic loop.

    Holds conversation history, state machine, and configuration.
    Persists across multiple process_input() calls so history carries over.
    """

    def __init__(
        self,
        config: SessionConfig,
        provider: Any,
        tools: dict[str, Any],
        hooks: Any,
        steering_queue: SteeringQueue | None = None,
        follow_up_queue: FollowUpQueue | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._tools = tools
        self._hooks = hooks
        self._state_machine = SessionStateMachine()
        self._history = SessionHistory()
        self._session_id = str(uuid.uuid4())
        self._session_started = False
        self._steering_queue = steering_queue or SteeringQueue()
        self._follow_up_queue = follow_up_queue or FollowUpQueue()
        self._loop_detector = LoopDetector(window_size=config.loop_detection_window)
        self._current_depth = config.current_depth

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_input(self, prompt: str) -> str:
        """Process a user input through the agentic loop.

        Appends user turn, calls LLM in a loop interleaved with tool
        execution, and returns the final text response.  The loop exits
        on natural completion (no tool calls), round limit, or turn limit.
        """
        # Emit session_start once (spec: SESSION_START on session creation)
        if not self._session_started:
            self._session_started = True
            await self._hooks.emit(
                AGENT_SESSION_START, {"session_id": self._session_id}
            )

        self._state_machine.submit()  # IDLE -> PROCESSING

        # Record user turn
        self._history.append(UserTurn(content=prompt))
        await self._hooks.emit(AGENT_USER_INPUT, {"content": prompt})

        # Drain steering before first LLM call (spec STEER-001)
        await self._drain_steering()

        round_count = 0
        last_text = ""

        while round_count < self._config.max_tool_rounds_per_input:
            # Check session-wide turn limit
            if (
                self._config.max_turns > 0
                and self._history.turn_count >= self._config.max_turns
            ):
                await self._hooks.emit(
                    AGENT_TURN_LIMIT,
                    {"total_turns": self._history.turn_count},
                )
                break

            # Build LLM request
            messages = self._convert_history_to_messages()
            tool_specs = self._get_tool_definitions()
            request = ChatRequest(
                messages=messages,
                tools=tool_specs,
                tool_choice="auto",
                reasoning_effort=self._config.reasoning_effort,
            )

            # Emit provider:request before LLM call
            await self._hooks.emit(PROVIDER_REQUEST, {})

            # Call LLM (single-shot, no SDK-level tool loop)
            try:
                response = await self._provider.complete(request)
            except LLMError as e:
                await self._emit_provider_error(e)
                await self._emit_error(str(e))
                if not e.retryable:
                    # Non-retryable (auth, context length) → CLOSED
                    self._state_machine.fatal_error()
                    await self._emit_session_end()
                raise
            except Exception as e:
                # Generic unexpected error → CLOSED
                await self._emit_error(str(e))
                self._state_machine.fatal_error()
                await self._emit_session_end()
                raise

            # Emit provider:response after LLM call with usage data
            usage_data = response.usage.model_dump() if response.usage else {}
            await self._hooks.emit(PROVIDER_RESPONSE, {"usage": usage_data})

            # Check context window usage (spec Section 5.5)
            await self._check_context_usage()

            # Extract text, reasoning, and thinking signature
            text = self._extract_text(response)
            reasoning = self._extract_reasoning(response)
            reasoning_sig = self._extract_reasoning_signature(response)
            if text:
                last_text = text

            # Record assistant turn
            tool_calls_data = []
            if response.tool_calls:
                tool_calls_data = [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                    }
                    for tc in response.tool_calls
                ]
            self._history.append(
                AssistantTurn(
                    content=text,
                    tool_calls=tool_calls_data,
                    reasoning=reasoning,
                    reasoning_signature=reasoning_sig,
                    usage=response.usage,
                )
            )

            # Emit assistant_text_end (spec EVENT-003)
            text_end_data: dict[str, Any] = {"text": text}
            if reasoning:
                text_end_data["reasoning"] = reasoning
            await self._hooks.emit(AGENT_ASSISTANT_TEXT_END, text_end_data)

            # Natural completion: no tool calls -> done
            if not response.tool_calls:
                self._state_machine.complete()  # PROCESSING -> IDLE
                await self._emit_session_end()
                # Process follow-up queue after loop completes
                return await self._process_follow_ups(text)

            # Execute tools in parallel
            results = await self._execute_tool_calls(response.tool_calls)
            self._history.append(ToolResultsTurn(results=results))
            round_count += 1

            # Record tool calls for loop detection
            if self._config.enable_loop_detection:
                for tc in response.tool_calls:
                    self._loop_detector.record(tc.name, tc.arguments)

            # Drain steering after each tool round (spec STEER-002)
            await self._drain_steering()

            # Check for loop detection (spec Section 2.10)
            await self._check_loop_detection()

        # Round limit reached
        await self._hooks.emit(AGENT_TURN_LIMIT, {"round_count": round_count})
        self._state_machine.complete()  # PROCESSING -> IDLE
        await self._emit_session_end()
        # Process follow-up queue after loop completes
        return await self._process_follow_ups(last_text)

    # ------------------------------------------------------------------
    # History -> Messages conversion
    # ------------------------------------------------------------------

    def _convert_history_to_messages(self) -> list[Message]:
        """Convert typed turn history to Message objects for ChatRequest.

        Delegates to the messages module which handles system-first
        ordering, content blocks, and ThinkingBlock preservation.
        """
        return convert_history_to_messages(self._history)

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def _get_tool_definitions(self) -> list[ToolSpec] | None:
        """Convert mounted tools to ToolSpec list for ChatRequest."""
        if not self._tools:
            return None
        return [
            ToolSpec(
                name=tool.name,
                description=tool.description,
                parameters=tool.input_schema,
            )
            for tool in self._tools.values()
        ]

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool_calls(self, tool_calls: list) -> list[ToolResult]:
        """Execute tool calls in parallel with asyncio.gather."""
        results = await asyncio.gather(
            *[self._execute_single_tool(tc) for tc in tool_calls]
        )
        return list(results)

    async def _execute_single_tool(self, tool_call: Any) -> ToolResult:
        """Execute a single tool call. Never raises — errors become results."""
        await self._hooks.emit(
            AGENT_TOOL_CALL_START,
            {"tool_name": tool_call.name, "call_id": tool_call.id},
        )

        start_time = time.monotonic()

        tool = self._tools.get(tool_call.name)
        if tool is None:
            duration_ms = (time.monotonic() - start_time) * 1000
            error_msg = f"Unknown tool: {tool_call.name}"
            await self._hooks.emit(
                AGENT_TOOL_CALL_END,
                {
                    "call_id": tool_call.id,
                    "error": error_msg,
                    "duration_ms": duration_ms,
                },
            )
            return ToolResult(success=False, output=error_msg)

        try:
            result = await tool.execute(tool_call.arguments)
            duration_ms = (time.monotonic() - start_time) * 1000
            await self._hooks.emit(
                AGENT_TOOL_CALL_END,
                {
                    "call_id": tool_call.id,
                    "output": str(result.output) if result.output else "",
                    "duration_ms": duration_ms,
                },
            )
            return result
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            error_msg = f"Tool error ({tool_call.name}): {e}"
            logger.error(error_msg)
            await self._hooks.emit(
                AGENT_TOOL_CALL_END,
                {
                    "call_id": tool_call.id,
                    "error": error_msg,
                    "duration_ms": duration_ms,
                },
            )
            return ToolResult(success=False, output=error_msg)

    # ------------------------------------------------------------------
    # Content extraction
    # ------------------------------------------------------------------

    def _extract_text(self, response: ChatResponse) -> str:
        """Extract text content from a ChatResponse's content blocks."""
        if not response.content:
            return ""
        parts = []
        for block in response.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
        return "\n\n".join(parts) if parts else ""

    def _extract_reasoning(self, response: ChatResponse) -> str | None:
        """Extract reasoning/thinking content from a ChatResponse."""
        if not response.content:
            return None
        parts = []
        for block in response.content:
            if isinstance(block, ThinkingBlock):
                parts.append(block.thinking)
        return "\n\n".join(parts) if parts else None

    def _extract_reasoning_signature(self, response: ChatResponse) -> str | None:
        """Extract ThinkingBlock signature for multi-turn preservation."""
        if not response.content:
            return None
        for block in response.content:
            if isinstance(block, ThinkingBlock) and block.signature:
                return block.signature
        return None

    # ------------------------------------------------------------------
    # Graceful shutdown (spec SHUT-001 through SHUT-009)
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Gracefully shut down the session.

        Spec ERR-015: Cancel in-flight work, emit session_end,
        transition to CLOSED. Idempotent — safe to call multiple times.
        """
        if self._state_machine.state == SessionState.CLOSED:
            return
        # Transition to CLOSED from any non-CLOSED state
        if self._state_machine.state == SessionState.PROCESSING:
            self._state_machine.fatal_error()
        elif self._state_machine.state == SessionState.AWAITING_INPUT:
            self._state_machine.abort()
        else:
            # IDLE → CLOSED
            self._state_machine.close()
        await self._emit_session_end()

    # ------------------------------------------------------------------
    # Error event helpers
    # ------------------------------------------------------------------

    async def _emit_error(self, message: str) -> None:
        """Emit agent:error event."""
        await self._hooks.emit(AGENT_ERROR, {"error": message})

    async def _emit_provider_error(self, error: LLMError) -> None:
        """Emit provider:error event with enriched LLMError data."""
        await self._hooks.emit(
            PROVIDER_ERROR,
            {
                "error": str(error),
                "retryable": error.retryable,
                "status_code": error.status_code,
                "provider": error.provider,
            },
        )

    async def _emit_session_end(self) -> None:
        """Emit agent:session_end with current state."""
        await self._hooks.emit(
            AGENT_SESSION_END,
            {"state": self._state_machine.state.value},
        )

    # ------------------------------------------------------------------
    # Steering (spec STEER-001 through STEER-010)
    # ------------------------------------------------------------------

    async def _drain_steering(self) -> None:
        """Drain pending steering messages into history.

        Each drained message becomes a SteeringTurn appended to history
        and an agent:steering_injected event is emitted.
        """
        messages = self._steering_queue.drain()
        for msg in messages:
            self._history.append(SteeringTurn(content=msg))
            await self._hooks.emit(AGENT_STEERING_INJECTED, {"content": msg})

    async def _process_follow_ups(self, last_result: str) -> str:
        """Process queued follow-up messages after the loop completes.

        Recursively calls process_input() for each follow-up message.
        Returns the result of the last processed message (or the
        original result if no follow-ups are pending).
        """
        result = last_result
        next_msg = self._follow_up_queue.drain()
        while next_msg is not None:
            result = await self.process_input(next_msg)
            next_msg = self._follow_up_queue.drain()
        return result

    # ------------------------------------------------------------------
    # Loop detection (spec Section 2.10)
    # ------------------------------------------------------------------

    async def _check_loop_detection(self) -> None:
        """Check for repeating tool call patterns and inject warning.

        If loop detection is enabled and a pattern is detected, injects
        a warning as a SteeringTurn and emits an agent:loop_detection event.
        """
        if not self._config.enable_loop_detection:
            return
        warning = self._loop_detector.check()
        if warning is not None:
            self._history.append(SteeringTurn(content=warning))
            await self._hooks.emit(AGENT_LOOP_DETECTION, {"warning": warning})
            # Reset detector after firing to avoid repeated warnings
            self._loop_detector.reset()

    # ------------------------------------------------------------------
    # Context window awareness (spec Section 5.5)
    # ------------------------------------------------------------------

    async def _check_context_usage(self) -> None:
        """Estimate context usage and emit warning if over 80%.

        Uses the heuristic: 1 token ~ 4 characters.
        Informational only — no automatic compaction.
        """
        window_size = self._config.context_window_size
        if window_size <= 0:
            return  # Unknown or unlimited — skip check

        # Estimate total characters across all messages
        messages = self._convert_history_to_messages()
        total_chars = 0
        for msg in messages:
            if isinstance(msg.content, str):
                total_chars += len(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        total_chars += len(block.text)
                    elif isinstance(block, ThinkingBlock):
                        total_chars += len(block.thinking)

        approx_tokens = total_chars / 4
        threshold = window_size * 0.8

        if approx_tokens > threshold:
            usage_percent = round(approx_tokens / window_size * 100)
            await self._hooks.emit(
                AGENT_CONTEXT_WARNING,
                {
                    "approx_tokens": int(approx_tokens),
                    "context_window_size": window_size,
                    "usage_percent": usage_percent,
                    "message": (
                        f"Context usage at ~{usage_percent}% "
                        f"of context window ({int(approx_tokens)}/{window_size} tokens)"
                    ),
                },
            )
