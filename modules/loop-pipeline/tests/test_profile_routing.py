"""Tests for profile routing in _build_backend().

Verifies that _build_backend() populates the profiles dict from:
1. Explicit orchestrator config: config["profiles"]
2. Auto-discovery from coordinator.config["agents"]
3. Explicit profiles take priority over auto-discovery
"""

from unittest.mock import MagicMock

from amplifier_module_loop_pipeline import _build_backend
from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.graph import Node


def _make_coordinator(has_spawn=True, agents=None):
    """Create a mock coordinator."""
    coordinator = MagicMock()
    if has_spawn:
        coordinator.get_capability = MagicMock(
            return_value=MagicMock()  # mock spawn_fn
        )
    else:
        coordinator.get_capability = MagicMock(return_value=None)

    config = {}
    if agents is not None:
        config["agents"] = agents
    coordinator.config = config
    return coordinator


def test_profiles_from_explicit_config():
    """Profiles in orchestrator config should be used directly."""
    coordinator = _make_coordinator(has_spawn=True)
    providers = {"anthropic": MagicMock()}

    orchestrator_config = {
        "profiles": {
            "anthropic": "attractor-anthropic",
            "openai": "attractor-openai",
        }
    }

    backend = _build_backend(providers, {}, None, coordinator, orchestrator_config)
    assert backend is not None
    assert backend._profiles == {
        "anthropic": "attractor-anthropic",
        "openai": "attractor-openai",
    }


def test_profiles_auto_discovered_from_agents():
    """When no explicit profiles, auto-discover from coordinator agents."""
    coordinator = _make_coordinator(
        has_spawn=True,
        agents={
            "attractor-anthropic": {
                "session": {"orchestrator": {"module": "loop-agent"}}
            },
            "attractor-openai": {"session": {"orchestrator": {"module": "loop-agent"}}},
        },
    )
    providers = {"anthropic": MagicMock()}

    backend = _build_backend(providers, {}, None, coordinator, {})

    assert backend is not None
    assert "attractor-anthropic" in backend._profiles
    assert "attractor-openai" in backend._profiles


def test_explicit_profiles_override_auto_discovery():
    """Explicit profiles should take priority over auto-discovery."""
    coordinator = _make_coordinator(
        has_spawn=True,
        agents={"auto-agent": {"session": {"orchestrator": {"module": "loop-agent"}}}},
    )
    providers = {"anthropic": MagicMock()}

    orchestrator_config = {"profiles": {"anthropic": "my-custom-agent"}}

    backend = _build_backend(providers, {}, None, coordinator, orchestrator_config)
    assert backend._profiles == {"anthropic": "my-custom-agent"}
    # Auto-discovered agent should NOT be present
    assert "auto-agent" not in backend._profiles


def test_empty_profiles_still_creates_backend():
    """Backend should be created even with empty profiles (with warning)."""
    coordinator = _make_coordinator(has_spawn=True, agents={})
    providers = {"anthropic": MagicMock()}

    backend = _build_backend(providers, {}, None, coordinator, {})

    assert backend is not None
    assert backend._profiles == {}


def test_no_spawn_falls_back_to_direct_provider():
    """Without session.spawn, the `direct` worker handles every node.

    CHANGED (DESIGN-worker-registry-core-split.md P1, gap-table row 2): the
    former assertion (``not hasattr(backend, "_profiles")``) pinned the
    pre-merge TWO-CLASS architecture -- proving "not AmplifierBackend" was
    the only available proxy for "used the direct-provider path" back when
    ``DirectProviderBackend`` was a separate class with no ``_profiles``
    attribute. The merge makes ``AmplifierBackend`` the ONE adapter class in
    every case (``_build_backend`` no longer constructs a second backend
    class at all) -- the capability-fallback SEMANTICS this test actually
    cares about (spawn absent -> the direct worker) are unchanged and are
    now asserted directly via the registry-backed selection method, rather
    than by a class-identity proxy that no longer distinguishes anything.
    """
    coordinator = _make_coordinator(has_spawn=False)
    providers = {"anthropic": MagicMock()}

    backend = _build_backend(providers, {}, None, coordinator, {})

    assert backend is not None
    assert isinstance(backend, AmplifierBackend)
    node = Node(id="n1", shape="box", prompt="do work")
    assert backend._resolve_worker_name(node) == "direct"


def test_orchestrator_config_threaded_from_execute():
    """PipelineOrchestrator.execute() should thread self.config to _build_backend."""
    # This test verifies the wiring: when PipelineOrchestrator has config with
    # profiles, they should reach _build_backend.
    # We test this indirectly by checking the function signature accepts the param.
    import inspect

    sig = inspect.signature(_build_backend)
    assert "orchestrator_config" in sig.parameters
