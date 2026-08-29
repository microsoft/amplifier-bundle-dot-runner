"""Attractor pipeline orchestrator module.

A DOT graph-driven multi-stage AI workflow engine. Parses directed graphs
(defined in Graphviz DOT syntax) to orchestrate multi-stage AI pipelines
where each node is an AI task and edges define the flow between them.

Implements the Attractor specification (attractor-spec.md).
"""

from __future__ import annotations

# Amplifier module metadata
__amplifier_module_type__ = "orchestrator"

import json
import logging
import os
import tempfile
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .context import PipelineContext
from .engine import PipelineEngine
from .handlers import HandlerRegistry
from .handlers.context import HandlerContext
from .outcome import Outcome
from .preflight import check_provider_preflight
from .transforms import apply_transforms
from .validation import validate_or_raise

logger = logging.getLogger(__name__)


class DirectProviderBackend:
    """DEPRECATED compatibility shim for the former standalone direct-execution backend.

    ``DirectProviderBackend`` was a documented library-integration path (the
    former class lived HERE, at ``__init__.py:39-394`` pre-merge, so
    ``from amplifier_module_loop_pipeline import DirectProviderBackend``
    worked) -- notably ``amplifier-bundle-attractor/README.md:323,343`` and
    ``examples/programmatic_usage.py:71,86``, plus that repo's
    ``docs/APP-INTEGRATION-GUIDE.md``. DESIGN-worker-registry-core-split.md
    P1 (gap-table row 2) merged that class's body into the registry's
    ``direct`` worker (``workers/direct_worker.py``) and deleted the
    standalone class. This shim exists ONLY to keep that documented import
    path and constructor/``run()`` signature working through a deprecation
    window -- it is not itself registered anywhere (the worker registry
    only ever knows the real ``\"direct\"`` worker by name; see
    ``workers/registry.py``) and carries no independent logic: every call
    delegates straight through to ``AmplifierBackend`` constructed in the
    spawn-absent shape (``coordinator=None``, ``default_worker=\"direct\"``),
    the exact routing ``_build_backend`` now uses for a standalone,
    no-coordinator caller.

    Migrate to: construct ``AmplifierBackend`` directly (optionally passing
    ``default_worker=\"direct\"`` to pin the worker explicitly, though a
    coordinator-less/spawn-less construction already resolves to ``direct``
    by the unchanged capability-fallback chain -- see
    ``specs/EXTENSIONS.md`` Sec40 for the full selection policy this
    replaces this class with).
    """

    def __init__(
        self,
        provider: Any,
        tools: dict[str, Any] | None = None,
        hooks: Any = None,
        coordinator: Any = None,
        unified_client: Any | None = None,
    ) -> None:
        warnings.warn(
            "DirectProviderBackend is deprecated and will be removed in a "
            "future release. Use the worker registry's `direct` worker via "
            "AmplifierBackend instead (see specs/EXTENSIONS.md Sec40 for "
            "the worker-selection policy that replaces this class). This "
            'shim delegates to AmplifierBackend(default_worker="direct") '
            "and will keep working, unchanged, through the deprecation "
            "window.",
            DeprecationWarning,
            stacklevel=2,
        )
        # coordinator is accepted for constructor-signature compatibility
        # with the pre-merge class (which took it but never consulted it in
        # run()) -- deliberately NOT forwarded to AmplifierBackend. This
        # shim always constructs the spawn-absent shape, matching the
        # original class's own contract: "the default backend when no
        # session.spawn capability is available."
        self._coordinator = coordinator

        from .backend import AmplifierBackend

        self._backend = AmplifierBackend(
            coordinator=None,
            provider=provider,
            tools=tools,
            hooks=hooks,
            unified_client=unified_client,
            default_worker="direct",
        )

    async def run(
        self,
        node: Any,
        prompt: str,
        context: PipelineContext,
        *,
        incoming_edge: Any | None = None,
        graph: Any | None = None,
        **kwargs: Any,
    ) -> Outcome:
        """Delegate to the ``direct`` worker via ``AmplifierBackend``.

        Preserves the pre-merge public ``run()`` signature and Outcome
        return shape so existing library-integration callers keep working,
        unmodified, through the deprecation window.
        """
        return await self._backend.run(
            node, prompt, context, incoming_edge=incoming_edge, graph=graph
        )


