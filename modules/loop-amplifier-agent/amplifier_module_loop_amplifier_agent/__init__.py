"""loop-amplifier-agent: adapter orchestrator hosting microsoft/amplifier-agent.

Lets a .dot pipeline node use amplifier-agent's ``Engine`` (a full,
self-contained coding-agent runtime -- its own bundle, its own tools, its own
provider mounting) as the node's worker, instead of this ecosystem's native
loop-agent. The engine stays agent-agnostic: this module is a second,
opt-in agent-entry option, never a forced dependency of the thin dot-runner
bundle (see behaviors/dot-runner-amplifier-agent.yaml, which is NOT included
by bundle.md).

THE MECHANISM (WAVE 4 update, maintainer ruling 2026-08-29): this module
used to mount a REACH-IN copy of the real ``tool-report-outcome`` module
directly onto the hosted amplifier-agent's own per-turn coordinator
(``session.coordinator.mount("tools", tool, name=tool.name)``) so that a
foreign tool the baked-in bundle never declared could capture an explicit
verdict. Ruling 5 retires that: reaching into a DIFFERENT agent runtime's
internals to mount a tool it never asked for is exactly the kind of
internals-reach-in the ruling forbids, even though the mechanism itself was
proven to work (see the historical probe at
``/var/tmp/aa-probe/probe_q1_q2.py``).

The replacement is the spec's OWN channel (canonical Sec 4.5 / Appendix C):
``amplifier_module_loop_pipeline.backend`` injects the ABSOLUTE path to this
node's stage-directory ``status.json`` (plus the envelope contract) directly
into the prompt text handed to ``execute()`` below -- see
``amplifier_module_loop_pipeline.status_contract``. The hosted amplifier-agent
writes that file using its OWN file-editing tools (no mounting required,
because writing a file is not a foreign capability); the file's presence and
content are only ever read back by the PARENT process
(``handlers/codergen.py``'s ``read_status_override``, EXTENSIONS.md Sec 41),
entirely outside this module. This adapter's own seam shrinks to: spawn a
fresh Engine, hand it the (already-contract-carrying) prompt, run one turn,
and return the reply -- files and ``status.json`` are the channel, not a
tool call this module has to police.

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
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from amplifier_core.events import ORCHESTRATOR_COMPLETE

logger = logging.getLogger(__name__)

#: Identifies this orchestrator in the ORCHESTRATOR_COMPLETE envelope's
#: ``orchestrator`` field -- mirrors loop-agent's own ``"loop-agent"`` value
#: (amplifier_module_loop_agent/__init__.py's ``_emit_completion``).
ORCHESTRATOR_NAME = "loop-amplifier-agent"

#: amplifier-agent's own baked-in default (bundle.md: ``default_provider:
#: anthropic``), used when the pipeline node's orchestrator_config carries no
#: ``llm_provider`` override and no parent ``provider_preferences`` resolve.
DEFAULT_PROVIDER = "anthropic"

#: v2 gap 3 (approvals): worker-parity headless approval policy. Default
#: flipped from "deny" to "accept" after a design challenge (maintainer-
#: decided; see the README's "Approvals" section for the full rationale).
#: Three grounds:
#:   1. Worker parity: loop-agent -- the DEFAULT worker -- has NO approval
#:      system at all (coding-agent-loop spec sec8 excludes it deliberately),
#:      i.e. it is behaviorally accept-everything. "deny" made this adapter
#:      STRICTER than the default worker: switching workers would make
#:      previously-succeeding approval-gated actions silently start failing
#:      -- a parity violation of exactly the class already fixed twice
#:      (support#497).
#:   2. The attractor spec's own posture: sec6.4 defines the
#:      AutoApproveInterviewer ("Always selects YES") as the non-interactive
#:      default. Autonomous convergence is the point of a pipeline node.
#:   3. House doctrine -- gates outside workers: the safety layer is the
#:      graph's evidence gates and budget walls, not an approval prompt
#:      inside a headless worker nobody is watching. "deny"-by-default was
#:      interactive-CLI instinct imported into a headless context.
#: "deny" remains available as opt-in hardening. See ``_run_turn``'s
#: docstring for the logging-level and unknown-value handling this default
#: pairs with.
DEFAULT_APPROVAL_POLICY = "accept"
VALID_APPROVAL_POLICIES = frozenset({"accept", "deny"})


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
    from amplifier_agent_lib._runtime import prepare_bundle_for_session
    from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
    from amplifier_agent_lib.engine import Engine
    from amplifier_agent_lib.persistence import resolve_workspace
    from amplifier_agent_lib.protocol import (
        PROTOCOL_VERSION,
        server_default_capabilities,
    )
    from amplifier_agent_lib.protocol_points.defaults_cli import (
        ApprovalOverride,
        CliApprovalSystem,
        CliDisplaySystem,
    )
    from amplifier_agent_lib.spawn import hydrate_agent_overlay, spawn_sub_session
    from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider

    return SimpleNamespace(
        aaa_version=aaa_version,
        load_and_prepare_cached=load_and_prepare_cached,
        Engine=Engine,
        PROTOCOL_VERSION=PROTOCOL_VERSION,
        server_default_capabilities=server_default_capabilities,
        ApprovalOverride=ApprovalOverride,
        CliApprovalSystem=CliApprovalSystem,
        CliDisplaySystem=CliDisplaySystem,
        inject_provider=inject_provider,
        # v2 capability closures (see README "Capability gaps" section):
        prepare_bundle_for_session=prepare_bundle_for_session,  # gap 1
        resolve_workspace=resolve_workspace,  # gap 1
        hydrate_agent_overlay=hydrate_agent_overlay,  # gap 2 (cold-path agent_configs)
        spawn_sub_session=spawn_sub_session,  # gap 2
        WireApprovalProvider=WireApprovalProvider,  # gap 3
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

    ``context`` (the PARENT session's mounted ContextManager), by contrast,
    IS consumed: the foundation spawn path seeds prior-turn history into it as
    ``parent_messages`` before ``execute`` runs, and this adapter replays that
    history into the hosted amplifier-agent session so that fidelity="full"
    cross-node continuity actually reaches the model (support#497). See
    ``execute`` / ``_history_from_context`` / ``_run_turn``.
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
            # Session continuity (support#497): the parent dot-pipeline backend
            # delivers prior-turn history as ``parent_messages``; the foundation
            # spawn path seeds it into THIS (adapter) session's mounted context
            # BEFORE ``execute`` is called. Read it here and replay it into the
            # hosted amplifier-agent session in ``_run_turn`` -- otherwise the
            # hosted turn boots with an empty transcript and fidelity="full"
            # cross-node continuity is silently inert (the model only ever sees
            # the fresh prompt). Mirrors the library's own is_resumed replay
            # (amplifier_agent_lib._runtime: coordinator.get("context")
            # .set_messages). Kept INSIDE this try so that a context whose
            # get_messages() raises still emits the ORCHESTRATOR_COMPLETE
            # envelope the spawn boundary is owed -- like every other exit path.
            history = await self._history_from_context(context)
            reply = await self._run_turn(prompt, history=history)
        except Exception:
            # A raised exception means the invocation never completed --
            # still owes the spawn boundary an envelope (mirrors loop-agent's
            # own cancelled/incomplete handling), but must NOT promote a
            # partial/absent report as a verdict.
            await self._emit_completion(hooks, status="incomplete", report_outcome=None)
            raise

        # WAVE 4 (maintainer ruling 2026-08-29, EXTENSIONS.md Sec 35 RETCON
        # note): no in-process report_outcome capture exists anymore -- this
        # adapter no longer mounts a reach-in tool onto the hosted agent's
        # coordinator (ruling 5). It is NOT this module's job to fabricate an
        # explicit verdict from nothing: `metadata.report_outcome` stays
        # empty, so `backend.py::_outcome_from_spawn_result` falls through to
        # the lifecycle-status-only path (`is_explicit=False` -- cannot
        # satisfy a goal_gate on its own, exactly as intended). The REAL
        # explicit-verdict channel for this worker is the status-file
        # contract already embedded in `prompt` (see
        # `amplifier_module_loop_pipeline.status_contract`): if the hosted
        # amplifier-agent wrote <stage_dir>/status.json with its own file
        # tools, `handlers/codergen.py`'s `read_status_override` (running in
        # the PARENT process, after this method returns) picks it up --
        # entirely outside this adapter's control or knowledge.
        await self._emit_completion(hooks, status="success", report_outcome=None)
        return reply

    @staticmethod
    async def _history_from_context(
        context: Any,
    ) -> list[dict[str, Any]] | None:
        """Read prior-turn messages from the (already-seeded) adapter context.

        The foundation spawn path seeds ``parent_messages`` into this session's
        mounted context via ``set_messages`` before ``execute`` is called
        (``amplifier_foundation.bundle._prepared.PreparedBundle.spawn``). Return
        them so ``_run_turn`` can replay them into the hosted amplifier-agent
        session. Returns ``None`` when there is no context, no ``get_messages``
        surface, or no prior messages -- each meaning "fresh turn, nothing to
        replay".
        """
        if context is None or not hasattr(context, "get_messages"):
            return None
        messages = await context.get_messages()
        # A conforming ContextManager returns ``list[dict]``; anything else (a
        # misbehaving custom module) is treated as "nothing to replay" rather
        # than forwarded into ``set_messages`` as garbage.
        if not isinstance(messages, list):
            return None
        return messages or None

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

    async def _run_turn(
        self, prompt: str, history: list[dict[str, Any]] | None = None
    ) -> str:
        """Boot a fresh Engine, run exactly one turn, return the reply text.

        WAVE 4: no longer returns a captured ``last_outcome`` -- there is no
        more in-process report_outcome tool to capture it from (ruling 5).
        An explicit verdict, if any, reaches the parent exclusively via the
        status-file contract (``status_contract.py``) already embedded in
        ``prompt`` -- see ``execute()``'s docstring update below.

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
            effort_override=reasoning_effort, model_override=...)`` mounts
            exactly one real, credentialed provider module.

            **v2 precedence vs. parent ``provider_preferences`` (gap 4):**
            an explicit ``llm_provider`` always wins PROVIDER SELECTION.
            ``backend.py`` (loop-pipeline's spawn caller) says so itself:
            "Provider SELECTION ... flows via orchestrator_config
            ['llm_provider']" while ``provider_preferences`` exists purely
            to carry the resolved concrete MODEL, which "has no other
            channel." So: if ``llm_provider`` is set, it selects the
            provider, and the parent's preferred model is honored ONLY when
            it names that SAME provider (a model pinned for a different
            provider would be nonsensical to force onto an explicitly
            chosen one, so it is dropped rather than silently applied to
            the wrong provider). If ``llm_provider`` is absent, the parent
            preference's own provider+model wins outright. See
            ``_resolve_parent_provider_preference`` for how the preference
            is recovered (there is no ``provider_preferences`` parameter on
            ``Orchestrator.execute()`` at all -- see that method's
            docstring for the real extraction seam).
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
          * ``workspace`` (v2, gap 1) -> the CLI's ``--workspace`` argv-flag
            analogue, forwarded to ``amplifier_agent_lib.persistence.
            resolve_workspace`` (argv > ``AMPLIFIER_AGENT_WORKSPACE`` env >
            cwd-derived slug, unchanged precedence). Always resolved (even
            when absent) so the child turn gets a real, isolated
            context-intelligence workspace bucket instead of amplifier-agent's
            bare baked-in bundle -- see ``prepare_bundle_for_session`` below.
          * ``host_config`` (v2, gap 1, optional/advanced) -> forwarded
            verbatim as ``prepare_bundle_for_session``'s ``host_config``
            (the CLI's ``--config`` file analogue). No natural CLI-config-file
            source exists for an embedded pipeline node, so this defaults to
            ``None`` (no-op merge_config overlay); a pipeline author may
            opt in with a host_config-shaped dict for parity with the CLI.
          * ``approval_policy`` (v2, gap 3) -> ``"accept"`` or ``"deny"``
            (default ``"accept"``), governs the ``ApprovalOverride`` handed
            to ``CliApprovalSystem`` -- see the class-level
            ``DEFAULT_APPROVAL_POLICY`` docstring for the worker-parity /
            spec sec6.4 rationale for the default, and the approval-policy
            block below for logging levels and unknown-value handling.

        v2 (gap 1): calls the REAL vendored ``prepare_bundle_for_session``
        (skills/modes ``BUNDLE_DIR`` injection, host-config ``merge_config``
        overlay, hook-context-intelligence workspace seed) instead of
        reimplementing its three transforms -- judged fully applicable to an
        embedded node worker (see docstring above for the CLI-only bits that
        get no-op defaults here, not silently dropped).
        """
        deps = _load_dependencies()

        cfg = self._config
        max_turns = cfg.get("max_turns")
        reasoning_effort = cfg.get("reasoning_effort")
        user_instructions = cfg.get("user_instructions")

        working_dir = self._resolve_working_dir()

        prepared = await deps.load_and_prepare_cached(aaa_version=deps.aaa_version)

        # --- gap 1: prepare_bundle_for_session ------------------------------
        resolved_workspace = deps.resolve_workspace(
            argv_workspace=cfg.get("workspace"),
            env=os.environ,
            cwd=working_dir,
        )
        deps.prepare_bundle_for_session(
            prepared,
            host_config=cfg.get("host_config"),
            workspace=resolved_workspace,
        )

        # Pre-hydrate agent overlays (gap 2 cold path) once per turn so
        # session.spawn's delegate lookups need no per-call I/O. Mirrors
        # make_turn_handler's identical cold-path hydration.
        agent_configs: dict[str, dict[str, Any]] = {
            name: deps.hydrate_agent_overlay(Path(entry["source_path"]))
            for name, entry in (prepared.mount_plan.get("agents") or {}).items()
            if isinstance(entry, dict) and "source_path" in entry
        }

        # --- gap 4: provider_preferences vs. llm_provider precedence --------
        llm_provider_cfg = cfg.get("llm_provider")
        parent_preference = self._resolve_parent_provider_preference()
        if llm_provider_cfg:
            effective_provider = llm_provider_cfg
            model_override = (
                parent_preference[1]
                if parent_preference is not None
                and parent_preference[0] == effective_provider
                else None
            )
        elif parent_preference is not None:
            effective_provider, model_override = parent_preference
        else:
            effective_provider = DEFAULT_PROVIDER
            model_override = None

        # llm_provider -> Engine's provider injection (probe-proven seam).
        prepared.mount_plan["providers"] = []
        inject_kwargs: dict[str, Any] = {"effort_override": reasoning_effort}
        if model_override is not None:
            inject_kwargs["model_override"] = model_override
        deps.inject_provider(prepared, effective_provider, **inject_kwargs)

        # max_turns -> best-effort forward into the session orchestrator's
        # own config (see docstring above).
        if max_turns is not None:
            session_plan = prepared.mount_plan.setdefault("session", {})
            orch_plan = session_plan.setdefault("orchestrator", {})
            orch_plan.setdefault("config", {})["max_turns"] = max_turns

        # --- gap 3: approval policy (accept by default; worker parity) -----
        approval_policy = cfg.get("approval_policy", DEFAULT_APPROVAL_POLICY)
        if approval_policy not in VALID_APPROVAL_POLICIES:
            # LOUD, but never fail OPEN to deny: a typo here silently
            # bricking every approval-gated action is the worse failure in
            # a headless pipeline -- the graph's own evidence gates and
            # budget walls catch a bad work product; a bricked worker
            # catches nothing. Name the bad value so it's fixable, then use
            # the default.
            logger.warning(
                "loop-amplifier-agent: unknown approval_policy=%r (expected "
                "one of %s); using default %r.",
                approval_policy,
                sorted(VALID_APPROVAL_POLICIES),
                DEFAULT_APPROVAL_POLICY,
            )
            approval_policy = DEFAULT_APPROVAL_POLICY
        # Log the active policy exactly ONCE per execute() -- not per-turn
        # spam. "accept" is the default, expected posture (worker parity
        # with loop-agent, which has no approval system at all) so it logs
        # at INFO; "deny" is the operator opting into hardening, worth
        # visibility, so it logs at WARNING. One line either way.
        if approval_policy == "accept":
            logger.info(
                "loop-amplifier-agent: approval_policy='accept' -- "
                "approval-gated actions in this child turn are "
                "auto-approved (default)."
            )
        else:
            logger.warning(
                "loop-amplifier-agent: approval_policy='deny' -- "
                "approval-gated actions in this child turn will be "
                "declined (opt-in hardening)."
            )
        approval_override = (
            deps.ApprovalOverride.YES
            if approval_policy == "accept"
            else deps.ApprovalOverride.NO
        )

        async def handler(ctx: Any) -> str:
            session_id = ctx.session_id or None
            # v2 (gap 5): mint a fresh id for a one-shot run (this adapter
            # always submits with no incoming session id -- see the
            # engine.submit_turn call below) so hooks.set_default_fields has
            # a non-empty session_id to stamp. Mirrors make_turn_handler's
            # identical ephemeral-id fallback and its rationale: the
            # context-intelligence LoggingHandler drops any event whose
            # session_id default field is empty.
            engine_session_id = session_id or f"ephemeral-{uuid.uuid4().hex}"

            session = await prepared.create_session(
                session_id=engine_session_id,
                session_cwd=working_dir,
                is_resumed=False,
            )

            # gap 1 (D5-equivalent): write the resolved workspace identity
            # onto the child's own coordinator config -- belt-and-suspenders
            # on top of prepare_bundle_for_session's hook-config pre-seed,
            # mirroring make_turn_handler exactly.
            session.coordinator.config["workspace"] = resolved_workspace
            session.coordinator.config["project_slug"] = resolved_workspace

            # gap 5: stamp session_id/turn_id as default event fields so
            # every tool/llm/execution event this turn emits is attributed.
            session.coordinator.hooks.set_default_fields(
                session_id=engine_session_id,
                turn_id=ctx.turn_id,
            )

            session.coordinator.register_capability("display.emit", ctx.display.emit)

            # gap 3: forward the REAL approval decision -- ctx.approval is
            # the Engine's own ApprovalSystem protocol point (constructed
            # below from approval_override), never a hardcoded stub.
            wire_approval_provider = deps.WireApprovalProvider(
                approval_request_fn=ctx.approval.request
            )
            session.coordinator.register_capability(
                "approval.request", wire_approval_provider.request_approval
            )

            # gap 2: register session.spawn so the child's own `delegate`
            # tool can spawn grandchild sessions. Mirrors make_turn_handler's
            # closure pattern exactly (parent_session is always THIS turn's
            # session, agent_configs defaults to the cold-path hydration).
            async def _spawn_fn(**kw: Any) -> dict[str, Any]:
                kw.setdefault("agent_configs", agent_configs)
                kw["parent_session"] = session
                return await deps.spawn_sub_session(**kw)

            session.coordinator.register_capability("session.spawn", _spawn_fn)

            # Session continuity (support#497): replay the parent's prior-turn
            # history into the hosted session's context BEFORE the turn runs,
            # so fidelity="full" cross-node memory actually reaches the model.
            # Mirrors amplifier_agent_lib._runtime's own is_resumed replay:
            # same mount-registry seam (context-simple mounts via
            # coordinator.mount, so use coordinator.get, NOT get_capability),
            # same hasattr guard so a context module without set_messages is
            # skipped rather than crashing the turn.
            if history:
                hosted_context = session.coordinator.get("context")
                if hosted_context is not None and hasattr(
                    hosted_context, "set_messages"
                ):
                    await hosted_context.set_messages(history)
                else:
                    logger.warning(
                        "loop-amplifier-agent: hosted session context module "
                        "does not expose set_messages -- prior-turn history "
                        "not replayed (fidelity='full' continuity inert). "
                        "Context module: %r",
                        hosted_context,
                    )

            async with session:
                return await session.execute(ctx.prompt)

        display = deps.CliDisplaySystem(stream=_NullStream(), verbosity="quiet")
        approval = deps.CliApprovalSystem(
            mode=approval_policy, override=approval_override
        )
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

        return result["reply"]

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

    def _resolve_parent_provider_preference(self) -> tuple[str, str] | None:
        """Recover the parent's resolved ``provider_preferences`` (gap 4), if any.

        ``provider_preferences`` never reaches ``execute()`` as its own
        parameter -- the kernel's orchestrator call boundary
        (``amplifier_core._session_exec.run_orchestrator``) threads through
        exactly ``prompt`` / ``context`` / ``providers`` / ``tools`` /
        ``hooks`` / ``coordinator``, nothing else. Instead, the generic
        foundation spawn path
        (``amplifier_foundation.bundle._prepared.PreparedBundle.spawn``)
        resolves ``provider_preferences`` BEFORE session creation, via
        ``apply_provider_preferences_with_resolution`` ->
        ``_apply_single_override``, which mutates the CHILD SESSION's OWN
        mount-plan ``providers`` list in place: it promotes the matching
        provider entry to ``config["priority"] = 0`` and stamps
        ``config["default_model"]`` with the resolved model. Since this
        orchestrator IS that child session's mounted orchestrator,
        ``self._coordinator.config["providers"]`` is exactly that mutated
        list -- read it back here.

        A plain, non-preference-promoted provider entry (declared straight
        in a bundle.md, or produced by this module's OWN ``inject_provider``
        calls) never carries ``default_model`` unless a caller explicitly
        set one (see ``build_provider_entry``'s docstring: "omitted
        entirely so the provider's own ``get_info().defaults`` wins") --
        so a non-empty ``config["default_model"]`` is the reliable signal
        that THIS entry was promoted by a parent preference, independent of
        priority-ordering ties.

        Returns ``(provider_short_name, model)`` for the first matching
        entry (``module`` with a leading ``"provider-"`` stripped, matching
        ``amplifier_agent_cli.provider_sources.PROVIDER_CATALOG``'s naming
        convention), or ``None`` when no provider_preferences were supplied,
        none matched, or ``self._coordinator`` carries no readable config.
        """
        session_config = getattr(self._coordinator, "config", None)
        if not isinstance(session_config, dict):
            return None
        provider_entries = session_config.get("providers")
        if not isinstance(provider_entries, list):
            return None
        for entry in provider_entries:
            if not isinstance(entry, dict):
                continue
            entry_config = entry.get("config")
            if not isinstance(entry_config, dict):
                continue
            model = entry_config.get("default_model")
            if not (isinstance(model, str) and model):
                continue
            module = entry.get("module")
            provider_name = module
            if isinstance(module, str) and module.startswith("provider-"):
                provider_name = module[len("provider-") :]
            if isinstance(provider_name, str) and provider_name:
                return provider_name, model
        return None

    @staticmethod
    def _build_prompt(prompt: str, user_instructions: str | None) -> str:
        """Compose the final prompt: base prompt + user_instructions.

        WAVE 4: no longer appends a report_outcome nudge -- ``prompt`` already
        carries the status-file contract (path + envelope), injected once by
        ``amplifier_module_loop_pipeline.backend`` for every spawn-capable
        worker (see ``status_contract.py``). Retiring a second, adapter-local
        nudge keeps this module's prompt-shaping honest: one contract, one
        injection point, taught identically to every spawn worker.
        """
        parts = [prompt]
        if user_instructions:
            parts.append(f"\n\nAdditional instructions:\n{user_instructions}")
        return "".join(parts)
