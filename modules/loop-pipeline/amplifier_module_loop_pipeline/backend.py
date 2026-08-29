"""AmplifierBackend — CodergenBackend adapter using session spawning.

This is the "sessions all the way down" integration point. When the
pipeline engine hits a codergen node, the CodergenHandler calls this
backend, which spawns a coding agent sub-session via the Amplifier
``session.spawn`` capability.

When session.spawn is not available, falls back to a direct provider
mini tool loop that calls LLM → execute tool calls → repeat until the
model returns a text-only response.

Spec coverage: Section 4.5 (CodergenBackend Interface), Section 1.4,
               FID-001–010, Section 5.4.

fidelity=full continuity (see docs/designs/fidelity-full-session-continuity.md):
  ``_thread_transcripts`` maps a branch-local thread_key to a list of
  (node_id, instruction, output) triples — the accumulated node-exchange
  history for that thread.  After each ``full`` node, the exchange is
  appended (truncating any stale tail first, for goal-gate-retry
  idempotency).  On the next same-thread ``full`` node the history is
  converted to a ``parent_messages`` list (user/assistant roles) and
  passed to a FRESH spawn — never a session_id re-pass.  This removes
  the type confusion (id-where-a-conversation-belongs) that caused the
  continuity bug.

  Thread_id is branch-local: ``clone()`` resets ``_thread_transcripts``
  so parallel branches each start with a fresh transcript even when they
  share an explicit ``thread_id``.  See EXTENSIONS.md §12–13.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, overload

try:
    from amplifier_foundation import ProviderPreference as _ProviderPreference
except ImportError:

    class _ProviderPreference:  # type: ignore[no-redef]
        """Placeholder raised when amplifier_foundation is not installed."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ImportError(
                "amplifier_foundation is required for ProviderPreference but is not installed. "
                "Install it with: pip install amplifier-foundation"
            )


from .context import PipelineContext
from .fidelity import (
    RESUME_FIDELITY_CAP_KEY,
    build_preamble,
    resolve_fidelity,
    resolve_thread_key,
)
from .graph import Edge, Graph, Node, resolve_bool_attr
from .outcome import Outcome, StageStatus
from .pipeline_events import MODEL_RESOLVED
from .status_contract import build_status_file_contract, current_node_status_path

# NOTE: `.workers` is imported LAZILY inside `AmplifierBackend.__init__`
# (not here at module level). `workers/direct_worker.py` imports several
# helpers back FROM this module (`_MAX_TOOL_LOOP_ROUNDS`, `_parse_outcome`,
# etc.) -- a top-level `from .workers import ...` here would run during
# THIS module's own top-level execution, before those names are defined
# below, producing a circular partial-init ImportError.
# This mirrors the existing lazy-import idiom already used elsewhere in this
# module/package to break the same class of cycle (e.g. `_build_backend`'s
# `from .backend import AmplifierBackend` in `__init__.py`).

logger = logging.getLogger(__name__)

# Map StageStatus value strings to enum members for parsing
_STATUS_MAP: dict[str, StageStatus] = {s.value: s for s in StageStatus}

# Maximum rounds for the direct tool loop fallback. Preserved verbatim as the
# merged `direct` worker's round cap (DESIGN-worker-registry-core-split.md
# P1 scope note) -- see workers/direct_worker.py.
_MAX_TOOL_LOOP_ROUNDS = 20

#: Reserved worker-selection name recognized by the adapter directly -- NOT a
#: `WorkerRegistry` entry. DESIGN-worker-registry-core-split.md P1 ("Why P1
#: lands standalone"): the registry keys NAMES, not source trees. The hosted
#: `session.spawn` path resolves an agent identity via the pre-existing
#: `profiles` map (provider -> agent name), untouched by this program --
#: there is no single Python `Worker` object to register for it. See
#: `AmplifierBackend._resolve_worker_name`.
_SPAWN_WORKER_SENTINEL = "spawn"


def _safe_get_spawn_fn(coordinator: Any) -> Any | None:
    """Resolve ``session.spawn`` off *coordinator*, tolerating a missing or
    ``None`` coordinator.

    Mirrors ``__init__.py``'s ``_spawn_capability`` (duplicated here, not
    imported, to avoid a module-load-order dependency between ``backend.py``
    and ``__init__.py`` -- see the lazy `.workers` import note above).
    Needed since P1: ``_build_backend`` now always constructs
    ``AmplifierBackend`` -- including the former ``DirectProviderBackend``
    standalone scenario, where ``coordinator`` may be ``None`` entirely.
    """
    if coordinator is None or not hasattr(coordinator, "get_capability"):
        return None
    try:
        return coordinator.get_capability("session.spawn")
    except Exception:  # noqa: BLE001 -- tolerant probe, mirrors _spawn_capability
        return None


