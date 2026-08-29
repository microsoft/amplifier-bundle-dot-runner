"""worker-parity-kit wiring for loop-pipeline's `direct` worker.

See modules/worker-parity-kit/README.md ("Why this exists") for the three
incidents this kit's suite guards against, and
DESIGN-worker-registry-core-split.md P1 item 5 ("Parity-kit admission:
write the direct worker's harness"). This file adds ONE thing: a
``WorkerHarness`` (``worker_parity_kit.protocol``) that drives THIS
module's REAL production path -- ``AmplifierBackend.run()`` -> the worker
registry -> ``DirectWorker.run()`` -- hermetically (no coordinator, so
``session.spawn`` is absent and every node routes to the `direct` worker;
no network, no credentials -- a fake ``unified_llm`` client stands in for
the provider boundary).

``from worker_parity_kit.suite import *`` below is what actually collects
the 3 MUST tests + the TARGET-tier parametrized test against the
``worker_harness`` fixture; nothing in this file re-implements any of them.

Scope note (judgment call, disclosed in the P1 report): M1's cited
authority is ``amplifier_core.interfaces.Orchestrator.execute(...) -> str``
-- literally true of ``loop-agent``/``loop-amplifier-agent`` (each an
installed module with its own session-level ``mount()``/``execute()``), but
`direct` is not a session-level orchestrator at all -- it is a Sec4.5-layer
worker reached through ``AmplifierBackend.run(node, prompt, context, ...)``.
This harness drives that REAL call directly (the actual seam `direct`
lives behind) and adapts its ``Outcome`` return into ``TurnResult.reply``
(a plain str, satisfying M1's observable check) rather than routing through
``PipelineOrchestrator.execute()`` and a synthetic single-node DOT graph,
which would add indirection without adding coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from worker_parity_kit.protocol import TurnResult
from worker_parity_kit.suite import *

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.fidelity import resolve_thread_key
from amplifier_module_loop_pipeline.graph import Graph, Node

#: DESIGN-worker-registry-core-split.md P1 item 5 / gap-table row 9 +
#: honest scope notes: capabilities `direct` openly does not honor.
#:   - user_instructions: spawn-path-only (workers/direct_worker.py row 9;
#:     ``AmplifierBackend._run_with_spawn`` resolves it, `direct` does not).
#:   - telemetry_session_id: `direct` is session-less by design -- there is
#:     no child session to stamp an id from (contrast the spawn path's
#:     `result["session_id"]`).
#:   - child_spawn_delegate: in-process only -- `direct` never spawns or
#:     delegates to a child session (that is exactly what distinguishes it
#:     from the "spawn" worker).
DECLARED_ABSENCES = frozenset(
    {"user_instructions", "telemetry_session_id", "child_spawn_delegate"}
)


@dataclass
class _FakeUnifiedClient:
    """Hermetic ``unified_llm.Client`` stand-in for ``DirectWorker``.

    No network, no credentials -- records every ``complete()`` request
    (what actually reached the "model" boundary) and returns one canned
    text response. Mirrors ``tests/test_unified_llm_wiring.py``'s
    ``_MockUnifiedClient`` (this repo's existing hermetic-client pattern).
    """

    reply_text: str = "ok"
    requests: list[Any] = field(default_factory=list)

    async def complete(self, request: Any) -> Any:
        import unified_llm

        self.requests.append(request)
        return unified_llm.Response(
            id="wpk-resp",
            model="test-model",
            provider="test",
            message=unified_llm.Message.assistant(self.reply_text),
            finish_reason=unified_llm.FinishReason(reason="stop"),
            usage=unified_llm.Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def _messages_as_dicts(messages: list[Any]) -> list[dict[str, Any]]:
    """Normalize a ``unified_llm`` request's messages into the kit's
    ``list[dict[str, Any]]`` seam shape (protocol.py's
    ``messages_sent_to_provider``)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        content = m.content
        if isinstance(content, str):
            text = content
        else:
            text = " ".join(p.text for p in content if getattr(p, "text", None))
        role = getattr(m.role, "value", None) or str(m.role)
        out.append({"role": role, "content": text})
    return out


class DirectWorkerHarness:
    """WorkerHarness driving the REAL ``AmplifierBackend`` -> registry ->
    `direct` worker path, hermetically -- no coordinator (spawn absent),
    no network."""

    declared_absences: frozenset[str] = DECLARED_ABSENCES

    async def run_turn(
        self,
        prompt: str,
        seeded_context_messages: list[dict[str, Any]] | None = None,
        orchestrator_config: dict[str, Any] | None = None,
    ) -> TurnResult:
        client = _FakeUnifiedClient(reply_text=f"reply to: {prompt}"[:500])
        backend = AmplifierBackend(provider=object(), unified_client=client)

        attrs: dict[str, Any] = {
            "llm_model": "test-model",
            "llm_provider": "test",
            "fidelity": "full",
            "thread_id": "wpk-thread",
        }
        cfg = dict(orchestrator_config or {})
        # TARGET-tier vocabulary alias: the kit's generic "max_turns" name ->
        # this worker's real node attribute.
        if "max_turns" in cfg:
            attrs["max_agent_turns"] = cfg.pop("max_turns")
        attrs.update(cfg)

        node = Node(id="wpk-probe", shape="box", prompt=prompt, attrs=attrs)
        graph = Graph(name="wpk", nodes={"wpk-probe": node}, edges=[])

        if seeded_context_messages:
            # Seed the adapter's node-exchange transcript (EXTENSIONS.md
            # Sec12) using the SAME production thread-key resolver
            # `AmplifierBackend.run()` itself calls -- this is exactly what
            # M2 (support#497's undisclosed-6th-gap class) exercises: does
            # seeded history actually reach the model boundary.
            thread_key = resolve_thread_key(node, None, graph, backend._last_node_id)
            triples: list[tuple[str, str, str]] = []
            pending_user: str | None = None
            for m in seeded_context_messages:
                if m.get("role") == "user":
                    pending_user = str(m.get("content", ""))
                elif m.get("role") == "assistant" and pending_user is not None:
                    triples.append(
                        ("wpk-seed", pending_user, str(m.get("content", "")))
                    )
                    pending_user = None
            backend._thread_transcripts[thread_key] = triples

        outcome = await backend.run(
            node, prompt, PipelineContext(), incoming_edge=None, graph=graph
        )

        sent = (
            _messages_as_dicts(client.requests[-1].messages) if client.requests else []
        )

        # M3 authority: metadata.report_outcome is the ONLY channel an
        # Outcome may carry is_explicit=True through (EXTENSIONS.md Sec35).
        # Faithfully mirror the worker's REAL outcome -- never synthesize a
        # report_outcome envelope the worker did not itself produce.
        metadata: dict[str, Any] = {}
        if outcome.is_explicit:
            metadata["report_outcome"] = {
                "status": outcome.status.value,
                "preferred_label": outcome.preferred_label,
                "notes": outcome.notes,
                "failure_reason": outcome.failure_reason,
            }
        completion_envelope = {
            "orchestrator": "loop-pipeline.direct",
            "status": outcome.status.value,
            "turn_count": len(client.requests),
            "metadata": metadata,
        }

        return TurnResult(
            reply=outcome.response_text or outcome.notes or "",
            messages_sent_to_provider=sent or None,
            completion_envelope=completion_envelope,
            warnings=[],
        )


@pytest.fixture
def worker_harness() -> DirectWorkerHarness:
    return DirectWorkerHarness()