def _spawn_capability(coordinator: Any | None) -> Any | None:
    """Resolve ``session.spawn`` off a coordinator, tolerating stand-ins.

    One home for the capability gate both profile-resolution call sites use
    (``_build_backend`` and ``execute()``'s preflight step 5b).  Bare test
    stubs may not expose ``get_capability`` at all, and a coordinator is free
    to raise for an unknown capability name; both mean "no spawn backend".
    """
    if coordinator is None or not hasattr(coordinator, "get_capability"):
        return None
    try:
        return coordinator.get_capability("session.spawn")
    except Exception:
        return None


def _resolve_profiles(
    config: dict[str, Any] | None,
    coordinator: Any | None,
) -> dict[str, str]:
    """THE single home for provider -> agent-profile resolution (issue #279).

    Consumed by BOTH homes that need it -- ``_build_backend()`` (which hands
    the result to ``AmplifierBackend``) and ``PipelineOrchestrator.execute()``
    step 5b (which hands the same result to the startup provider preflight).
    They previously carried two independent copies of this logic with no test
    pinning them to each other, so a solo edit to either could drift silently;
    ``tests/test_profile_resolver_parity.py`` now pins both call sites here.

    Resolution order (either/or, by truthiness -- an explicit but EMPTY
    ``profiles`` mapping therefore falls through to auto-discovery, in both
    homes, exactly as before):

    1. Explicit ``config["profiles"]`` mapping, e.g.
       ``{"anthropic": "attractor-anthropic"}``.
    2. Auto-discovery from ``coordinator.config["agents"]`` -- each dict-valued
       agent entry maps as ``agent_name -> agent_name``.  Gated on the
       ``session.spawn`` capability, because auto-discovered profiles are only
       ever consumed by the spawn backend.

    Raises whatever a malformed coordinator config raises (e.g. a non-mapping
    ``agents``).  ``_build_backend`` lets that propagate, as it always has;
    the preflight call site catches it and proceeds with FEWER profiles, so a
    discovery crash can only ever produce a refusal, never a false accept.
    """
    profiles: dict[str, str] = {}
    cfg = config or {}

    # Source 1: Explicit profiles mapping in orchestrator config
    explicit_profiles = cfg.get("profiles")
    if isinstance(explicit_profiles, dict):
        profiles.update(explicit_profiles)

    # Source 2: Auto-discover from coordinator.config["agents"]
    if not profiles and _spawn_capability(coordinator) is not None:
        coordinator_config = getattr(coordinator, "config", None) or {}
        agents = coordinator_config.get("agents", {})
        for agent_name, agent_cfg in agents.items():
            if isinstance(agent_cfg, dict):
                profiles[agent_name] = agent_name

    return profiles


def _spawn_resolvable_agents(coordinator: Any | None) -> frozenset[str] | None:
    """Profile names the spawn backend can actually resolve (issue #195).

    A profile is a STRING naming an agent.  ``AmplifierBackend._run_with_spawn``
    resolves it in exactly one place -- ``coordinator.config["agents"]`` -- and
    refuses an entry it cannot find there.  A profile naming an absent agent is
    therefore unserviceable no matter how many credentials are set, and every
    visit to a node declaring that provider fails, draining the budget (the
    residual #155 crash loop reported in #195).  The keys of that same mapping
    are the statically knowable answer, read with no spawn and no live call.

    Returns ``None`` -- meaning "not knowable here; do not police it" -- when:

    - there is no ``session.spawn`` capability (the spawn backend is not the
      one that will run, so profiles are never consumed at all); or
    - ``coordinator.config`` / its ``agents`` entry is not a mapping we can
      inspect statically (a stub or proxy coordinator).  The runtime guard in
      ``backend.py`` still covers those.
    """
    if _spawn_capability(coordinator) is None:
        return None
    coordinator_config = getattr(coordinator, "config", None)
    if not isinstance(coordinator_config, Mapping):
        return None
    agents = coordinator_config.get("agents", {})
    if not isinstance(agents, Mapping):
        return None
    return frozenset(str(name) for name in agents)


