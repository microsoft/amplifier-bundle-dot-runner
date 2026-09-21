"""Exercise the Core loading boundary before calling the progress hook."""

import logging
from pathlib import Path
from types import SimpleNamespace

import amplifier_module_hooks_pipeline_progress as module
import pytest
from amplifier_core.coordinator import ModuleCoordinator
from amplifier_core.loader import TYPE_TO_MOUNT_POINT, ModuleLoader


def test_declared_type_maps_to_core_hook_mount_point():
    assert TYPE_TO_MOUNT_POINT.get(module.__amplifier_module_type__) == "hooks"


@pytest.mark.asyncio
async def test_core_loads_mounts_and_delivers_pipeline_events(caplog):
    root = Path(module.__file__).resolve().parent.parent
    coordinator = ModuleCoordinator(
        SimpleNamespace(session_id="progress-loader-test", parent_id=None, config={})
    )
    # Local source resolution only; Core's metadata, validation, import and
    # mount path are real and must not be replaced with a direct mount() call.
    source = SimpleNamespace(resolve=lambda: root)
    resolver = SimpleNamespace(resolve=lambda *args, **kwargs: source)
    await coordinator.mount("module-source-resolver", resolver)
    loader = ModuleLoader(coordinator)

    mount = await loader.load("hooks-pipeline-progress", coordinator=coordinator)
    await mount(coordinator)
    with caplog.at_level(logging.INFO, logger=module.__name__):
        await coordinator.hooks.emit(
            "pipeline:start", {"goal": "loader regression", "node_count": 2}
        )

    assert "loader regression" in caplog.text
    assert coordinator.hooks.list_handlers("pipeline:complete")
