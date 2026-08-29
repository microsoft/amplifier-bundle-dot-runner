"""Contract tests for the `orchestrator:complete` completion envelope.

EXTENSIONS.md §35 ("Spawned-Agent Outcome Transport and `report_outcome`
Ordering Barrier").

WAVE 5 repair (2026-08-30, maintainer ruling): `report_outcome` the VERDICT
TRANSPORT is REMOVED, no compat window. `_emit_completion`'s `metadata` is
always `{}` now -- there is no `metadata.report_outcome` channel left for a
node's semantic verdict to ride out of a spawn boundary on (see
`amplifier_module_loop_agent/__init__.py`'s WAVE 5 repair notes). The tests
this file used to carry for that channel (a report's contents reaching
`metadata.report_outcome`, last-write-wins across a batch, rejection/promotion
semantics, etc.) tested a mechanism that no longer exists and are deleted
with it -- see git history for `test_report_outcome_verdict_rides_the_
completion_envelope` and its siblings.

Two pieces of `report_outcome`-adjacent behavior are UNCHANGED and remain
covered below, because they never depended on the metadata channel:

  * the ORDERING BARRIER (`agent_session.py`): a successful tool call
    literally named `report_outcome` still terminates the `execute()`
    invocation immediately -- no further provider call. This is a
    tool-loop control-flow mechanic, not a metadata-population mechanic.
  * lifecycle-only `status` on the completion envelope (success / incomplete
    / cancelled), independent of any verdict.

`metadata` is asserted to be `{}` on every remaining test below -- this is
now the universal case (see `__init__.py`), not a special "no report" case.
"""

import asyncio
from typing import ClassVar
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


class _ReportOutcomeDouble:
    """Faithful stand-in for `tool-report-outcome`'s `ReportOutcomeTool`.

    loop-agent does not depend on the tool package, so the two behaviors the
    envelope actually relies on are reproduced here verbatim:

      * a SUCCESSFUL call overwrites `last_outcome` (last-write-wins);
      * a call that fails validation returns an unsuccessful ToolResult and
        leaves `last_outcome` untouched.

    WAVE 5 repair (2026-08-30): the metadata-population half of this
    contract is gone (see module docstring); this double is kept only to
    drive the still-real ordering-barrier test below, which depends on the
    tool call being *named* ``report_outcome`` and *succeeding*, not on
    anything it writes to ``metadata``.
    """

    name = "report_outcome"
    description = "Report the outcome of your work."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    }

    def __init__(self) -> None:
        self.last_outcome: dict | None = None

    async def execute(self, input: dict) -> ToolResult:
        status = input.get("status")
        if status not in {"success", "fail", "partial_success", "retry"}:
            return ToolResult(
                success=False,
                output=f"Invalid status: {status!r}",
                error={"message": "invalid status"},
            )
        self.last_outcome = {k: v for k, v in input.items() if v is not None}
        return ToolResult(success=True, output={"message": f"Outcome: {status}"})


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
async def test_no_report_outcome_leaves_metadata_empty():
    """A child that never reports carries NO verdict — `metadata == {}`.

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
# Last-declared-report-wins
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Terminal report path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_report_terminates_the_invocation():
    """A successful report ends the invocation — no further provider call.

    The provider below would raise if a second call were made, so this asserts
    the barrier rather than merely observing a convenient response list.
    """
    report = _ReportOutcomeDouble()
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[
            _tool_response(
                ("tc1", "report_outcome", {"status": "success"}),
                text="closing prose emitted alongside the verdict",
            ),
            AssertionError("a second provider call was made after report_outcome"),
        ],
        tools={"report_outcome": report},
    )

    result = await orch.execute("go", ctx, provs, tools, hooks)

    assert result == "closing prose emitted alongside the verdict"
    assert provs["test"].complete.await_count == 1
    assert _completions(hooks)[0]["turn_count"] == 1


@pytest.mark.asyncio
async def test_report_outcome_terminates_even_though_metadata_stays_empty():
    """WAVE 5 regression: the ordering barrier and the (removed) metadata
    channel are INDEPENDENT mechanisms. A successful `report_outcome` call
    still ends the invocation with no further provider call (unchanged tool-
    loop control flow), even though `metadata` carries none of its content
    anymore (the transport channel is gone). Proves the repair did not
    accidentally couple the two -- removing metadata population must not
    also have silently removed the termination behavior, or vice versa.
    """
    report = _ReportOutcomeDouble()
    orch, ctx, provs, tools, hooks = _make_orchestrator(
        responses=[
            _tool_response(
                (
                    "tc1",
                    "report_outcome",
                    {"status": "fail", "preferred_label": "escalate"},
                ),
                text="closing prose",
            ),
            AssertionError("a second provider call was made after report_outcome"),
        ],
        tools={"report_outcome": report},
    )

    result = await orch.execute("go", ctx, provs, tools, hooks)

    assert result == "closing prose"
    assert provs["test"].complete.await_count == 1, (
        "the ordering barrier must still terminate on a successful "
        "report_outcome call, independent of metadata population"
    )
    envelope = _completions(hooks)[0]
    assert envelope["status"] == "success"
    assert envelope["metadata"] == {}, (
        "report_outcome's content must never populate metadata anymore"
    )
    # The double itself still recorded the call (proves the tool DID run
    # and DID succeed -- the barrier fired for the right reason, not
    # because the call was silently skipped).
    assert report.last_outcome == {"status": "fail", "preferred_label": "escalate"}


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
