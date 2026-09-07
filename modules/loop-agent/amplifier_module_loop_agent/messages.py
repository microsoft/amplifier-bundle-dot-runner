"""History-to-messages conversion for LLM requests.

Spec coverage: LOOP-010, STEER-003, STEER-010.

Converts typed Turn history to Message objects suitable for ChatRequest.
Key behaviors:
- System messages are placed first regardless of history order.
- AssistantTurn reasoning is preserved as ThinkingBlock (with signature
  for multi-turn Anthropic conversations).
- SteeringTurns become user-role messages.
- ToolResultsTurn entries are mapped to individual tool-role messages
  with matching tool_call_id from the preceding AssistantTurn.
- Tool-result RETENTION (``tool_result_retention_turns``): tool results
  older than the retention window are replaced by a short stub. See
  ``_elision_stub`` and specs/EXTENSIONS.md Sec 45.
"""

from __future__ import annotations

from typing import Any, Iterable

from amplifier_core.message_models import (
    ContentBlockUnion,
    Message,
    TextBlock,
    ThinkingBlock,
)

from .turns import (
    AssistantTurn,
    SteeringTurn,
    SystemTurn,
    ToolResultsTurn,
    Turn,
    UserTurn,
)


#: Marker every elided tool result carries. Public so tests, log scanners,
#: and downstream consumers can detect an elision without string-matching a
#: format that may gain fields later.
TOOL_RESULT_ELIDED_MARKER = "[tool result elided:"


def _elision_stub(tool_name: str, char_count: int) -> str:
    """Replacement content for a tool result outside the retention window.

    Mirrors the truncation hook's contract (spec Section 5.1): say plainly
    that content was removed, how much, and how to get it back -- never
    silently drop it. The full output remains in the event stream
    (``agent:tool_call_end`` carries it untruncated), so "re-run to see" is
    the model's recovery path and the transcript is the operator's.
    """
    return f"{TOOL_RESULT_ELIDED_MARKER} {tool_name} {char_count} chars; re-run to see]"


def convert_history_to_messages(
    turns: Iterable[Turn],
    *,
    tool_result_retention_turns: int = 0,
) -> list[Message]:
    """Convert typed turn history to Message objects for ChatRequest.

    System messages are collected and placed first. All other messages
    preserve their relative order.

    Args:
        turns: The session's typed turn history.
        tool_result_retention_turns: How many of the MOST RECENT
            ``ToolResultsTurn`` groups keep their content verbatim. Older
            tool results are replaced by :func:`_elision_stub` -- the
            message itself (role, ``tool_call_id``, position) is always
            preserved, so provider-side tool_use/tool_result pairing is
            never broken. ``0`` (the default) disables elision entirely and
            reproduces the pre-retention behavior byte-for-byte.

            Only ``role="tool"`` content is ever elided. Assistant text,
            assistant reasoning, user turns and steering turns are always
            verbatim: the model's own record of what it decided and why is
            what makes an elided result recoverable, and eliding it would
            make the loop forget its own plan.
    """
    turn_list = list(turns)

    # Ordinal of the oldest tool-results turn that keeps its content. A
    # tool-results turn at ordinal < keep_from is elided. -1 keeps
    # everything (retention disabled, or history shorter than the window).
    keep_from = -1
    if tool_result_retention_turns > 0:
        total_tool_turns = sum(
            1 for t in turn_list if isinstance(t, ToolResultsTurn)
        )
        keep_from = total_tool_turns - tool_result_retention_turns

    system_messages: list[Message] = []
    other_messages: list[Message] = []
    pending_tool_calls: list[dict[str, Any]] = []
    tool_turn_ordinal = 0

    for turn in turn_list:
        if isinstance(turn, SystemTurn):
            system_messages.append(Message(role="system", content=turn.content))

        elif isinstance(turn, UserTurn):
            other_messages.append(Message(role="user", content=turn.content))

        elif isinstance(turn, SteeringTurn):
            # Steering turns become user messages (spec STEER-003)
            other_messages.append(Message(role="user", content=turn.content))

        elif isinstance(turn, AssistantTurn):
            msg = _build_assistant_message(turn)
            if turn.tool_calls:
                pending_tool_calls = turn.tool_calls
            other_messages.append(msg)

        elif isinstance(turn, ToolResultsTurn):
            elide = tool_turn_ordinal < keep_from
            tool_turn_ordinal += 1
            for i, result in enumerate(turn.results):
                call = pending_tool_calls[i] if i < len(pending_tool_calls) else None
                call_id = call["id"] if call else None
                serialized = result.get_serialized_output()
                content = (
                    _elision_stub((call or {}).get("name") or "tool", len(serialized))
                    if elide
                    else serialized
                )
                other_messages.append(
                    Message(
                        role="tool",
                        content=content,
                        tool_call_id=call_id,
                    )
                )
            pending_tool_calls = []

    return system_messages + other_messages