class AmplifierBackend:
    """CodergenBackend implementation using Amplifier session spawning.

    Resolves the provider profile from node attributes, spawns a child
    coding agent session, and parses the outcome from the response.

    Supports two execution paths:
    - **Path A (spawn)**: If ``session.spawn`` is available, delegates to
      a full child session with the complete tool loop.
    - **Path B (direct tool loop)**: If spawn is unavailable but a provider
      and tools are available, runs a mini agentic loop directly
      (LLM call → tool execution → repeat).

    Supports fidelity-based context control:
    - ``full``: Fresh spawn with prior node exchanges replayed as ``parent_messages``
      (thread-keyed transcript, NOT a session pool -- see the module docstring above).
    - ``compact``/``truncate``/``summary:*``: Fresh session with preamble.
    """

    def __init__(
        self,
        coordinator: Any = None,
        profiles: dict[str, str] | None = None,
        provider: Any | None = None,
        tools: dict[str, Any] | None = None,
        unified_client: Any | None = None,
        hooks: Any | None = None,
        default_worker: str | None = None,
    ) -> None:
        """Initialize the backend -- the Sec4.5 "adapter" (see the
        ``amplifier_module_loop_pipeline.workers`` package docstring for the
        registry that lives one layer below it).

        Args:
            coordinator: Amplifier coordinator with session.spawn capability.
                Optional (default ``None``): a coordinator lacking (or
                without) ``session.spawn`` simply means the ``"spawn"``
                worker is never selectable (see ``_resolve_worker_name``) --
                this mirrors the former standalone ``DirectProviderBackend``,
                which never required a coordinator at all.
            profiles: Map of provider name to profile/bundle name.
                      e.g. {"anthropic": "attractor-anthropic", ...}
            provider: Optional LLM provider for the ``direct`` worker.
                      Used as a truthiness flag to enable it.
            tools: Optional tool dict, shared with the ``direct`` worker.
            unified_client: Optional ``unified_llm.Client`` for the
                            ``direct`` worker's LLM calls. Created lazily if
                            not provided.
            hooks: Optional HookRegistry for emitting provider-level events.
            default_worker: Run-level worker-selection default
                (EXTENSIONS.md Sec40 /
                DESIGN-worker-registry-core-split.md P1 item 3). One of the
                registry's registered names (today: only ``"direct"``) or
                the reserved ``"spawn"`` sentinel. Selection precedence:
                per-node ``worker=`` attribute > this run-level default >
                today's capability-fallback chain (spawn if resolved, else
                direct). ``None`` (the default) leaves the fallback chain as
                the sole selector -- a zero-config run is unaffected.
        """
        self._coordinator = coordinator
        self._profiles = profiles or {}
        self._provider = provider
        self._tools = tools or {}
        self._hooks = hooks
        self._spawn_fn: Any | None = None
        self._spawn_checked = False

        # The worker registry (DESIGN-worker-registry-core-split.md P1,
        # gap-table row 1). `direct` is the one Python-level Worker this
        # phase ships -- the merge of the former `_run_with_tool_loop` +
        # `DirectProviderBackend` (gap-table row 2). `"spawn"` is a reserved
        # sentinel, never a registry entry -- see `_SPAWN_WORKER_SENTINEL`.
        # Lazy import: see the module-level NOTE above this class for why.
        from .workers import DirectWorker, WorkerRegistry

        self._registry = WorkerRegistry()
        # Concrete reference (not the Protocol-typed registry lookup) so the
        # `_unified_client` backward-compat property below stays type-clean.
        self._direct_worker = DirectWorker(
            provider=provider,
            tools=tools,
            hooks=hooks,
            unified_client=unified_client,
        )
        self._registry.register("direct", self._direct_worker)
        known_workers = frozenset({_SPAWN_WORKER_SENTINEL}) | self._registry.names()
        if default_worker is not None and default_worker not in known_workers:
            raise ValueError(
                f"Unknown default_worker={default_worker!r}. Known workers: "
                f"{sorted(known_workers)}."
            )
        self._default_worker = default_worker

        # _thread_transcripts: thread_key → list of (node_id, instruction, output) triples.
        # Replaces the former _session_pool (which stored a session_id — a type confusion:
        # an id where a conversation belongs).  Each triple represents one node-exchange
        # at user/assistant granularity.  Idempotent under goal-gate retries via
        # truncate-to-node-then-append (see _append_to_transcript).
        # Born branch-local: clone() resets to {} so parallel branches never share history.
        # See docs/designs/fidelity-full-session-continuity.md and EXTENSIONS.md §12–13.
        # Shared uniformly by BOTH the spawn path and the `direct` worker path since P1 --
        # see `run()`'s worker-name routing and EXTENSIONS.md §40.
        self._thread_transcripts: dict[str, list[tuple[str, str, str]]] = {}
        self._completed_nodes: dict[str, Outcome] = {}
        self._last_node_id: str | None = None

    @property
    def _unified_client(self) -> Any:
        """Backward-compat passthrough to the `direct` worker's cached
        client. Kept as a real (readable/settable) attribute because
        existing tests read/write ``backend._unified_client`` directly
        (e.g. ``test_backend_clone.py``, ``test_unified_llm_wiring.py``)."""
        return self._direct_worker._unified_client

    @_unified_client.setter
    def _unified_client(self, value: Any) -> None:
        self._direct_worker._unified_client = value

    def clone(self) -> AmplifierBackend:
        """Create a clone with shared immutable refs but fresh mutable state.

        Used for parallel branch isolation so concurrent branches don't
        corrupt each other's thread transcripts or completion tracking.
        (``_thread_transcripts`` is reset here, so branches sharing an
        explicit ``thread_id`` still each start fresh -- EXTENSIONS.md §13.)
        """
        new = AmplifierBackend.__new__(AmplifierBackend)
        # Shared immutable refs
        new._coordinator = self._coordinator
        new._profiles = self._profiles
        new._provider = self._provider
        new._hooks = self._hooks
        new._default_worker = self._default_worker

        # Worker registry: each registered worker (today: `direct`) gets its
        # own branch-isolated clone -- see `WorkerRegistry.clone()` /
        # `DirectWorker.clone()`. This is what makes `_unified_client`'s
        # backward-compat property above return the right (shared-by-
        # reference) object for the clone too.
        new._registry = self._registry.clone()
        new._direct_worker = new._registry.resolve("direct")

        # Copy tools: stateless tools are shared across clones (safe); stateful tools
        # (those exposing last_outcome) get an independent shallow copy with last_outcome
        # reset to None, so parallel branches start clean regardless of prior use.
        def _clone_tool(tool: Any) -> Any:
            # Detect stateful tools via explicit __dict__ inspection — not hasattr(),
            # which returns True for MagicMock and other proxy objects that fabricate
            # attributes dynamically.
            is_stateful = (
                # Instance attribute (e.g. ReportOutcomeTool sets self.last_outcome in __init__)
                "last_outcome" in getattr(tool, "__dict__", {})
                # Class attribute (e.g. _MockReportOutcomeTool defines last_outcome at class level)
                or any("last_outcome" in vars(cls) for cls in type(tool).__mro__)
            )
            if is_stateful:
                c = copy.copy(tool)
                c.last_outcome = None
                return c
            return tool

        new._tools = {k: _clone_tool(v) for k, v in self._tools.items()}
        # Inherit resolved spawn capability — the capability is a stateless
        # function from the shared _coordinator, so sharing the reference is as
        # safe as the clone already sharing _coordinator.  Inheriting prevents
        # concurrent first-resolution when N branch clones run under
        # asyncio.gather (each clone would otherwise race to call
        # _coordinator.get_capability("session.spawn") simultaneously, causing
        # some branches to receive None and fall to the tool-loop fallback).
        new._spawn_fn = self._spawn_fn
        new._spawn_checked = self._spawn_checked
        # Fresh mutable state — transcripts are born branch-local so that two
        # branches sharing the same thread_id maintain independent histories
        # (§3.8 isolation, EXTENSIONS.md §9 / §13).
        new._thread_transcripts = {}
        new._completed_nodes = {}
        new._last_node_id = None
        return new

    def ensure_spawn_resolved(self) -> None:
        """Resolve the session.spawn capability in place, once.

        Call this on the parent backend before creating branch clones via
        ``clone()``.  This guarantees that all clones inherit an already-
        resolved ``_spawn_fn`` (and ``_spawn_checked = True``) instead of
        performing a concurrent first-resolution when N branch engines each
        hit the lazy-check block in ``run()`` simultaneously under
        ``asyncio.gather``.

        Idempotent: safe to call multiple times; subsequent calls are no-ops.
        """
        if not self._spawn_checked:
            self._spawn_fn = _safe_get_spawn_fn(self._coordinator)
            self._spawn_checked = True

    def _resolve_worker_name(self, node: Node) -> str:
        """Selection precedence (EXTENSIONS.md §40 / DESIGN-worker-registry-
        core-split.md P1 item 3): per-node ``worker=`` attribute > run-level
        ``default_worker`` (orchestrator config) > today's capability-
        fallback chain (``"spawn"`` if ``session.spawn`` resolved, else
        ``"direct"``).

        Raises ``ValueError`` (never a silent fallback) naming every known
        worker when the node declares an unrecognized ``worker=`` value.
        Must be called AFTER step 1's spawn-capability resolution so
        ``self._spawn_fn`` reflects this run's real capability.
        """
        known_workers = frozenset({_SPAWN_WORKER_SENTINEL}) | self._registry.names()
        node_worker = node.attrs.get("worker") if node.attrs else None
        if node_worker:
            if node_worker not in known_workers:
                raise ValueError(
                    f"Node '{node.id}' declared worker={node_worker!r}, which "
                    f"is not a known worker. Known workers: "
                    f"{sorted(known_workers)}."
                )
            return node_worker
        if self._default_worker is not None:
            return self._default_worker
        return _SPAWN_WORKER_SENTINEL if self._spawn_fn is not None else "direct"

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge: Edge | None = None,
        graph: Graph | None = None,
    ) -> Outcome:
        """Execute a coding task by spawning a child session.

        Falls back to a direct provider tool loop when session.spawn is
        not available.

        Args:
            node: The pipeline node being executed.
            prompt: The expanded prompt string.
            context: The current pipeline context.
            incoming_edge: The edge leading to this node (for fidelity resolution).
            graph: The pipeline graph (for fidelity resolution).

        Returns:
            Outcome parsed from the child session's response.
        """
        # 1. Get spawn capability (lazy resolution, checked once)
        if not self._spawn_checked:
            self._spawn_fn = _safe_get_spawn_fn(self._coordinator)
            self._spawn_checked = True

        # 2. Resolve provider and profile from node attributes
        provider = node.attrs.get("llm_provider", "anthropic")
        model = node.attrs.get("llm_model")
        model = await _resolve_concrete_model(provider, model, emit=self._emit)
        reasoning_effort = node.attrs.get("reasoning_effort")
        max_agent_turns_raw = node.attrs.get("max_agent_turns")
        max_agent_turns = (
            int(max_agent_turns_raw) if max_agent_turns_raw is not None else None
        )
        # Profile lookup is exact-or-nothing (issue #155 R3): the former
        # `next(iter(self._profiles.values()), "")` default silently routed a
        # node whose declared provider had no profile onto SOME OTHER
        # provider's profile -- the "silent single-provider fallback" that
        # defeats dual-family critique (a run reporting a dual-critic quorum
        # while both critics ran on the same model family). A missing profile
        # now fails loud on the spawn path (see step 6 below); the tool-loop
        # path never consumes a profile.
        profile_name = self._profiles.get(provider)

        # 3. Resolve fidelity mode (spec FID-001–010)
        if graph is not None:
            fidelity = resolve_fidelity(node, incoming_edge, graph)
        else:
            # Fallback when graph not provided (backward compat)
            fidelity = node.attrs.get("fidelity", "compact")

        # 3b. Spec §5.3 rule 6 — one-hop resume fidelity cap.
        # `full` continuity here is _thread_transcripts: an in-memory dict of
        # node exchanges replayed as parent_messages into a fresh spawn.  A
        # killed process loses it unrecoverably, so the first node executed
        # after a resume would otherwise spawn with EMPTY history and no sign
        # that anything degraded.  The engine sets this reserved key on exactly
        # that hop and clears it the moment the handler returns; honoring it
        # here substitutes the ~3000-token summary preamble, which is built
        # from restored context + completed-node history — precisely the
        # serializable state the spec says survives.
        resume_cap = context.get(RESUME_FIDELITY_CAP_KEY)
        if resume_cap and fidelity == "full":
            logger.info(
                "Node %s: resume fidelity cap full->%s (spec §5.3 rule 6)",
                node.id,
                resume_cap,
            )
            fidelity = resume_cap

        # CR-1 loud guard (silent-continuity-loss class): a fidelity=full node
        # needs `graph` to resolve its thread key and drive the transcript
        # store/read.  If `full` continuity is requested but `graph` is missing,
        # the store/read gates below would silently skip — exactly the dead-code
        # bug a live DTU run exposed (seeds wrote codewords, recall came back
        # empty because CodergenHandler.execute dropped `graph`).  Warn loudly so
        # a future caller that drops `graph` fails visibly instead of silently
        # losing continuity.  Scoped to the `full` path only: non-full nodes and
        # legitimately thread-less nodes never need a graph and must not warn.
        if fidelity == "full" and graph is None:
            logger.warning(
                "Node %s requested fidelity=full continuity but no graph was "
                "passed to backend.run() — the thread key cannot be resolved, so "
                "conversation continuity will NOT be honored for this node. The "
                "caller (handler/engine) must forward `graph`. See "
                "docs/designs/fidelity-full-session-continuity.md.",
                node.id,
            )

        # 3c. Resolve the thread key once, uniformly for BOTH the spawn path
        # and the `direct` worker path (DESIGN-worker-registry-core-split.md
        # P1, gap-table row 32: the adapter resolves fidelity/thread-key and
        # hands the worker its already-replayed history; the worker never
        # sees `graph`/`incoming_edge` itself). `_run_with_spawn` ALSO
        # independently recomputes the same thread_key internally (untouched
        # by this change, to keep the well-exercised spawn path's diff
        # minimal) -- both computations are pure and take identical inputs,
        # so they cannot disagree.
        thread_key: str | None = None
        if fidelity == "full" and graph is not None:
            thread_key = resolve_thread_key(
                node, incoming_edge, graph, self._last_node_id
            )

        # 3d. Selection precedence (EXTENSIONS.md §40 / DESIGN-worker-
        # registry-core-split.md P1 item 3): per-node `worker=` attribute >
        # run-level `default_worker` > today's capability-fallback chain.
        worker_name = self._resolve_worker_name(node)

        # 4. Build the instruction with preamble for non-full modes
        if fidelity == "full":
            instruction = prompt
        else:
            preamble = build_preamble(fidelity, context, self._completed_nodes)
            instruction = f"{preamble}\n\n---\n\n{prompt}" if preamble else prompt

        # 5. Inject human gate response if present (consume-once)
        #
        # When a freeform hexagon gate precedes this node, the human's text
        # is stored in context as "human.gate.text".  We prepend it to the
        # instruction so it becomes part of the user message in the session's
        # conversation history.  With fidelity=full and session reuse, the
        # instruction IS a durable user turn in the persistent session record
        # — all future nodes on the same thread inherit it.
        gate_text = context.get("human.gate.text")
        if gate_text is not None:
            # Consume-once: always clear after the first LLM node following a
            # human gate, regardless of whether the text was empty.
            context.set("human.gate.text", None)
            if gate_text:  # Only inject if the human actually typed something
                gate_label = context.get("human.gate.label", "")
                gate_section = (
                    f'Human response at gate "{gate_label}":\n{gate_text}\n\n---\n\n'
                )
                instruction = gate_section + instruction

        # 6. Route by resolved worker name -- "spawn" (Path A) or a
        # registry-resolved worker, today only "direct" (Path B). Selection
        # precedence and the fallback-chain default are unchanged in
        # substance from the pre-registry code below; what changed is that
        # the choice now has a NAME, explicitly selectable (EXTENSIONS.md
        # §40).
        if worker_name == _SPAWN_WORKER_SENTINEL:
            if self._spawn_fn is None:
                raise ValueError(
                    f"Node '{node.id}' selected worker={_SPAWN_WORKER_SENTINEL!r} "
                    f"(explicitly or via run-level default) but the "
                    f"session.spawn capability is not available for this run."
                )
            # Fail-loud guard (issue #155 R3, defense-in-depth under the
            # startup preflight in preflight.py): the spawn path consumes a
            # provider->agent profile, and there is NO fallback when the
            # node's provider has none.  Silently substituting another
            # provider's profile is how a run reports a dual-critic quorum
            # while both critics ran on the same model family.  ValueError is
            # terminal in the retry ladder (retry.should_retry), so this
            # surfaces once, loudly -- never as a budget-draining crash loop.
            if profile_name is None:
                from .preflight import PROVIDER_KEY_ENV

                raise ValueError(
                    f"Node '{node.id}' resolved llm_provider={provider!r} but no "
                    f"profile is mounted for that provider (mounted profiles: "
                    f"{sorted(self._profiles) or 'none'}). Refusing to fall back "
                    f"to another provider's profile -- silent single-provider "
                    f"fallback defeats dual-family critique (issue #155, "
                    f"EXTENSIONS.md section 36). Fix: add "
                    f"'{provider}: <agent-name>' to the orchestrator 'profiles' "
                    f"config and ensure its credential "
                    f"({PROVIDER_KEY_ENV.get(provider, '<PROVIDER>_API_KEY')}) "
                    f"is set, or change the node's llm_provider."
                )
            outcome = await self._run_with_spawn(
                node,
                instruction,
                provider,
                model,
                reasoning_effort,
                max_agent_turns,
                profile_name,
                fidelity,
                incoming_edge,
                graph,
                context,
            )
            # When _run_with_spawn returns here, it either extracted an outcome
            # from the child's output / report_outcome / status, or it returned
            # Outcome(FAIL) so the engine can route via FAIL-edge → retry_target /
            # goal_gate.  No silent in-process fallback occurs inside that method.
        elif self._provider is not None:
            outcome = await self._run_with_tool_loop(
                node,
                instruction,
                reasoning_effort,
                max_agent_turns,
                context=context,
                fidelity=fidelity,
                thread_key=thread_key,
                worker_name=worker_name,
            )
        else:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=(
                    "Neither session.spawn nor a direct provider is "
                    "available — cannot execute node"
                ),
            )

        # Record completed node outcome for future preambles
        self._completed_nodes[node.id] = outcome
        self._last_node_id = node.id

        return outcome

    # ------------------------------------------------------------------
    # Path A: Full child session via session.spawn
    # ------------------------------------------------------------------

    async def _run_with_spawn(
        self,
        node: Node,
        instruction: str,
        provider: str,
        model: str | None,
        reasoning_effort: str | None,
        max_agent_turns: int | None,
        profile_name: str,
        fidelity: str,
        incoming_edge: Edge | None,
        graph: Graph | None,
        context: PipelineContext | None = None,
    ) -> Outcome:
        """Spawn a full child session via the CLI's session.spawn capability."""
        assert self._spawn_fn is not None  # guaranteed by caller

        # Obtain parent_session from coordinator
        parent_session = getattr(self._coordinator, "session", None)

        # Obtain agent_configs from coordinator config
        coordinator_config = getattr(self._coordinator, "config", None) or {}
        agent_configs: dict[str, Any] = coordinator_config.get("agents", {})

        # FAIL-LOUD GUARD: detect agent config that would cause loop-pipeline to recurse.
        #
        # The spawn capability resolves the child's orchestrator by calling
        # merge_configs(parent.config, agent_configs[profile_name]).  It merges only
        # 'session:', 'providers:', 'tools:', and similar mount-plan keys — no external
        # references are resolved or loaded.
        #
        # Two conditions both cause the child to re-enter loop-pipeline:
        #
        #   (a) session.orchestrator.module is absent or None  → child inherits the
        #       parent's loop-pipeline orchestrator and re-executes the same DOT graph.
        #   (b) session.orchestrator.module is "loop-pipeline" → child IS loop-pipeline
        #       and re-executes the same DOT graph.
        #
        # Both were observed as 9,854-session infinite recursion (0 LLM calls, no
        # artifact produced).
        #
        # Fix: add an inline session.orchestrator with a non-pipeline module (e.g.
        # loop-agent) to the agent entry in your pipeline profile or bundle config.
        _agent_cfg_for_node: dict[str, Any] = agent_configs.get(profile_name) or {}
        _effective_orch_module: str | None = (
            (_agent_cfg_for_node.get("session") or {})
            .get("orchestrator", {})
            .get("module")
        )
        if _effective_orch_module is None or _effective_orch_module == "loop-pipeline":
            raise ValueError(
                f"loop-pipeline recursion guard: agent '{profile_name}' has "
                f"session.orchestrator.module={_effective_orch_module!r}. "
                f"The child would inherit or re-enter loop-pipeline, causing "
                f"infinite recursion. "
                f"Fix: add an inline session.orchestrator (non-pipeline, e.g. "
                f"loop-agent) to the '{profile_name}' agent definition in your "
                f"pipeline profile or bundle config."
            )

        # Resolve runtime user_instructions override (Layer-5, highest precedence).
        # Sources (first non-empty wins):
        #   1. node.attrs["user_instructions"] — per-node DOT attribute
        #   2. PipelineContext["user_instructions"] — per-run caller-supplied override
        # See docs/designs/layer-1-profile-owned-system-prompt.md §B.
        node_user_instructions: str | None = node.attrs.get("user_instructions") or None
        ctx_user_instructions: str | None = (
            (context.get("user_instructions") or None) if context is not None else None
        )
        user_instructions_override = node_user_instructions or ctx_user_instructions

        # WAVE 4 (status_contract.py): teach the spawned child the exact
        # absolute status.json path for THIS node, per spec Sec 4.5 /
        # Appendix C's status-file contract -- the spec-native, taught
        # channel for a spawned worker's explicit outcome (maintainer ruling
        # 2026-08-29 retconning report_outcome; see EXTENSIONS.md Sec 35's
        # dated RETCON note). Applies uniformly to every spawn-capable
        # worker (loop-agent, loop-amplifier-agent) since both receive
        # whatever instruction text is placed in spawn_kwargs below -- one
        # injection point teaches both. `current_node_status_path` is set by
        # `handlers/codergen.py` around this call and is always an absolute
        # path; `None` here means there is no stage directory for this
        # invocation (e.g. a unit test calling the backend directly without
        # going through the handler) -- in that case nothing is injected,
        # preserving prior behavior exactly. The transcript-carried
        # `instruction` (used by `_append_to_transcript` below) is left
        # UNCHANGED -- the per-node absolute path has no business being
        # replayed into a later node's full-fidelity history.
        _status_path = current_node_status_path.get()
        spawn_instruction = (
            instruction + build_status_file_contract(_status_path)
            if _status_path is not None
            else instruction
        )

        # Build spawn kwargs matching the CLI spawn_capability signature
        spawn_kwargs: dict[str, Any] = {
            "agent_name": profile_name,
            "instruction": spawn_instruction,
            "parent_session": parent_session,
            "agent_configs": agent_configs,
            # orchestrator_config: pass only non-None values so that loop-agent's
            # numeric comparisons (e.g. max_turns > 0) don't receive None and
            # throw TypeError.  Omitting a key lets the child orchestrator use its
            # own default, which is always safer than injecting None.
            "orchestrator_config": {
                k: v
                for k, v in {
                    "reasoning_effort": reasoning_effort,
                    "max_turns": max_agent_turns,
                    # user_instructions (Layer-5): per-node or per-run override
                    "user_instructions": user_instructions_override,
                    # llm_provider: the node's intended provider (Bug B). loop-agent
                    # reads this from its raw orchestrator config to select BOTH the
                    # completion provider and the matching Layer-1 base prompt, instead
                    # of blindly taking the first mounted provider. `provider` is
                    # computed at the top of this method (anthropic-defaulted, never
                    # None), so it always survives the `v is not None` filter.
                    "llm_provider": provider,
                }.items()
                if v is not None
            },
        }
        if model:
            # provider_preferences carries the resolved concrete `model`: foundation's
            # apply_provider_preferences_with_resolution promotes the matching provider
            # and sets its default_model (spawn_utils._apply_single_override). That model
            # delivery is load-bearing and has no other channel. Provider SELECTION,
            # however, now flows via orchestrator_config["llm_provider"] above.
            spawn_kwargs["provider_preferences"] = [
                _ProviderPreference(provider=provider, model=model)
            ]

        # Inject shared execution environment attachment for child session
        if context is not None:
            container_id = context.get("internal.env_container_id")
            env_type = context.get("internal.env_type")
            if container_id:
                spawn_kwargs["tools"] = spawn_kwargs.get("tools", []) + [
                    {
                        "module": "tools-env-all",
                        "config": {
                            "auto_attach": {
                                "type": env_type,
                                "name": "pipeline-workspace",
                                "attach_to": container_id,
                            }
                        },
                    }
                ]

        # fidelity=full: resolve thread_key once for both pre-spawn history injection
        # and post-spawn transcript append.
        #
        # The former _session_pool stored a session_id and re-passed it as
        # sub_session_id — a type confusion (an id where a conversation belongs).
        # The fix: carry the actual node-exchange history in _thread_transcripts and
        # pass it as parent_messages to a FRESH spawn.  Foundation injects it via
        # set_messages before the child session runs.
        #
        # Mutual-exclusion invariant: for a full-fidelity carry, parent_messages and
        # sub_session_id are NEVER both present — parent_messages drives continuity;
        # sub_session_id is never set here.  The assert below enforces this so that
        # any future code path that accidentally re-introduces the old mechanism will
        # fail loudly rather than silently dropping history (the original symptom).
        thread_key: str | None = None
        if fidelity == "full" and graph is not None:
            thread_key = resolve_thread_key(
                node, incoming_edge, graph, self._last_node_id
            )
            prior_messages = self._get_parent_messages_for_thread(thread_key)
            if prior_messages:
                spawn_kwargs["parent_messages"] = prior_messages
            # Continuity optimization (feat/agent-always-installed, WAVE 6):
            # thread the already-resolved thread_key through the SAME public
            # orchestrator_config seam llm_provider/max_turns/user_instructions
            # already use, so a spawn-capable worker CAN derive a stable
            # cross-node session identity for this thread if it knows how to
            # (today: loop-amplifier-agent -- see that module's own use of
            # this key). This is an ADDITIVE optimization layer only:
            # parent_messages (above) remains the actual continuity
            # mechanism and is completely unaffected by whether a worker
            # reads this key or not.
            spawn_kwargs["orchestrator_config"]["thread_key"] = thread_key

        # CR-1 guard: parent_messages and sub_session_id must never coexist.
        # (sub_session_id is not set by this method for full-fidelity, so this
        # fires only if a caller or future patch accidentally introduces it.)
        assert not (
            spawn_kwargs.get("parent_messages") and spawn_kwargs.get("sub_session_id")
        ), (
            "BUG: parent_messages and sub_session_id cannot both be set for a "
            "full-fidelity spawn.  parent_messages drives continuity; "
            "sub_session_id re-passes a session identity and would cause "
            "foundation to silently drop the injected history."
        )

        # Spawn the child session
        try:
            result = await self._spawn_fn(**spawn_kwargs)
        except Exception as e:
            # Infrastructure failure: the spawn mechanism itself broke (e.g.
            # agent profile not found, session init error).  Return FAIL so the
            # engine can route via the spec's FAIL-edge (retry_target / goal_gate)
            # rather than silently re-running the node in a different harness.
            logger.warning("Spawn failed for node %s: %s", node.id, e)
            return Outcome(status=StageStatus.FAIL, failure_reason=str(e))

        # Parse outcome from result.
        #
        # WAVE 5 repair (2026-08-30, maintainer ruling): report_outcome is
        # REMOVED (specs/EXTENSIONS.md §35 RETCON, dated status: REMOVED).
        # There is no metadata.report_outcome precedence read here anymore.
        # The spec's own channels are what remain: an empty final message is
        # recovered from the orchestrator's own completion status below
        # (spec §4.5 treats a clean completion with empty prose as SUCCESS),
        # and non-empty output is still run through _parse_outcome's
        # fail-closed JSON-verdict ladder (§25). status.json (§41) is applied
        # afterward by the handler layer, unaffected by this method.
        output = result.get("output", "") if isinstance(result, dict) else str(result)

        if not output.strip():
            # The child's FINAL assistant message was empty — but that does NOT
            # mean the child failed.  A child that did its work via tool calls
            # and ended on a terminal tool call legitimately has no closing
            # prose.  Before falling back, honor the orchestrator's own
            # completion status (captured in the spawn result, see
            # _prepared.py spawn()).
            spawn_outcome = _outcome_from_spawn_result(result)
            if spawn_outcome is not None:
                session_id = (
                    result.get("session_id") if isinstance(result, dict) else None
                )
                if session_id:
                    spawn_outcome.session_id = session_id
                # support#498: a recovered (non-explicit) outcome (e.g.
                # orchestrator completion status=success with no closing
                # prose) is still a recoverable outcome, so the exchange is
                # appended with a synthesized marker rather than dropped.
                # See _synthesize_outcome_marker.
                if fidelity == "full" and graph is not None and thread_key is not None:
                    self._append_to_transcript(
                        thread_key,
                        node.id,
                        instruction,
                        _synthesize_outcome_marker(spawn_outcome),
                    )
                return spawn_outcome

            # Genuinely empty: no text, no success status.
            # support#498 / issue #287: this remains the ONE case that skips
            # the transcript append entirely — there is no recoverable outcome
            # to synthesize an honest marker from, so appending would mean
            # inventing content the child never produced.  Return FAIL so the
            # engine can route via the spec's FAIL-edge (retry_target /
            # goal_gate) rather than silently re-running the node in a
            # materially different in-process harness.
            logger.warning(
                "Node %s: spawn returned empty output with no recoverable outcome.",
                node.id,
            )
            return Outcome(
                status=StageStatus.FAIL,
                notes="No output from child session",
                failure_reason="Empty spawn output",
            )

        outcome = _parse_outcome(output, node=node)

        # Capture session_id from spawn result for status.json observability.
        # session_id is kept on the Outcome for telemetry/debugging — it no longer
        # drives continuity (that role belongs to _thread_transcripts).
        session_id = result.get("session_id") if isinstance(result, dict) else None
        if session_id:
            outcome.session_id = session_id

        # Append this node's exchange to the thread transcript (full fidelity only).
        # Uses truncate-to-node-then-append for idempotency: if this node is being
        # re-run (e.g., after a goal-gate retry), its prior turn is replaced rather
        # than duplicated.  See _append_to_transcript for the algorithm.
        if fidelity == "full" and graph is not None and thread_key is not None:
            self._append_to_transcript(thread_key, node.id, instruction, output)

        return outcome

    # ------------------------------------------------------------------
    # Path B: Direct provider mini tool loop (spawn-unavailable path)
    # ------------------------------------------------------------------

    async def _run_with_tool_loop(
        self,
        node: Node,
        instruction: str,
        reasoning_effort: str | None,
        max_agent_turns: int | None = None,
        *,
        context: PipelineContext | None = None,
        fidelity: str | None = None,
        thread_key: str | None = None,
        worker_name: str = "direct",
    ) -> Outcome:
        """Path B entry point -- now a thin dispatch to a registry-resolved
        worker (default: ``direct``), not an inline tool-loop implementation.

        Kept under its original name (this is the pre-existing Path B
        method call sites and tests already know) even though the body no
        longer implements the loop itself -- the merge
        (DESIGN-worker-registry-core-split.md P1, gap-table row 2) moved
        that implementation to ``workers.direct_worker.DirectWorker``. This
        method's remaining job is exactly what row 32 assigns the adapter:
        resolve the worker, hand it ``replayed_history``, and own the
        node-exchange transcript append -- uniformly with the spawn path.

        ``reasoning_effort``/``max_agent_turns`` are accepted for backward
        compatibility with existing call sites/tests but are no longer
        consulted here -- ``DirectWorker.run()`` re-derives both directly
        from ``node.attrs`` (matching the former standalone
        ``DirectProviderBackend``'s self-sufficient style), so a worker is
        usable hermetically without an upstream resolver.
        """
        worker = self._registry.resolve(worker_name)
        replayed_history: list[dict[str, Any]] = []
        if fidelity == "full" and thread_key is not None:
            replayed_history = self._get_parent_messages_for_thread(thread_key)

        output_text, outcome = await worker.run(
            node, instruction, context or PipelineContext(), replayed_history
        )

        if fidelity == "full" and thread_key is not None:
            transcript_output = (
                output_text
                if output_text.strip()
                else _synthesize_outcome_marker(outcome)
            )
            self._append_to_transcript(
                thread_key, node.id, instruction, transcript_output
            )

        return outcome

    # ------------------------------------------------------------------
    # _thread_transcripts helpers — fidelity=full continuity carrier
    # ------------------------------------------------------------------

    def _append_to_transcript(
        self,
        thread_key: str,
        node_id: str,
        instruction: str,
        output: str,
    ) -> None:
        """Append a node's (instruction, output) exchange to the thread transcript.

        Implements **truncate-to-node-then-append** for goal-gate-retry
        idempotency: if this node already has a turn in the transcript (from a
        prior attempt in the same run), all turns from that node onwards are
        removed before the new turn is appended.  This means a re-run node
        *replaces* its prior exchange rather than duplicating it.

        Algorithm:
            1. Scan the current transcript for the first tuple whose node_id
               matches the incoming node_id.
            2. If found at position i: truncate the list to the first i entries
               (discarding that node and all subsequent nodes' entries).
            3. Append the new triple (node_id, instruction, output).

        Called with role=user/assistant only (system/developer roles are
        stripped at this layer, matching app-cli behavior).

        Thread_id is branch-local (EXTENSIONS.md §13): ``clone()`` resets
        ``_thread_transcripts`` so sibling parallel branches each maintain
        independent transcripts even when they share an explicit thread_id.

        Note on sequentiality (§3.8 / design §2):
            Same-thread-key full nodes within a single branch always run
            sequentially (the engine while-loop is sequential; parallel
            branches each receive an isolated backend clone).  No asyncio
            lock is required.
        """
        turns = self._thread_transcripts.get(thread_key, [])
        # Truncate from the first occurrence of this node_id, replacing any
        # stale tail left by a prior attempt of the same node or later nodes.
        for i, (nid, _, _) in enumerate(turns):
            if nid == node_id:
                turns = turns[:i]
                break
        turns.append((node_id, instruction, output))
        self._thread_transcripts[thread_key] = turns

    def _get_parent_messages_for_thread(self, thread_key: str) -> list[dict[str, Any]]:
        """Return the accumulated conversation history for a thread as a flat
        ``parent_messages`` list (user/assistant dicts).

        Each stored triple ``(node_id, instruction, output)`` expands to two
        messages:
            {"role": "user",      "content": instruction}
            {"role": "assistant", "content": output}

        Returns an empty list if the thread has no prior exchanges (first node
        on the thread — no parent_messages will be set in that case).
        """
        messages: list[dict[str, Any]] = []
        for _, instr, out in self._thread_transcripts.get(thread_key, []):
            messages.append({"role": "user", "content": instr})
            messages.append({"role": "assistant", "content": out})
        return messages

    async def close(self) -> None:
        """Release every registered worker's held resources (spec finalize
        contract) -- e.g. the `direct` worker's cached ``unified_llm``
        client, which wraps an ``AsyncAnthropic``/httpx client bound to the
        running event loop.  Under the per-article ``asyncio.run()``
        lifecycle, that client must be closed WITHIN its loop; otherwise GC
        later runs ``aclose()`` on a dead loop, raising ``RuntimeError:
        Event loop is closed``.  This method is called by the
        orchestrator's finalize path.

        Idempotent and safe: delegates to ``WorkerRegistry.close_all()``,
        which is itself a no-op per worker when nothing was ever created.
        """
        await self._registry.close_all()

    async def _emit(self, event_name: str, data: dict[str, Any]) -> Any:
        """Emit an event via hooks, if provided.

        Returns the HookResult from hooks.emit(), or None if hooks is not set.
        Unlike the engine's fire-and-forget _emit, this returns the result
        so callers can inspect the action (deny, modify, etc.).
        """
        if self._hooks is not None:
            return await self._hooks.emit(event_name, data)
        return None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _resolve_model(node: Node) -> str:
    """Resolve the LLM model identifier from a pipeline node.

    Precedence (spec §8.5 / Appendix A, node attribute table -- llm_model
    "inherited", llm_provider "auto-detected"):

      1. Explicit node attribute (``node.llm_model``) -- highest precedence.
         This also covers rung 2 (model_stylesheet rule) and rung 3
         (graph-level default), both of which are already resolved onto
         ``node.llm_model``/``node.attrs`` by transforms applied before this
         function ever runs (stylesheet.py's ``apply_stylesheet``).
      2. NEW -- rung 4, "Handler/system default" (spec §8.5 item 4): when the
         node has no ``llm_model`` but DOES have an explicit ``llm_provider``
         (again, either a raw DOT attribute or one set by the model
         stylesheet -- both promote onto ``node.llm_provider``), resolve a
         per-provider default MODEL FAMILY TOKEN so `llm_provider`-alone
         works on the direct path instead of failing loud (maintainer
         ruling: llm_provider is a spec-level node property, honor it
         spec-first). The returned token is resolved live by
         ``_resolve_concrete_model`` -- same machinery as an author writing
         ``llm_model=sonnet`` today -- so this table never pins a rotting
         concrete model id (see ``_PROVIDER_DEFAULT_MODEL_PATTERN`` below).
      3. Otherwise (no llm_model AND no llm_provider at all on the node):
         unchanged fail-loud. This is deliberately NOT widened by the new
         rung 4 -- the ruling's surprise-case is an author who wrote
         `llm_provider=` alone, not a node with neither attribute set.

    Args:
        node: The pipeline node to resolve a model for.

    Returns:
        The explicit model identifier from ``node.llm_model``, or (rung 4)
        a per-provider default model-family token to be resolved live.

    Raises:
        ValueError: If ``node.llm_model`` is unset AND either (a) no
            ``llm_provider`` is set at all, or (b) ``llm_provider`` is set
            to a value with no documented default (unknown/malformed
            provider stays loud -- no silent guess).
    """
    if node.llm_model:
        return node.llm_model
    provider = node.llm_provider
    if provider is not None:
        default = _PROVIDER_DEFAULT_MODEL_PATTERN.get(provider)
        if default is not None:
            token, _stable_only = default
            logger.info(
                "Node %r set llm_provider=%r with no llm_model -- using "
                "per-provider default model family token %r (spec §8.5 rung "
                "4; see specs/EXTENSIONS.md).",
                node.id,
                provider,
                token,
            )
            return token
        raise ValueError(
            f"Node '{node.id}' set llm_provider={provider!r} but no "
            f"llm_model, and no per-provider default model is documented "
            f"for {provider!r} (known defaults: "
            f"{sorted(_PROVIDER_DEFAULT_MODEL_PATTERN)}). Set "
            f'llm_model="<model-name>" explicitly, or add a default for '
            f"this provider to _PROVIDER_DEFAULT_MODEL_PATTERN. Malformed "
            f"or unrecognized providers are never silently guessed."
        )
    raise ValueError(
        f"Node '{node.id}' requires an explicit 'llm_model' attribute. "
        f'Set llm_model="<model-name>" in the node\'s DOT attributes or '
        f"via the pipeline's model_stylesheet. "
        f"No default model is provided — this prevents silently running "
        f"against a deprecated or unintended model."
    )


