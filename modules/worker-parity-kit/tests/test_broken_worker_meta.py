"""Non-vacuity meta-tests (design decision 4): RED-prove M2 and M3.

A parity kit whose M2/M3 tests pass against EVERY conceivable worker --
including a deliberately broken one -- proves nothing. These tests call the
shared suite's own M1/M2/M3 test functions directly (not through pytest
collection -- ``@pytest.mark.asyncio`` is only collection metadata; calling
an async test function directly and awaiting it runs it exactly like pytest
would) against ``BrokenWorker`` and ``PartialSuccessBrokenWorker`` and
assert:

  * M2 and M3 raise ``AssertionError`` (RED) against ``BrokenWorker`` --
    its two deliberate bugs are exactly what those tests are supposed to
    catch.
  * M3 (widened) also raises ``AssertionError`` (RED) against
    ``PartialSuccessBrokenWorker`` -- the review-mandated fix (M3 widened
    from a SUCCESS-only status check to ``outcome.is_success``, matching
    engine.py:1669's own gate check) must catch a worker that fabricates
    PARTIAL_SUCCESS instead of SUCCESS.
  * M1 stays green for both fixtures -- each is broken in exactly its
    documented dimension(s), not gratuitously in every dimension (a
    fixture broken EVERYWHERE would not isolate which test actually caught
    which bug). ``PartialSuccessBrokenWorker`` additionally keeps M2 green
    (it forwards seeded context faithfully), isolating the RED-proof below
    to the M3/PARTIAL_SUCCESS gap alone.

Imported under non-``test_``-prefixed aliases deliberately: pytest collects
module-level callables by NAME, including merely-imported ones, so importing
them under their original names would make pytest ALSO try to collect (and
fail to run, for lack of a ``worker_harness`` fixture) the raw suite
functions here. Aliasing sidesteps that double-collection without changing
what's actually being called.
"""

from __future__ import annotations

import pytest
from worker_parity_kit.broken_worker import BrokenWorker, PartialSuccessBrokenWorker
from worker_parity_kit.suite import test_m1_mount_shape_execute_returns_str as _m1
from worker_parity_kit.suite import test_m2_seeded_context_reaches_model_boundary as _m2
from worker_parity_kit.suite import (
    test_m3_no_signal_never_fabricates_explicit_success as _m3,
)


@pytest.mark.asyncio
async def test_broken_worker_fails_m2_seeded_context() -> None:
    """RED-proof: BrokenWorker drops seeded context -- M2 must catch it."""
    harness = BrokenWorker()
    with pytest.raises(AssertionError, match="never reached the model request"):
        await _m2(harness)


@pytest.mark.asyncio
async def test_broken_worker_fails_m3_fabricated_verdict() -> None:
    """RED-proof: BrokenWorker fabricates SUCCESS -- M3 must catch it."""
    harness = BrokenWorker()
    with pytest.raises(
        AssertionError, match="fabricated an explicit SUCCESS-or-PARTIAL_SUCCESS"
    ):
        await _m3(harness)


@pytest.mark.asyncio
async def test_broken_worker_still_passes_m1() -> None:
    """Control: BrokenWorker is broken in exactly two documented dimensions
    (M2, M3) -- its mount shape (M1) is otherwise conformant, so M1 stays
    green. Proves the RED-proofs above isolate the right failure, rather
    than BrokenWorker being globally broken in a way that would make every
    test fail for an uninteresting reason.
    """
    harness = BrokenWorker()
    await _m1(harness)


@pytest.mark.asyncio
async def test_partial_success_worker_fails_widened_m3() -> None:
    """RED-proof (review HIGH fix): a worker fabricating an explicit
    PARTIAL_SUCCESS verdict -- with zero real verdict mechanism -- must be
    caught by the widened M3, exactly as a fabricated SUCCESS is.

    Before the widening, M3 asserted
    ``outcome.status is StageStatus.SUCCESS`` only, so this exact fixture
    passed M3 green while the real engine's gate check (engine.py:1669,
    ``gate_satisfied = outcome.is_success and outcome.is_explicit``) honors
    PARTIAL_SUCCESS as gate-satisfying identically to SUCCESS -- the exact
    bug class M3 exists to catch. This test proves the widened assertion
    (``outcome.is_success``, covering SUCCESS and PARTIAL_SUCCESS) closes
    that gap.
    """
    harness = PartialSuccessBrokenWorker()
    with pytest.raises(
        AssertionError, match="fabricated an explicit SUCCESS-or-PARTIAL_SUCCESS"
    ):
        await _m3(harness)


@pytest.mark.asyncio
async def test_partial_success_worker_still_passes_m1_and_m2() -> None:
    """Control: PartialSuccessBrokenWorker is broken in exactly ONE
    dimension (M3/PARTIAL_SUCCESS fabrication) -- its mount shape (M1) and
    seeded-context fidelity (M2) are otherwise conformant, so both stay
    green. Isolates the RED-proof above to the M3 gap alone, rather than
    this fixture being globally broken.
    """
    harness = PartialSuccessBrokenWorker()
    await _m1(harness)
    await _m2(harness)