def _build_assistant_message(turn: AssistantTurn) -> Message:
    """Build a Message from an AssistantTurn with proper content blocks.

    If the turn has reasoning, content is a list of blocks:
        [ThinkingBlock(...), TextBlock(...)] -- the TextBlock is OMITTED
        when there is no non-whitespace text (see below).
    Otherwise content is the text string directly, or an empty list when
    there is no text but the turn issued tool calls.

    Anthropic's Messages API rejects ANY text content block whose text is
    empty ("messages: text content blocks must be non-empty"). This bites
    exactly when the model's response is tool-call-only -- no prose at all
    (e.g. its very first action is a bash tool_use). Previously this
    produced content="" (bare string) or
    content=[ThinkingBlock(...), TextBlock(text="")], and BOTH shapes get
    unconditionally wrapped/serialized into an empty text content part
    downstream, which providers reject on the request that follows (once
    the tool result turn is appended). Omitting the empty/whitespace-only
    text block here is the fix at the cause: history assembly never
    produces the invalid shape in the first place.

    Tool calls are passed as extra kwargs (Message uses extra="allow").
    """
    kwargs: dict[str, Any] = {"role": "assistant"}
    has_text = bool(turn.content and turn.content.strip())

    # Build content: use blocks when reasoning is present
    if turn.reasoning:
        blocks: list[ContentBlockUnion] = []
        # ThinkingBlock first (provider convention)
        thinking_kwargs: dict[str, Any] = {"thinking": turn.reasoning}
        if turn.reasoning_signature:
            thinking_kwargs["signature"] = turn.reasoning_signature
        blocks.append(ThinkingBlock(**thinking_kwargs))
        # Then text -- only when there is actual (non-whitespace) text.
        # An empty TextBlock alongside a tool-call-only response is
        # exactly the invalid shape described above.
        if has_text:
            blocks.append(TextBlock(text=turn.content))
        kwargs["content"] = blocks
    elif has_text:
        kwargs["content"] = turn.content
    elif turn.tool_calls:
        # No text, no reasoning, but this turn issued tool calls: use an
        # empty content list rather than a bare "" string. A bare ""
        # string is auto-wrapped downstream into a TEXT content part with
        # empty text -- the same bug, one layer up (see
        # unified_provider_adapter.py::_translate_content).
        kwargs["content"] = []
    else:
        # Genuinely empty turn (no text, no reasoning, no tool calls):
        # preserve existing behavior of an empty string.
        kwargs["content"] = ""

    # Tool calls (passed as extra field via extra="allow")
    if turn.tool_calls:
        # Emit the function name under BOTH "name" and "tool". Provider request
        # builders disagree on the key: the OpenAI provider reads tc.get("name")
        # (an empty name silently drops the function_call item from the Responses
        # API input, orphaning its function_call_output), while the Anthropic
        # provider reads tc.get("tool"). Carrying both keys is additive and keeps
        # us compatible with both until the ecosystem canonicalizes on one key.
        kwargs["tool_calls"] = [
            {
                "id": tc["id"],
                "name": tc["name"],
                "tool": tc["name"],
                "arguments": tc["arguments"],
            }
            for tc in turn.tool_calls
        ]

    return Message(**kwargs)