def _default_model_stable_only(provider: str) -> bool:
    """Whether the rung-4 default token for *provider* should exclude
    preview/experimental variants (see ``_PROVIDER_DEFAULT_MODEL_PATTERN``).

    Irrelevant when ``node.llm_model`` was explicit -- callers only consult
    this on the rung-4 (default-token) path.
    """
    default = _PROVIDER_DEFAULT_MODEL_PATTERN.get(provider)
    return default[1] if default is not None else True


# ---------------------------------------------------------------------------
# Live model-token resolution (family token / glob -> concrete served id)
# ---------------------------------------------------------------------------
# Mirrors the proven wiki-weaver shim (wiki_weaver/model_resolver.py): an
# explicit id is returned unchanged with NO network call; a family token or a
# glob is resolved live against the provider's own served list via
# unified_llm.resolve_latest_for, which closes the id-seam (lister and
# generator share one adapter). Fail-loud: no match -> ValueError propagates.

# The ONE place to extend family-name support. Exact, case-insensitive token
# match only -- a concrete id that merely CONTAINS "sonnet"
# (e.g. "claude-sonnet-4-5") is NOT a family token and passes through unchanged.
_FAMILY_TOKENS: frozenset[str] = frozenset({"opus", "sonnet", "haiku"})

# ---------------------------------------------------------------------------
# Per-provider DEFAULT model (spec §8.5 rung 4 / Appendix A -- "Handler/
# system default"). The canonical attractor spec explicitly reserves this
# rung for the implementor; it names no concrete defaults. The unified-llm
# spec (§2.9) says implementations "should default to the latest available
# models when no model is specified by the caller" and names
# ``get_latest_model()``/live-resolution as the mechanism for that. Rather
# than pin a literal model id here (the old, removed ``_DEFAULT_MODELS``
# table -- see test_profile_no_default_model.py -- rotted exactly this way),
# each entry names a FAMILY TOKEN/GLOB resolved live via the SAME
# ``_resolve_concrete_model``/``unified_llm.resolve_latest_for`` machinery
# already used for an author-written ``llm_model=sonnet``.
#
#   "anthropic" -> "sonnet": an EXISTING family token (_FAMILY_TOKENS above).
#       Matches the spec's OWN model_stylesheet example (§8.6:
#       `* { llm_model: claude-sonnet-4-5; llm_provider: anthropic; }`).
#   "openai"    -> "gpt-5.*[0-9]": current flagship generation (unified-llm
#       spec §2.9: "GPT-5+ series"). Anchored to END in a digit so
#       tier-suffixed siblings (e.g. "-mini", "-codex") do not outrank the
#       bare release under the resolver's version-sort -- verified
#       empirically: a bare "gpt-5*" glob against
#       ["gpt-5.2", "gpt-5.2-mini", "gpt-5.2-codex"] picks "gpt-5.2-mini"
#       (a longer, non-numeric suffix compares *greater* once the shared
#       prefix ties), which is the wrong default. stable_only=True is safe
#       here (no provider-side "-preview" marker on this family today).
#   "gemini"    -> "gemini-3*pro*", stable_only=False: current flagship
#       generation is the Pro tier (unified-llm spec §2.9: "Gemini 3.1 Pro
#       Preview"). The provider's own current top model is itself
#       "-preview"-named, so stable_only=True (the resolver's default)
#       would filter out every candidate and always raise.
#
# Value: (family_token_or_glob, stable_only).
_PROVIDER_DEFAULT_MODEL_PATTERN: dict[str, tuple[str, bool]] = {
    "anthropic": ("sonnet", True),
    "openai": ("gpt-5.*[0-9]", True),
    "gemini": ("gemini-3*pro*", False),
}

