"""mount() protocol compliance (the Iron Law: mount() must register something).

Mirrors loop-agent's own mount() contract: an orchestrator-type module calls
``coordinator.mount("orchestrator", instance)`` (no ``name=`` kwarg -- that is
only for namespaced kinds like "tools").
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_loop_amplifier_agent import AmplifierAgentOrchestrator, mount


@pytest.mark.asyncio
async def test_mount_registers_orchestrator():
    coordinator = MagicMock()
    coordinator.mount = AsyncMock()

    await mount(coordinator, {"llm_provider": "anthropic"})

    coordinator.mount.assert_called_once()
    call_args = coordinator.mount.call_args
    assert call_args[0][0] == "orchestrator"
    assert isinstance(call_args[0][1], AmplifierAgentOrchestrator)


@pytest.mark.asyncio
async def test_mount_defaults_config_to_empty_dict():
    coordinator = MagicMock()
    coordinator.mount = AsyncMock()

    await mount(coordinator, None)

    orchestrator = coordinator.mount.call_args[0][1]
    assert orchestrator._config == {}


@pytest.mark.asyncio
async def test_mounted_orchestrator_exposes_execute():
    """The Orchestrator protocol (amplifier_core.interfaces.Orchestrator) requires execute()."""
    coordinator = MagicMock()
    coordinator.mount = AsyncMock()

    await mount(coordinator)

    orchestrator = coordinator.mount.call_args[0][1]
    assert callable(orchestrator.execute)
