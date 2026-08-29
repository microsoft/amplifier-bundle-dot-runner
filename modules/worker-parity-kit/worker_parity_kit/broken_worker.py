"""BrokenWorker: the kit's own deliberately-broken fixture harnesses.

Non-vacuity (design decision 4): a parity kit whose M2/M3 tests pass against
EVERY conceivable worker -- including a broken one -- proves nothing. This
module ships two fixture harnesses, each modeled on a REAL incident from the
kit's own motivating story (see ``modules/worker-parity-kit/README.md``,
"Why this exists"):

  * ``BrokenWorker`` -- drops seeded ``context`` silently (mirrors the
    undisclosed 6th gap: an adapter that had already disclosed five OTHER
    capability gaps still had a SIXTH, undisclosed one -- silently dropping
    ``context`` -- found only by a teammate's independent read) -- breaks
    M2; AND fabricates an explicit SUCCESS verdict unconditionally,
    regardless of whether any real verdict mechanism was ever invoked --
    breaks M3.
  * ``PartialSuccessBrokenWorker`` -- fabricates an explicit PARTIAL_SUCCESS
    verdict unconditionally instead of SUCCESS. Added post-merge-review
    (HIGH finding): the real engine's gate check
    (``amplifier_module_loop_pipeline.engine``:1669,
    ``gate_satisfied = outcome.is_success and outcome.is_explicit``) treats
    an explicit PARTIAL_SUCCESS as gate-satisfying identically to SUCCESS,
    so a worker fabricating THAT status is the same bug class M3 exists to
    catch -- a SUCCESS-only M3 assertion would have let it through. This
    fixture is otherwise conformant (M1 and M2 both green) so it isolates
    ONLY the PARTIAL_SUCCESS gap, rather than being broken everywhere.

``tests/test_broken_worker_meta.py`` proves the kit's MUST tests actually go
RED against these fixtures (and that the non-targeted MUSTs stay green,
since each fixture is broken in exactly its documented dimension(s), not
gratuitously in every dimension). Never used outside worker-parity-kit's
own ``tests/`` -- importing this module from a real worker's test tree
would be a misuse of the kit.
"""

from __future__ import annotations

from typing import Any

from .protocol import TurnResult


class BrokenWorker:
    """A deliberately non-conformant ``WorkerHarness``. Kit-internal only."""

    declared_absences: frozenset[str] = frozenset()

    async def run_turn(
        self,
        prompt: str,
        seeded_context_messages: list[dict[str, Any]] | None = None,
        orchestrator_config: dict[str, Any] | None = None,
    ) -> TurnResult:
        # BUG 1 (M2 violation): `seeded_context_messages` is accepted --
        # the signature matches WorkerHarness exactly -- but never forwarded
        # anywhere. The "model" sees ONLY the fresh prompt. This mirrors the
        # undisclosed 6th gap precisely: nothing about the call SIGNATURE
        # reveals the drop; only inspecting what reached the model does.
        del seeded_context_messages
        del orchestrator_config
        messages_sent = [{"role": "user", "content": prompt}]

        # BUG 2 (M3 violation): fabricates an explicit SUCCESS verdict
        # unconditionally -- there is no report_outcome call, no verdict
        # mechanism of any kind, yet the completion envelope claims one
        # anyway.
        envelope = {
            "orchestrator": "broken-worker",
            "status": "success",
            "turn_count": 1,
            "metadata": {},
        }
        return TurnResult(
            reply="done (this worker is DELIBERATELY broken -- see module docstring)",
            messages_sent_to_provider=messages_sent,
            completion_envelope=envelope,
            warnings=[],
        )


class PartialSuccessBrokenWorker:
    """A second deliberately non-conformant ``WorkerHarness``: fabricates an
    explicit PARTIAL_SUCCESS verdict unconditionally. Kit-internal only.

    Unlike ``BrokenWorker``, this fixture forwards seeded context faithfully
    (M2-conformant) -- it exists to isolate ONE bug in ONE dimension: a
    worker whose completion envelope claims
    ``metadata.report_outcome={"status": "partial_success"}`` with zero real
    verdict mechanism behind it. Pre-widening, M3 checked
    ``outcome.status is StageStatus.SUCCESS`` only, so this exact fixture
    passed M3 green while the real engine (engine.py:1669) honors
    PARTIAL_SUCCESS as gate-satisfying identically to SUCCESS -- see
    ``tests/test_broken_worker_meta.py`` for the RED-proof against the
    widened assertion.
    """

    declared_absences: frozenset[str] = frozenset()

    async def run_turn(
        self,
        prompt: str,
        seeded_context_messages: list[dict[str, Any]] | None = None,
        orchestrator_config: dict[str, Any] | None = None,
    ) -> TurnResult:
        del orchestrator_config
        # M2-conformant: seeded context is forwarded faithfully so this
        # fixture isolates ONLY the M3/PARTIAL_SUCCESS gap.
        messages_sent = list(seeded_context_messages or [])
        messages_sent.append({"role": "user", "content": prompt})

        # THE bug: fabricates an explicit PARTIAL_SUCCESS verdict
        # unconditionally -- there is no report_outcome call, no verdict
        # mechanism of any kind, yet the completion envelope claims one
        # anyway. This is the exact shape the reviewer reproduced against
        # the real engine: engine.py:1669's
        # ``gate_satisfied = outcome.is_success and outcome.is_explicit``
        # honors this identically to a fabricated SUCCESS.
        envelope = {
            "orchestrator": "broken-worker-partial-success",
            "status": "partial_success",
            "turn_count": 1,
            "metadata": {},
        }
        return TurnResult(
            reply=(
                "done (this worker is DELIBERATELY broken -- fabricates "
                "PARTIAL_SUCCESS -- see module docstring)"
            ),
            messages_sent_to_provider=messages_sent,
            completion_envelope=envelope,
            warnings=[],
        )