# Per-process cache: (provider, raw_token) -> concrete served id. A given
# pattern resolves at most once per run, so a run is deterministic and the
# resolved id is stable across every node/loop iteration that uses it.
_MODEL_RESOLUTION_CACHE: dict[tuple[str, str, bool], str] = {}


def _is_model_pattern(model: str) -> bool:
    """True when *model* must be resolved (glob chars OR a known family token).

    A concrete id (no glob chars, not a family token) returns False and is
    passed straight through -- zero behavior change, no network call.
    """
    if any(ch in model for ch in "*?["):
        return True
    return model.strip().lower() in _FAMILY_TOKENS


@overload
async def _resolve_concrete_model(
    provider: str, model: str, *, emit: Any = None, stable_only: bool = True
) -> str: ...
@overload
async def _resolve_concrete_model(
    provider: str, model: str | None, *, emit: Any = None, stable_only: bool = True
) -> str | None: ...
async def _resolve_concrete_model(
    provider: str, model: str | None, *, emit: Any = None, stable_only: bool = True
) -> str | None:
    """Resolve a node's ``llm_model`` token to a concrete served model id.

    - ``None``/empty  -> returned unchanged (spawn path tolerates a missing
      model; the direct paths have already fail-loud'd via ``_resolve_model``).
    - concrete id     -> returned unchanged, NO network call (full back-compat).
    - glob / family   -> resolved live via ``unified_llm.resolve_latest_for``
      and cached per ``(provider, token, stable_only)`` so the run resolves
      once.

    ``stable_only`` (default ``True``, unchanged for every existing caller)
    lets a rung-4 provider-default (see ``_PROVIDER_DEFAULT_MODEL_PATTERN``)
    opt OUT of the stable-only filter when a provider's own current flagship
    is itself preview-named (e.g. Gemini 3.1 Pro Preview) -- otherwise the
    filter would exclude every candidate and always raise.

    Fail-loud: a no-match / unresolvable / missing-adapter condition raises
    ``ValueError`` from the resolver -- never a silent default.
    """
    if not model or not _is_model_pattern(model):
        return model

    cache_key = (provider, model, stable_only)
    cached = _MODEL_RESOLUTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # lazy import, matching the existing import idiom in this module
    from unified_llm import resolve_latest_for

    token = model.strip().lower()
    pattern = f"*{token}*" if token in _FAMILY_TOKENS else model
    concrete = await resolve_latest_for(provider, pattern, stable_only=stable_only)

    _MODEL_RESOLUTION_CACHE[cache_key] = concrete
    logger.info(
        "loop-pipeline resolved llm_model %r -> %r (provider=%s, pattern=%s, "
        "stable_only=%s)",
        model,
        concrete,
        provider,
        pattern,
        stable_only,
    )
    # Emit the resolution as a pipeline event so the run's event stream records
    # exactly which concrete model a pattern/family token resolved to (audit /
    # eval reproducibility). Fires once per distinct resolution (cache miss).
    if emit is not None:
        await emit(
            MODEL_RESOLVED,
            {
                "raw": model,
                "resolved": concrete,
                "provider": provider,
                "pattern": pattern,
            },
        )
    return concrete


