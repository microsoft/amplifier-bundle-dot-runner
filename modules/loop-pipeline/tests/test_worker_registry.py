"""Tests for ``amplifier_module_loop_pipeline.workers.WorkerRegistry``.

DESIGN-worker-registry-core-split.md P1 test-discipline item 1: "Registry:
named registration/resolution; unknown worker name -> loud error listing
registered names (never silent fallback)."
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.workers import DirectWorker, WorkerRegistry


def _direct() -> DirectWorker:
    return DirectWorker(provider=object())


class TestRegisterAndResolve:
    def test_register_then_resolve_returns_the_same_instance(self):
        registry = WorkerRegistry()
        worker = _direct()
        registry.register("direct", worker)

        assert registry.resolve("direct") is worker

    def test_names_reports_every_registered_worker(self):
        registry = WorkerRegistry()
        registry.register("direct", _direct())

        assert registry.names() == frozenset({"direct"})

    def test_constructor_accepts_an_initial_mapping(self):
        worker = _direct()
        registry = WorkerRegistry({"direct": worker})

        assert registry.resolve("direct") is worker
        assert registry.names() == frozenset({"direct"})


class TestUnknownNameIsLoud:
    """RED-proof: a registry that silently fell back (e.g. returning the
    first-registered worker, or None) instead of raising would pass no test
    here -- every assertion below requires the loud, name-listing error."""

    def test_resolve_unknown_name_raises_value_error(self):
        registry = WorkerRegistry()
        registry.register("direct", _direct())

        with pytest.raises(ValueError, match="Unknown worker 'bogus'"):
            registry.resolve("bogus")

    def test_unknown_name_error_lists_every_registered_worker(self):
        registry = WorkerRegistry()
        registry.register("direct", _direct())
        registry.register("also-direct", _direct())

        with pytest.raises(ValueError) as exc_info:
            registry.resolve("nonexistent")

        message = str(exc_info.value)
        assert "'direct'" in message
        assert "'also-direct'" in message

    def test_resolve_on_empty_registry_still_raises_loud_not_silent(self):
        registry = WorkerRegistry()

        with pytest.raises(ValueError, match=r"Registered workers: \[\]"):
            registry.resolve("anything")


class TestCloneCloseParity:
    """Gap-table rows 6/7: "every registered worker clones, or declares
    absence loudly" -- enforced at REGISTRATION time, not a per-call
    ``hasattr`` guess (the exact pattern that let the pre-merge
    ``DirectProviderBackend`` -- which had neither ``clone()`` nor a
    registration gate -- go unnoticed by ``handlers/__init__.py``'s
    ``hasattr(backend, "clone")`` guard)."""

    def test_register_refuses_a_worker_missing_clone_or_close(self):
        class _NoCloneNoClose:
            async def run(self, node, prompt, context, replayed_history):
                raise NotImplementedError

        registry = WorkerRegistry()
        with pytest.raises(TypeError, match="clone"):
            registry.register("broken", _NoCloneNoClose())

    def test_register_accepts_a_worker_with_all_three_members(self):
        registry = WorkerRegistry()
        # Must not raise.
        registry.register("direct", _direct())
        assert "direct" in registry.names()


class TestRegistryClone:
    @pytest.mark.asyncio
    async def test_clone_produces_a_new_registry_with_cloned_workers(self):
        registry = WorkerRegistry()
        original_worker = _direct()
        registry.register("direct", original_worker)

        cloned_registry = registry.clone()

        assert cloned_registry is not registry
        cloned_worker = cloned_registry.resolve("direct")
        assert cloned_worker is not original_worker
        assert cloned_registry.names() == registry.names()

    @pytest.mark.asyncio
    async def test_close_all_closes_every_worker(self):
        class _RecordingWorker:
            def __init__(self) -> None:
                self.closed = False

            async def run(self, node, prompt, context, replayed_history):
                raise NotImplementedError

            def clone(self):
                return self

            async def close(self) -> None:
                self.closed = True

        registry = WorkerRegistry()
        w = _RecordingWorker()
        registry.register("w", w)

        await registry.close_all()

        assert w.closed is True
