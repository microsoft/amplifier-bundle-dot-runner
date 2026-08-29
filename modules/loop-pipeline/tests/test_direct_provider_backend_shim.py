"""Deprecation-shim coverage for ``DirectProviderBackend`` (adversarial-review Fix 1).

DESIGN-worker-registry-core-split.md P1 deleted the standalone
``DirectProviderBackend`` class (former ``__init__.py:39-394``), merging its
body into the registry's ``direct`` worker
(``amplifier_module_loop_pipeline/workers/direct_worker.py``). That class was
a documented library-integration path ("Path A"):
``amplifier-bundle-attractor/README.md:323,343`` and
``examples/programmatic_usage.py:71,86`` both do
``from amplifier_module_loop_pipeline import DirectProviderBackend``, and
that repo's ``docs/APP-INTEGRATION-GUIDE.md`` teaches it. Deleting the class
outright would break those documented imports on the next copy-refresh.

This file pins the compatibility shim (``amplifier_module_loop_pipeline.
__init__.DirectProviderBackend``) that keeps the import path and the
constructor/``run()`` signature working, unchanged, through a deprecation
window:

1. The documented import path still resolves.
2. Constructing the shim emits exactly one ``DeprecationWarning`` naming the
   replacement.
3. A basic ``run()`` through the shim produces the SAME outcome as calling
   the `direct` worker directly (reusing the ``_RecordingClient`` fixture
   pattern from ``test_direct_worker_merge.py``, this repo's existing
   hermetic-client convention for the direct/AmplifierBackend path).
4. The shim is excluded from the worker registry -- it is not, and never
   becomes, a registered ``Worker`` name.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.outcome import StageStatus


class _RecordingClient:
    """Hermetic unified_llm client recording every request it receives.

    Identical pattern to ``test_direct_worker_merge.py``'s
    ``_RecordingClient`` -- reused here (not reimplemented from scratch) per
    the fix's instruction to reuse an existing direct-worker test fixture.
    """

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> Any:
        import unified_llm

        self.requests.append(request)
        return unified_llm.Response(
            id="r",
            model="test-model",
            provider="test",
            message=unified_llm.Message.assistant(self.reply),
            finish_reason=unified_llm.FinishReason(reason="stop"),
            usage=unified_llm.Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def _node(**attrs: Any) -> Node:
    defaults = {"llm_model": "test-model", "llm_provider": "test"}
    defaults.update(attrs)
    return Node(id="probe", shape="box", prompt="do work", attrs=defaults)


def test_import_path_matches_documented_consumer_usage():
    """``from amplifier_module_loop_pipeline import DirectProviderBackend``
    is the exact import both ``amplifier-bundle-attractor/README.md:323,343``
    and ``examples/programmatic_usage.py:71,86`` use -- it must keep
    resolving through the deprecation window."""
    from amplifier_module_loop_pipeline import DirectProviderBackend

    assert DirectProviderBackend is not None


@pytest.mark.asyncio
async def test_construction_emits_deprecation_warning_naming_the_replacement():
    """Each construction emits exactly one ``DeprecationWarning`` -- never
    suppressed after the first (no module-level "warn once" flag) -- and the
    message names the replacement (the worker registry / ``AmplifierBackend``
    with the ``direct`` worker) plus a migration pointer to EXTENSIONS Sec40.
    """
    from amplifier_module_loop_pipeline import DirectProviderBackend

    with warnings.catch_warnings(record=True) as first:
        warnings.simplefilter("always")
        DirectProviderBackend(provider=object())
    deprecation_warnings_first = [
        w for w in first if issubclass(w.category, DeprecationWarning)
    ]
    assert len(deprecation_warnings_first) == 1
    message = str(deprecation_warnings_first[0].message)
    assert "deprecated" in message.lower()
    assert "AmplifierBackend" in message
    assert "direct" in message
    assert "Sec40" in message or "EXTENSIONS" in message

    # A SECOND construction fires its OWN warning too -- fires once PER
    # CONSTRUCTION, not once ever (never latched off after the first call).
    with warnings.catch_warnings(record=True) as second:
        warnings.simplefilter("always")
        DirectProviderBackend(provider=object())
    deprecation_warnings_second = [
        w for w in second if issubclass(w.category, DeprecationWarning)
    ]
    assert len(deprecation_warnings_second) == 1


@pytest.mark.asyncio
async def test_shim_run_produces_same_outcome_as_direct_worker():
    """A basic ``run()`` through the shim must produce the same observable
    outcome as the same turn run through ``AmplifierBackend`` (the `direct`
    worker's real production seam) directly -- the shim must be a pure
    delegation, not a second implementation."""
    from amplifier_module_loop_pipeline import DirectProviderBackend

    node = _node()
    context = PipelineContext()

    direct_client = _RecordingClient(reply="hello from direct")
    direct_backend = AmplifierBackend(provider=object(), unified_client=direct_client)
    direct_outcome = await direct_backend.run(node, "do work", context)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        shim_client = _RecordingClient(reply="hello from direct")
        shim = DirectProviderBackend(provider=object(), unified_client=shim_client)
    shim_outcome = await shim.run(node, "do work", PipelineContext())

    assert shim_outcome.status == StageStatus.SUCCESS
    assert direct_outcome.status == shim_outcome.status
    assert direct_outcome.context_updates == shim_outcome.context_updates
    assert shim_client.requests, "the shim never reached the provider boundary"


def test_shim_is_not_registered_as_a_worker():
    """The shim is excluded from the registry: it is not, and never
    becomes, a ``WorkerRegistry`` entry. Only the real ``\"direct\"`` worker
    is registered -- constructing the shim must not add or alias any
    additional registry entry."""
    from amplifier_module_loop_pipeline.workers import (
        DirectWorker,
        Worker,
        WorkerRegistry,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from amplifier_module_loop_pipeline import DirectProviderBackend

        DirectProviderBackend(provider=object())

    # The shim class itself is not exported from the workers package at all.
    import amplifier_module_loop_pipeline.workers as workers_pkg

    assert "DirectProviderBackend" not in workers_pkg.__all__
    assert not hasattr(workers_pkg, "DirectProviderBackend")

    # A freshly constructed AmplifierBackend registers only "direct" --
    # never a "DirectProviderBackend" (or any other shim-derived) name.
    backend = AmplifierBackend(provider=object())
    assert backend._registry.names() == frozenset({"direct"})

    # Sanity: the real DirectWorker/WorkerRegistry/Worker symbols this test
    # imports are unaffected by the shim's existence.
    assert DirectWorker is not None
    assert Worker is not None
    assert WorkerRegistry is not None
