"""Tests for worker-selection precedence and default-behavior-unchanged
proofs (DESIGN-worker-registry-core-split.md P1 items 3 + verification bar;
EXTENSIONS.md Sec40).

Precedence: per-node ``worker=`` attribute > run-level ``default_worker``
(orchestrator config) > today's capability-fallback chain (spawn if
resolved, else direct).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from amplifier_module_loop_pipeline import _build_backend
from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus


def _make_node(worker: str | None = None, **extra_attrs: Any) -> Node:
    attrs: dict[str, Any] = {"llm_model": "test-model", "llm_provider": "test"}
    if worker is not None:
        attrs["worker"] = worker
    attrs.update(extra_attrs)
    return Node(id="probe", shape="box", prompt="do work", attrs=attrs)


class _SpawnCoordinator:
    """Minimal coordinator exposing a resolvable session.spawn capability."""

    config: ClassVar[dict[str, Any]] = {"agents": {"agent-a": {}}}

    def get_capability(self, name: str) -> Any:
        return object() if name == "session.spawn" else None


class _NoSpawnCoordinator:
    config: ClassVar[dict[str, Any]] = {"agents": {}}

    def get_capability(self, name: str) -> Any:
        return None


# ---------------------------------------------------------------------------
# Selection precedence -- each RED-proven where new (see docstrings): a
# backend that ignored `worker=`/`default_worker` and always used the
# fallback chain would fail every assertion below.
# ---------------------------------------------------------------------------


class TestSelectionPrecedence:
    def test_node_attr_overrides_run_level_default(self):
        """Node attr `worker="direct"` wins even when the run-level default
        is `"spawn"` AND spawn is actually available -- proving node attr
        is the HIGHEST-precedence selector, not merely "checked first when
        nothing else applies"."""
        backend = AmplifierBackend(
            coordinator=_SpawnCoordinator(),
            profiles={"test": "some-agent"},
            provider=object(),
            default_worker="spawn",
        )
        backend.ensure_spawn_resolved()

        node = _make_node(worker="direct")
        assert backend._resolve_worker_name(node) == "direct"

    def test_run_level_default_overrides_fallback_chain(self):
        """No node attr, but a run-level default of `"direct"` wins over
        the fallback chain even though spawn IS available (which the
        fallback chain would otherwise select)."""
        backend = AmplifierBackend(
            coordinator=_SpawnCoordinator(),
            profiles={"test": "some-agent"},
            provider=object(),
            default_worker="direct",
        )
        backend.ensure_spawn_resolved()

        node = _make_node()
        assert backend._resolve_worker_name(node) == "direct"

    def test_no_override_falls_back_to_spawn_when_available(self):
        backend = AmplifierBackend(
            coordinator=_SpawnCoordinator(), profiles={}, provider=object()
        )
        backend.ensure_spawn_resolved()

        assert backend._resolve_worker_name(_make_node()) == "spawn"

    def test_no_override_falls_back_to_direct_when_spawn_absent(self):
        backend = AmplifierBackend(
            coordinator=_NoSpawnCoordinator(), profiles={}, provider=object()
        )
        backend.ensure_spawn_resolved()

        assert backend._resolve_worker_name(_make_node()) == "direct"

    def test_unknown_node_worker_attr_raises_loud_error(self):
        backend = AmplifierBackend(provider=object())

        with pytest.raises(ValueError, match="not a known worker"):
            backend._resolve_worker_name(_make_node(worker="nonexistent"))

    def test_unknown_default_worker_raises_at_construction_time(self):
        with pytest.raises(ValueError, match="Unknown default_worker"):
            AmplifierBackend(provider=object(), default_worker="nonexistent")

    def test_explicit_spawn_selection_without_capability_raises_loud(self):
        """Explicitly requesting worker="spawn" when session.spawn was
        never resolved must fail loud, not silently reroute to direct."""
        backend = AmplifierBackend(
            coordinator=_NoSpawnCoordinator(), profiles={}, provider=object()
        )

        node = _make_node(worker="spawn")
        with pytest.raises(
            ValueError, match="session.spawn capability is not available"
        ):
            import asyncio

            asyncio.run(backend.run(node, "do work", PipelineContext()))


# ---------------------------------------------------------------------------
# Default-behavior-unchanged proofs (verification bar): a zero-config,
# zero-attribute run routes EXACTLY as it did before this program.
# ---------------------------------------------------------------------------


class TestDefaultBehaviorUnchanged:
    def test_zero_config_spawn_present_builds_backend_that_selects_spawn(self):
        providers = {"test": object()}
        coordinator = _SpawnCoordinator()

        backend = _build_backend(providers, {}, None, coordinator, {})

        assert isinstance(backend, AmplifierBackend)
        backend.ensure_spawn_resolved()
        assert backend._resolve_worker_name(_make_node()) == "spawn"

    def test_zero_config_spawn_absent_builds_backend_that_selects_direct(self):
        providers = {"test": object()}
        coordinator = _NoSpawnCoordinator()

        backend = _build_backend(providers, {}, None, coordinator, {})

        assert isinstance(backend, AmplifierBackend)
        backend.ensure_spawn_resolved()
        assert backend._resolve_worker_name(_make_node()) == "direct"

    def test_zero_config_no_provider_no_spawn_returns_none(self):
        """Simulation-mode fallback (no providers, no spawn) is unchanged."""
        backend = _build_backend({}, {}, None, None, {})
        assert backend is None

    @pytest.mark.asyncio
    async def test_a_community_dot_with_no_worker_attribute_runs_via_direct_unchanged(
        self,
    ):
        """A zero-`worker=`-attribute node, with spawn absent, executes via
        the `direct` worker and returns an ordinary Outcome -- the whole
        point of Sec40's compat commitment ("no community graph needs
        `worker=`")."""

        class _FakeClient:
            async def complete(self, request: Any) -> Any:
                import unified_llm

                return unified_llm.Response(
                    id="r",
                    model="test-model",
                    provider="test",
                    message=unified_llm.Message.assistant("plain text reply"),
                    finish_reason=unified_llm.FinishReason(reason="stop"),
                    usage=unified_llm.Usage(
                        input_tokens=1, output_tokens=1, total_tokens=2
                    ),
                )

        backend = AmplifierBackend(provider=object(), unified_client=_FakeClient())
        node = _make_node()  # no `worker=` attribute at all

        outcome = await backend.run(node, "do work", PipelineContext())

        assert isinstance(outcome, Outcome)
        assert outcome.status == StageStatus.SUCCESS
