"""Faithful fakes for the amplifier-agent seam, shared by the hermetic tests.

Each fake mirrors the REAL object's contract closely enough to exercise the
actual seam this module depends on (not just a name-shaped stub):

  * ``FakeEngine.submit_turn`` builds a context object with the same fields
    ``amplifier_agent_lib.engine.Engine.submit_turn`` builds
    (``session_id``/``turn_id``/``prompt``/``approval``/``display``), calls
    the injected ``turn_handler`` with it, and returns a ``reply`` key --
    exactly the seam ``AmplifierAgentOrchestrator._run_turn`` depends on.
  * ``FakePreparedBundle.create_session`` returns a ``FakeSession`` whose
    ``coordinator.mount("tools", tool, name=...)`` records any mounted tool
    exactly like the real per-turn coordinator does (WAVE 4: the real
    orchestrator no longer mounts a report_outcome reach-in itself -- ruling
    5 -- so ``mounted_tools`` is normally empty; this fake keeps the
    recording behavior only because ``FakeSessionCoordinator`` is a general
    double other tests may still exercise).

See ``/var/tmp/aa-probe/probe_q1_q2.py`` for the real end-to-end mechanism
these fakes stand in for.
"""

from __future__ import annotations

import enum
from types import SimpleNamespace
from typing import Any, Self


class FakeHookRegistry:
    """Mirrors amplifier_core's Rust-backed HookRegistry seam this module uses.

    Real contract: ``amplifier_core/_engine.pyi``'s
    ``set_default_fields(self, **kwargs: Any) -> None`` -- the real
    AmplifierSession (``amplifier_core/session.py``) and the vendored
    ``make_turn_handler`` both call this to stamp ``session_id``/``turn_id``
    (etc.) onto every subsequent event the coordinator's hooks emit. This
    fake just records every call so a test can assert the right fields were
    stamped (gap 5).
    """

    def __init__(self) -> None:
        self.default_fields_calls: list[dict[str, Any]] = []

    def set_default_fields(self, **kwargs: Any) -> None:
        self.default_fields_calls.append(kwargs)


class FakeContextManager:
    """Minimal double for the ContextManager mount seam (support#497 replay).

    Honest scope note (adopted-PR review): ``amplifier_core.interfaces.
    ContextManager`` declares FIVE async methods -- ``add_message``,
    ``get_messages_for_request``, ``get_messages``, ``set_messages``,
    ``clear``. This fake implements only TWO of them: ``get_messages`` and
    ``set_messages``. That is deliberate, not an oversight -- those are the
    only two the continuity-replay seam under test ever calls
    (``_history_from_context`` reads the PARENT context via
    ``get_messages``; ``_run_turn`` replays into the HOSTED context via
    ``set_messages``). It does not model compaction, the dynamic
    system-prompt-factory mechanism, or ``get_messages_for_request``
    filtering -- see ``FakeFactoryContextManager`` below for the one test
    that actually needs that fuller behavior. Reach for a fuller double
    (or extend that one) rather than growing this one past what its own
    tests exercise.

    The real context module (context-simple) stores messages as its single
    source of truth: ``set_messages`` writes ``self.messages`` and
    ``get_messages`` reads it back. This fake records ``set_messages`` calls
    and serves ``get_messages`` from the same list so a hermetic test can
    assert the continuity handoff without the real amplifier-agent stack.
    """

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.messages: list[dict[str, Any]] = list(messages or [])
        self.set_messages_calls: list[list[dict[str, Any]]] = []

    async def get_messages(self) -> list[dict[str, Any]]:
        return list(self.messages)

    async def set_messages(self, messages: list[dict[str, Any]]) -> None:
        self.set_messages_calls.append(list(messages))
        self.messages = list(messages)


