"""loop-amplifier-agent: adapter orchestrator hosting microsoft/amplifier-agent.

Lets a .dot pipeline node use amplifier-agent's ``Engine`` (a full,
self-contained coding-agent runtime -- its own bundle, its own tools, its own
provider mounting) as the node's worker, instead of this ecosystem's native
loop-agent. The engine stays agent-agnostic: this module is a second,
opt-in agent-entry option, never a forced dependency of the thin dot-runner
bundle (see behaviors/dot-runner-amplifier-agent.yaml, which is NOT included
by bundle.md).

THE MECHANISM (proven empirically before this module was written -- see
``/var/tmp/aa-probe/probe_q1_q2.py``, "Q1: can a host mount a foreign tool
module into Engine's session? Q2: does the model actually call it?" -- both
answered yes against a real Engine + real haiku turn):

  1. ``amplifier_agent_lib.engine.Engine`` takes a caller-injected
     ``turn_handler`` coroutine (``engine.py`` around the ``TurnHandler``
     type alias and ``Engine.__init__``). The vendored CLI's own handler
     factory, ``amplifier_agent_lib._runtime.make_turn_handler``, builds a
     handler that calls ``prepared.create_session()`` and then wires
     capabilities onto ``session.coordinator``.
  2. A CUSTOM turn handler can do the same ``create_session()`` call and
     additionally ``await session.coordinator.mount("tools", tool,
     name=tool.name)`` to add a tool amplifier-agent's own bundle never
     declared -- in this case the REAL ``tool-report-outcome`` module's
     ``ReportOutcomeTool``. This is the *same call shape*
     ``amplifier_agent_http/_session_runner.py`` uses (around line 134) to
     mount its own ``HostToolProxy`` tools onto a live per-turn coordinator,
     so it is not a fragile private hack -- it is the documented extension
     point for "give this turn a tool the baked-in bundle doesn't have".
  3. After the turn, ``tool.last_outcome`` holds the agent's
     ``report_outcome`` call arguments (or ``None`` if it never called the
     tool). This module reads that register and republishes it as
     ``metadata.report_outcome`` on an ``ORCHESTRATOR_COMPLETE`` event, in
     the exact envelope shape ``amplifier_module_loop_agent`` uses (see
     ``AgentOrchestrator._emit_completion`` there), which
     ``amplifier_module_loop_pipeline.backend._outcome_from_spawn_result``
     already knows how to read.

RECURSION GUARD: an agent entry that uses this orchestrator must declare
``session.orchestrator.module: loop-amplifier-agent`` (non-None, and not
``loop-pipeline``) in the pipeline's agent config, exactly like loop-agent
does today -- see ``amplifier_module_loop_pipeline.backend``'s
"loop-pipeline recursion guard" and this module's README.

STATE ISOLATION: a fresh ``Engine`` (and fresh amplifier-agent
``AmplifierSession``) is booted per ``execute()`` call. Nothing is cached
across pipeline-node invocations -- each node invocation gets an
independent amplifier-agent turn, mirroring the "no caching" per-node
isolation the dot-pipeline backend already assumes of its orchestrators.
"""

from __future__ import annotations

__amplifier_module_type__ = "orchestrator"

import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from amplifier_core.events import ORCHESTRATOR_COMPLETE

logger = logging.getLogger(__name__)

#: Identifies this orchestrator in the ORCHESTRATOR_COMPLETE envelope's
#: ``orchestrator`` field -- mirrors loop-agent's own ``"loop-agent"`` value
#: (amplifier_module_loop_agent/__init__.py's ``_emit_completion``).
ORCHESTRATOR_NAME = "loop-amplifier-agent"

#: Mirrors tool-report-outcome's own ``VALID_STATUSES`` -- the vocabulary a
#: verdict's ``status`` field must use for
#: ``backend.py::_outcome_from_spawn_result`` to accept it as explicit.
VALID_STATUSES = frozenset({"success", "fail", "partial_success", "retry"})

#: amplifier-agent's own baked-in default (bundle.md: ``default_provider:
#: anthropic``), used when the pipeline node's orchestrator_config carries no
#: ``llm_provider`` override.
DEFAULT_PROVIDER = "anthropic"