def _make_tool_handler(pipeline_tool: Any) -> Any:
    """Create a unified_llm-compatible execute handler from a pipeline tool.

    Pipeline tools expect ``execute(input: dict)``.
    unified_llm calls ``tool.execute(**kwargs)``.
    This wrapper bridges the two conventions.
    """

    async def handler(**kwargs: Any) -> str:
        result = await pipeline_tool.execute(kwargs)
        if hasattr(result, "output"):
            return result.output
        return str(result)

    return handler


def _build_unified_tools(pipeline_tools: dict[str, Any]) -> list[Any]:
    """Convert pipeline tools to unified_llm.Tool objects."""
    import unified_llm

    tools: list[Any] = []
    for tool in pipeline_tools.values():
        schema = (
            getattr(tool, "parameters", None)
            or getattr(tool, "schema", None)
            or getattr(tool, "input_schema", None)  # ReportOutcomeTool exposes this
        )
        if schema is None:
            schema = {"type": "object", "properties": {}}

        execute_fn = None
        if hasattr(tool, "execute"):
            execute_fn = _make_tool_handler(tool)

        tools.append(
            unified_llm.Tool(
                name=getattr(tool, "name", str(tool)),
                description=getattr(tool, "description", ""),
                parameters=schema if isinstance(schema, dict) else {},
                execute=execute_fn,
            )
        )
    return tools


