"""The WorkerHarness protocol + TurnResult: worker-parity-kit's consumer seam.

Design decision 2 (maintainer-ratified): this kit ships EXECUTED TESTS, not a
new doctrine document. Every MUST test cites an EXISTING authority; this
module only defines the SHAPE a worker's test harness must expose so the
shared suite (``worker_parity_kit.suite``) can drive it generically.

~5-member contract surface, split across two small shapes:

  * ``WorkerHarness.declared_absences`` -- the TARGET-tier skip registry.
  * ``WorkerHarness.run_turn(...)`` -- the one entry point. A conforming
    harness (a) mounts its orchestrator hermetically with kernel-faithful
    doubles, (b) runs exactly ONE turn, (c) returns a ``TurnResult``.
  * ``TurnResult``'s three observable fields -- ``reply``,
    ``messages_sent_to_provider``, ``completion_envelope`` (``warnings`` is a
    convenience list, defaulted empty) -- are the only things the shared
    suite is allowed to look at. Nothing about a worker's internal
    architecture (its own fakes, its own Engine, its own session model)
    leaks through this seam.

Kept intentionally small: a bigger protocol would either force every worker
to expose internals the kit has no business touching, or force the kit to
special-case per-worker internals it cannot know about in advance (there are
only two workers in existence at the time of writing -- see the kit's
README "why this exists" for why a normative contract DOC is deliberately
NOT part of this change; this Protocol is the only place the shape lives).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class TurnResult:
    """What one worker turn produced, at the seam this kit's tests need.

    Fields:
      reply -- the raw string ``Orchestrator.execute()`` returned (M1
        authority: ``amplifier_core.interfaces.Orchestrator.execute(...) ->
        str``).
      messages_sent_to_provider -- normalized ``list[dict[str, Any]]``
        (each dict carries at least ``role``/``content``) reconstructing
        what actually reached the model/provider boundary for this turn, or
        ``None`` when the harness cannot observe that boundary at all. It is
        the HARNESS's job to normalize its own worker's internal message
        representation (typed objects, dicts, whatever) into this shape --
        the shared suite never reaches past it.
      completion_envelope -- the ``ORCHESTRATOR_COMPLETE``-shaped dict the
        worker emitted (``orchestrator``/``status``/``turn_count``/
        ``metadata`` -- EXTENSIONS.md sec35).
      warnings -- any warning-level messages the harness observed during
        the turn (harness-defined; may be empty). Used by the TARGET tier's
        not-silently-dropped check.
    """

    reply: str
    messages_sent_to_provider: list[dict[str, Any]] | None
    completion_envelope: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class WorkerHarness(Protocol):
    """The consumer seam every worker implements to run this kit's suite.

    ``declared_absences`` is a frozenset of TARGET-tier capability names
    (see ``worker_parity_kit.suite.TARGET_CAPABILITIES``) this worker openly
    admits it does not honor. It is the honest alternative to a test that
    silently warns-and-drops a config key: declare it here and the shared
    TARGET test SKIPS, visibly, naming the capability. An UNDECLARED
    capability is expected to be honored (or at least not silently dropped);
    see ``suite.py`` for exactly what "honored" is checked to mean at this
    generic seam.
    """

    declared_absences: frozenset[str]

    async def run_turn(
        self,
        prompt: str,
        seeded_context_messages: list[dict[str, Any]] | None = None,
        orchestrator_config: dict[str, Any] | None = None,
    ) -> TurnResult:
        """Mount hermetically (kernel-faithful doubles, no network/keys),
        run exactly ONE turn with the given prompt/seeded context/config,
        and return a ``TurnResult``. Must not raise for any input this
        kit's suite passes (a raised exception fails the calling test with
        the real traceback -- there is no swallow-and-report-None path)."""
        ...
