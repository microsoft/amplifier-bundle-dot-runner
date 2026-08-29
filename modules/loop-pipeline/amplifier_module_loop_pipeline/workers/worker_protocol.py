"""The ``Worker`` protocol -- the seam BELOW canonical Sec4.5's CodergenBackend.

DESIGN-worker-registry-core-split.md Sec2.1 ("The seam contract
(spec-verified)"):

    A worker is stateless per node visit: ``(prompt, context, replayed
    history)`` in, ``(output, outcome)`` out. No session identity ever
    crosses the seam. Same-thread reuse under fidelity=full is a PERMITTED
    internal worker optimization, never a seam requirement.

And gap-table row 32's resolution: "the Sec4.5 adapter (engine side)
resolves fidelity, applies the Sec5.3 resume degrade, and hands the worker
its already-replayed history; workers never receive `graph` or
`incoming_edge`."

Judgment call (disclosed in the P1 report): this Protocol's ``run()`` keeps
``node`` as a parameter, alongside the three the design doc names. The doc's
exclusion list is specific -- "workers never receive `graph` or
`incoming_edge`" -- and does not extend to `node` itself. Both predecessor
implementations (``AmplifierBackend._run_with_tool_loop`` and
``DirectProviderBackend.run``) read ordinary per-node attributes directly
off ``node`` (``llm_provider``, ``llm_model``, ``reasoning_effort``,
``max_agent_turns``, ``response_schema``, ``goal_gate``) -- none of that is
the fidelity/thread-key plumbing the two named exclusions target. Dropping
`node` too would force the adapter to pre-flatten every one of those
attributes into ad hoc keyword arguments for no seam-purity gain.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..context import PipelineContext
from ..graph import Node
from ..outcome import Outcome


@runtime_checkable
class Worker(Protocol):
    """A registry-resolved worker backing a codergen node.

    Every registered worker MUST implement all three members -- ``run``,
    ``clone``, ``close`` -- so that ``clone()``/``close()`` parity (gap-table
    rows 6/7) is a registration-time guarantee, never a per-call
    ``hasattr``/``getattr`` guess. ``WorkerRegistry.register`` enforces this.
    """

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        replayed_history: list[dict[str, Any]],
    ) -> tuple[str, Outcome]:
        """Run one stateless turn.

        Args:
            node: The pipeline node being executed. Used only for ordinary
                per-node attributes (provider/model/reasoning_effort/
                max_agent_turns/response_schema/goal_gate) -- never for
                `graph`/`incoming_edge`-shaped fidelity plumbing, which is
                the adapter's job one layer up.
            prompt: The already-expanded instruction text (preamble and any
                human.gate.text already applied by the adapter).
            context: The current pipeline context.
            replayed_history: Prior node-exchange turns to replay for this
                thread (node-exchange granularity, EXTENSIONS.md Sec12),
                already resolved by the adapter. Empty when this worker's
                node is not part of a `fidelity=full` thread with prior
                turns.

        Returns:
            ``(output_text, outcome)`` -- the raw text produced (for
            transcript-continuity bookkeeping, owned by the adapter) and the
            parsed ``Outcome``.
        """
        ...

    def clone(self) -> Worker:
        """Return a branch-isolated clone (fresh mutable state, shared
        immutable refs) -- see EXTENSIONS.md Sec9/Sec13 for why parallel
        branches must never share worker-held state."""
        ...

    async def close(self) -> None:
        """Release any held resources (e.g. a cached LLM client) -- spec
        finalize contract. Must be a no-op when nothing was ever created."""
        ...
