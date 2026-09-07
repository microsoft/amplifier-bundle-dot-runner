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

TELEMETRY (2026-09-07): the hosted amplifier-agent session is a SECOND,
independent ``ModuleCoordinator`` with its OWN hook registry -- it is not the
spawned adapter session, and nothing composed into the adapter's bundle
reaches it.  EXTENSIONS.md Sec 26's session-event persister therefore never
saw a single event from the worker that actually does the work: the node's
``sessions/<id>/events.jsonl`` carried the adapter's own lifecycle brackets
and nothing else, so a finished run could not answer "how many LLM calls,
how many tokens, how much money?" for an ``amplifier-agent`` node at all
(measured: node-matrix run ``20260907T043835Z``, rows ``aa-anthropic-default``
and ``aa-gpt5-medium`` -- both PASS, both unmeasured).  ``_attach_child_
session_telemetry`` closes that: it mounts the SHIPPED persister onto the
hosted session's registry, so the child's own events land under the same
``<logs>/<node>/sessions/<id>/events.jsonl`` layout every other worker uses,
and it carries the hosted session's id up through the completion envelope so
``status.json``'s ``session_id`` names the stream that actually exists.  See
that method for the ``llm:response`` -> ``provider:response`` translation the
hosted orchestrator's own event vocabulary makes necessary.

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
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from amplifier_core.events import LLM_RESPONSE, ORCHESTRATOR_COMPLETE, PROVIDER_RESPONSE

logger = logging.getLogger(__name__)

#: Sanitizes a `.dot` graph's free-form thread_id/node-id (fidelity.py's
#: `resolve_thread_key`) into a safe amplifier-agent sessionId component.
#: amplifier-agent's own sessionId is an opaque string handed straight to
#: its persistence layer (see docs/INTEGRATION.md's (workspace, sessionId)
#: contract) -- this is deliberately conservative (no assumption about what
#: characters that layer tolerates) rather than passing thread_key through
#: verbatim.
_THREAD_KEY_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


def _slugify_thread_key(thread_key: str) -> str:
    return _THREAD_KEY_UNSAFE_CHARS.sub("-", thread_key)[:80]


#: Identifies this orchestrator in the ORCHESTRATOR_COMPLETE envelope's
#: ``orchestrator`` field -- mirrors loop-agent's own ``"loop-agent"`` value
#: (amplifier_module_loop_agent/__init__.py's ``_emit_completion``).
ORCHESTRATOR_NAME = "loop-amplifier-agent"

#: Provider families whose module name can be read out of an address by
#: substring (``provider-openai`` -> ``openai``), and the compound names that
#: must NOT be (``azure-openai`` is a distinct, differently configured
#: provider, not the openai family under another spelling).  Both mirror
#: loop-agent's ``agent_session.KNOWN_PROVIDERS`` /
#: ``_DISTINCT_COMPOUND_PROVIDERS`` deliberately rather than importing them:
#: the two worker adapters are independent packages and neither may depend on
#: the other at runtime.  The SHARED contract is pinned executably by
#: ``worker_parity_kit.suite``'s ``telemetry_provider_identity`` TARGET row,
#: which drives BOTH workers through the SAME assertion -- that is the seam
#: that catches drift between these two copies, not a shared import.
_KNOWN_PROVIDER_FAMILIES = ("anthropic", "openai", "gemini")
_DISTINCT_COMPOUND_PROVIDERS = (
    "azure-openai",
    "azure_openai",
    "openai-chatgpt",
    "openai_chatgpt",
)


def _provider_instance_id(mount_key: str | None, module_name: str | None) -> str | None:
    """The configured INSTANCE id this provider address is, or ``None``.

    Same rule as ``loop-agent``'s ``agent_session.provider_instance_id`` (see
    that docstring for the full reasoning): an address that is neither the
    module's own name nor a mere naming VARIANT of it is an instance alias.
    ``None`` whenever either half is unknown -- a worker that cannot tell
    must say so rather than invent a configured instance that does not exist.
    """
    if not mount_key or not module_name:
        return None
    if mount_key == module_name:
        return None
    lowered = mount_key.lower()
    if any(compound in lowered for compound in _DISTINCT_COMPOUND_PROVIDERS):
        return mount_key
    for family in _KNOWN_PROVIDER_FAMILIES:
        if family in lowered:
            return None if family == module_name else mount_key
    return mount_key

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

