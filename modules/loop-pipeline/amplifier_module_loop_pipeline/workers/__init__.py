"""The worker registry -- P1 of DESIGN-worker-registry-core-split.md.

Lives BELOW the canonical spec's Sec4.5 ``CodergenBackend`` seam, inside the
engine (see design doc Sec2.1 / Sec4 "Phase 1"). The seam the pipeline engine
holds (``CodergenHandler`` -> ``AmplifierBackend.run(node, prompt, context,
incoming_edge, graph)``) is UNCHANGED -- that is the spec-shaped "adapter"
layer. What is new is what lives one layer further down: named, registry-
resolved *workers*, each satisfying the narrow contract in
``worker_protocol.Worker`` -- stateless per node visit, ``(prompt, context,
replayed_history)`` in, ``(output, outcome)`` out, never `graph`/
`incoming_edge` (design doc gap-table rows 5/32).

``direct`` (``direct_worker.DirectWorker``) is the one worker this phase
ships: the merge of the former ``AmplifierBackend._run_with_tool_loop`` and
the standalone ``DirectProviderBackend`` class (gap-table row 2). Hosted
workers reached via ``session.spawn`` (today: agents whose orchestrator
module is e.g. ``loop-agent``) are NOT registry-managed Worker instances --
the registry keys *names*, not source trees (design doc Sec4 "Phase 1", "Why
P1 lands standalone"), and the hosted path continues to resolve exactly as
today via the existing ``profiles`` map. See ``backend.py``'s
``_KNOWN_WORKER_SENTINELS`` for that reserved name.
"""

from __future__ import annotations

from .direct_worker import DirectWorker
from .registry import WorkerRegistry
from .worker_protocol import Worker

__all__ = ["DirectWorker", "Worker", "WorkerRegistry"]
