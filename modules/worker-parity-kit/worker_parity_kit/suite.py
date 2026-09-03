"""The shared, parametrized worker-parity test suite.

A consumer (a worker's own test tree) does::

    from worker_parity_kit.suite import *  # noqa: F401,F403

    @pytest.fixture
    def worker_harness():
        return MyWorkerHarness()

in one test file, and pytest collects the 3 MUST tests plus the TARGET-tier
parametrized test below against that fixture.

Design decision 1 (maintainer-ratified): tests only -- no new doctrine
document, no new EXTENSIONS ledger entries. Every MUST test below cites an
EXISTING authority in its docstring; it never restates or paraphrases the
authority as if this file were itself normative.

Design decision 2: exactly 3 MUSTs (M1 mount shape, M2 honor seeded context,
M3 never fabricate a verdict) -- see each test's docstring for its cited
authority. A 4th tier, TARGET, is honored-OR-documented-absent and
non-blocking (design decision 3).
"""

from __future__ import annotations

from typing import Any

import pytest

# M3's authority is enforced by feeding a worker's own completion envelope
# (and its reply text) to the REAL engine readers -- never a
# reimplementation of their logic. These are the actual production symbols
# loop-pipeline's spawn path (`_run_with_spawn`) calls, and the SAME branch
# decision is mirrored below (empty final text -> `_outcome_from_spawn_
# result`; non-empty -> `_parse_outcome`) -- WAVE 5 repair (2026-08-30):
# `metadata.report_outcome` is removed, so `_outcome_from_spawn_result`
# alone can never again produce a fabricated `is_explicit=True` verdict;
# the ONLY channel left for a spawn-path verdict is the child's own final
# text parsing as JSON (`_parse_outcome`).
from amplifier_module_loop_pipeline.backend import (
    _outcome_from_spawn_result,
    _parse_outcome,
)

from .protocol import TurnResult, WorkerHarness

__all__ = [
    "TARGET_CAPABILITIES",
    "test_m1_mount_shape_execute_returns_str",
    "test_m2_seeded_context_reaches_model_boundary",
    "test_m3_no_signal_never_fabricates_explicit_success",
    "test_target_capability_honored_or_declared_absent",
]


def _message_contents(messages: list[dict[str, Any]] | None) -> list[str]:
    """Extract string ``content`` values from a normalized message list.

    Tolerant of a stray non-dict/non-string-content entry (never crashes a
    test on an odd shape) -- mirrors the same tolerance
    ``loop-agent``/``loop-amplifier-agent``'s own history-replay code applies
    to unexpected seeded-history shapes.
    """
    if not messages:
        return []
    out: list[str] = []
    for m in messages:
        content = (
            m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        )
        if isinstance(content, str):
            out.append(content)
    return out


# ---------------------------------------------------------------------------
# M1 -- Mount shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m1_mount_shape_execute_returns_str(
    worker_harness: WorkerHarness,
) -> None:
    """M1 -- Mount shape.

    Authority (cite, never restate): the kernel's Orchestrator protocol,
    ``amplifier_core.interfaces.Orchestrator.execute(prompt, context,
    providers, tools, hooks, **kwargs) -> str``. A worker satisfies M1 when
    its installed module's ``mount(coordinator, config)`` yields an object
    whose ``execute()`` accepts that exact parameter shape and returns a
    plain string.

    ``WorkerHarness.run_turn`` IS that call (see ``protocol.py``): this test
    drives it and checks the contract observably, without reaching into any
    worker's internals.
    """
    result = await worker_harness.run_turn("Say a short greeting.", None)
    assert isinstance(result, TurnResult)
    assert isinstance(result.reply, str), (
        "Orchestrator.execute() must return a plain str per "
        f"amplifier_core.interfaces.Orchestrator; got {type(result.reply)!r}"
    )
    assert isinstance(result.completion_envelope, dict), (
        "run_turn() must surface the ORCHESTRATOR_COMPLETE-shaped envelope "
        "(EXTENSIONS.md sec35) even when execute() otherwise succeeds"
    )


# ---------------------------------------------------------------------------
# M2 -- Honor seeded context
# ---------------------------------------------------------------------------

_M2_MARKER = "WPK-CONTINUITY-MARKER-7f3c1a"


