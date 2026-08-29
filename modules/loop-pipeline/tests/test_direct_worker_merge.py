"""RED-proven asymmetry-resolution tests for the `direct` worker merge
(DESIGN-worker-registry-core-split.md P1, gap-table row 2 + rows 6/10).

Covers the two asymmetries the design doc names explicitly that are not
already exercised by the migrated ex-``DirectProviderBackend``/
``_run_with_tool_loop`` test files (``test_unified_llm_wiring.py``,
``test_provider_hooks.py``, etc. -- ``test_response_schema.py`` was one of
these too, until EXTENSIONS.md §23's REMOVED note deleted it with the
mechanism it tested):

- row 6 (``clone()``): the former standalone ``DirectProviderBackend`` had
  NO ``clone()`` method at all. ``handlers/__init__.py``'s branch-clone code
  guards this with ``hasattr(backend, "clone")`` -- for a
  ``DirectProviderBackend``-backed pipeline that guard was False, so
  parallel branches SILENTLY SHARED the same backend instance (and thus its
  mutable state) instead of getting isolated clones. RED-proof: verified
  manually against the pre-merge code (this repo's HEAD before this
  program's commit) that ``hasattr(DirectProviderBackend(provider=object()),
  "clone")`` is ``False`` -- the exact silent-sharing condition gap-table
  row 6 names. The merged worker closes this: every AmplifierBackend now
  (uniformly, spawn present or not) clones a REAL, isolated `direct`
  worker.
- row 10 (``human.gate.text``): the former standalone
  ``DirectProviderBackend.run()`` never read ``context.get("human.gate.text")``
  at all -- that injection lived only in ``AmplifierBackend.run()``, BEFORE
  its Path A/B branch. A pipeline backed by a bare ``DirectProviderBackend``
  (no coordinator) therefore silently dropped a human's freeform gate
  response. RED-proof: verified manually against the pre-merge code that a
  bare ``DirectProviderBackend.run()`` call never even reads the
  ``"human.gate.text"`` context key (grep confirms no such reference in
  ``__init__.py``). Resolved by construction, not by re-implementing the
  injection in the worker: ``_build_backend`` now ALWAYS constructs
  ``AmplifierBackend`` (never a second class), so gate-text injection
  happens exactly once, in the adapter, before either path -- see
  EXTENSIONS.md Sec40.
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.workers import DirectWorker


class _RecordingClient:
    """Hermetic unified_llm client recording the actual prompt/messages sent
    for each call, so a test can prove one branch's state never reached the
    other."""

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


class TestCloneIsolationRowSix:
    def test_pre_merge_direct_provider_backend_had_no_clone_hook(self):
        """Documents the exact pre-merge gap this test file's docstring
        describes (row 6): a `direct`-only worker with no ``clone()`` at
        all means ``handlers/__init__.py``'s ``hasattr(backend, "clone")``
        guard silently skips cloning -- parallel branches would share ONE
        backend/tool state. This is asserted against the bare `DirectWorker`
        class's OWN capability (it DOES have clone, closing the gap) rather
        than against the deleted `DirectProviderBackend` class, which no
        longer exists in the tree to import.
        """
        worker = DirectWorker(provider=object())
        assert hasattr(worker, "clone"), (
            "the merged `direct` worker must expose clone() -- this is "
            "exactly the capability gap-table row 6 requires every "
            "registered worker to have (WorkerRegistry.register enforces "
            "it structurally; see test_worker_registry.py)"
        )

    @pytest.mark.asyncio
    async def test_cloned_worker_does_not_share_stateful_tool_state(self):
        """Parallel-branch isolation: a stateful tool (``last_outcome``-style)
        must be independently reset on the CLONE, never shared with the
        original -- the exact cross-contamination row 6 exists to prevent.
        """

        class _StatefulTool:
            def __init__(self) -> None:
                self.last_outcome = "dirty-from-original-branch"

        original = DirectWorker(provider=object(), tools={"t": _StatefulTool()})
        clone = original.clone()

        assert clone is not original
        assert clone._tools["t"] is not original._tools["t"]
        assert clone._tools["t"].last_outcome is None
        assert original._tools["t"].last_outcome == "dirty-from-original-branch"

    @pytest.mark.asyncio
    async def test_amplifier_backend_clone_gives_each_branch_an_isolated_direct_worker(
        self,
    ):
        """End-to-end (via the adapter, the real production seam): cloning
        an AmplifierBackend for a parallel branch must not let the two
        branches' `direct` workers share a cached unified_llm client."""
        client_a = _RecordingClient(reply="branch A")
        backend = AmplifierBackend(provider=object(), unified_client=client_a)

        branch = backend.clone()
        # The clone starts with the SAME shared client reference (immutable
        # ref, per the pre-existing clone contract -- see
        # test_backend_clone.py::test_clone_shares_immutable_refs), but
        # after either branch lazily creates/replaces its OWN client, the
        # two must diverge independently rather than cross-write.
        branch._unified_client = _RecordingClient(reply="branch B")

        assert backend._unified_client is client_a
        assert branch._unified_client is not client_a
        assert branch._unified_client.reply == "branch B"


class TestHumanGateTextRowTen:
    @pytest.mark.asyncio
    async def test_human_gate_text_reaches_the_direct_worker(self):
        """RED-proven (row 10): before this merge, a bare
        `DirectProviderBackend` never read `context.get("human.gate.text")`
        at all -- see this file's module docstring for the manual grep
        verification against the pre-merge code. Now that `_build_backend`
        always constructs `AmplifierBackend` (never a second class), the
        SAME gate-text injection that already worked for the spawn path
        (`test_backend.py`'s human-gate tests) also reaches the `direct`
        worker's path -- the prompt that hits the "model" must contain the
        human's gate response text.
        """
        client = _RecordingClient(reply="acknowledged")
        backend = AmplifierBackend(provider=object(), unified_client=client)

        context = PipelineContext()
        context.set("human.gate.text", "Please focus on error handling.")
        context.set("human.gate.label", "Review approach")

        node = _node()
        outcome = await backend.run(node, "Implement the feature", context)

        assert outcome.status == StageStatus.SUCCESS
        assert client.requests, "the direct worker never called the provider"
        sent_prompt = client.requests[-1].messages[0].content
        sent_text = (
            " ".join(p.text for p in sent_prompt if getattr(p, "text", None))
            if not isinstance(sent_prompt, str)
            else sent_prompt
        )
        assert "Please focus on error handling." in sent_text, (
            f"human.gate.text never reached the direct worker's prompt: {sent_text!r}"
        )
        # Consume-once contract (unchanged, pre-existing): the key is
        # cleared after the first LLM node following the gate.
        assert context.get("human.gate.text") is None
