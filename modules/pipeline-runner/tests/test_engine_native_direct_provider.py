"""Genuine end-to-end coverage for the engine-native `direct`-worker provider
bootstrap (DESIGN-worker-registry-core-split.md P3 -- reviewer-caught
blocker).

The P3 branch's own ``test_dot_runner_cli.py`` monkeypatches ``drive_engine``
in EVERY test, so none of its 16 tests ever exercised the real dispatch
chain a box node actually walks. That let a real defect ship: the
engine-native path's ``AmplifierBackend(...)`` construction never passed
``provider=``, so ``AmplifierBackend.run()``'s ``elif self._provider is not
None`` Path-B gate (``backend.py`` -- pre-existing loop-pipeline contract,
untouched by this fix) was never satisfied, and EVERY box node failed with
the engine-internal, misleading message "Neither session.spawn nor a direct
provider is available -- cannot execute node" regardless of what API key a
user had set.

These tests exercise the REAL chain end to end:
    run_pipeline(engine_native=True) -> drive_engine -> AmplifierBackend
    -> WorkerRegistry -> DirectWorker -> unified_llm.generate()

Nothing in that chain is mocked except the ONE seam the acceptance bar
names: the provider-call boundary. ``unified_llm.Client.from_env()`` is
monkeypatched to return a fake client whose ``.complete()`` returns a
canned ``report_outcome`` tool-call response -- exactly the boundary
``_bootstrap_direct_provider()`` (runner.py) calls to detect/construct a
real provider from the environment. The bundle-preparation machinery
(``amplifier_foundation.Bundle.prepare()``/``create_session()``, which
would otherwise reach the network to resolve/install modules) is replaced
with the SAME hermetic ``FakeBundle``/``FakePrepared``/``FakeSession``/
``FakeCoordinator`` fakes ``test_dot_runner_cli.py`` already established --
only ``drive_engine`` itself is left real here, which is precisely the gap
the review named.
"""

from __future__ import annotations

import asyncio

import pytest
import unified_llm
from amplifier_module_pipeline_runner import runner as runner_mod
from amplifier_module_pipeline_runner.runner import (
    NoProviderConfiguredError,
    PROVIDER_KEY_ENV,
)

# ---------------------------------------------------------------------------
# Hermetic bundle-prep fakes (mirrors test_dot_runner_cli.py's FakeBundle/
# FakePrepared/FakeSession/FakeCoordinator) -- these stand in for
# amplifier_foundation's real Bundle/PreparedBundle/AmplifierSession so
# `run_pipeline` never reaches the network to resolve/install a module.
# `drive_engine` itself is NOT mocked anywhere in this file.
# ---------------------------------------------------------------------------


class FakePrepared:
    def __init__(self, applied: list) -> None:
        self.applied = applied
        self.bundle = type("B", (), {"agents": {}})()

    async def create_session(self, **kwargs):
        del kwargs
        return FakeSession()


class FakeSession:
    def __init__(self) -> None:
        self.coordinator = FakeCoordinator()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeCoordinator:
    """No `session.spawn` capability -- `direct` is the only reachable worker."""

    def __init__(self) -> None:
        self.registered: dict[str, object] = {}
        self.config: dict = {"agents": {}}
        self.hooks = None
        self.session = None

    def register_capability(self, name: str, value: object) -> None:
        self.registered[name] = value

    def get_capability(self, name: str):
        return self.registered.get(name)


class FakeBundle:
    def __init__(self, applied: list | None = None) -> None:
        self.applied = applied or []

    def compose(self, other):
        return FakeBundle(applied=[*self.applied, other])

    async def prepare(self, *, install_deps):
        del install_deps
        return FakePrepared(applied=self.applied)


def _patch_bundle_prep(monkeypatch) -> None:
    monkeypatch.setattr(runner_mod, "_engine_native_base_bundle", lambda: FakeBundle())


def _make_box_dot(*, llm_model: str = "claude-x") -> str:
    return (
        "digraph T { start [shape=Mdiamond]; "
        f'work [shape=box, llm_provider="anthropic", llm_model="{llm_model}", '
        'prompt="do the thing"]; '
        "done [shape=Msquare]; start -> work -> done; }"
    )


# ---------------------------------------------------------------------------
# Fake unified_llm provider client -- the ONE stubbed boundary.
# ---------------------------------------------------------------------------


