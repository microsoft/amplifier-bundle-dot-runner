"""Contract tests for the `orchestrator:complete` completion envelope.

EXTENSIONS.md §35 ("Spawned-Agent Outcome Transport and `report_outcome`
Ordering Barrier") — both halves of which are now historical.

WAVE 5 repair (2026-08-30, maintainer ruling) REMOVED the verdict transport,
no compat window: `_emit_completion`'s `metadata` is always `{}` — there is
no channel left for a node's semantic verdict to ride out of a spawn boundary
on. The owner ruling of 2026-09-06 finished the job, deleting the ordering
barrier in `agent_session.py` as residue: it keyed on a tool that no longer
exists, so it could never fire again.

The tests this file used to carry for both mechanisms — a report's contents
reaching the envelope metadata, last-write-wins across a batch,
rejection/promotion semantics, and the barrier's terminate-the-invocation
control flow — tested mechanisms that no longer exist and are deleted with
them. See git history for `test_report_outcome_verdict_rides_the_completion_
envelope`, `test_successful_report_terminates_the_invocation`, and their
siblings.

What remains covered here never depended on either mechanism: lifecycle-only
`status` on the completion envelope (success / incomplete / cancelled), and
per-invocation `turn_count`. `metadata` is asserted to be `{}` on every test
below — the universal case now, not a special "no report" case.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_core.events import ORCHESTRATOR_COMPLETE
from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ToolResult
from amplifier_module_loop_agent import AgentOrchestrator

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _tool_response(*calls, text: str = "") -> ChatResponse:
    """A response carrying tool calls, optionally alongside assistant text."""
    return ChatResponse(
        content=[{"type": "text", "text": text}] if text else [],
        tool_calls=[
            ToolCall(id=cid, name=name, arguments=args) for cid, name, args in calls
        ],
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _make_hooks():
    hooks = MagicMock()
    hooks._emitted: list[tuple[str, dict]] = []

    async def _emit(event: str, data: dict):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_emit)
    return hooks


def _make_orchestrator(responses, tools=None, config=None):
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=responses)
    providers = {"test": provider}
    cfg = {"system_prompt": "You are a test coding agent.", **(config or {})}
    orch = AgentOrchestrator(coordinator=MagicMock(), config=cfg)
    return orch, MagicMock(), providers, dict(tools or {}), _make_hooks()


def _completions(hooks) -> list[dict]:
    return [d for name, d in hooks._emitted if name == ORCHESTRATOR_COMPLETE]


# ---------------------------------------------------------------------------
# The transport itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_still_returns_the_final_string():
    """§35 Compatibility: the `execute(...) -> str` contract is unchanged."""
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[_text_response("the answer")]
    )
    assert await orch.execute("go", ctx, provs, tools, hooks) == "the answer"


# ---------------------------------------------------------------------------
# Fail-closed: no verdict must never look like a verdict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_plain_prose_turn_leaves_metadata_empty():
    """A child that asserts nothing carries NO verdict — `metadata == {}`.

    This is what keeps a downstream goal gate fail-closed (EXTENSIONS.md §25):
    the parent sees a status-only completion and records `is_explicit=False`.
    """
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[_text_response("I did the thing, all good!")]
    )

    await orch.execute("go", ctx, provs, tools, hooks)

    envelope = _completions(hooks)[0]
    assert envelope["status"] == "success"
    assert envelope["metadata"] == {}


# ---------------------------------------------------------------------------
# Per-invocation isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_count_is_per_invocation():
    """`turn_count` counts THIS invocation's provider calls, not the session's."""
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[
            _tool_response(("tc1", "noop", {})),
            _text_response("done"),
            _text_response("second invocation"),
        ],
        tools={"noop": _make_noop_tool()},
    )

    await orch.execute("first", ctx, provs, tools, hooks)
    await orch.execute("second", ctx, provs, tools, hooks)

    first, second = _completions(hooks)
    assert first["turn_count"] == 2
    assert second["turn_count"] == 1


def _make_noop_tool():
    tool = MagicMock()
    tool.name = "noop"
    tool.description = "no-op"
    tool.input_schema = {"type": "object", "properties": {}}
    tool.execute = AsyncMock(return_value=ToolResult(success=True, output="ok"))
    return tool


# ---------------------------------------------------------------------------
# Interrupted invocations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_raising_invocation_still_emits_exactly_one_envelope():
    """An exception must not swallow the envelope — the spawn boundary needs it."""
    boom = RuntimeError("provider exploded")
    orch, ctx, provs, tools, hooks = _make_orchestrator(responses=[boom])

    with pytest.raises(RuntimeError, match="provider exploded"):
        await orch.execute("go", ctx, provs, tools, hooks)

    completions = _completions(hooks)
    assert len(completions) == 1
    assert completions[0]["status"] == "incomplete"
    assert completions[0]["metadata"] == {}


@pytest.mark.asyncio
async def test_a_cancelled_invocation_reports_cancelled_and_no_verdict():
    """Cancellation is a lifecycle state, and never promotes a partial report."""
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[asyncio.CancelledError()]
    )

    with pytest.raises(asyncio.CancelledError):
        await orch.execute("go", ctx, provs, tools, hooks)

    completions = _completions(hooks)
    assert len(completions) == 1
    assert completions[0]["status"] == "cancelled"
    assert completions[0]["metadata"] == {}


@pytest.mark.asyncio
async def test_a_turn_limited_invocation_is_incomplete():
    """max_turns is a wall, not a completion — lifecycle status says so."""
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[_text_response("never reached")],
        config={"max_turns": 1},
    )

    await orch.execute("go", ctx, provs, tools, hooks)

    envelope = _completions(hooks)[0]
    assert envelope["status"] == "incomplete"
    assert envelope["metadata"] == {}