class FakeFactoryContextManager:
    """Models context-simple's dynamic-system-prompt path (the REAL, only
    mechanism amplifier-agent's own baked-in bundle ever uses -- see
    ``amplifier_agent_lib/bundle/bundle.md``'s ``context: module:
    context-simple``, and ``amplifier_module_context_simple.
    SimpleContextManager``, the module installed at that seam).

    Built for exactly one question raised in PR #3 review (adopted-with-
    changes): ``_run_turn``'s history replay
    (``hosted_context.set_messages(history)``) runs AFTER
    ``prepared.create_session()`` has already seeded the hosted session's
    system framing. ``create_session()`` prefers
    ``context.set_system_prompt_factory(factory)`` when the context module
    supports it (``hasattr(context_manager, "set_system_prompt_factory")``
    -- see ``amplifier_foundation/bundle/_prepared.py::create_session``),
    falling back to a static ``add_message({"role": "system", ...})`` only
    when it does not. context-simple -- the ONLY context module
    amplifier-agent's bundle ever mounts -- always supports the factory.

    The real ``SimpleContextManager.get_messages_for_request()`` calls the
    factory fresh on EVERY request and PREPENDS its output, filtering any
    stored ``role="system"`` message out of ``self.messages`` (unless
    hook-injected). Critically, a factory-produced system message is NEVER
    written into ``self.messages`` at all -- so ``set_messages()`` replacing
    ``self.messages`` wholesale (exactly what history replay does) has
    nothing of the system framing to clobber. This fake reproduces that
    subset of the real behavior precisely enough to pin that claim; see
    ``test_system_framing_survives_history_replay`` in
    ``test_orchestrator.py``.
    """

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._system_prompt_factory: Any = None
        self.set_messages_calls: list[list[dict[str, Any]]] = []

    async def add_message(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    async def set_system_prompt_factory(self, factory: Any) -> None:
        self._system_prompt_factory = factory

    async def get_messages(self) -> list[dict[str, Any]]:
        return list(self.messages)

    async def set_messages(self, messages: list[dict[str, Any]]) -> None:
        self.set_messages_calls.append(list(messages))
        self.messages = list(messages)

    async def get_messages_for_request(
        self, token_budget: int | None = None, provider: Any | None = None
    ) -> list[dict[str, Any]]:
        """Mirrors SimpleContextManager's factory-mode branch: fresh system
        message from the factory, prepended to conversation messages with
        any stored system messages filtered out."""
        if self._system_prompt_factory is not None:
            system_content = await self._system_prompt_factory()
            conversation = [m for m in self.messages if m.get("role") != "system"]
            return [{"role": "system", "content": system_content}, *conversation]
        return list(self.messages)


class FakeSessionCoordinator:
    def __init__(self, context_module: Any = None) -> None:
        self.capabilities: dict[str, Any] = {}
        self.mounted_tools: dict[str, Any] = {}
        # Mirrors the coordinator's ``.config`` attribute -- the live session
        # mount plan. Gap 4's ``_resolve_parent_provider_preference`` reads
        # this (a REAL seam: ``ModuleCoordinator.config`` is the same dict
        # ``AmplifierSession.__init__`` stores as ``self.config``). Empty by
        # default; tests that exercise provider_preferences assign to it
        # directly before calling ``execute()``.
        self.config: dict[str, Any] = {}
        # Mirrors ``coordinator.hooks`` (gap 5).
        self.hooks = FakeHookRegistry()
        # Mirrors the mounted "context" module (context-simple) that the
        # support#497 continuity fix seeds via
        # coordinator.get("context").set_messages(history). Present-but-empty
        # by default; only exercised when execute() is handed a non-empty
        # parent context. A test that needs to pin the system-prompt-survival
        # question (does replay clobber the hosted session's OWN system
        # framing?) overrides this with a ``FakeFactoryContextManager``.
        self.context_module = (
            context_module if context_module is not None else FakeContextManager()
        )

    def get(self, name: str) -> Any:
        """Mount-registry accessor (``coordinator.get``), mirroring the real
        seam the support#497 continuity fix relies on: context-simple mounts
        via ``coordinator.mount()``, so the adapter reads it back with
        ``coordinator.get`` (NOT ``get_capability``). Returns the mounted
        context module for "context"; None for any other name.
        """
        if name == "context":
            return self.context_module
        return None

    def register_capability(self, name: str, fn: Any) -> None:
        self.capabilities[name] = fn

    async def mount(self, kind: str, obj: Any, name: str | None = None) -> None:
        if kind == "tools":
            key = name if name is not None else str(getattr(obj, "name", ""))
            self.mounted_tools[key] = obj


class FakeSession:
    """Stands in for the AmplifierSession the real ``create_session`` returns."""

    def __init__(
        self,
        *,
        reply_text: str = "",
        raise_on_execute: Exception | None = None,
        context_module: Any = None,
    ) -> None:
        self.coordinator = FakeSessionCoordinator(context_module=context_module)
        self._reply_text = reply_text
        self._raise_on_execute = raise_on_execute
        self.prompt_seen: str | None = None
        # Set during execute() if an "approval.request" capability is
        # registered (gap 3) -- mirrors a real child turn that hits at
        # least one approval-gated tool call. Faithful to the real seam:
        # amplifier-agent's own hooks-approval module calls exactly this
        # capability the same way, with a real ``ApprovalRequest``-shaped
        # payload; the exact payload shape doesn't matter here because
        # every code path this module wires (an ``ApprovalOverride`` of
        # YES or NO) decides before ever inspecting the request.
        self.last_approval_response: dict[str, Any] | None = None
        # Captured during execute() when the mounted context module exposes
        # ``get_messages_for_request`` (only ``FakeFactoryContextManager``
        # does) -- i.e. what the hosted loop-streaming orchestrator would
        # actually have sent to the provider for this turn. Lets a test pin
        # the system-prompt-survival question: does history replay clobber
        # the hosted session's own system framing? See
        # ``test_system_framing_survives_history_replay``.
        self.messages_sent_to_provider: list[dict[str, Any]] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def execute(self, prompt: str) -> str:
        self.prompt_seen = prompt
        if self._raise_on_execute is not None:
            raise self._raise_on_execute
        # Mirrors what the REAL hosted loop-streaming orchestrator does
        # before every provider call: ask the mounted context module for
        # the request-ready message list. Only meaningful for context
        # doubles that implement it (FakeFactoryContextManager); the plain
        # FakeContextManager doesn't, so this is a no-op for every other
        # existing test.
        get_for_request = getattr(
            self.coordinator.context_module, "get_messages_for_request", None
        )
        if callable(get_for_request):
            self.messages_sent_to_provider = await get_for_request()
        approval_fn = self.coordinator.capabilities.get("approval.request")
        if approval_fn is not None:
            self.last_approval_response = await approval_fn(
                {"kind": "tool_call", "payload": {"toolName": "fake_tool"}}
            )
        return self._reply_text


class FakePreparedBundle:
    """Stands in for the ``PreparedBundle`` ``load_and_prepare_cached`` returns."""

    def __init__(self, session: FakeSession) -> None:
        # Mirrors amplifier-agent's baked-in bundle.md: 9 install-only
        # provider stubs declared before any injection clears them.
        self.mount_plan: dict[str, Any] = {
            "providers": [{"module": "provider-anthropic", "source": "stub"}]
        }
        self._session = session

    async def create_session(
        self, *, session_id: str | None, session_cwd: Any, is_resumed: bool
    ) -> FakeSession:
        return self._session


class FakeEngine:
    """Mirrors amplifier_agent_lib.engine.Engine's public contract exactly."""

    def __init__(
        self,
        *,
        turn_handler: Any,
        protocol_points: dict[str, Any],
        shutdown_exception: Exception | None = None,
    ) -> None:
        self._turn_handler = turn_handler
        self._protocol_points = protocol_points
        self.booted = False
        self.shutdown_called = False
        self.boot_params: dict[str, Any] | None = None
        # Lets a test simulate Engine.shutdown() itself raising (e.g. during
        # the `finally` cleanup after submit_turn already raised), to prove
        # a shutdown failure never masks the original exception.
        self._shutdown_exception = shutdown_exception

    async def boot(
        self, params: dict[str, Any], bundle_override: Any = None
    ) -> dict[str, Any]:
        self.booted = True
        self.boot_params = params
        return {"capabilities": {}, "serverInfo": {}, "sessionState": {}}

    async def submit_turn(self, params: dict[str, Any]) -> dict[str, Any]:
        ctx = SimpleNamespace(
            session_id=params["sessionId"],
            turn_id=params["turnId"],
            prompt=params["prompt"],
            approval=self._protocol_points["approval"],
            display=self._protocol_points["display"],
        )
        reply = await self._turn_handler(ctx)
        return {
            "reply": reply,
            "turnId": params["turnId"],
            "sessionId": params["sessionId"],
            "tokensIn": 0,
            "tokensOut": 0,
            "cacheReadTokens": 0,
            "cacheWriteTokens": 0,
            "costUsd": 0.0,
        }

    async def shutdown(self, _params: Any = None) -> dict[str, Any]:
        self.shutdown_called = True
        if self._shutdown_exception is not None:
            raise self._shutdown_exception
        return {}


class FakeApprovalOverride(enum.Enum):
    """Mirrors amplifier_agent_lib.protocol_points.defaults_cli.ApprovalOverride."""

    YES = "yes"
    NO = "no"


class FakeCliApprovalSystem:
    """Mirrors CliApprovalSystem's ``request()`` contract for the
    override-only branches this module actually drives (gap 3):
    override YES -> accept, override NO (or anything else) -> decline.
    The real class's is_tty/prompt_fn fallback path is never exercised by
    this adapter (it always passes an explicit override), so it is not
    faked here.
    """

    def __init__(self, *, mode: str | None = None, override: Any = None) -> None:
        self.mode = mode
        self._override = override

    async def request(self, req: Any) -> dict[str, str]:
        if self._override is FakeApprovalOverride.YES:
            return {"action": "accept"}
        return {"action": "decline"}


class FakeWireApprovalProvider:
    """Mirrors amplifier_agent_lib.wire_approval_provider.WireApprovalProvider's
    forwarding contract: ``request_approval(request)`` calls the injected
    ``approval_request_fn`` (here, the FakeCliApprovalSystem's ``request``
    bound method -- exactly ``ctx.approval.request`` in the real handler)
    and returns its result. The real class also translates
    ApprovalRequest<->wire-shape around that call; this module's own
    approval_override branches always short-circuit BEFORE the real
    CliApprovalSystem.request() ever inspects the request shape (see that
    class's real source), so the translation step is faithfully omitted
    here rather than faked for its own sake.
    """

    def __init__(self, *, approval_request_fn: Any) -> None:
        self._approval_request_fn = approval_request_fn

    async def request_approval(self, request: Any) -> dict[str, Any]:
        return await self._approval_request_fn(request)


class FakeCliDisplaySystem:
    def __init__(self, stream: Any = None, verbosity: str = "quiet") -> None:
        self.stream = stream
        self.verbosity = verbosity

    def emit(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def make_fake_deps(
    *,
    reply_text: str = "",
    raise_on_execute: Exception | None = None,
    inject_provider_calls: list[tuple[Any, ...]] | None = None,
    shutdown_raises: Exception | None = None,
    resolved_workspace: str = "fake-default-workspace",
    agents_mount_plan: dict[str, Any] | None = None,
    spawn_sub_session_result: dict[str, Any] | None = None,
    context_module: Any = None,
) -> tuple[SimpleNamespace, dict[str, Any]]:
    """Build a fake ``_load_dependencies()``-shaped namespace + capture dict.

    Returns ``(deps, captured)`` where ``captured`` accumulates:
      * ``"engine"``     -> the FakeEngine instance actually constructed
      * ``"prepared"``   -> the FakePreparedBundle instance actually used
      * ``"session"``    -> the FakeSession instance actually created
      * ``"inject_provider_calls"`` -> list of (provider_name, kwargs) tuples
      * ``"inject_routing_matrix_calls"`` -> list of provider_name values
        every ``inject_routing_matrix(prepared, provider)`` call actually
        passed (amplifier-agent docs/INTEGRATION.md's separate, required
        routing-matrix injection alongside ``inject_provider``)
      * ``"prepare_bundle_for_session_calls"`` -> list of
        ``{"host_config": ..., "workspace": ...}`` dicts (gap 1)
      * ``"spawn_sub_session_calls"`` -> list of kwargs dicts every
        ``session.spawn`` invocation actually forwarded (gap 2)

    ``shutdown_raises``, if given, makes the constructed ``FakeEngine``'s
    ``shutdown()`` raise that exception (after still recording
    ``shutdown_called = True``) -- for proving a shutdown failure never
    masks whatever ``submit_turn`` already raised/returned.

    ``resolved_workspace`` is what the fake ``resolve_workspace`` returns
    when no ``argv_workspace`` is given (mirrors the real function's
    cwd-derived fallback tier, without replicating its slugify logic).

    ``agents_mount_plan``, if given, seeds ``prepared.mount_plan["agents"]``
    so gap 2's cold-path ``agent_configs`` hydration has something to hydrate.

    ``spawn_sub_session_result``, if given, is what the fake
    ``spawn_sub_session`` returns (defaults to a plausible delegate result).
    """
    captured: dict[str, Any] = {
        "inject_provider_calls": [],
        "inject_routing_matrix_calls": [],
        "prepare_bundle_for_session_calls": [],
        "spawn_sub_session_calls": [],
    }

    session = FakeSession(
        reply_text=reply_text,
        raise_on_execute=raise_on_execute,
        context_module=context_module,
    )
    prepared = FakePreparedBundle(session)
    if agents_mount_plan is not None:
        prepared.mount_plan["agents"] = agents_mount_plan
    captured["session"] = session
    captured["prepared"] = prepared

    async def fake_load_and_prepare_cached(*, aaa_version: str) -> FakePreparedBundle:
        return prepared

    def fake_inject_provider(
        prepared_arg: Any, provider_name: str, **kwargs: Any
    ) -> None:
        captured["inject_provider_calls"].append((provider_name, kwargs))

    def fake_inject_routing_matrix(prepared_arg: Any, provider_name: str) -> None:
        captured["inject_routing_matrix_calls"].append(provider_name)

    def fake_prepare_bundle_for_session(
        prepared_arg: Any, *, host_config: Any, workspace: str
    ) -> None:
        captured["prepare_bundle_for_session_calls"].append(
            {"host_config": host_config, "workspace": workspace}
        )
        prepared_arg.mount_plan["_prepared_bundle_applied"] = {
            "host_config": host_config,
            "workspace": workspace,
        }

    def fake_resolve_workspace(
        *, argv_workspace: str | None, env: Any, cwd: Any
    ) -> str:
        if argv_workspace:
            return argv_workspace
        return resolved_workspace

    def fake_hydrate_agent_overlay(path: Any) -> dict[str, Any]:
        return {"instruction": f"fake-overlay:{path}"}

    async def fake_spawn_sub_session(**kwargs: Any) -> dict[str, Any]:
        captured["spawn_sub_session_calls"].append(kwargs)
        if spawn_sub_session_result is not None:
            return spawn_sub_session_result
        return {
            "output": "child delegate done",
            "session_id": "fake-child-session-1",
            "status": "success",
            "turn_count": 1,
            "metadata": {},
        }

    engines_built: list[FakeEngine] = []

    def engine_factory(
        *, turn_handler: Any, protocol_points: dict[str, Any]
    ) -> FakeEngine:
        engine = FakeEngine(
            turn_handler=turn_handler,
            protocol_points=protocol_points,
            shutdown_exception=shutdown_raises,
        )
        engines_built.append(engine)
        captured["engine"] = engine
        return engine

    deps = SimpleNamespace(
        aaa_version="0.0.0-fake",
        load_and_prepare_cached=fake_load_and_prepare_cached,
        Engine=engine_factory,
        PROTOCOL_VERSION="fake-protocol-1",
        server_default_capabilities=dict,
        ApprovalOverride=FakeApprovalOverride,
        CliApprovalSystem=FakeCliApprovalSystem,
        CliDisplaySystem=FakeCliDisplaySystem,
        inject_provider=fake_inject_provider,
        inject_routing_matrix=fake_inject_routing_matrix,
        prepare_bundle_for_session=fake_prepare_bundle_for_session,
        resolve_workspace=fake_resolve_workspace,
        hydrate_agent_overlay=fake_hydrate_agent_overlay,
        spawn_sub_session=fake_spawn_sub_session,
        WireApprovalProvider=FakeWireApprovalProvider,
    )
    return deps, captured


class CapturingHooks:
    """Stands in for the parent session's hook registry.

    Mirrors ``pipeline-runner``'s own ``_CapturingHooks`` test double: records
    every emitted event and keeps the last ``orchestrator:complete`` payload.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.completion: dict[str, Any] = {}

    async def emit(self, event: str, data: dict[str, Any]) -> Any:
        self.events.append((event, data))
        from amplifier_core.events import ORCHESTRATOR_COMPLETE

        if event == ORCHESTRATOR_COMPLETE:
            self.completion = data
        return None
