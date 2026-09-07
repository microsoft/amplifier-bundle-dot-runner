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
        "Do some work, but never assert any verdict of any kind.",
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
    "telemetry_provider_identity",
    "child_spawn_delegate",
    "tools_passthrough",
)

#: The five keys BOTH workers' provider events must carry, in one vocabulary
#: (EXTENSIONS.md Sec 36 addendum 3, 2026-09-07). Every key must be PRESENT;
#: `provider_instance` and `reasoning_effort` are legitimately `None` (this
#: mount is not an instance / the node declared no effort), and the honest
#: report of "not applicable" is exactly what makes them readable at all.
PROVIDER_IDENTITY_KEYS: tuple[str, ...] = (
    "provider",
    "provider_module",
    "provider_instance",
    "model",
    "reasoning_effort",
)

#: The identity the `telemetry_provider_identity` probe declares and then
#: expects to read back off the turn's own provider events.
_IDENTITY_PROBE_PROVIDER = "anthropic"
_IDENTITY_PROBE_EFFORT = "high"

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
    "telemetry_provider_identity": {
        "llm_provider": _IDENTITY_PROBE_PROVIDER,
        "reasoning_effort": _IDENTITY_PROBE_EFFORT,
    },
    "child_spawn_delegate": {},
    "tools_passthrough": {},
}


def _assert_provider_identity(result: TurnResult) -> None:
    """Every provider event this turn emitted NAMES who served the call.

    The one TARGET row besides ``user_instructions`` that this generic suite
    can verify BEHAVIORALLY, and deliberately so: the shallow
    not-silently-dropped bar is what let the sibling ``telemetry_session_id``
    row stay green through three separate end-to-end telemetry breaks (see
    this kit's README). A row that observes nothing cannot fail.

    ``provider_events is None`` -- the harness cannot see the stream at all
    -- is NOT a silent pass: it fails, naming ``declared_absences`` as the
    honest channel. A harness that CAN see the stream but saw no provider
    events likewise fails: a turn that reached a model and emitted no
    provider event is precisely the unmeasurable-run defect this row exists
    to catch.

    What is asserted is PRESENCE and CONSISTENCY, never a specific vendor:
    all five keys on every ``provider:response``, one identity across them,
    and the declared ``reasoning_effort`` echoed back -- the one identity
    fact that reaches the model while appearing on no event a worker emits
    by default.

    SCOPE: ``provider:response`` ONLY, deliberately. That is the event a
    run's evidence is actually read off (it is where usage and cost ride),
    and it is the only one both workers own. ``loop-agent`` emits its own
    ``provider:request`` and carries the same identity there as a bonus;
    ``loop-amplifier-agent`` does not emit that event at all -- the hosted
    runtime does -- and enriching it would mean re-emitting, which would
    double every row's call count. Demanding identity on an event a worker
    cannot write without corrupting the call count would make the honest
    implementation fail the parity row. A request that DOES carry identity
    is still checked for agreement with the response below.
    """
    events = result.provider_events
    assert events is not None, (
        "telemetry_provider_identity: this harness reports no provider "
        "event stream at all (provider_events is None), so the row cannot "
        "verify that provider events name who served the call. Expose the "
        "turn's provider:request/provider:response payloads, or declare "
        "'telemetry_provider_identity' in declared_absences -- an "
        "unobservable telemetry row is how three end-to-end telemetry "
        "breaks stayed green (see this kit's README)."
    )
    provider_events = [
        e
        for e in events
        if isinstance(e, dict) and str(e.get("event", "")) == "provider:response"
    ]
    assert provider_events, (
        "telemetry_provider_identity: the turn emitted no provider:response "
        f"at all (saw: {[e.get('event') for e in events]}). A run whose "
        "provider:response events are absent is exactly the unmeasurable run "
        "this row exists to catch -- two node-matrix rows PASSed their gate "
        "while reporting no calls, no tokens and no cost."
    )

    for event in provider_events:
        missing = [k for k in PROVIDER_IDENTITY_KEYS if k not in event]
        assert not missing, (
            f"telemetry_provider_identity: {event.get('event')!r} is missing "
            f"identity key(s) {missing} (carried: {sorted(event)}). All five "
            f"of {list(PROVIDER_IDENTITY_KEYS)} must be PRESENT -- a None "
            "value is an honest 'not applicable', an absent key is a reader "
            "left to infer."
        )
        assert event.get("reasoning_effort") == _IDENTITY_PROBE_EFFORT, (
            "telemetry_provider_identity: the probe declared "
            f"reasoning_effort={_IDENTITY_PROBE_EFFORT!r} and the event "
            f"reports {event.get('reasoning_effort')!r}. Effort reaches the "
            "model but rides on no event by default, so a run's own evidence "
            "could not tell one price/latency regime from another."
        )
        assert event.get("provider_module"), (
            "telemetry_provider_identity: "
            f"{event.get('event')!r} names no provider_module "
            f"({event.get('provider_module')!r}). The family that served is "
            "the fact a misroute is read off; the mount key alone is the "
            "address that was ASKED for."
        )

    identities = {
        tuple(str(e.get(k)) for k in PROVIDER_IDENTITY_KEYS) for e in provider_events
    }
    assert len(identities) == 1, (
        "telemetry_provider_identity: provider:response events disagree "
        f"about who served this turn: {sorted(identities)}. One turn on one "
        "mounted provider must report ONE identity, or a reader cannot join "
        "a cost back to who was paid for it."
    )

    # A worker that ALSO carries identity on its own provider:request (only
    # loop-agent does today) must agree with the response. An absent key
    # there is the documented worker-shape difference, not a failure -- see
    # this function's SCOPE note.
    served = next(iter(identities))
    for event in events:
        if not isinstance(event, dict) or event.get("event") != "provider:request":
            continue
        carried = [k for k in PROVIDER_IDENTITY_KEYS if k in event]
        if not carried:
            continue
        mismatched = {
            k: (event.get(k), served[PROVIDER_IDENTITY_KEYS.index(k)])
            for k in carried
            if str(event.get(k)) != served[PROVIDER_IDENTITY_KEYS.index(k)]
        }
        assert not mismatched, (
            "telemetry_provider_identity: provider:request and "
            f"provider:response disagree on {mismatched} (request value, "
            "response value). A worker that names an identity on both ends "
            "of one call must name the SAME one."
        )


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
    ``user_instructions`` and ``telemetry_provider_identity`` are the two
    capabilities this generic suite CAN verify behaviorally (each is
    observable at a seam the protocol already exposes --
    ``messages_sent_to_provider`` for the first, ``provider_events`` for the
    second); the rest get the shallower not-silently-dropped smoke check.
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

    if capability == "telemetry_provider_identity":
        _assert_provider_identity(result)

    _not_silently_dropped(capability, result.warnings)