#: ORCHESTRATOR_COMPLETE metadata key carrying the HOSTED amplifier-agent
#: session's id up to the parent (``backend.py``'s
#: ``_session_id_from_spawn_result``), so ``status.json``'s ``session_id``
#: names the session whose ``events.jsonl`` was actually written.
#:
#: NOT a verdict channel.  WAVE 5 (2026-08-30) removed the only
#: verdict-carrying key this envelope ever had and the 2026-09-06 owner ruling
#: deleted the residual parameter that could still have populated it; nothing
#: here reopens that.  ``_outcome_from_spawn_result`` reads ``status`` and
#: nothing else, an Outcome recovered from a spawn result is still
#: ``is_explicit=False``, and a ``goal_gate`` node still cannot be satisfied by
#: anything in this dict.  This key is read on exactly ONE line, to set
#: ``Outcome.session_id`` -- an observability join key, never an outcome.
WORKER_SESSION_ID_METADATA_KEY = "worker_session_id"


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the loop-amplifier-agent orchestrator."""
    orchestrator = AmplifierAgentOrchestrator(coordinator, config or {})
    await coordinator.mount("orchestrator", orchestrator)
    logger.info("loop-amplifier-agent orchestrator mounted")


def _load_dependencies() -> SimpleNamespace:
    """Lazy-import the real amplifier-agent seam.

    Extracted into its own function -- rather than inlined into
    ``_run_turn`` -- for two reasons:

    1. amplifier-agent requires Python >=3.12 and pulls in a heavy
       dependency tree (fastapi/uvicorn/mcp/...); nothing in this module's
       top-level import surface should require it just to construct an
       ``AmplifierAgentOrchestrator`` or run its hermetic unit tests.
    2. Hermetic unit tests monkeypatch THIS ONE seam
       (``amplifier_module_loop_amplifier_agent._load_dependencies``) with
       faithful fakes (a fake ``Engine`` whose ``submit_turn`` really does
       invoke the injected ``turn_handler``) instead of requiring the real,
       network-fetching, Python-3.12-only library to be installed just to exercise
       the envelope-shape / config-mapping / fail-closed contracts this
       module owns.

    Returns a namespace of exactly the symbols ``_run_turn`` needs, mirroring
    the real modules the probe (``/var/tmp/aa-probe/probe_q1_q2.py``)
    exercised directly.
    """
    from amplifier_agent_cli.provider_sources import (
        inject_provider,
        inject_routing_matrix,
    )
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
        inject_routing_matrix=inject_routing_matrix,
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

        Boots a fresh Engine (no caching -- per-node state isolation), runs
        the turn, and emits ONE ``ORCHESTRATOR_COMPLETE`` event mirroring
        loop-agent's envelope shape (EXTENSIONS.md 35) so
        ``backend.py::_outcome_from_spawn_result`` can read the child's
        lifecycle status across the spawn boundary.  No tool is mounted onto
        the hosted agent's session: the reach-in mount WAVE 4 retired is gone,
        and the explicit-verdict channel is the status-file contract carried
        in ``prompt`` (canonical Sec 4.5 / Appendix C, EXTENSIONS.md 41).
        """
        if coordinator is not None:
            self._coordinator = coordinator

        # Resolved HERE, not inside _run_turn, for one reason: every exit path
        # -- including the exception path below, which can fire before a single
        # amplifier-agent object exists -- owes the parent the SAME id that
        # keys the hosted session's persisted event stream. Computing it once,
        # up front, is what makes "status.json names the stream that exists"
        # true on the failure paths too, not just the happy one.
        engine_session_id = self._resolve_engine_session_id()

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
            reply = await self._run_turn(
                prompt, history=history, engine_session_id=engine_session_id
            )
        except Exception:
            # A raised exception means the invocation never completed --
            # still owes the spawn boundary an envelope (mirrors loop-agent's
            # own cancelled/incomplete handling), but must NOT promote a
            # partial/absent report as a verdict.
            await self._emit_completion(
                hooks, status="incomplete", worker_session_id=engine_session_id
            )
            raise

        # WAVE 4 (maintainer ruling 2026-08-29) retired this adapter's
        # reach-in tool mount; WAVE 5 (2026-08-30) removed the in-process
        # verdict channel repo-wide. It is NOT this module's job to fabricate
        # an explicit verdict from nothing: `metadata` stays empty, so
        # `backend.py::_outcome_from_spawn_result` falls through to the
        # lifecycle-status-only path (`is_explicit=False` -- cannot satisfy a
        # goal_gate on its own, exactly as intended). The REAL
        # explicit-verdict channel for this worker is the status-file
        # contract already embedded in `prompt` (see
        # `amplifier_module_loop_pipeline.status_contract`): if the hosted
        # amplifier-agent wrote <stage_dir>/status.json with its own file
        # tools, `handlers/codergen.py`'s `read_status_override` (running in
        # the PARENT process, after this method returns) picks it up --
        # entirely outside this adapter's control or knowledge.
        await self._emit_completion(
            hooks, status="success", worker_session_id=engine_session_id
        )
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
        worker_session_id: str | None = None,
    ) -> None:
        """Emit the single ORCHESTRATOR_COMPLETE envelope for an invocation.

        Mirrors ``amplifier_module_loop_agent.AgentOrchestrator._emit_completion``'s
        envelope shape exactly (``orchestrator`` / ``status`` / ``turn_count`` /
        ``metadata``) so the parent's reader
        (``backend.py::_outcome_from_spawn_result``) needs no adapter-specific
        branch.

        ``metadata`` carries exactly ONE key, and it is not a verdict:
        ``worker_session_id`` (see ``WORKER_SESSION_ID_METADATA_KEY``), the
        HOSTED amplifier-agent session's id. The parent's spawn result reports
        the ADAPTER session's id -- a real id, but the wrong one: the adapter
        session emits lifecycle brackets only, while every provider and tool
        event lives in the hosted session. Without this key, ``status.json``
        named a stream with no telemetry in it while the stream that had the
        telemetry went unnamed. WAVE 5's removal of the verdict key stands:
        nothing here is read as an outcome (see the constant's docstring).
        """
        metadata: dict[str, Any] = {}
        if worker_session_id:
            metadata[WORKER_SESSION_ID_METADATA_KEY] = worker_session_id
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
        self,
        prompt: str,
        history: list[dict[str, Any]] | None = None,
        engine_session_id: str | None = None,
    ) -> str:
        """Boot a fresh Engine, run exactly one turn, return the reply text.

        WAVE 4: no longer returns a captured ``last_outcome`` -- there is no
        more in-process verdict-capturing tool to capture it from (ruling 5).
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
            ``session.execute()`` (Layer-5 override).
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
        # amplifier-agent docs/INTEGRATION.md:65,85 (fresh clone, commit 66d5896):
        # `inject_routing_matrix(prepared, provider)` is a SEPARATE, REQUIRED
        # call alongside `inject_provider` -- without it the prepared bundle's
        # routing matrix still points at whatever the baked-in bundle declared,
        # so a non-default `llm_provider` on this node would inject the right
        # provider mount but never actually get ROUTED to.
        deps.inject_routing_matrix(prepared, effective_provider)

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
            # v2 (gap 5): hooks.set_default_fields needs a non-empty
            # session_id to stamp (the context-intelligence LoggingHandler
            # drops any event whose session_id default field is empty).
            #
            # 2026-09-07: the fallback is now the id THIS invocation already
            # resolved and already reported upward, not a fresh
            # `ephemeral-<uuid4>`. A random fallback could only ever produce
            # an id the parent has never heard of -- i.e. a persisted stream
            # at a path status.json does not name, which is the exact defect
            # this change exists to close. In practice submit_turn always
            # passes a real sessionId (below), so both sides agree; when it
            # somehow does not, they still agree.
            hosted_session_id = ctx.session_id or engine_session_id

            session = await prepared.create_session(
                session_id=hosted_session_id,
                session_cwd=working_dir,
                # amplifier-agent docs/INTEGRATION.md:155,158,322-324,366-367
                # (fresh clone, commit 66d5896): continuity is keyed on
                # (workspace, sessionId), and a turn that continues a prior
                # conversation must pass is_resumed=True, reusing the same
                # sessionId/workspace -- not sessionId=""+is_resumed=False on
                # every turn regardless of whether history is being replayed.
                # `history` (this adapter's own parent_messages capture, see
                # _history_from_context above) is exactly that signal: a
                # present, non-empty history means this turn continues an
                # earlier one.
                is_resumed=bool(history),
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
                session_id=hosted_session_id,
                turn_id=ctx.turn_id,
            )

            # Telemetry (2026-09-07): mount the pipeline's session-event
            # persister onto THIS session's registry. Must come after
            # set_default_fields above -- the persister keys its output
            # directory off each event's own `session_id` field, and an
            # unstamped event is one it drops.
            # The identity handed in is this turn's RESOLVED routing (gap 4's
            # precedence already applied above), not the raw node attributes
            # -- reporting what was asked for rather than what was resolved is
            # exactly the inference this event exists to retire.
            self._attach_child_session_telemetry(
                session.coordinator,
                requested_provider=effective_provider,
                requested_model=model_override,
                reasoning_effort=reasoning_effort,
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

        # amplifier-agent docs/INTEGRATION.md:90-91,155,158,161,322-324,366-367
        # (fresh clone, commit 66d5896): "Build one Engine per turn and pass
        # is_resumed=True for every turn after the first, reusing the same
        # sessionId and workspace" -- this adapter already boots one fresh
        # Engine per node invocation (module docstring's "STATE ISOLATION");
        # what was missing is threading a REAL, stable sessionId (not the ""
        # sentinel) and `resume`/is_resumed matching whether this invocation
        # is continuing a fidelity="full" thread (`history` non-empty) or
        # starting fresh.
        #
        # WAVE 6 (feat/agent-always-installed): `thread_key` (read from
        # orchestrator_config above -- backend.py's own fidelity="full"
        # thread resolution, threaded across the spawn boundary via the
        # SAME public config seam every other per-node override already
        # uses) lets THIS turn derive the SAME sessionId a later revisit of
        # the same thread will also derive, satisfying amplifier-agent's own
        # (workspace, sessionId) continuity contract for real, instead of a
        # fresh random id every single turn. `parent_messages` replay
        # (`history`, above) remains the actual correctness mechanism --
        # this is an ADDITIVE optimization layer: if `thread_key` is absent
        # (fidelity != "full", or a caller that predates this key), behavior
        # is UNCHANGED (a fresh ephemeral id every turn).
        #
        # 2026-09-07: the derivation itself moved to
        # ``_resolve_engine_session_id`` and is now normally computed by
        # ``execute()`` BEFORE this method is called, so the id that reaches
        # the completion envelope (and hence status.json) is the same one on
        # every exit path -- including the one where this method raises. The
        # fallback below keeps ``_run_turn`` callable on its own.
        if engine_session_id is None:
            engine_session_id = self._resolve_engine_session_id()
        is_resumed = bool(history)
        init_params = {
            "protocolVersion": deps.PROTOCOL_VERSION,
            "clientInfo": {"name": ORCHESTRATOR_NAME, "version": "0.1.0"},
            "capabilities": dict(deps.server_default_capabilities()),
            "sessionId": engine_session_id,
            "resume": is_resumed,
        }

        try:
            await engine.boot(init_params, bundle_override=prepared)
            full_prompt = self._build_prompt(prompt, user_instructions)
            result = await engine.submit_turn(
                {
                    "sessionId": engine_session_id,
                    "turnId": "turn-1",
                    "prompt": full_prompt,
                }
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

    def _resolve_engine_session_id(self) -> str:
        """Derive the id of the HOSTED amplifier-agent session for this turn.

        Continuity optimization (feat/agent-always-installed, WAVE 6):
        ``amplifier_module_loop_pipeline.backend`` threads the ALREADY-RESOLVED
        fidelity="full" thread key through the SAME public
        ``orchestrator_config`` seam ``llm_provider`` / ``max_turns`` /
        ``user_instructions`` already use (see that module's
        ``_run_with_spawn`` -- it sets this key right alongside
        ``spawn_kwargs["parent_messages"]``). A public, documented seam: no
        reach into loop-pipeline internals, no new protocol.

        A present ``thread_key`` makes this turn derive the SAME sessionId a
        later revisit of the same thread will derive, satisfying
        amplifier-agent's own (workspace, sessionId) continuity contract;
        absent it, a fresh per-invocation id. Unchanged behavior -- extracted
        from ``_run_turn`` only so ``execute()`` can resolve it once, up
        front, and report the same id on every exit path.
        """
        thread_key = self._config.get("thread_key")
        if thread_key:
            return f"dot-runner-thread-{_slugify_thread_key(thread_key)}"
        return f"engine-{uuid.uuid4().hex}"

    def _attach_child_session_telemetry(
        self,
        coordinator: Any,
        *,
        requested_provider: str | None = None,
        requested_model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        """Persist the HOSTED session's own events under the pipeline run.

        THE DEFECT THIS CLOSES. EXTENSIONS.md Sec 26's persister reaches a
        worker session by riding the PARENT bundle through
        ``PreparedBundle.spawn``'s composition. That composition ends at the
        ADAPTER session -- the one this orchestrator is mounted into. The
        session that actually calls the model and the tools is a SECOND
        coordinator this adapter builds itself from amplifier-agent's own
        bundle (``prepared.create_session``), with its own registry that no
        composition ever touched. So the node's ``sessions/`` directory held
        the adapter's lifecycle brackets and nothing else, and an
        ``amplifier-agent`` node's cost was structurally unmeasurable
        (node-matrix ``20260907T043835Z``: two PASSing rows, zero token
        evidence). Registering the SHIPPED persister here -- the same class,
        the same curated event set, the same write-time redaction -- is what
        makes the child's stream land at
        ``<logs>/<node>/sessions/<id>/events.jsonl``, the identical layout the
        collector already reads for every other worker. One persister, one
        layout, one source of truth.

        THE TRANSLATION, AND WHY IT IS SCOPED HERE. The two workers speak
        different event vocabularies for the same fact:

          * ``loop-agent`` (the coding-agent worker) emits BOTH
            ``provider:request`` and ``provider:response`` itself, and puts
            the provider's usage block on the latter
            (``agent_session.py``: ``emit(PROVIDER_RESPONSE, {"usage":
            usage_data})``).
          * ``loop-streaming`` (the orchestrator inside amplifier-agent's
            baked-in bundle) emits ``provider:request`` and
            ``provider:error`` -- and never imports ``PROVIDER_RESPONSE`` at
            all. Its usage rides on ``content_block:end``, which Sec 26
            deliberately excludes as UI cadence.

        The usage itself is not missing: the provider MODULE emits it, on its
        own ``llm:response`` event, with the exact ``#69`` schema the
        collector reads (``input_tokens`` / ``output_tokens`` /
        ``cache_read_tokens`` / ``cache_write_tokens`` / ``cost_usd``, the
        cost computed by the provider's own ``_cost.py`` price table). So this
        registers a translation, not a fabrication: on ``llm:response`` with a
        usage block, re-emit the canonical ``provider:response`` the
        observability contract names, forwarding ``usage`` VERBATIM.

        It is registered HERE, on the hosted session only, and deliberately
        NOT inside ``hooks-pipeline-observability`` -- because in a loop-agent
        worker BOTH events fire for a single call, and a bridge living in the
        shared persister would double every coding-agent row's tokens and
        cost. The precondition for translating is "this orchestrator provably
        does not emit ``provider:response``", which is a fact about the hosted
        runtime, so the bridge belongs where that fact is known.

        ``provider:request`` is NOT bridged for the mirror-image reason:
        loop-streaming already emits one per LLM call, and ``llm:request``
        would double the call count.

        WHO SERVED IT (2026-09-07, the same addendum that put identity on
        loop-agent's provider events).  ``llm:response`` already carries the
        provider MODULE's own answer to "what am I" (``provider``) and the
        model it called (``model``); those ride through verbatim, and the
        module's answer always outranks this adapter's request.  The three
        this adapter alone knows are stamped alongside them:

        * ``provider_module`` -- the family that served.  Taken from the
          payload's own ``provider`` when the module reported one, else the
          provider this adapter injected.
        * ``provider_instance`` -- the configured instance id when the node
          addressed one (``_provider_instance_id``), ``None`` otherwise.
        * ``reasoning_effort`` -- the ``effort_override`` handed to
          ``inject_provider`` for this turn.  It reaches the model but
          appears on no event the hosted runtime emits, so a cost read off
          an ``amplifier-agent`` node could not tell ``high`` from ``low``.

        Same five keys, same meanings, as loop-agent's own
        ``_provider_identity`` -- one vocabulary across both workers, so the
        harness that reads a node's evidence never has to know which worker
        produced it.

        KNOWN LIMIT: a grandchild session (the hosted agent's own ``delegate``
        spawns) gets its own coordinator, which this does not reach -- its
        events are not persisted under the node. Same boundary as every other
        worker; named here rather than discovered later.

        Never fatal: an absent persister module, or a registry without
        ``register``, logs once and leaves the turn untouched -- the same
        both-sides-optional posture Sec 26's seam already documents.
        """
        hooks = getattr(coordinator, "hooks", None)
        register = getattr(hooks, "register", None)
        if not callable(register):
            logger.warning(
                "loop-amplifier-agent: hosted session's hook registry exposes "
                "no register() -- child session events will NOT be persisted "
                "under this node (EXTENSIONS.md Sec 26). Registry: %r",
                hooks,
            )
            return

        try:
            from amplifier_module_hooks_pipeline_observability.session_events import (
                PERSISTED_SESSION_EVENTS,
                SessionEventPersister,
            )
        except ImportError:
            # The observability module is not a dependency of this adapter --
            # it arrives with the pipeline that mounts it. Outside a pipeline
            # (a bare unit test, a foreign host) there is nothing to persist
            # to, which is not an error.
            logger.info(
                "loop-amplifier-agent: hooks-pipeline-observability not "
                "importable -- child session events not persisted."
            )
            return

        persister = SessionEventPersister()
        for event_name in PERSISTED_SESSION_EVENTS:
            register(
                event_name,
                persister.make_handler(event_name),
                name=f"loop_amplifier_agent_session_events:{event_name}",
            )

        async def _bridge_llm_response(
            event: str | None = None, data: dict[str, Any] | None = None
        ) -> None:
            """``llm:response`` -> canonical ``provider:response``.

            Forwards the provider module's own usage block unchanged. Skips a
            failed call (``status != "ok"``) and a response with no usage:
            loop-streaming already emits ``provider:error`` for the former,
            and a synthesized empty-usage record would read to the collector
            as a real call that cost nothing.
            """
            payload = data or {}
            if payload.get("status") not in (None, "ok"):
                return
            usage = payload.get("usage")
            if not isinstance(usage, dict) or not usage:
                return
            bridged: dict[str, Any] = {"usage": usage}
            for key in ("provider", "model", "duration_ms"):
                value = payload.get(key)
                if value is not None:
                    bridged[key] = value
            # The provider module's own report of what it is outranks this
            # adapter's request for it; the request is the fallback for a
            # module that reported nothing, never an override.
            served_by = payload.get("provider") or requested_provider
            bridged.setdefault("provider", served_by)
            bridged.setdefault("model", requested_model)
            bridged["provider_module"] = served_by
            bridged["provider_instance"] = _provider_instance_id(
                requested_provider, served_by
            )
            bridged["reasoning_effort"] = reasoning_effort
            try:
                await coordinator.hooks.emit(PROVIDER_RESPONSE, bridged)
            except Exception:  # never break the turn for observability
                logger.debug("provider:response bridge emit failed", exc_info=True)

        register(
            LLM_RESPONSE,
            _bridge_llm_response,
            name="loop_amplifier_agent_provider_response_bridge",
        )

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

        WAVE 4: no longer appends a verdict-reporting nudge -- ``prompt`` already
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
