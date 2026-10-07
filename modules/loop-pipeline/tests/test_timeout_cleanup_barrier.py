import asyncio

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import StageStatus


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
        assert outcome.failure_reason == "node_cancellation_unconfirmed"
        assert backend.calls == ["Implement"]
        assert backend.cancel_count == 1
        assert not backend.cleanup_finished
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
