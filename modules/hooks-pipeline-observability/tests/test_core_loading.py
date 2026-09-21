"""Exercise the Core loading boundary before mounting pipeline observers."""

from pathlib import Path
from types import SimpleNamespace

import amplifier_module_hooks_pipeline_observability as module
import pytest
from amplifier_core.coordinator import ModuleCoordinator
from amplifier_core.loader import TYPE_TO_MOUNT_POINT, ModuleLoader


def test_declared_type_maps_to_core_hook_mount_point():
    assert TYPE_TO_MOUNT_POINT.get(module.__amplifier_module_type__) == "hooks"


@pytest.mark.asyncio
async def test_core_loads_mounts_and_updates_pipeline_state():
    root = Path(module.__file__).resolve().parent.parent
    coordinator = ModuleCoordinator(
        SimpleNamespace(
            session_id="observability-loader-test", parent_id=None, config={}
        )
    )
    # Resolve this checkout without bypassing Core validation or its loader.
    source = SimpleNamespace(resolve=lambda: root)
    resolver = SimpleNamespace(resolve=lambda *args, **kwargs: source)
    await coordinator.mount("module-source-resolver", resolver)
    loader = ModuleLoader(coordinator)

    mount = await loader.load("hooks-pipeline-observability", coordinator=coordinator)
    await mount(coordinator)
    await coordinator.hooks.emit(
        "pipeline:start", {"graph_name": "loader-regression", "node_count": 2}
    )
    states = await coordinator.collect_contributions("pipeline.state")
    assert len(states) == 1
    assert states[0].pipeline_id == "loader-regression"
    assert states[0].status == "running"
    assert "pipeline:start" in coordinator.get_capability("observability.events")

    await coordinator.hooks.emit(
        "pipeline:complete", {"status": "success", "total_nodes_executed": 2}
    )
    states = await coordinator.collect_contributions("pipeline.state")
    assert states[0].status == "complete"
    assert states[0].nodes_completed == 2
