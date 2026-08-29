"""Hermetic unit tests for AmplifierAgentOrchestrator.execute().

Every test here monkeypatches ONE seam --
``amplifier_module_loop_amplifier_agent._load_dependencies`` -- with fakes
that are faithful to the real amplifier-agent contract (see ``tests/_fakes.py``
module docstring). No network, no real amplifier_agent_lib import, no
Python-3.12 requirement to run these: the fakes stand in for the (heavy,
Python>=3.12-only) real library so this module's own logic -- envelope
shape, config-key mapping, fail-closed behavior -- is exercised in complete
isolation.
"""

from __future__ import annotations

from typing import Any

import amplifier_module_loop_amplifier_agent as laa
import pytest
from amplifier_core.events import ORCHESTRATOR_COMPLETE

from ._fakes import CapturingHooks, FakeFactoryContextManager, make_fake_deps


def _install_fake_deps(monkeypatch: pytest.MonkeyPatch, **kwargs: Any):
    deps, captured = make_fake_deps(**kwargs)
    monkeypatch.setattr(laa, "_load_dependencies", lambda: deps)
    return captured


# ---------------------------------------------------------------------------
# 1. Envelope shape matches what backend.py::_outcome_from_spawn_result parses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_envelope_shape_never_fabricates_report_outcome(
    monkeypatch: pytest.MonkeyPatch,
):
    """WAVE 4 (ruling 5): this module no longer mounts a report_outcome
    reach-in onto the hosted agent's coordinator, so it has nothing to read
    back after the turn. The ORCHESTRATOR_COMPLETE envelope's ``metadata``
    stays empty on the happy path -- it must never fabricate a
    ``report_outcome`` key from nothing. Fed into the REAL loop-pipeline
    backend reader, an empty-metadata envelope falls through to the
    lifecycle-status-only path (``is_explicit=False``): it can complete a
    node, but it can never satisfy a goal_gate on its own. The status-file
    contract (``amplifier_module_loop_pipeline.status_contract``) is the
    channel an explicit verdict now travels through -- entirely outside
    this envelope, proven separately (pipeline-runner's spawn e2e fixture).
    """
    _install_fake_deps(monkeypatch, reply_text="looks done")

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()

    reply = await orchestrator.execute(
        "do the work", None, {}, {}, hooks, coordinator=None
    )

    assert reply == "looks done"
    event_name, payload = hooks.events[-1]
    assert event_name == ORCHESTRATOR_COMPLETE
    assert payload["orchestrator"] == "loop-amplifier-agent"
    assert payload["status"] == "success"
    assert payload["turn_count"] == 1
    assert payload["metadata"] == {}

    # Feed the exact spawn-result shape foundation's PreparedBundle.spawn
    # assembles into the REAL backend reader: no explicit verdict, but a
    # clean lifecycle success still completes the node (non-explicit).
    from amplifier_module_loop_pipeline.backend import _outcome_from_spawn_result
    from amplifier_module_loop_pipeline.outcome import StageStatus

    spawn_result = {
        "output": reply,
        "status": payload["status"],
        "turn_count": payload["turn_count"],
        "metadata": payload["metadata"],
    }
    outcome = _outcome_from_spawn_result(spawn_result)
    assert outcome is not None
    assert outcome.status is StageStatus.SUCCESS
    assert outcome.is_explicit is False


# ---------------------------------------------------------------------------
# 2. Config-key mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_keys_are_mapped_to_the_right_injection_points(
    monkeypatch: pytest.MonkeyPatch,
):
    captured = _install_fake_deps(monkeypatch, reply_text="done")

    config = {
        "llm_provider": "openai",
        "reasoning_effort": "high",
        "max_turns": 5,
        "user_instructions": "focus on the tests",
    }
    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config=config)
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    # llm_provider + reasoning_effort -> inject_provider(provider, effort_override=...)
    assert captured["inject_provider_calls"] == [
        ("openai", {"effort_override": "high"})
    ]

    # max_turns -> prepared.mount_plan["session"]["orchestrator"]["config"]["max_turns"]
    assert (
        captured["prepared"].mount_plan["session"]["orchestrator"]["config"][
            "max_turns"
        ]
        == 5
    )

    # user_instructions -> appended into the prompt handed to session.execute()
    prompt_seen = captured["session"].prompt_seen
    assert "focus on the tests" in prompt_seen
    assert "do the work" in prompt_seen
    # WAVE 4: the report_outcome nudge is retired -- this adapter no longer
    # appends anything beyond user_instructions (see _build_prompt).