@pytest.mark.asyncio
async def test_m2_seeded_context_reaches_model_boundary(
    worker_harness: WorkerHarness,
) -> None:
    """M2 -- Honor seeded ``context``.

    Authority (cite, never restate): attractor spec sec5.4's fidelity table
    (``full``: "Reused (same thread)" session / "Full conversation history
    preserved"), realized per EXTENSIONS.md sec12 via foundation's
    ``child_context.set_messages(parent_messages)`` seeded onto the worker's
    mounted context, at node-exchange granularity, BEFORE ``execute()`` runs.

    This is incident #1 (the undisclosed 6th gap, support#497) from the
    kit's own motivating story: a worker can accept ``context``, return a
    perfectly normal-looking reply and completion envelope, and STILL have
    silently dropped every seeded message. The only way to catch that class
    of bug is to look at what actually reached the model -- which is
    exactly what ``messages_sent_to_provider`` is for.
    """
    seeded = [
        {"role": "user", "content": f"Remember this exact phrase: {_M2_MARKER}"},
        {"role": "assistant", "content": "Understood, I will remember it."},
    ]
    result = await worker_harness.run_turn(
        "What phrase did I ask you to remember?", seeded
    )
    assert result.messages_sent_to_provider is not None, (
        "worker reported NO messages reaching the model/provider boundary at "
        "all -- cannot demonstrate seeded-context fidelity (M2)"
    )
    contents = _message_contents(result.messages_sent_to_provider)
    assert any(_M2_MARKER in c for c in contents), (
        f"seeded context marker {_M2_MARKER!r} never reached the model "
        f"request -- messages_sent_to_provider={result.messages_sent_to_provider!r}"
    )


# ---------------------------------------------------------------------------
# M3 -- Never fabricate a verdict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m3_no_signal_never_fabricates_explicit_success(
    worker_harness: WorkerHarness,
) -> None:
    """M3 -- Never fabricate a verdict.

    Authority (cite, never restate): EXTENSIONS.md sec25 (``is_explicit`` is
    load-bearing at the goal-gate check -- only an unambiguous verdict
    mechanism may set it True) and sec35 (``metadata.report_outcome`` is the
    ONLY channel by which a spawn-path ``Outcome`` carries
    ``is_explicit=True``).

    Widened (review HIGH finding, post-merge-review fix): the real gate
    check -- ``amplifier_module_loop_pipeline.engine``:1669,
    ``gate_satisfied = outcome.is_success and outcome.is_explicit`` --
    treats an explicit PARTIAL_SUCCESS as gate-satisfying identically to an
    explicit SUCCESS, because ``Outcome.is_success`` (outcome.py) is True
    for both ``StageStatus.SUCCESS`` and ``StageStatus.PARTIAL_SUCCESS``. A
    worker unconditionally emitting
    ``metadata.report_outcome={"status": "partial_success"}`` with no real
    verdict mechanism therefore fabricates a gate-satisfying verdict just as
    surely as one emitting ``"success"`` -- so this test asserts on
    ``outcome.is_success`` (SUCCESS-or-PARTIAL_SUCCESS), not a
    SUCCESS-only status check, to match what the engine actually honors.

    Verified via the REAL engine readers, never a reimplementation of their
    logic (binding design mandate): this feeds the worker's own reply text
    and completion envelope through the SAME branch loop-pipeline's spawn
    path (``_run_with_spawn``) actually applies -- empty final text ->
    ``_outcome_from_spawn_result``, non-empty -> ``_parse_outcome`` -- and
    checks the result does NOT come back as an explicit, gate-satisfying
    verdict. Incident #2/#3 from the kit's motivating story (the same bug
    class recurred in a SECOND worker, support#497's actual incident): a
    worker must not let "no verdict" quietly read as "success" -- in either
    flavor, and on either branch of the real decision.

    WAVE 5 repair (2026-08-30) note: ``metadata.report_outcome`` is removed,
    so ``_outcome_from_spawn_result`` can never again produce
    ``is_explicit=True`` by itself -- that half of this check is now a
    permanent, structural regression guard (it MUST stay green; a
    reintroduced metadata-verdict channel should make it red again). The
    live remaining attack surface is a worker whose final reply text itself
    parses as a bare verdict object with zero real mechanism behind it --
    ``_parse_outcome`` is exercised for that branch, matching
    ``broken_worker.py``'s current fixtures (see there).
    """
    result = await worker_harness.run_turn(
        "Do some work, but never call report_outcome or assert any verdict.",
        None,
    )
    if result.reply.strip():
        outcome = _parse_outcome(result.reply, node=None)
    else:
        spawn_result: dict[str, Any] = {
            "output": result.reply,
            "status": result.completion_envelope.get("status"),
            "metadata": result.completion_envelope.get("metadata", {}),
            "session_id": None,
        }
        outcome = _outcome_from_spawn_result(spawn_result)
    fabricated_verdict = (
        outcome is not None and outcome.is_explicit and outcome.is_success
    )
    assert not fabricated_verdict, (
        "worker fabricated an explicit SUCCESS-or-PARTIAL_SUCCESS verdict "
        "with no real verdict signal present (engine.py:1669's "
        "gate_satisfied = outcome.is_success and outcome.is_explicit honors "
        "PARTIAL_SUCCESS identically to SUCCESS): "
        f"completion_envelope={result.completion_envelope!r} "
        f"recovered outcome={outcome!r}"
    )