# Spawn-result status strings that count as a real, non-failing completion.
# These map 1:1 to non-FAIL StageStatus members; any other string (e.g.
# "error", "", or a missing status) is treated as "no success signal".
_SPAWN_SUCCESS_STATUSES = frozenset(
    {
        StageStatus.SUCCESS.value,
        StageStatus.PARTIAL_SUCCESS.value,
    }
)


def _outcome_from_spawn_result(result: Any) -> Outcome | None:
    """Recover an Outcome from a spawn result whose final text was empty.

    A child that completed its work via tool calls legitimately returns
    empty final text.  The spawn result still carries a signal in the
    orchestrator's own completion ``status``: a recognized success status
    means the child finished cleanly; empty closing prose is acceptable
    (spec Section 4.5 treats prose/empty success as SUCCESS).

    WAVE 5 repair (2026-08-30): the former ``metadata["report_outcome"]``
    branch is removed -- ``report_outcome`` is gone repo-wide, no compat
    window (specs/EXTENSIONS.md §35 RETCON, dated status: REMOVED).

    Returns the recovered Outcome, or ``None`` when there is genuinely no
    success signal, in which case the caller falls back / fails loud as
    before.
    """
    if not isinstance(result, dict):
        return None

    if result.get("status") in _SPAWN_SUCCESS_STATUSES:
        status = _STATUS_MAP[result["status"]]
        # The orchestrator's completion status is NOT an explicit verdict from
        # the node itself (no report_outcome, no JSON, no embedded recovery).
        # is_explicit=False so that a goal_gate node cannot satisfy its gate
        # solely because the spawn wrapper reported a clean exit.
        # EXTENSIONS.md §25: a goal_gate node requires is_explicit=True.
        return Outcome(
            status=status,
            notes="Child session completed with empty final message",
            is_explicit=False,
        )

    return None


