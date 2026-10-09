import json
import os
from pathlib import Path
import time

import pytest

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.handlers.codergen import CodergenHandler
from amplifier_module_loop_pipeline.graph import Graph
from amplifier_module_loop_pipeline.status_contract import current_node_status_path
from .test_attribute_passthrough import MockCoordinator, _make_context, _make_node
from .test_engine import _make_engine


class ConvergingBackend:
    def __init__(self):
        self.calls = 0

    async def run(self, *args, **kwargs):
        self.calls += 1
        return Outcome(status=StageStatus.SUCCESS, preferred_label='done' if self.calls >= 160 else 'again')


@pytest.mark.parametrize('value', ['-1', 'invalid'])
def test_invalid_step_bound_rejected(value, tmp_path):
    with pytest.raises(ValueError):
        _make_engine(dot_source=f'''digraph {{
            graph [max_steps="{value}"]
            start [shape=Mdiamond]
            exit [shape=Msquare]
            start -> exit
        }}''', logs_root=str(tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize('subgraph', [False, True])
async def test_explicit_zero_step_limit_converges_beyond_legacy_bound(tmp_path, subgraph):
    backend = ConvergingBackend()
    engine = _make_engine(
        dot_source='''digraph {
            graph [max_steps=0]
            start [shape=Mdiamond]
            work [prompt="Continue until complete"]
            exit [shape=Msquare]
            start -> work
            work -> work [label="again"]
            work -> exit [label="done"]
        }''',
        backend=backend,
        logs_root=str(tmp_path),
    )
    result = await engine.run_subgraph('work') if subgraph else await engine.run()
    assert result.status == StageStatus.SUCCESS
    assert backend.calls == 160


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['incomplete', 'cancelled'])
@pytest.mark.parametrize('text', ['Let me now run the main script', '{"status":"success"}', ''])
async def test_interrupted_spawn_never_promotes_partial_text(status, text):
    coordinator = MockCoordinator()

    async def spawn(**kwargs):
        return {'output': text, 'session_id': 'child-1', 'status': status}

    coordinator._spawn_fn = spawn
    backend = AmplifierBackend(coordinator=coordinator, profiles={'anthropic': 'attractor-anthropic'})
    result = await backend.run(_make_node(attrs={'llm_provider': 'anthropic'}), 'perform work', _make_context())
    assert result.status == StageStatus.FAIL
    assert result.is_explicit is False
    assert result.failure_reason == f'Child session {status}'
    assert result.execution_complete is False


@pytest.mark.asyncio
async def test_unbounded_failure_routing_continues_past_fifty_attempts(tmp_path):
    class RecoveringBackend:
        def __init__(self):
            self.calls = 0

        async def run(self, *args, **kwargs):
            self.calls += 1
            return Outcome(status=StageStatus.SUCCESS if self.calls >= 60 else StageStatus.FAIL)

    backend = RecoveringBackend()
    engine = _make_engine(dot_source='''digraph {
        graph [max_steps=0]
        start [shape=Mdiamond]
        work [prompt="Converge", retry_target="work"]
        exit [shape=Msquare]
        start -> work
        work -> exit [condition="outcome=success"]
    }''', backend=backend, logs_root=str(tmp_path))
    result = await engine.run()
    assert result.status == StageStatus.SUCCESS
    assert backend.calls == 60


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['incomplete', 'cancelled', 'completed'])
async def test_child_lifecycle_precedes_fresh_success_file(tmp_path, status):
    coordinator = MockCoordinator()

    async def spawn(**kwargs):
        status_path = Path(current_node_status_path.get())
        status_path.write_text(json.dumps({'outcome': 'success'}))
        timestamp = time.time() + 1
        os.utime(status_path, (timestamp, timestamp))
        return {'output': 'Intermediate text', 'session_id': 'child-1', 'status': status}

    coordinator._spawn_fn = spawn
    backend = AmplifierBackend(coordinator=coordinator, profiles={'anthropic': 'attractor-anthropic'})
    node = _make_node(attrs={'llm_provider': 'anthropic'})
    result = await CodergenHandler(backend).execute(node, _make_context(), Graph('test', {node.id: node}, []), str(tmp_path))
    expected = StageStatus.SUCCESS if status == 'completed' else StageStatus.FAIL
    assert result.status == expected
    assert json.loads((tmp_path / node.id / 'status.json').read_text())['status'] == expected.value