def _build_backend(
    providers: dict[str, Any],
    tools: dict[str, Any],
    hooks: Any,
    coordinator: Any | None,
    orchestrator_config: dict[str, Any] | None = None,
) -> Any | None:
    """Auto-construct a backend from the available providers.

    Resolution order (DESIGN-worker-registry-core-split.md P1, gap-table
    row 2): ``AmplifierBackend`` is now the ONE adapter class constructed
    here in every case -- what used to be a choice between two top-level
    *classes* (``AmplifierBackend`` vs. the now-deleted
    ``DirectProviderBackend``) is a choice the adapter itself makes at
    ``run()`` time between registered *workers* (``"spawn"`` vs.
    ``"direct"``; see ``AmplifierBackend._resolve_worker_name``). The
    observable ROUTING is unchanged (capability-fallback still selects the
    `direct` worker when spawn is absent, exactly as the former
    `DirectProviderBackend` branch did) -- what's new is that `direct` is
    now also selectable BY NAME even when spawn IS available.

    1. If coordinator exposes ``session.spawn`` -> profiles are resolved from
       ``orchestrator_config["profiles"]`` or auto-discovered from
       ``coordinator.config["agents"]``; the "spawn" worker becomes
       selectable.
    2. Else if at least one provider is available -> the adapter is still
       constructed (with no working spawn capability), so the "direct"
       worker handles every node -- the former ``DirectProviderBackend``
       standalone-usage scenario.
    3. Otherwise -> return None (codergen handler falls through to
       simulation mode).

    ``orchestrator_config["worker"]`` (if set) becomes the run-level
    ``default_worker`` -- EXTENSIONS.md \u00a740.
    """
    first_provider = next(iter(providers.values()), None) if providers else None
    default_worker = (orchestrator_config or {}).get("worker")

    if first_provider is None and (
        coordinator is None or _spawn_capability(coordinator) is None
    ):
        logger.warning(
            "No providers available \u2014 codergen nodes will run in simulation mode"
        )
        return None

    from .backend import AmplifierBackend

    spawn_fn = _spawn_capability(coordinator) if coordinator is not None else None
    if spawn_fn is not None:
        # Resolve profiles: explicit config > auto-discovery from agents.
        # ONE home for that rule -- _resolve_profiles() (issue #279); the
        # startup preflight in execute() step 5b consumes the SAME
        # function, and tests/test_profile_resolver_parity.py pins both
        # call sites to it so the two can no longer drift apart.
        profiles: dict[str, str] = _resolve_profiles(orchestrator_config, coordinator)

        if profiles:
            logger.info(
                "Using AmplifierBackend (session.spawn available, profiles=%s)",
                list(profiles.keys()),
            )
        else:
            logger.warning(
                "Using AmplifierBackend but profiles dict is empty. "
                "Pipeline nodes may fail to resolve agent profiles. "
                "Add 'profiles' to orchestrator config or 'agents' "
                "to the bundle."
            )
    else:
        profiles = {}
        logger.info(
            "Using AmplifierBackend (session.spawn unavailable -- the "
            "`direct` worker will handle every node)"
        )

    return AmplifierBackend(
        coordinator,
        profiles=profiles,
        provider=first_provider,
        tools=tools,
        hooks=hooks,
        default_worker=default_worker,
    )


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the loop-pipeline orchestrator.

    Config options:
        dot_source: Inline DOT digraph string.
        dot_file: Path to a .dot file.
    """
    cfg = config or {}
    orchestrator = PipelineOrchestrator(cfg)
    await coordinator.mount("orchestrator", orchestrator)
    logger.info("loop-pipeline orchestrator mounted")


class PipelineOrchestrator:
    """DOT graph-driven pipeline orchestrator.

    Parses a DOT digraph and walks it node-by-node, executing handlers
    for each node type and selecting edges based on outcomes.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def execute(
        self,
        prompt: str,
        context: Any,
        providers: dict[str, Any],
        tools: dict[str, Any],
        hooks: Any,
        **kwargs: Any,
    ) -> str:
        """Execute the pipeline.

        Parses the DOT graph, validates it, and walks from start to exit.

        Returns a JSON string with the pipeline outcome.
        """
        # 1. Get DOT source
        dot_source, source_dir = self._resolve_dot_source()

        # 2. Parse the DOT graph — materialize first if it's a remote entry.
        # Shared with the direct-engine `drive_engine`/`_load_graph` hook in
        # pipeline-runner via remote_dot.load_remote_or_local_graph, so the two
        # engine entry points can't diverge (see that function's docstring).
        from .remote_dot import (
            load_remote_or_local_graph,
        )  # lazy: keeps import net-free

        graph, _source_cleanup = await load_remote_or_local_graph(dot_source)
        # A file-backed root graph carries the directory it was read from, so
        # relative dot_file= children resolve beside the pipeline rather than
        # falling through resolve_dot_path()'s precedence chain to
        # context.target_dir (--cwd). Mirrors the same fix already applied to
        # the standalone CLI path (pipeline-runner cli.py/runner.py) -- see
        # AGENTS.md's S4 partial-coverage-symmetry note; this is the second of
        # the two remaining sites. Never clobber: a remote package or an
        # already-parsed Graph carries its own, more specific source_dir.
        if source_dir and not getattr(graph, "source_dir", ""):
            graph.source_dir = source_dir

        try:
            # 3. Create pipeline context with goal from the prompt
            pipeline_context = PipelineContext()
            if prompt:
                pipeline_context.set("graph.goal", prompt)

            # Set params for $param expansion in transforms
            params = self.config.get("params")
            if params:
                pipeline_context.set("graph.params_values", params)

            # 4. Apply transforms (variable expansion, stylesheet) before validation
            apply_transforms(graph, pipeline_context)

            # 5. Validate the (transformed) graph
            validate_or_raise(graph)

            # 5b. Provider preflight (issue #155, EXTENSIONS.md section 36):
            # before the walk begins, cross-check every node's DECLARED
            # llm_provider against what this run can serve and refuse to
            # start -- naming each failing node, its provider, and the
            # missing credential.  An unserviceable provider used to crash
            # on every visit and drain the entire iteration budget in a
            # crash loop.  Skipped when the caller injects an explicit
            # backend (kwargs["backend"]): an injected backend's
            # serviceability is the injector's responsibility and is not
            # described by `providers`/`profiles` (this is also what keeps
            # mock-backend tests honest -- they are not making provider
            # claims).  The auto-constructed backend path (production) is
            # always checked.
            if kwargs.get("backend") is None:
                _coordinator = kwargs.get("coordinator")
                # Resolve profiles for the preflight through the SAME function
                # _build_backend() uses -- _resolve_profiles() (issue #279).
                # Before the extraction this call site carried its own copy of
                # the two-source rule (explicit config first, then
                # auto-discovery from coordinator.config["agents"]), pinned to
                # the other copy only by a line-number comment; the copies
                # could drift silently, and a preflight that saw FEWER sources
                # than the backend raised false refusals (issue #196).
                # Fail-closed: a discovery crash yields FEWER profiles, which
                # can only ever produce a refusal -- never a false accept.
                try:
                    preflight_profiles: dict[str, str] | None = _resolve_profiles(
                        self.config, _coordinator
                    )
                except Exception:
                    preflight_profiles = None
                # Issue #195: a profile is a STRING naming an agent.  Knowing
                # it is MAPPED is not knowing it can be RESOLVED -- a profile
                # naming an absent agent passes credential presence and then
                # fails at every spawn, draining the budget instead of
                # refusing.  Hand the preflight the names the spawn backend
                # can actually resolve so that class refuses at startup.
                # Fail-closed here too: unknowable-because-it-crashed becomes
                # the empty set (refuse), never None (skip the check).
                try:
                    _resolvable = _spawn_resolvable_agents(_coordinator)
                except Exception:
                    _resolvable = frozenset()
                check_provider_preflight(
                    graph,
                    mounted_providers=tuple(providers) if providers else (),
                    profiles=preflight_profiles,
                    resolvable_profiles=_resolvable,
                )

            # 6. Set up logs directory
            logs_root = self.config.get(
                "logs_root", os.path.join(tempfile.gettempdir(), "attractor-pipeline")
            )
            os.makedirs(logs_root, exist_ok=True)

            # 6b. Write the DOT source for dashboard visualization
            dot_path = os.path.join(logs_root, "graph.dot")
            with open(dot_path, "w") as f:
                f.write(
                    dot_source
                    if not dot_source.startswith("git+https://")
                    else f"// materialized from {dot_source}\n"
                )

            # 7. Resolve backend: explicit kwarg \u2192 auto-construct from providers
            coordinator = kwargs.get("coordinator")
            backend = kwargs.get("backend")
            if backend is None:
                backend = _build_backend(
                    providers, tools, hooks, coordinator, self.config
                )

            # 7b. Environment setup (if configured)
            env_config: dict[str, Any] | None = self.config.get("execution_environment")
            container_id = None
            env_instance_name = "pipeline-workspace"  # default for teardown
            if env_config:
                env_instance_name = env_config.get("name", "pipeline-workspace")
                if "env_create" in tools:
                    env_create_args = dict(env_config)  # copy to avoid mutating config
                    env_create_args.setdefault("type", "docker")
                    env_create_args.setdefault("name", "pipeline-workspace")
                    result = await tools["env_create"].execute(env_create_args)
                    try:
                        parsed = json.loads(result.output)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(
                            "env_create returned unparseable output: %s", result.output
                        )
                        parsed = {}
                    container_id = parsed.get("container_id")
                    if container_id:
                        pipeline_context.set("internal.env_container_id", container_id)
                        pipeline_context.set(
                            "internal.env_type", env_config.get("type", "docker")
                        )
                        logger.info(
                            "Execution environment created: %s (container_id=%s)",
                            env_instance_name,
                            container_id,
                        )
                    else:
                        logger.warning(
                            "env_create succeeded but returned no container_id "
                            "— falling back to local execution"
                        )
                else:
                    logger.warning(
                        "execution_environment configured but env_create tool not "
                        "available (env-all bundle not composed?) — falling back "
                        "to local execution"
                    )

            # 8. Create registry (no closures, no rewire — engine passes self at call time)
            registry = HandlerRegistry(
                HandlerContext(
                    backend=backend,
                    hooks=hooks,
                )
            )

            # 9. Create engine (carries itself to handlers via execute(engine=...))
            engine = PipelineEngine(
                graph=graph,
                context=pipeline_context,
                handler_registry=registry,
                logs_root=logs_root,
                hooks=hooks,
            )

            # 10. Run the engine (with environment teardown in finally)
            try:
                outcome = await engine.run(goal=prompt or None)
            finally:
                # Backend teardown: release any cached LLM client (e.g. the
                # AsyncAnthropic/httpx client the fallback path lazily creates)
                # WITHIN this event loop. Skipping it lets GC run aclose() on a
                # closed loop later, raising "RuntimeError: Event loop is closed"
                # (spec finalize contract: attractor-spec.md Section 3.1 step 6).
                backend_close = getattr(backend, "close", None)
                if backend_close is not None:
                    try:
                        await backend_close()
                    except Exception:
                        logger.exception(
                            "Failed to close backend during finalize - LLM client may leak"
                        )

                # Environment teardown
                if container_id and "env_destroy" in tools:
                    try:
                        await tools["env_destroy"].execute(
                            {"instance": env_instance_name}
                        )
                        logger.info(
                            "Execution environment destroyed: %s",
                            env_instance_name,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to destroy execution environment %s "
                            "— container may need manual cleanup",
                            env_instance_name,
                        )

            # 12. Build a meaningful summary from all completed nodes
            summary = self._build_pipeline_summary(engine, outcome)

            # 13. Return the final outcome as JSON
            result = {
                "status": outcome.status.value,
                "notes": summary,
                "failure_reason": outcome.failure_reason,
                "nodes_completed": len(engine.completed_nodes),
                "node_statuses": {
                    nid: engine.node_outcomes[nid].status.value
                    for nid in engine.completed_nodes
                    if nid in engine.node_outcomes
                },
            }
            return json.dumps(result)
        finally:
            _source_cleanup()

    def _build_pipeline_summary(self, engine: PipelineEngine, outcome: Outcome) -> str:
        """Build a human-readable pipeline summary.

        If the final outcome has meaningful notes, use them.
        Otherwise, synthesize a summary from all completed nodes.
        """
        # Use the outcome's notes if they exist and are meaningful
        if outcome.notes and len(outcome.notes) > 20:
            return outcome.notes

        # Synthesize from all node outcomes
        parts: list[str] = []
        total = len(engine.completed_nodes)
        succeeded = sum(
            1
            for nid in engine.completed_nodes
            if nid in engine.node_outcomes and engine.node_outcomes[nid].is_success
        )
        failed = total - succeeded

        parts.append(f"Pipeline completed: {succeeded}/{total} nodes succeeded.")

        if failed:
            failed_nodes = [
                nid
                for nid in engine.completed_nodes
                if nid in engine.node_outcomes
                and not engine.node_outcomes[nid].is_success
            ]
            parts.append(f"Failed nodes: {', '.join(failed_nodes)}.")

        # Include the last node's notes if available
        if engine.completed_nodes:
            last_id = engine.completed_nodes[-1]
            last_out = engine.node_outcomes.get(last_id)
            if last_out and last_out.notes:
                # Truncate to avoid bloating the summary
                snippet = last_out.notes[:300]
                parts.append(f"Last node ({last_id}): {snippet}")

        return " ".join(parts)

    def _resolve_dot_source(self) -> tuple[str, str | None]:
        """Resolve DOT source from config (inline or file).

        Returns ``(dot_source, source_dir)``. ``source_dir`` is the directory
        the ``dot_file`` was read from, so the caller can seed a file-backed
        root graph's ``source_dir`` for relative ``dot_file=`` child
        resolution (see ``resolve_dot_path`` in ``handlers/pipeline.py``).

        ``source_dir`` is ``None`` when:
          - the source came from inline ``dot_source`` config -- no file, no
            directory to claim -- OR
          - the caller (e.g. the ``run_pipeline`` tool) already resolved a
            ``dot_file`` to text itself and forwarded the directory
            explicitly via the ``source_dir`` config key, in which case that
            explicit value is returned instead of re-deriving one.
        """
        dot_source = self.config.get("dot_source")
        if dot_source:
            return dot_source, self.config.get("source_dir")

        dot_file = self.config.get("dot_file")
        if dot_file:
            with open(dot_file) as f:
                return f.read(), str(Path(dot_file).resolve().parent)

        raise ValueError(
            "No DOT source configured. Set 'dot_source' or 'dot_file' in config."
        )