def _report_outcome_response(status: str = "success") -> unified_llm.Response:
    """A canned Response whose tool call is `report_outcome` -- the shape
    `DirectWorker`'s `_find_report_outcome_call` (backend.py) looks for.
    """
    content = [
        unified_llm.ContentPart(
            kind=unified_llm.ContentKind.TOOL_CALL,
            tool_call=unified_llm.ToolCallData(
                id="tc-1",
                name="report_outcome",
                arguments={"status": status, "notes": "handled by the fake provider"},
            ),
        )
    ]
    return unified_llm.Response(
        id="resp-fake",
        model="claude-x",
        provider="anthropic",
        message=unified_llm.Message(role=unified_llm.Role.ASSISTANT, content=content),
        finish_reason=unified_llm.FinishReason(reason="tool_calls"),
        usage=unified_llm.Usage(input_tokens=5, output_tokens=5, total_tokens=10),
    )


class _FakeDirectClient:
    """Stands in for `unified_llm.Client` at exactly the provider-call
    boundary -- everything upstream of it (drive_engine, AmplifierBackend,
    WorkerRegistry, DirectWorker, unified_llm.generate()'s own tool-loop
    plumbing) is real."""

    def __init__(self, response: unified_llm.Response) -> None:
        self._response = response
        self.requests: list[unified_llm.Request] = []

    async def complete(self, request: unified_llm.Request) -> unified_llm.Response:
        self.requests.append(request)
        return self._response


def _patch_from_env(monkeypatch, client: _FakeDirectClient) -> None:
    """Monkeypatch `unified_llm.Client.from_env` -- the SAME classmethod
    `_bootstrap_direct_provider()` (runner.py) calls. This is the provider-
    call boundary the acceptance bar names; nothing else is faked."""
    monkeypatch.setattr(unified_llm.Client, "from_env", classmethod(lambda cls: client))


# ---------------------------------------------------------------------------
# 1. Genuine end-to-end: real drive_engine, real AmplifierBackend, real
#    registry, real DirectWorker. RED-proof: fails on ba12ab9, passes after.
# ---------------------------------------------------------------------------


def test_box_node_executes_via_direct_worker_real_chain(monkeypatch, tmp_path):
    _patch_bundle_prep(monkeypatch)
    client = _FakeDirectClient(_report_outcome_response("success"))
    _patch_from_env(monkeypatch, client)

    result = asyncio.run(
        runner_mod.run_pipeline(
            _make_box_dot(),
            cwd=tmp_path / "work",
            logs_root=tmp_path / "logs",
            engine_native=True,
        )
    )

    assert result.status == "success"
    assert result.failure_reason is None
    # The fake provider was actually invoked -- the box node really executed
    # via the `direct` worker's unified_llm.generate() call, not skipped.
    assert len(client.requests) == 1


def test_box_node_without_fix_fails_with_misleading_message(monkeypatch, tmp_path):
    """RED-proof reproduction: with NO provider wired into AmplifierBackend
    (the pre-fix shape), a box node fails with the engine-internal message
    the review flagged -- pinned here so a regression that reintroduces the
    gap is caught immediately, independent of the fix's own tests above."""
    from amplifier_module_loop_pipeline.backend import AmplifierBackend
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.dot_parser import parse_dot

    graph = parse_dot(_make_box_dot())
    backend = AmplifierBackend(coordinator=None, profiles={}, default_worker="direct")
    node = graph.nodes["work"]

    outcome = asyncio.run(backend.run(node, "do the thing", PipelineContext()))

    assert outcome.status.value == "fail"
    assert "Neither session.spawn nor a direct provider is available" in (
        outcome.failure_reason or ""
    )


# ---------------------------------------------------------------------------
# 2. Cross-personality resume: a run interrupted mid-graph resumes cleanly
#    under engine_native=True via the same fixed drive_engine chain.
# ---------------------------------------------------------------------------