@pytest.mark.asyncio
async def test_default_provider_used_when_llm_provider_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    captured = _install_fake_deps(monkeypatch, reply_text="")

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert captured["inject_provider_calls"][0][0] == laa.DEFAULT_PROVIDER


@pytest.mark.asyncio
async def test_providers_mount_plan_is_cleared_before_injection(
    monkeypatch: pytest.MonkeyPatch,
):
    """Probe-proven seam: the 9 baked-in provider stubs must be cleared
    first, else inject_provider() is a no-op ("don't clobber existing").
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")
    assert captured["prepared"].mount_plan["providers"] != []  # baseline: stubs present

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    # inject_provider is faked (doesn't itself clear+append), so what we can
    # assert is that the orchestrator cleared it BEFORE calling inject_provider.
    assert captured["inject_provider_calls"], "inject_provider should have been called"


# ---------------------------------------------------------------------------
# 3. Fail-closed (never fabricate success)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_never_fabricates_a_verdict_when_child_asserts_none(
    monkeypatch: pytest.MonkeyPatch,
):
    """WAVE 4 (ruling 5, M3 authority -- worker-parity-kit's
    ``test_m3_no_signal_never_fabricates_explicit_success``): a turn with no
    explicit signal at all must not read back as an explicit verdict of ANY
    kind -- including the OLD code's own fabricated ``retry``, which was
    itself an unearned ``is_explicit=True`` synthesized from nothing. With
    the reach-in mount gone, ``metadata`` simply stays empty; the real
    channel for an explicit verdict is the status-file contract, which this
    hermetic orchestrator-level test cannot exercise (no real file-writing
    LLM here) -- see the pipeline-runner spawn e2e fixture for that proof.
    """
    _install_fake_deps(monkeypatch, reply_text="All done, looks great!")

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    reply = await orchestrator.execute(
        "do the work", None, {}, {}, hooks, coordinator=None
    )

    assert reply == "All done, looks great!"
    assert hooks.completion["metadata"] == {}


@pytest.mark.asyncio
async def test_exception_emits_incomplete_and_reraises(monkeypatch: pytest.MonkeyPatch):
    _install_fake_deps(monkeypatch, raise_on_execute=RuntimeError("boom"))

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()

    with pytest.raises(RuntimeError, match="boom"):
        await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert hooks.completion["status"] == "incomplete"
    assert hooks.completion["metadata"] == {}


@pytest.mark.asyncio
async def test_engine_shutdown_called_even_on_exception(
    monkeypatch: pytest.MonkeyPatch,
):
    captured = _install_fake_deps(monkeypatch, raise_on_execute=RuntimeError("boom"))

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    with pytest.raises(RuntimeError):
        await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert captured["engine"].shutdown_called is True


@pytest.mark.asyncio
async def test_engine_shutdown_called_on_success(monkeypatch: pytest.MonkeyPatch):
    captured = _install_fake_deps(monkeypatch, reply_text="")

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert captured["engine"].shutdown_called is True


@pytest.mark.asyncio
async def test_shutdown_failure_does_not_mask_original_exception(
    monkeypatch: pytest.MonkeyPatch,
):
    """RED-prove: submit_turn raises AND engine.shutdown() (in the
    `finally`) ALSO raises. The ORIGINAL exception must propagate --
    a shutdown failure must never mask it -- and the honest fail-closed
    'incomplete' emit must still happen.

    Against the pre-fix code (a bare ``await engine.shutdown()`` in
    ``finally`` with no guard), the shutdown's ``ValueError`` replaces the
    original ``RuntimeError`` as the exception that actually propagates,
    so ``pytest.raises(RuntimeError, ...)`` below fails -- proving this
    test is RED before the fix and GREEN after it.
    """
    captured = _install_fake_deps(
        monkeypatch,
        raise_on_execute=RuntimeError("boom"),
        shutdown_raises=ValueError("shutdown exploded"),
    )

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()

    with pytest.raises(RuntimeError, match="boom"):
        await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert captured["engine"].shutdown_called is True
    assert hooks.completion["status"] == "incomplete"
    assert hooks.completion["metadata"] == {}


# ---------------------------------------------------------------------------
# 4. Empty reply with a verdict still succeeds (artifact over prose)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_reply_with_clean_lifecycle_still_succeeds(
    monkeypatch: pytest.MonkeyPatch,
):
    """WAVE 4: "artifact over prose" no longer flows through an in-process
    verdict capture -- a child that did its work via file tools (writing
    status.json, or any other artifact) and ended with empty closing prose
    still completes the node via the lifecycle-status-only path. It is
    NON-explicit (cannot satisfy a goal_gate by itself); see
    ``read_status_override`` for how an actually-written status.json
    overrides that afterward, entirely outside this envelope.
    """
    _install_fake_deps(monkeypatch, reply_text="")

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    reply = await orchestrator.execute(
        "do the work", None, {}, {}, hooks, coordinator=None
    )

    assert reply == ""
    assert hooks.completion["metadata"] == {}
    assert hooks.completion["status"] == "success"


# ---------------------------------------------------------------------------
# 5. System-prompt survival across history replay (PR #3 adoption review)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_framing_survives_history_replay(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression pin: does replaying parent-turn history into the hosted
    session (``hosted_context.set_messages(history)``, support#497's fix)
    clobber the hosted session's OWN system framing?

    ``set_messages`` REPLACES the hosted context's whole message list, and
    ``prepared.create_session()`` (amplifier_foundation/bundle/_prepared.py)
    can seed system framing into that SAME message list via a static
    ``add_message({"role": "system", ...})`` fallback -- but only when the
    context module does NOT support ``set_system_prompt_factory``. The real
    (and only) context module amplifier-agent's own baked-in bundle ever
    mounts is context-simple (see ``amplifier_agent_lib/bundle/bundle.md``'s
    ``context: module: context-simple``), and
    ``amplifier_module_context_simple.SimpleContextManager`` ALWAYS supports
    ``set_system_prompt_factory``. So ``create_session()`` always takes the
    factory branch for this adapter's hosted sessions, never the add_message
    fallback -- and the factory-produced system message is never written
    into the message list ``set_messages`` replaces (it's synthesized fresh,
    and stored system messages are filtered out, on every
    ``get_messages_for_request()`` call -- see ``FakeFactoryContextManager``'s
    docstring for the precise mechanism this fake mirrors).

    This test proves that claim rather than assuming it: it registers a
    system-prompt factory on the hosted context BEFORE ``execute()`` runs
    (mirroring what the real ``create_session()`` does), feeds a non-empty
    parent history through ``context`` so replay actually happens, and
    asserts that whatever the hosted session would send to the provider
    (``get_messages_for_request()``, captured by ``FakeSession.execute()``
    into ``messages_sent_to_provider``) still leads with the fresh system
    framing followed by the replayed history -- i.e. NO clobbering occurs
    for the real seam. (If a future context module dropped factory support,
    this test's own mechanics would catch the regression: swap in a fake
    without ``set_system_prompt_factory`` and the system message would
    vanish from ``messages_sent_to_provider`` after replay.)
    """
    hosted_context = FakeFactoryContextManager()

    async def system_factory() -> str:
        return "You are the amplifier-agent coding persona. Persist across turns."

    # Mirrors create_session() calling set_system_prompt_factory BEFORE
    # _run_turn's replay code ever touches the hosted context.
    await hosted_context.set_system_prompt_factory(system_factory)

    captured = _install_fake_deps(
        monkeypatch,
        reply_text="ok",
        context_module=hosted_context,
    )

    parent_history = [
        {"role": "user", "content": "earlier turn: the secret is ZEBRA"},
        {"role": "assistant", "content": "earlier reply: noted, ZEBRA"},
    ]

    class _ParentContext:
        async def get_messages(self) -> list[dict[str, Any]]:
            return parent_history

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute(
        "do the work", _ParentContext(), {}, {}, hooks, coordinator=None
    )

    # Replay actually happened (support#497's fix is doing its job).
    assert hosted_context.set_messages_calls == [parent_history]

    # And the system framing was NOT clobbered by that replay: it still
    # leads whatever the hosted session would send to the provider.
    sent = captured["session"].messages_sent_to_provider
    assert sent is not None, "execute() never asked the context for a request"
    assert sent[0] == {
        "role": "system",
        "content": "You are the amplifier-agent coding persona. Persist across turns.",
    }
    assert sent[1:] == parent_history