def _synthesize_outcome_marker(outcome: Outcome) -> str:
    """Build an honest structured marker for a terminal turn that produced
    no closing prose (support#498).

    "work → report_outcome → end" is the normal agentic turn shape: a child
    can legitimately finish via tool calls and end on a terminal
    report_outcome with no trailing assistant text.  When that happens the
    transcript still needs a non-empty assistant half (issue #287: never emit
    a literal empty assistant message some providers reject) -- but it must
    not be invented prose either.  This synthesizes the missing half as
    attributed tool-event content, never a fabrication of what the child
    "would have said".

    Marker shape (support#498 review fix, binding on ``outcome.is_explicit``,
    EXTENSIONS.md §25/§35 -- this function reads the flag rather than
    trusting the caller's branch to imply it):
      - ``outcome.is_explicit is True``: a real ``report_outcome`` tool call
        (or an equivalent explicit JSON/embedded verdict) produced this
        outcome. Marker is prefixed ``report_outcome``::

            [report_outcome: status=success preferred_label=validated notes="..."]

      - ``outcome.is_explicit is False``: NO ``report_outcome`` call
        happened -- the outcome was INFERRED from the orchestrator's own
        completion status (spec §4.5 / EXTENSIONS.md §25). Using the
        ``report_outcome`` prefix here would assert a tool call that never
        occurred. Marker is prefixed ``spawn-completion`` instead::

            [spawn-completion: status=success notes="..."]

        A later model reading this transcript must be able to tell
        attributed-tool-event content (a real verdict) from an inferred
        completion status -- the prefix is the only signal it has.

    Contract (support#498 design, binding, applies to BOTH shapes above):
      - NO ``{``/``}`` anywhere in the returned string. ``_parse_outcome``'s
        prose-recovery rung scans a LATER turn's output for the last balanced
        ``{...}`` pair; a braced marker echoed back by a later model could be
        misparsed as a fresh embedded verdict.  Any brace characters that leak
        in via ``preferred_label``/``notes`` are neutralized defensively.
      - ``status`` is spelled in the lowercase spec §5.2 / StageStatus.value
        vocabulary (success|partial_success|retry|fail|skipped) -- never
        upper-cased or renamed.
      - Always non-empty: ``status`` is a required ``Outcome`` field, so the
        marker never degenerates to an empty string even when
        ``preferred_label`` and ``notes`` are both absent (e.g.
        ``"[report_outcome: status=success]"`` /
        ``"[spawn-completion: status=success]"``).
    """

    def _debrace(value: str) -> str:
        # Defensive: guarantee the "no braces anywhere" invariant even if a
        # child's report_outcome notes/label happened to contain literal
        # braces -- the marker itself must never be misparsable as JSON.
        return value.replace("{", "(").replace("}", ")")

    prefix = "report_outcome" if outcome.is_explicit else "spawn-completion"
    parts = [f"status={outcome.status.value}"]
    if outcome.preferred_label:
        parts.append(f"preferred_label={_debrace(str(outcome.preferred_label))}")
    if outcome.notes:
        parts.append(f'notes="{_debrace(str(outcome.notes))}"')
    return f"[{prefix}: " + " ".join(parts) + "]"


