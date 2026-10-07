import asyncio
import json

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


class RecordingHooks:
    def __init__(self):
        self.events = []

    async def emit(self, name, data):
        self.events.append((name, data))


class CleanupBackend:
    def __init__(self, release):
        self.release = release
        self.calls = []
        self.cancel_count = 0
        self.cleanup_finished = False
        self.worker_task = None
        self.verify_saw_cleanup = None

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls.append(node.id)
        if node.id == "Verify":
            self.verify_saw_cleanup = self.cleanup_finished
            return "verified"
        self.worker_task = asyncio.current_task()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancel_count += 1
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_count += 1
            self.cleanup_finished = True
            raise


def make_engine(tmp_path, backend, allow_partial):
    graph = parse_dot(
        f"""digraph {{
            Start [shape=Mdiamond];
            Implement [prompt="implement", timeout="20ms", allow_partial="{str(allow_partial).lower()}"];
            Verify [prompt="verify"];
            Exit [shape=Msquare];
            Start -> Implement -> Verify -> Exit;
            Implement -> Verify [condition="outcome=fail"];
        }}"""
    )
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=str(tmp_path),
        hooks=RecordingHooks(),
    )
    engine._FUSE_CANCEL_GRACE_S = 0.03
    return engine


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_partial", [False, True])
async def test_unconfirmed_cleanup_blocks_verification_and_returns_bounded_failure(
    tmp_path, allow_partial
):
    release = asyncio.Event()
    backend = CleanupBackend(release)
    engine = make_engine(tmp_path, backend, allow_partial)
    run_task = asyncio.create_task(engine.run())
    try:
        completed, _ = await asyncio.wait({run_task}, timeout=0.3)
        assert completed, "timeout cleanup must not hold the engine indefinitely"
        outcome = run_task.result()
        assert outcome.status is StageStatus.FAIL
        assert outcome.failure_reason == "timeout"
        assert "cleanup is unconfirmed" in outcome.notes
        assert backend.calls == ["Implement"]
        assert backend.cancel_count == 1
        assert not backend.cleanup_finished
        completions = [
            data for name, data in engine.hooks.events if name == "pipeline:complete"
        ]
        assert len(completions) == 1
        assert completions[0]["status"] == "fail"
        node_completions = [
            data
            for name, data in engine.hooks.events
            if name == "pipeline:node_complete"
        ]
        interrupted = next(
            data for data in node_completions if data["node_id"] == "Implement"
        )
        assert interrupted["failure_reason"] == "timeout"
        status = json.loads((tmp_path / "Implement/status.json").read_text())
        assert status["failure_reason"] == "timeout"
        assert "Implement" not in engine.completed_nodes
    finally:
        release.set()
        await asyncio.gather(run_task, return_exceptions=True)
        if backend.worker_task is not None:
            await asyncio.gather(backend.worker_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_confirmed_cleanup_preserves_existing_partial_timeout_routing(tmp_path):
    release = asyncio.Event()
    backend = CleanupBackend(release)
    engine = make_engine(tmp_path, backend, True)

    async def release_cleanup():
        await asyncio.sleep(0.035)
        release.set()

    releaser = asyncio.create_task(release_cleanup())
    outcome = await engine.run()
    await releaser

    assert outcome.is_success
    assert backend.calls == ["Implement", "Verify"]
    assert backend.verify_saw_cleanup is True
    assert backend.cancel_count == 1


@pytest.mark.asyncio
async def test_pipeline_fuse_preserves_existing_reason_when_cleanup_is_pending(
    tmp_path,
):
    release = asyncio.Event()
    backend = CleanupBackend(release)
    engine = make_engine(tmp_path, backend, True)
    engine.graph.max_pipeline_duration = 100
    engine.graph.nodes["Implement"].timeout = 1000
    run_task = asyncio.create_task(engine.run())
    try:
        completed, _ = await asyncio.wait({run_task}, timeout=0.5)
        assert completed
        outcome = run_task.result()
        assert outcome.status is StageStatus.FAIL
        assert outcome.failure_reason == "max_pipeline_duration_exceeded"
        assert "cleanup is unconfirmed" in outcome.notes
        assert backend.calls == ["Implement"]
        assert backend.cancel_count == 1
        status = json.loads((tmp_path / "Implement/status.json").read_text())
        assert status["failure_reason"] == "max_pipeline_duration_exceeded"
        completions = [
            data for name, data in engine.hooks.events if name == "pipeline:complete"
        ]
        assert len(completions) == 1
        assert completions[0]["status"] == "fail"
    finally:
        release.set()
        await asyncio.gather(run_task, return_exceptions=True)
        if backend.worker_task is not None:
            await asyncio.gather(backend.worker_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["timeout", "node_cancellation_unconfirmed"])
async def test_returned_failure_reason_does_not_control_cleanup_routing(
    tmp_path, reason
):
    release = asyncio.Event()
    backend = CleanupBackend(release)
    engine = make_engine(tmp_path, backend, False)
    registry = engine.handler_registry

    class ReturnedFailure:
        async def execute(self, node, context, graph, logs_root, *, engine=None):
            return Outcome(status=StageStatus.FAIL, failure_reason=reason)

    class ReturnedFailureRegistry:
        def get(self, node):
            if node.id == "Implement":
                return ReturnedFailure()
            return registry.get(node)

    engine.handler_registry = ReturnedFailureRegistry()
    outcome = await engine.run()

    assert outcome.is_success
    assert backend.calls == ["Verify"]


@pytest.mark.asyncio
async def test_bounded_wait_returns_typed_internal_cleanup_state(tmp_path):
    release = asyncio.Event()
    backend = CleanupBackend(release)
    engine = make_engine(tmp_path, backend, True)

    async def returned_failure():
        return Outcome(status=StageStatus.FAIL, failure_reason="timeout")

    result = await engine._await_node_bounded(returned_failure(), timeout_s=0.1)
    assert result.outcome.failure_reason == "timeout"
    assert not result.timed_out
    assert not result.cleanup_pending