#: Appended to every prompt sent to the amplifier-agent turn so the child
#: knows to leave an explicit verdict behind (EXTENSIONS.md 35 transport).
_REPORT_OUTCOME_NUDGE = (
    "\n\nWhen you have finished this task, call the `report_outcome` tool "
    "exactly once with a final status of 'success', 'fail', "
    "'partial_success', or 'retry' (plus notes / preferred_label / "
    "context_updates as appropriate) before ending your turn."
)


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the loop-amplifier-agent orchestrator."""
    orchestrator = AmplifierAgentOrchestrator(coordinator, config or {})
    await coordinator.mount("orchestrator", orchestrator)
    logger.info("loop-amplifier-agent orchestrator mounted")


def _load_dependencies() -> SimpleNamespace:
    """Lazy-import the real amplifier-agent + tool-report-outcome seam.

    Extracted into its own function -- rather than inlined into
    ``_run_turn`` -- for two reasons:

    1. amplifier-agent requires Python >=3.12 and pulls in a heavy
       dependency tree (fastapi/uvicorn/mcp/...); nothing in this module's
       top-level import surface should require it just to construct an
       ``AmplifierAgentOrchestrator`` or run its hermetic unit tests.
    2. Hermetic unit tests monkeypatch THIS ONE seam
       (``amplifier_module_loop_amplifier_agent._load_dependencies``) with
       faithful fakes (a fake ``Engine`` whose ``submit_turn`` really does
       invoke the injected ``turn_handler``, a fake ``ReportOutcomeTool``
       exposing ``.last_outcome``) instead of requiring the real, network-
       fetching, Python-3.12-only library to be installed just to exercise
       the envelope-shape / config-mapping / fail-closed contracts this
       module owns.

    Returns a namespace of exactly the symbols ``_run_turn`` needs, mirroring
    the real modules the probe (``/var/tmp/aa-probe/probe_q1_q2.py``)
    exercised directly.
    """
    from amplifier_agent_cli.provider_sources import inject_provider
    from amplifier_agent_lib import __version__ as aaa_version
    from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
    from amplifier_agent_lib.engine import Engine
    from amplifier_agent_lib.protocol import (
        PROTOCOL_VERSION,
        server_default_capabilities,
    )
    from amplifier_agent_lib.protocol_points.defaults_cli import (
        CliApprovalSystem,
        CliDisplaySystem,
    )
    from amplifier_module_tool_report_outcome import ReportOutcomeTool

    return SimpleNamespace(
        aaa_version=aaa_version,
        load_and_prepare_cached=load_and_prepare_cached,
        Engine=Engine,
        PROTOCOL_VERSION=PROTOCOL_VERSION,
        server_default_capabilities=server_default_capabilities,
        CliApprovalSystem=CliApprovalSystem,
        CliDisplaySystem=CliDisplaySystem,
        inject_provider=inject_provider,
        ReportOutcomeTool=ReportOutcomeTool,
    )


class _NullStream:
    """Discards everything written to it.

    ``CliDisplaySystem`` needs a stream to write display events to; this
    adapter runs headless inside a pipeline node, so display output has
    nowhere sensible to go and is discarded rather than polluting the
    parent process's stdout/stderr.
    """

    def write(self, _data: str) -> int:
        return 0

    def flush(self) -> None:
        return None


class AmplifierAgentOrchestrator:
    """Orchestrator adapter: hosts an amplifier-agent Engine per invocation.

    Implements the ``Orchestrator`` protocol
    (``amplifier_core.interfaces.Orchestrator.execute``): ``execute(prompt,
    context, providers, tools, hooks, **kwargs) -> str``.

    Note that ``providers`` / ``tools`` (the PARENT session's mounted
    providers and tools) are accepted for protocol conformance but
    deliberately NOT used to drive the child turn: amplifier-agent boots its
    OWN bundle with its OWN provider mounting (see ``_run_turn``), because
    it is a self-contained agent runtime, not a participant in this
    kernel's provider/tool mount plan.
    """

    def __init__(self, coordinator: Any, config: dict[str, Any]) -> None:
        self._coordinator = coordinator
        self._config = config

    async def execute(
        self,
        prompt: str,
        context: Any,
        providers: dict[str, Any],
        tools: dict[str, Any],
        hooks: Any,
        coordinator: Any = None,
    ) -> str:
        """Run exactly one amplifier-agent turn and publish its completion envelope.

        Boots a fresh Engine (no caching -- per-node state isolation),
        mounts the real ``report_outcome`` tool onto the turn's session,
        runs the turn, and emits ONE ``ORCHESTRATOR_COMPLETE`` event mirroring
        loop-agent's envelope shape (EXTENSIONS.md 35) so
        ``backend.py::_outcome_from_spawn_result`` can recover an explicit
        verdict across the spawn boundary.
        """
        if coordinator is not None:
            self._coordinator = coordinator

        try:
            reply, last_outcome = await self._run_turn(prompt)
        except Exception:
            # A raised exception means the invocation never completed --
            # still owes the spawn boundary an envelope (mirrors loop-agent's
            # own cancelled/incomplete handling), but must NOT promote a
            # partial/absent report as a verdict.
            await self._emit_completion(hooks, status="incomplete", report_outcome=None)
            raise

        if (
            isinstance(last_outcome, dict)
            and last_outcome.get("status") in VALID_STATUSES
        ):
            report_outcome = last_outcome
        else:
            # FAIL-CLOSED (the whole point of this block): the turn
            # completed without ever calling report_outcome, or called it
            # with something malformed. Never fabricate success --
            # synthesize an explicit, non-passing verdict so a goal_gate
            # (and even a plain node) sees a real "needs another look"
            # signal instead of silently deriving SUCCESS from the
            # lifecycle status alone
            # (backend.py::_outcome_from_spawn_result's status-only
            # fallback, which is_explicit=False on purpose).
            reason = (
                f"amplifier-agent turn ended without a valid report_outcome "
                f"verdict (last_outcome={last_outcome!r}); treating as "
                f"retry rather than fabricating success."
            )
            logger.warning(reason)
            report_outcome = {"status": "retry", "notes": reason}

        await self._emit_completion(
            hooks, status="success", report_outcome=report_outcome
        )
        return reply

    async def _emit_completion(
        self,
        hooks: Any,
        *,
        status: str,
        report_outcome: dict[str, Any] | None,
    ) -> None:
        """Emit the single ORCHESTRATOR_COMPLETE envelope for an invocation.

        Mirrors ``amplifier_module_loop_agent.AgentOrchestrator._emit_completion``'s
        envelope shape exactly (``orchestrator`` / ``status`` / ``turn_count`` /
        ``metadata``) so the parent's reader
        (``backend.py::_outcome_from_spawn_result``) needs no adapter-specific
        branch.
        """
        metadata = (
            {"report_outcome": report_outcome}
            if isinstance(report_outcome, dict)
            else {}
        )
        await hooks.emit(
            ORCHESTRATOR_COMPLETE,
            {
                "orchestrator": ORCHESTRATOR_NAME,
                "status": status,
                # Exactly one amplifier-agent turn per execute() call (see
                # module docstring: no multi-round retry loop is
                # implemented here -- the amplifier-agent Engine's own
                # session.execute() already runs its own internal
                # tool-calling loop for this one turn).
                "turn_count": 1,
                "metadata": metadata,
            },
        )

    async def _run_turn(self, prompt: str) -> tuple[str, dict[str, Any] | None]:
        """Boot a fresh Engine, run exactly one turn, return (reply, last_outcome).

        Config-key mapping (``orchestrator_config`` keys the dot-pipeline
        backend passes blind -- see ``backend.py``'s spawn_kwargs
        construction):

          * ``llm_provider`` -> ``Engine`` provider injection. The probe-
            proven seam: ``prepared.mount_plan["providers"]`` must be
            cleared first (amplifier-agent's baked-in bundle declares 9
            install-only provider *stubs*; leaving them in place makes
            ``inject_provider`` a no-op -- "don't clobber existing" -- and
            mounting all 9 can trigger interactive OAuth for
            openai-chatgpt), then ``inject_provider(prepared, provider,
            effort_override=reasoning_effort)`` mounts exactly one real,
            credentialed provider module.
          * ``reasoning_effort`` -> forwarded to that same
            ``inject_provider`` call as ``effort_override`` (see
            ``amplifier_agent_cli.provider_sources.build_provider_entry``:
            "``effort_override``: When provided, injects
            ``config["effort"]``").
          * ``max_turns`` -> best-effort forward into
            ``prepared.mount_plan["session"]["orchestrator"]["config"]``.
            amplifier-agent's Engine has no "max turns" knob at the
            boot/turn-submit layer (a single ``execute()`` call here is a
            single ``submit_turn``); this mirrors how the dot-pipeline
            backend itself blindly forwards ``orchestrator_config`` keys to
            whatever orchestrator module is configured, honored only if
            the mounted session orchestrator (``loop-streaming`` by
            default) recognizes the key.
          * ``user_instructions`` -> appended to the prompt text handed to
            ``session.execute()`` (Layer-5 override), the same place the
            report_outcome nudge is appended.
        """
        deps = _load_dependencies()

        cfg = self._config
        max_turns = cfg.get("max_turns")
        llm_provider = cfg.get("llm_provider") or DEFAULT_PROVIDER
        reasoning_effort = cfg.get("reasoning_effort")
        user_instructions = cfg.get("user_instructions")

        working_dir = self._resolve_working_dir()

        prepared = await deps.load_and_prepare_cached(aaa_version=deps.aaa_version)

        # llm_provider -> Engine's provider injection (probe-proven seam).
        prepared.mount_plan["providers"] = []
        deps.inject_provider(prepared, llm_provider, effort_override=reasoning_effort)

        # max_turns -> best-effort forward into the session orchestrator's
        # own config (see docstring above).
        if max_turns is not None:
            session_plan = prepared.mount_plan.setdefault("session", {})
            orch_plan = session_plan.setdefault("orchestrator", {})
            orch_plan.setdefault("config", {})["max_turns"] = max_turns

        captured: dict[str, Any] = {}

        async def handler(ctx: Any) -> str:
            session = await prepared.create_session(
                session_id=ctx.session_id or None,
                session_cwd=working_dir,
                is_resumed=False,
            )
            session.coordinator.register_capability("display.emit", ctx.display.emit)

            async def _approval_request(_req: Any) -> dict[str, str]:
                return {"action": "accept"}

            session.coordinator.register_capability(
                "approval.request", _approval_request
            )

            # THE MECHANISM (see module docstring + probe_q1_q2.py): mount
            # the REAL tool-report-outcome module directly on the live
            # per-turn coordinator.
            tool = deps.ReportOutcomeTool(config={}, coordinator=session.coordinator)
            await session.coordinator.mount("tools", tool, name=tool.name)
            captured["tool"] = tool

            async with session:
                return await session.execute(ctx.prompt)

        display = deps.CliDisplaySystem(stream=_NullStream(), verbosity="quiet")
        approval = deps.CliApprovalSystem(mode="yes")
        engine = deps.Engine(
            turn_handler=handler,
            protocol_points={"approval": approval, "display": display},
        )

        init_params = {
            "protocolVersion": deps.PROTOCOL_VERSION,
            "clientInfo": {"name": ORCHESTRATOR_NAME, "version": "0.1.0"},
            "capabilities": dict(deps.server_default_capabilities()),
            "sessionId": "",
            "resume": False,
        }

        try:
            await engine.boot(init_params, bundle_override=prepared)
            full_prompt = self._build_prompt(prompt, user_instructions)
            result = await engine.submit_turn(
                {"sessionId": "", "turnId": "turn-1", "prompt": full_prompt}
            )
        finally:
            # A shutdown failure here must never mask whatever the try block
            # is already propagating (an exception from submit_turn, or its
            # result on the happy path) -- log-and-swallow rather than let a
            # raised shutdown exception replace the real one.
            try:
                await engine.shutdown()
            except Exception:
                logger.warning(
                    "engine.shutdown() failed during turn cleanup", exc_info=True
                )

        tool = captured.get("tool")
        last_outcome = getattr(tool, "last_outcome", None)
        return result["reply"], last_outcome

    def _resolve_working_dir(self) -> Path:
        """Resolve the child session's working directory.

        Priority: explicit config -> ``session.working_dir`` capability ->
        ``os.getcwd()``. Mirrors loop-agent's own
        ``_execute_session`` resolution order.
        """
        configured = self._config.get("working_dir")
        if isinstance(configured, str) and configured:
            return Path(configured)
        get_cap = getattr(self._coordinator, "get_capability", None)
        cap_val = get_cap("session.working_dir") if callable(get_cap) else None
        if isinstance(cap_val, str) and cap_val:
            return Path(cap_val)
        return Path(os.getcwd())

    @staticmethod
    def _build_prompt(prompt: str, user_instructions: str | None) -> str:
        """Compose the final prompt: base prompt + user_instructions + nudge."""
        parts = [prompt]
        if user_instructions:
            parts.append(f"\n\nAdditional instructions:\n{user_instructions}")
        parts.append(_REPORT_OUTCOME_NUDGE)
        return "".join(parts)