def _parse_outcome(output: str, *, node: object = None) -> Outcome:
    """Parse an outcome from child session output.

    Tries JSON first (from tool-report-outcome). Plain text responses return
    SUCCESS per spec Section 4.5 for ordinary nodes — the backend is only
    responsible for producing Outcome objects when it wants non-SUCCESS status.
    Empty output returns FAIL (no work was done).

    EXTENSIONS.md §25 — fail-closed goal-gate contract:
    When ``node`` is provided and the node carries ``goal_gate=true``, a
    plain-text response (no JSON, no report_outcome, no embedded verdict) is
    NOT sufficient to satisfy the gate.  In that case the outcome is RETRY
    (respects max_retries, then degrades to FAIL) rather than silent SUCCESS.
    ``is_explicit=True`` is set on every outcome that came from a real verdict
    (JSON / fenced JSON / embedded recovery); the plain-text fallback leaves
    ``is_explicit=False`` so downstream checks and analysts can distinguish the
    two cases without reverse-engineering the notes prefix.

    Args:
        output: The raw text output from the LLM node.
        node: Optional graph Node object.  When supplied and the node has
            ``goal_gate=true``, the fail-closed contract applies to the
            plain-text fallback path.
    """
    # Empty/whitespace-only output means no work was done
    stripped = output.strip()
    if not stripped:
        return Outcome(
            status=StageStatus.FAIL,
            notes="No output from LLM",
            failure_reason="Empty LLM response",
            is_explicit=False,
        )

    # Strip markdown code fences (```json...``` or ```...```) that LLMs sometimes
    # emit despite explicit "no fences" instructions.  This is a common failure mode
    # when the eval node prompt asks for a JSON object: the LLM wraps it in a fence,
    # making stripped.startswith("{") false and causing context_updates to be lost.
    # Example: "```json\n{...}\n```" -> "{...}"
    _fence_match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", stripped, re.DOTALL)
    if _fence_match:
        stripped = _fence_match.group(1).strip()

    # Try to parse JSON outcome
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if "status" in data:
                status = _STATUS_MAP.get(data["status"])
                if status is not None:
                    return Outcome(
                        status=status,
                        failure_reason=data.get("failure_reason"),
                        notes=data.get("notes"),
                        preferred_label=data.get("preferred_label"),
                        suggested_next_ids=data.get("suggested_next_ids"),
                        context_updates=data.get("context_updates"),
                        is_explicit=True,
                        response_text=output,  # EXTENSIONS.md §26: carry full text
                    )
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # Before falling through to the SUCCESS default, attempt to RECOVER an
    # embedded verdict from prose-wrapped responses.  Models sometimes emit prose
    # followed by a JSON verdict object (e.g. "Here's my verdict:\n{...}") rather
    # than pure JSON.  We find the LAST balanced {...} in the string and, if it
    # contains a recognised status, honour it rather than silently coercing to
    # SUCCESS.  Pure-JSON and fenced-JSON paths above are unchanged; this only
    # fires when both prior branches were skipped (stripped does NOT start with
    # "{" or a code fence).
    #
    # Spec invariant: an explicit FAIL/RETRY verdict MUST NOT be silently coerced
    # to SUCCESS.  Verdict nodes that want reliable parsing should emit pure JSON
    # or call the report_outcome tool.
    last_open = stripped.rfind("{")
    if last_open != -1:
        depth = 0
        end_pos = -1
        for _i in range(last_open, len(stripped)):
            _ch = stripped[_i]
            if _ch == "{":
                depth += 1
            elif _ch == "}":
                depth -= 1
                if depth == 0:
                    end_pos = _i
                    break
        if end_pos != -1:
            candidate = stripped[last_open : end_pos + 1]
            try:
                _embedded = json.loads(candidate)
                if isinstance(_embedded, dict) and "status" in _embedded:
                    _recovered_status = _STATUS_MAP.get(_embedded["status"])
                    if _recovered_status is not None:
                        logger.warning(
                            "Verdict recovered from prose-wrapped response "
                            "(embedded status=%r).  Verdict nodes should emit "
                            "pure JSON or call the report_outcome tool.",
                            _embedded["status"],
                        )
                        return Outcome(
                            status=_recovered_status,
                            failure_reason=_embedded.get("failure_reason"),
                            notes=_embedded.get("notes"),
                            preferred_label=_embedded.get("preferred_label"),
                            suggested_next_ids=_embedded.get("suggested_next_ids"),
                            context_updates=_embedded.get("context_updates"),
                            is_explicit=True,
                            response_text=output,  # EXTENSIONS.md §26: carry full text
                        )
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    # Plain text response — no explicit verdict recovered.
    # EXTENSIONS.md §25: for goal_gate=true nodes, a plain-text response is NOT
    # sufficient — the gate requires an explicit verdict.  Return RETRY so the
    # engine respects max_retries and degrades to FAIL rather than silently
    # satisfying the gate.  For all other nodes, fall through to SUCCESS per
    # spec Section 4.5 (backward-compatible default).
    _is_goal_gate = (
        node is not None
        and hasattr(node, "attrs")
        and resolve_bool_attr(node.attrs.get("goal_gate"), "goal_gate")  # type: ignore[union-attr]
    )
    if _is_goal_gate:
        logger.warning(
            "Node %r (goal_gate=true) produced plain-text output with no "
            "explicit verdict (no report_outcome, no JSON, no embedded verdict). "
            "Fail-closed contract (EXTENSIONS.md §25): returning RETRY so the "
            "gate is not satisfied by a defaulted plain-text response. "
            "Node output (first 200 chars): %r",
            getattr(node, "id", repr(node)),
            output[:200],
        )
        return Outcome(
            status=StageStatus.RETRY,
            notes=f"No explicit verdict from goal_gate node — plain text only: {output[:200]}",
            failure_reason="goal_gate node requires an explicit verdict (report_outcome / JSON)",
            is_explicit=False,
            response_text=output,  # EXTENSIONS.md §26: carry full text
        )

    # Non-goal_gate node: plain text response — per spec Section 4.5, treat as SUCCESS.
    return Outcome(
        status=StageStatus.SUCCESS,
        notes=f"Plain text response: {output[:200]}",
        is_explicit=False,
        response_text=output,  # EXTENSIONS.md §26: carry full text
    )
