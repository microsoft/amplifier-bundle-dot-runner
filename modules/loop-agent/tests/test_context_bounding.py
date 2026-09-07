"""Per-call context bounding: the tool-result retention window.

THE MEASURED PROBLEM (read-only evidence, capsule-64-run2, worker session
``c2e6940c-4582-4569-9f51-d8d90ff44c48``): ONE node visit made 164 provider
calls. Input tokens per call went 24,334 -> 227,605 (median 161,866). The
history held 166 tool results totalling 382,222 chars, and the whole
accumulated pile was re-sent on every single call. Wall clock tracks
sum(input tokens): 60 minutes for that visit against 20 for round 1's
92-call, 17K->142K equivalent.

Crucially, NOT ONE of those 166 results exceeded its per-tool character
limit -- the largest was 17,112 chars against bash's 30,000 default. So
per-result truncation (spec Section 5.1, ``hooks-tool-truncation``) would
not have changed that run at all. The leak is ACCUMULATION, and it needs
its own bound.

These tests pin that bound at the one seam every provider request is built
through: ``AgentSession._convert_history_to_messages``.

Hermetic: fake provider, fake tool, no network, no keys. The truncation
test additionally mounts the REAL ``hooks-tool-truncation`` module on a
REAL ``amplifier_core.HookRegistry`` (see
``test_truncation_e2e_real_hook.py`` for that pattern's rationale).

RED-proof: ``test_disabled_retention_reproduces_the_measured_leak`` asserts
the OLD behavior (``tool_result_retention_turns=0``) on the same driver and
the same measurement -- linear growth, no plateau. It passes on main and
after this change alike, which is what makes the plateau assertions in the
other tests meaningful rather than tautological.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_core import HookRegistry
from amplifier_core.message_models import ChatRequest, ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult
from amplifier_module_hooks_tool_truncation import mount as mount_truncation_hook
from amplifier_module_loop_agent.agent_session import AgentSession
from amplifier_module_loop_agent.config import SessionConfig
from amplifier_module_loop_agent.messages import TOOL_RESULT_ELIDED_MARKER

#: Big enough that accumulation dominates every other payload term, and the
#: same order of magnitude as a real `cat` of a log file.
TOOL_OUTPUT_CHARS = 50_000

#: Long enough to run well past the default retention window (20) so the
#: post-window slope is measured on its own, not on the window filling up.
ROUNDS = 40


def _tool_response(call_id: str, tool_name: str, args: dict, text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=[ToolCall(id=call_id, name=tool_name, arguments=args)],
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _make_tool(name: str, output: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name}"
    tool.input_schema = {"type": "object", "properties": {}}
    tool.execute = AsyncMock(return_value=ToolResult(success=True, output=output))
    return tool


def _assistant_note(i: int) -> str:
    """Unique per-round assistant text -- the thing that must NEVER be elided."""
    return f"round {i}: reading the log, will summarize (marker-{i})"


def _payload_chars(request: ChatRequest) -> int:
    """Total characters this request would put on the wire.

    Counts every message's content (string form and block form, including
    thinking blocks) plus serialized tool-call arguments. This is the
    quantity the measured evidence's ``input_tokens`` curve tracks.
    """
    total = 0
    for msg in request.messages:
        content = msg.content
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                total += len(getattr(block, "text", "") or "")
                total += len(getattr(block, "thinking", "") or "")
        for call in getattr(msg, "tool_calls", None) or []:
            total += len(json.dumps(call.get("arguments", {})))
    return total


async def _drive(
    *,
    retention: int,
    rounds: int = ROUNDS,
    hooks=None,
    tool_output: str | None = None,
) -> tuple[list[int], ChatRequest]:
    """Run ``rounds`` tool rounds and return (per-call payload sizes, last request).

    Every round issues a tool call with DISTINCT arguments -- identical
    arguments would trip loop detection (window 10) and inject a steering
    turn, contaminating the measurement with content this test does not own.
    """
    tool = _make_tool("bash", tool_output or ("L" * TOOL_OUTPUT_CHARS))

    responses: list[ChatResponse] = [
        _tool_response(f"tc{i}", "bash", {"command": f"cat log-{i}.txt"}, _assistant_note(i))
        for i in range(rounds)
    ]
    responses.append(_text_response("done."))

    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=responses)

    session = AgentSession(
        config=SessionConfig(
            system_prompt="You are a test coding agent.",
            tool_result_retention_turns=retention,
        ),
        provider=provider,
        tools={"bash": tool},
        hooks=hooks if hooks is not None else AsyncMock(),
    )
    await session.process_input("summarize the logs")

    requests = [call[0][0] for call in provider.complete.call_args_list]
    assert len(requests) == rounds + 1, (
        f"driver did not run to completion: {len(requests)} provider calls, "
        f"expected {rounds + 1}"
    )
    return [_payload_chars(r) for r in requests], requests[-1]


def _tool_contents(request: ChatRequest) -> list[str]:
    return [m.content for m in request.messages if m.role == "tool"]


# ── The bound ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retention_window_plateaus_per_call_payload():
    """THE money assertion: past the window, per-call payload stops growing.

    Early slope (rounds 1-10, window still filling) is ~one full tool result
    per call. Late slope (rounds 31-40) is ~one STUB per call. On main --
    and with retention disabled, see the RED-proof test below -- the two
    slopes are identical, which is exactly the 24K->228K curve.
    """
    retention = SessionConfig().tool_result_retention_turns
    payloads, last = await _drive(retention=retention)

    growth_early = payloads[10] - payloads[1]
    growth_late = payloads[ROUNDS] - payloads[ROUNDS - 9]

    assert growth_early > 9 * (TOOL_OUTPUT_CHARS * 0.9), (
        "driver is not accumulating tool results at all -- the measurement "
        f"itself is broken (early growth {growth_early} chars over 9 calls)"
    )
    assert growth_late < 0.02 * growth_early, (
        "per-call payload is still growing at the pre-window rate: "
        f"late growth {growth_late} chars over 9 calls vs early "
        f"{growth_early}. The retention window is not bounding anything."
    )

    hard_bound = (retention + 2) * TOOL_OUTPUT_CHARS
    assert max(payloads) <= hard_bound, (
        f"peak payload {max(payloads)} chars exceeds the window's own hard "
        f"bound of {hard_bound} ({retention} retained results + slack)"
    )

    # Exactly `retention` results survive verbatim; the rest are stubs.
    contents = _tool_contents(last)
    assert len(contents) == ROUNDS
    verbatim = [c for c in contents if TOOL_RESULT_ELIDED_MARKER not in c]
    elided = [c for c in contents if TOOL_RESULT_ELIDED_MARKER in c]
    assert len(verbatim) == retention, (
        f"expected {retention} verbatim tool results, got {len(verbatim)}"
    )
    assert len(elided) == ROUNDS - retention
    # The stub names the tool and the size it stands in for -- never a bare
    # placeholder the model cannot act on.
    assert f"{TOOL_RESULT_ELIDED_MARKER} bash {TOOL_OUTPUT_CHARS} chars" in elided[0]
    assert "re-run to see" in elided[0]

    # The retained ones are the MOST RECENT ones (a window, not a sample).
    assert verbatim == contents[-retention:]


@pytest.mark.asyncio
async def test_assistant_text_is_never_elided():
    """The model's own record of what it decided survives verbatim, forever.

    This is the line between "bounded context" and "amnesia": an elided
    tool result is recoverable (re-run the tool); an elided decision is not.
    """
    _, last = await _drive(retention=SessionConfig().tool_result_retention_turns)

    assistant_text = "\n".join(
        m.content if isinstance(m.content, str) else
        "".join(getattr(b, "text", "") or "" for b in (m.content or []))
        for m in last.messages
        if m.role == "assistant"
    )
    missing = [i for i in range(ROUNDS) if _assistant_note(i) not in assistant_text]
    assert not missing, (
        f"assistant text from round(s) {missing} was dropped from the request "
        "-- retention must only ever touch role='tool' content"
    )

    # The user turn survives too.
    assert any(
        m.role == "user" and "summarize the logs" in str(m.content)
        for m in last.messages
    )


@pytest.mark.asyncio
async def test_elided_messages_keep_their_tool_call_id_and_position():
    """Pairing is structural, not cosmetic.

    Anthropic/OpenAI both reject a ``tool_use`` with no matching
    ``tool_result``. Eliding by DELETING old tool messages would break every
    request the moment the window slid; eliding their CONTENT does not.
    """
    _, last = await _drive(retention=5, rounds=12)

    tool_msgs = [m for m in last.messages if m.role == "tool"]
    assert len(tool_msgs) == 12
    assert [m.tool_call_id for m in tool_msgs] == [f"tc{i}" for i in range(12)]

    # Every assistant tool_call id has a tool message answering it, in order.
    call_ids = [
        c["id"]
        for m in last.messages
        if m.role == "assistant"
        for c in (getattr(m, "tool_calls", None) or [])
    ]
    assert call_ids == [m.tool_call_id for m in tool_msgs]


# ── The RED-proof / baseline ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_retention_reproduces_the_measured_leak():
    """``tool_result_retention_turns=0`` is main's behavior, and it grows linearly.

    This is the RED half of the proof, kept permanently: it pins what the
    other tests are measuring against. If this ever starts plateauing, the
    plateau assertions above have stopped meaning anything.
    """
    payloads, last = await _drive(retention=0)

    growth_early = payloads[10] - payloads[1]
    growth_late = payloads[ROUNDS] - payloads[ROUNDS - 9]

    assert growth_late > 0.9 * growth_early, (
        "retention=0 must reproduce the unbounded curve exactly: "
        f"early {growth_early} vs late {growth_late}"
    )
    assert max(payloads) > (ROUNDS - 2) * TOOL_OUTPUT_CHARS
    assert all(
        TOOL_RESULT_ELIDED_MARKER not in c for c in _tool_contents(last)
    ), "retention=0 must not elide anything at all"


# ── Composition with the spec's own per-result truncation ───────────────


@pytest.mark.asyncio
async def test_truncation_and_retention_compose():
    """Both bounds, together, on a real hook: one caps a result, one caps the pile.

    Spec Section 5.1 truncation bounds a SINGLE oversized result (and says
    so, in the marker). The retention window bounds their ACCUMULATION. They
    are independent, and the measured evidence needed the second one.
    """
    char_limit = 4_000
    retention = 20
    real_hooks = HookRegistry()
    coordinator = SimpleNamespace(
        get=lambda key: real_hooks if key == "hooks" else None
    )
    cleanup = await mount_truncation_hook(
        coordinator,
        config={"char_limits": {"bash": char_limit}, "line_limits": {}, "modes": {}},
    )
    assert cleanup is not None
    try:
        payloads, last = await _drive(retention=retention, hooks=real_hooks)
    finally:
        cleanup()

    contents = _tool_contents(last)
    verbatim = [c for c in contents if TOOL_RESULT_ELIDED_MARKER not in c]
    elided = [c for c in contents if TOOL_RESULT_ELIDED_MARKER in c]

    # Bound 1 (spec Section 5.1): each retained result carries the marker and
    # is near the configured limit, not the raw 50,000 chars.
    assert len(verbatim) == retention
    for content in verbatim:
        assert "[WARNING: Tool output was truncated" in content
        assert len(content) <= char_limit + 500

    # Bound 2 (this change): older ones are stubs, sized by what the LLM
    # actually saw (the truncated text), not the raw output.
    assert len(elided) == ROUNDS - retention
    assert f"{TOOL_RESULT_ELIDED_MARKER} bash" in elided[0]

    # Together: peak payload is bounded by the window, not by round count.
    assert max(payloads) <= (retention + 2) * (char_limit + 500)