# ---------------------------------------------------------------------------
# TARGET tier -- honored-OR-documented-absent, non-blocking
# ---------------------------------------------------------------------------

#: Design decision 3's TARGET list, verbatim.
TARGET_CAPABILITIES: tuple[str, ...] = (
    "max_turns",
    "llm_provider",
    "user_instructions",
    "reasoning_effort",
    "provider_preferences_precedence",
    "approvals_posture",
    "telemetry_session_id",
    "child_spawn_delegate",
    "tools_passthrough",
)

_USER_INSTRUCTIONS_MARKER = "WPK-TARGET-USER-INSTRUCTIONS-9d2e"

#: A plausible probe config value per capability. Values are deliberately
#: mundane (nothing that should ever crash a conforming worker) -- the point
#: of the TARGET tier is presence/absence bookkeeping, not stress-testing.
_PROBE_CONFIG: dict[str, dict[str, Any]] = {
    "max_turns": {"max_turns": 1},
    "llm_provider": {"llm_provider": "anthropic"},
    "user_instructions": {"user_instructions": _USER_INSTRUCTIONS_MARKER},
    "reasoning_effort": {"reasoning_effort": "high"},
    # Precedence authority: loop-pipeline's own backend.py spawn_kwargs
    # comment ("Provider SELECTION ... flows via
    # orchestrator_config['llm_provider']" while provider_preferences exists
    # purely to carry the model) -- the task's cited "spec sec8.5" maps to
    # the Model Stylesheet's Application Order section in
    # contracts/external/attractor-spec-canonical.md, not to provider
    # precedence; no spec section actually governs provider_preferences
    # precedence today, so the REAL authority cited here is the backend.py
    # comment + each worker's own README ("gap 4" for loop-amplifier-agent).
    "provider_preferences_precedence": {"llm_provider": "anthropic"},
    "approvals_posture": {"approval_policy": "accept"},
    "telemetry_session_id": {},
    "child_spawn_delegate": {},
    "tools_passthrough": {},
}


def _not_silently_dropped(capability: str, warnings: list[str]) -> None:
    """An UNDECLARED capability must not itself admit, via a logged warning,
    that it silently ignored the config key -- if a worker can't honor a
    capability, ``declared_absences`` is the honest channel, not a buried
    log line nobody reads.
    """
    drop_words = ("ignor", "unsupported", "unknown", "not honored", "not supported")
    for w in warnings:
        lowered = w.lower()
        names_capability = (
            capability in lowered or capability.replace("_", " ") in lowered
        )
        if names_capability and any(word in lowered for word in drop_words):
            raise AssertionError(
                f"capability {capability!r} was silently dropped (saw "
                f"warning: {w!r}) without being declared absent -- add "
                f"it to declared_absences instead of warn-and-drop"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", TARGET_CAPABILITIES)
async def test_target_capability_honored_or_declared_absent(
    worker_harness: WorkerHarness, capability: str
) -> None:
    """TARGET tier -- honored-OR-documented-absent, non-blocking.

    Mechanism (design decision 3): each TARGET test consults the harness's
    own ``declared_absences``. A declared absence SKIPS (visibly, naming the
    capability in the skip reason) -- never silent. An UNDECLARED capability
    must at minimum survive being asked for (no crash) and must not itself
    admit, via a logged warning, that it was silently ignored.

    Honest scope note (judgment call, disclosed in the kit README and PR
    report): a generic, worker-agnostic harness (~5 members, see
    ``protocol.py``) cannot deeply verify EVERY capability's semantics
    without reaching into worker-specific internals that differ across
    workers by design -- that depth is each worker's OWN responsibility
    (e.g. ``loop-amplifier-agent/tests/test_v2_capabilities.py`` already
    asserts exact forwarding for these same keys against its OWN fakes).
    ``user_instructions`` is the one capability this generic suite CAN
    verify behaviorally (it is observable at the same
    ``messages_sent_to_provider`` seam M2 already uses); the rest get the
    shallower not-silently-dropped smoke check.
    """
    if capability in worker_harness.declared_absences:
        pytest.skip(
            f"declared absent by worker harness: {capability!r} "
            "(see worker_harness.declared_absences)"
        )

    config = dict(_PROBE_CONFIG.get(capability, {}))
    result = await worker_harness.run_turn(
        f"TARGET-tier probe turn for capability={capability}.", None, config
    )
    assert isinstance(result, TurnResult)

    if capability == "user_instructions":
        contents = _message_contents(result.messages_sent_to_provider)
        assert any(_USER_INSTRUCTIONS_MARKER in c for c in contents), (
            "user_instructions marker never reached the model boundary: "
            f"{result.messages_sent_to_provider!r}"
        )

    _not_silently_dropped(capability, result.warnings)
