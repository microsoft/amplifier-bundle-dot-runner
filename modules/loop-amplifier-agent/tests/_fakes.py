"""Faithful fakes for the amplifier-agent seam, shared by the hermetic tests.

Each fake mirrors the REAL object's contract closely enough to exercise the
actual seam this module depends on (not just a name-shaped stub):

  * ``FakeEngine.submit_turn`` builds a context object with the same fields
    ``amplifier_agent_lib.engine.Engine.submit_turn`` builds
    (``session_id``/``turn_id``/``prompt``/``approval``/``display``), calls
    the injected ``turn_handler`` with it, and returns a ``reply`` key --
    exactly the seam ``AmplifierAgentOrchestrator._run_turn`` depends on.
  * ``FakeReportOutcomeTool`` exposes the one attribute
    (``last_outcome``) the orchestrator reads after the turn.
  * ``FakePreparedBundle.create_session`` returns a ``FakeSession`` whose
    ``coordinator.mount("tools", tool, name=...)`` records the mounted tool
    exactly like the real per-turn coordinator does, and whose
    ``execute(prompt)`` can simulate the child agent calling
    ``report_outcome`` (by setting ``tool.last_outcome`` before returning),
    or NOT calling it (leaving ``last_outcome`` at ``None``) -- the fail-
    closed case under test.

See ``/var/tmp/aa-probe/probe_q1_q2.py`` for the real end-to-end mechanism
these fakes stand in for.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Self


class FakeSessionCoordinator:
    def __init__(self) -> None:
        self.capabilities: dict[str, Any] = {}
        self.mounted_tools: dict[str, Any] = {}

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
        outcome_to_set: dict[str, Any] | None = None,
        raise_on_execute: Exception | None = None,
    ) -> None:
        self.coordinator = FakeSessionCoordinator()
        self._reply_text = reply_text
        self._outcome_to_set = outcome_to_set
        self._raise_on_execute = raise_on_execute
        self.prompt_seen: str | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def execute(self, prompt: str) -> str:
        self.prompt_seen = prompt
        if self._raise_on_execute is not None:
            raise self._raise_on_execute
        tool = self.coordinator.mounted_tools.get("report_outcome")
        if tool is not None and self._outcome_to_set is not None:
            tool.last_outcome = self._outcome_to_set
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


class FakeReportOutcomeTool:
    """Mirrors amplifier_module_tool_report_outcome.ReportOutcomeTool's shape."""

    name = "report_outcome"

    def __init__(self, config: dict[str, Any], coordinator: Any = None) -> None:
        self.config = config
        self.coordinator = coordinator
        self.last_outcome: dict[str, Any] | None = None


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


class FakeCliApprovalSystem:
    def __init__(self, mode: str = "yes") -> None:
        self.mode = mode


class FakeCliDisplaySystem:
    def __init__(self, stream: Any = None, verbosity: str = "quiet") -> None:
        self.stream = stream
        self.verbosity = verbosity

    def emit(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def make_fake_deps(
    *,
    reply_text: str = "",
    outcome_to_set: dict[str, Any] | None = None,
    raise_on_execute: Exception | None = None,
    inject_provider_calls: list[tuple[Any, ...]] | None = None,
    shutdown_raises: Exception | None = None,
) -> tuple[SimpleNamespace, dict[str, Any]]:
    """Build a fake ``_load_dependencies()``-shaped namespace + capture dict.

    Returns ``(deps, captured)`` where ``captured`` accumulates:
      * ``"engine"``     -> the FakeEngine instance actually constructed
      * ``"prepared"``   -> the FakePreparedBundle instance actually used
      * ``"session"``    -> the FakeSession instance actually created
      * ``"tool"``       -> the FakeReportOutcomeTool instance actually mounted
      * ``"inject_provider_calls"`` -> list of (provider_name, kwargs) tuples

    ``shutdown_raises``, if given, makes the constructed ``FakeEngine``'s
    ``shutdown()`` raise that exception (after still recording
    ``shutdown_called = True``) -- for proving a shutdown failure never
    masks whatever ``submit_turn`` already raised/returned.
    """
    captured: dict[str, Any] = {"inject_provider_calls": []}

    session = FakeSession(
        reply_text=reply_text,
        outcome_to_set=outcome_to_set,
        raise_on_execute=raise_on_execute,
    )
    prepared = FakePreparedBundle(session)
    captured["session"] = session
    captured["prepared"] = prepared

    async def fake_load_and_prepare_cached(*, aaa_version: str) -> FakePreparedBundle:
        return prepared

    def fake_inject_provider(
        prepared_arg: Any, provider_name: str, **kwargs: Any
    ) -> None:
        captured["inject_provider_calls"].append((provider_name, kwargs))

    real_report_outcome_tool_cls = FakeReportOutcomeTool

    def tool_factory(
        config: dict[str, Any], coordinator: Any = None
    ) -> FakeReportOutcomeTool:
        tool = real_report_outcome_tool_cls(config, coordinator)
        captured["tool"] = tool
        return tool

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
        CliApprovalSystem=FakeCliApprovalSystem,
        CliDisplaySystem=FakeCliDisplaySystem,
        inject_provider=fake_inject_provider,
        ReportOutcomeTool=tool_factory,
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