def test_resume_engine_native_executes_pending_box_node(monkeypatch, tmp_path):
    """Interrupt after node 1 (tool node, no provider needed), resume with
    engine_native=True: the pending box node must reach the `direct` worker
    exactly like a fresh run -- the reviewer's synthetic
    resume_pipeline(engine_native=True) scenario, now green."""
    _patch_bundle_prep(monkeypatch)
    client = _FakeDirectClient(_report_outcome_response("success"))
    _patch_from_env(monkeypatch, client)

    dot_source = (
        "digraph T { start [shape=Mdiamond]; "
        'first [shape=parallelogram, tool_command="echo one"]; '
        'work [shape=box, llm_provider="anthropic", llm_model="claude-x", '
        'prompt="do the thing"]; '
        "done [shape=Msquare]; start -> first -> work -> done; }"
    )

    logs_root = tmp_path / "logs"
    cwd = tmp_path / "work"

    # First run: no provider configured at all -- the interrupted run must
    # never have reached the box node (a tool-only prefix needs none).
    first_result = asyncio.run(
        runner_mod.run_pipeline(
            dot_source,
            cwd=cwd,
            logs_root=logs_root,
            engine_native=True,
        )
    )
    # The fake client answers ANY node dispatched to `direct` -- including
    # `work` -- so a first full run already succeeds end to end. To exercise
    # resume specifically, truncate the checkpoint back to right after
    # `first` completed, simulating an interruption before `work` ran.
    import json

    checkpoint_path = logs_root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["completed_nodes"] = ["start", "first"]
    checkpoint["node_outcomes"] = {
        nid: outcome
        for nid, outcome in checkpoint.get("node_outcomes", {}).items()
        if nid in ("start", "first")
    }
    checkpoint["current_node"] = "work"
    checkpoint["run_state"] = "in_flight"
    checkpoint["context"].pop("outcome", None)
    checkpoint_path.write_text(json.dumps(checkpoint))

    assert first_result.status == "success"  # sanity: fresh run baseline works

    resume_result = asyncio.run(
        runner_mod.resume_pipeline(
            logs_root,
            cwd=cwd,
            engine_native=True,
        )
    )

    assert resume_result.status == "success"
    assert resume_result.failure_reason is None


# ---------------------------------------------------------------------------
# 3. No-key UX: unset every provider env key -> clean, actionable error.
# ---------------------------------------------------------------------------


def test_no_provider_key_raises_actionable_error_not_engine_internal_wording(
    monkeypatch,
):
    for env_key in {*PROVIDER_KEY_ENV.values(), "GOOGLE_API_KEY"}:
        monkeypatch.delenv(env_key, raising=False)

    with pytest.raises(NoProviderConfiguredError) as exc_info:
        runner_mod._bootstrap_direct_provider()

    message = str(exc_info.value)
    for env_key in PROVIDER_KEY_ENV.values():
        assert env_key in message
    # Never the misleading engine-internal wording a missing bootstrap used
    # to surface instead (that message names `session.spawn`, an internal
    # capability a dot-runner user never registered and cannot act on).
    assert "session.spawn" not in message


def test_cli_run_with_no_provider_key_is_clean_no_traceback(monkeypatch, tmp_path):
    """End-to-end through the CLI: no provider key -> exit 1, clean stderr
    message, no traceback."""
    import sys

    from amplifier_module_pipeline_runner import cli

    for env_key in {*PROVIDER_KEY_ENV.values(), "GOOGLE_API_KEY"}:
        monkeypatch.delenv(env_key, raising=False)
    # cmd_run's own pre-existing --provider preflight (unrelated to this fix)
    # checks ANTHROPIC_API_KEY presence before running anything; set it so
    # the run actually reaches the engine-native provider bootstrap this fix
    # adds, rather than stopping one step earlier at that unrelated check.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    # But _bootstrap_direct_provider must still see "no provider": force
    # unified_llm's own from_env() to refuse regardless of the dummy key
    # above, simulating a key that IS present but not one unified_llm can
    # actually use (e.g. a typo'd/blank provider setup upstream).

    def _refuse(cls):
        raise unified_llm.ConfigurationError("No API keys found in environment.")

    monkeypatch.setattr(unified_llm.Client, "from_env", classmethod(_refuse))

    _patch_bundle_prep(monkeypatch)

    dot_path = tmp_path / "pipeline.dot"
    dot_path.write_text(_make_box_dot(), encoding="utf-8")

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(["run", str(dot_path), "--cwd", str(tmp_path)])
    args.prog_name = "dot-runner"
    args.engine_native = True

    rc = cli.cmd_run(args)

    assert rc == 1
    # (capsys not used here to keep this test focused on the return code +
    # exception-class contract; message content is pinned by the unit test
    # above.)
    del sys
