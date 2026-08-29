"""Hermetic unit tests for the five v2 capability closures.

Companion to ``test_orchestrator.py``: same seam
(``amplifier_module_loop_amplifier_agent._load_dependencies``), same fakes
(``tests/_fakes.py``), extended here with the doubles the v2 work needed
(``FakeHookRegistry``, ``FakeApprovalOverride``, ``FakeWireApprovalProvider``,
``coordinator.config``, ``spawn_sub_session``, ``prepare_bundle_for_session``,
``resolve_workspace``). See each fake's docstring in ``_fakes.py`` for how it
mirrors the real amplifier-agent contract.

Each test class below corresponds to one of the five v1 capability gaps
closed in this change. Gaps 2 (session.spawn), 3 (approvals), and 5
(telemetry) are RED-proof: the docstring on the relevant test states what
the v1 adapter did instead, and the assertion is written so it fails against
that v1 behavior and passes against the v2 fix (verified by hand against
the pre-fix source before the fix landed).
"""

from __future__ import annotations

import logging
from typing import Any

import amplifier_module_loop_amplifier_agent as laa
import pytest

from ._fakes import (
    CapturingHooks,
    FakeContextManager,
    FakeSessionCoordinator,
    make_fake_deps,
)


def _install_fake_deps(monkeypatch: pytest.MonkeyPatch, **kwargs: Any):
    deps, captured = make_fake_deps(**kwargs)
    monkeypatch.setattr(laa, "_load_dependencies", lambda: deps)
    return captured


# ---------------------------------------------------------------------------
# Gap 1: prepare_bundle_for_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_bundle_for_session_called_with_resolved_workspace(
    monkeypatch: pytest.MonkeyPatch,
):
    """No config: prepare_bundle_for_session is still called (skills/modes +
    workspace seed apply unconditionally), with host_config=None (no natural
    CLI --config-file source for an embedded pipeline node) and the
    resolve_workspace fallback slug.
    """
    captured = _install_fake_deps(
        monkeypatch,
        reply_text="",
        resolved_workspace="fallback-slug",
    )

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    calls = captured["prepare_bundle_for_session_calls"]
    assert len(calls) == 1
    assert calls[0] == {"host_config": None, "workspace": "fallback-slug"}

    # And the child session's own coordinator.config was written with the
    # SAME resolved workspace (D5-equivalent belt-and-suspenders, mirrors
    # make_turn_handler).
    assert captured["session"].coordinator.config["workspace"] == "fallback-slug"
    assert captured["session"].coordinator.config["project_slug"] == "fallback-slug"


@pytest.mark.asyncio
async def test_prepare_bundle_for_session_honors_explicit_workspace_and_host_config(
    monkeypatch: pytest.MonkeyPatch,
):
    """orchestrator_config.workspace / .host_config (v2, optional/advanced)
    are forwarded verbatim -- the CLI's --workspace / --config analogues.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")
    host_config = {"mcp": {"configPath": "/tmp/mcp.json"}}

    orchestrator = laa.AmplifierAgentOrchestrator(
        coordinator=None,
        config={"workspace": "my-explicit-workspace", "host_config": host_config},
    )
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    calls = captured["prepare_bundle_for_session_calls"]
    assert calls == [{"host_config": host_config, "workspace": "my-explicit-workspace"}]


# ---------------------------------------------------------------------------
# Gap 2: session.spawn (RED-proof)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_spawn_registered_and_forwards_to_delegate(
    monkeypatch: pytest.MonkeyPatch,
):
    """RED-proof: against the v1 adapter, ``session.coordinator`` NEVER had a
    ``"session.spawn"`` capability registered at all -- the delegate tool's
    ``spawn_fn(**kwargs)`` call inside the child turn would fail with a
    missing-capability error. Post-fix, the capability is present, callable,
    and forwards to the REAL ``spawn_sub_session`` contract (agent_configs
    default + parent_session pinned to THIS turn's session).
    """
    captured = _install_fake_deps(
        monkeypatch,
        reply_text="",
        agents_mount_plan={
            "explorer": {"name": "explorer", "source_path": "/fake/explorer.md"}
        },
    )

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    session = captured["session"]
    spawn_fn = session.coordinator.capabilities.get("session.spawn")
    assert spawn_fn is not None, (
        "session.spawn was never registered -- v1 regression: the child's "
        "delegate tool would break"
    )

    result = await spawn_fn(agent_name="self", instruction="do a subtask")

    assert result == {
        "output": "child delegate done",
        "session_id": "fake-child-session-1",
        "status": "success",
        "turn_count": 1,
        "metadata": {},
    }
    spawn_calls = captured["spawn_sub_session_calls"]
    assert len(spawn_calls) == 1
    assert spawn_calls[0]["parent_session"] is session
    assert spawn_calls[0]["agent_name"] == "self"
    # agent_configs defaults to the cold-path hydration when the caller
    # (delegate tool) doesn't pass its own.
    assert "explorer" in spawn_calls[0]["agent_configs"]


@pytest.mark.asyncio
async def test_session_spawn_lets_caller_override_agent_configs(
    monkeypatch: pytest.MonkeyPatch,
):
    """A caller-supplied ``agent_configs`` kwarg is NOT clobbered (mirrors
    ``kw.setdefault(...)`` in both this module and the vendored handler).
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")
    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    spawn_fn = captured["session"].coordinator.capabilities["session.spawn"]
    custom_configs = {"custom-agent": {"instruction": "custom"}}
    await spawn_fn(
        agent_name="custom-agent", instruction="go", agent_configs=custom_configs
    )

    assert captured["spawn_sub_session_calls"][0]["agent_configs"] is custom_configs


# ---------------------------------------------------------------------------
# Gap 3: approvals (RED-proof)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_defaults_to_accept(monkeypatch: pytest.MonkeyPatch):
    """RED-proof: this pins the flipped default. Against the PRE-FLIP
    adapter (``DEFAULT_APPROVAL_POLICY = "deny"``), this assertion (expecting
    an ACCEPT with no config at all) fails -- the whole point of this test.
    Post-flip, the default (``approval_policy`` unset -> "accept") makes the
    approval-gated tool call inside the child turn come back accepted --
    worker parity with loop-agent, which has no approval system at all (see
    README's "Approvals" section for the full rationale).
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert captured["session"].last_approval_response == {"action": "accept"}


@pytest.mark.asyncio
async def test_approval_policy_accept_forwards_accept(monkeypatch: pytest.MonkeyPatch):
    """Explicit ``approval_policy: "accept"`` (same as the default, but
    spelled out) really does forward an accept decision through the REAL
    WireApprovalProvider/ctx.approval.request seam (not a bypassed hardcoded
    stub).
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")

    orchestrator = laa.AmplifierAgentOrchestrator(
        coordinator=None, config={"approval_policy": "accept"}
    )
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert captured["session"].last_approval_response == {"action": "accept"}


@pytest.mark.asyncio
async def test_approval_policy_deny_forwards_decline(monkeypatch: pytest.MonkeyPatch):
    """ "deny" remains available as opt-in hardening: it still forwards a
    real decline through the REAL WireApprovalProvider/ctx.approval.request
    seam when a pipeline author explicitly opts in.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")

    orchestrator = laa.AmplifierAgentOrchestrator(
        coordinator=None, config={"approval_policy": "deny"}
    )
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert captured["session"].last_approval_response == {"action": "decline"}


@pytest.mark.asyncio
async def test_approval_policy_accept_logs_at_info_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """The default, expected posture ("accept") logs exactly ONE line per
    ``execute()``, at INFO -- not a per-turn WARNING. Per-turn WARNING spam
    for the new default would be noise: the whole point of flipping the
    default was that auto-approval is no longer the dangerous, watch-out-for
    -this choice, it is the expected one (worker parity with loop-agent,
    which has no approval system at all).
    """
    _install_fake_deps(monkeypatch, reply_text="")

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    with caplog.at_level(logging.INFO, logger="amplifier_module_loop_amplifier_agent"):
        await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    policy_records = [r for r in caplog.records if "approval_policy=" in r.message]
    assert len(policy_records) == 1, (
        "expected exactly one approval-policy log line per execute(), got "
        f"{[r.message for r in policy_records]!r}"
    )
    assert policy_records[0].levelno == logging.INFO
    assert "'accept'" in policy_records[0].message


@pytest.mark.asyncio
async def test_approval_policy_deny_logs_at_warning_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """Opting into "deny" hardening is worth visibility -- logged at
    WARNING, but still exactly one line per ``execute()``, not per-turn
    spam.
    """
    _install_fake_deps(monkeypatch, reply_text="")

    orchestrator = laa.AmplifierAgentOrchestrator(
        coordinator=None, config={"approval_policy": "deny"}
    )
    hooks = CapturingHooks()
    with caplog.at_level(logging.INFO, logger="amplifier_module_loop_amplifier_agent"):
        await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    policy_records = [r for r in caplog.records if "approval_policy=" in r.message]
    assert len(policy_records) == 1, (
        "expected exactly one approval-policy log line per execute(), got "
        f"{[r.message for r in policy_records]!r}"
    )
    assert policy_records[0].levelno == logging.WARNING
    assert "'deny'" in policy_records[0].message


@pytest.mark.asyncio
async def test_approval_policy_invalid_value_warns_and_uses_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """An unrecognized approval_policy value must never silently brick every
    approval-gated action in a headless pipeline (the OLD unknown -> "deny"
    fail-closed behavior). It warns loudly, naming the bad value, and then
    uses the default ("accept") -- the graph's own evidence gates and budget
    walls are the safety net for a bad work product, not a bricked worker.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")

    orchestrator = laa.AmplifierAgentOrchestrator(
        coordinator=None, config={"approval_policy": "sure-why-not"}
    )
    hooks = CapturingHooks()
    with caplog.at_level("WARNING", logger="amplifier_module_loop_amplifier_agent"):
        await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert captured["session"].last_approval_response == {"action": "accept"}
    assert any(
        "unknown approval_policy" in record.message and "sure-why-not" in record.message
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Gap 4: provider_preferences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_preferences_honored_when_llm_provider_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    """No explicit llm_provider: the parent's resolved provider_preferences
    (recovered from coordinator.config["providers"] -- see
    ``_resolve_parent_provider_preference``'s docstring for why that's the
    real extraction seam) wins outright, provider AND model.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")
    parent_coordinator = FakeSessionCoordinator()
    parent_coordinator.config = {
        "providers": [
            {
                "module": "provider-openai",
                "config": {"priority": 0, "default_model": "gpt-5-mini"},
            }
        ]
    }

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute(
        "do the work", None, {}, {}, hooks, coordinator=parent_coordinator
    )

    assert captured["inject_provider_calls"] == [
        ("openai", {"effort_override": None, "model_override": "gpt-5-mini"})
    ]


@pytest.mark.asyncio
async def test_explicit_llm_provider_wins_selection_and_honors_matching_model(
    monkeypatch: pytest.MonkeyPatch,
):
    """Explicit llm_provider selects the provider; the parent preference's
    model is honored because it names that SAME provider.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")
    parent_coordinator = FakeSessionCoordinator()
    parent_coordinator.config = {
        "providers": [
            {
                "module": "provider-anthropic",
                "config": {"priority": 0, "default_model": "claude-haiku-4-5"},
            }
        ]
    }

    orchestrator = laa.AmplifierAgentOrchestrator(
        coordinator=None, config={"llm_provider": "anthropic"}
    )
    hooks = CapturingHooks()
    await orchestrator.execute(
        "do the work", None, {}, {}, hooks, coordinator=parent_coordinator
    )

    assert captured["inject_provider_calls"] == [
        ("anthropic", {"effort_override": None, "model_override": "claude-haiku-4-5"})
    ]


@pytest.mark.asyncio
async def test_mismatched_parent_preference_model_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
):
    """Explicit llm_provider selects a DIFFERENT provider than the parent's
    preference -- the mismatched model must never be forced onto the
    explicitly-selected provider (that would very likely be a nonsense
    model id for it). Provider selection still wins; the model is simply
    dropped, matching v1's existing (correct) no-model-override behavior.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")
    parent_coordinator = FakeSessionCoordinator()
    parent_coordinator.config = {
        "providers": [
            {
                "module": "provider-openai",
                "config": {"priority": 0, "default_model": "gpt-5-mini"},
            }
        ]
    }

    orchestrator = laa.AmplifierAgentOrchestrator(
        coordinator=None, config={"llm_provider": "anthropic"}
    )
    hooks = CapturingHooks()
    await orchestrator.execute(
        "do the work", None, {}, {}, hooks, coordinator=parent_coordinator
    )

    assert captured["inject_provider_calls"] == [
        ("anthropic", {"effort_override": None})
    ]


@pytest.mark.asyncio
async def test_no_parent_preference_and_no_llm_provider_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
):
    """No provider_preferences AND no llm_provider: DEFAULT_PROVIDER, no
    model override -- the pre-existing v1 default-provider behavior is
    unaffected by gap 4's new resolution path.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")
    parent_coordinator = FakeSessionCoordinator()  # config stays {}

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute(
        "do the work", None, {}, {}, hooks, coordinator=parent_coordinator
    )

    assert captured["inject_provider_calls"] == [
        (laa.DEFAULT_PROVIDER, {"effort_override": None})
    ]


# ---------------------------------------------------------------------------
# Gap 5: telemetry (RED-proof)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hooks_default_fields_stamped_with_session_and_turn_id(
    monkeypatch: pytest.MonkeyPatch,
):
    """RED-proof: the v1 adapter never called
    ``session.coordinator.hooks.set_default_fields(...)`` at all, so
    ``default_fields_calls`` stays empty against v1 -- this assertion fails
    pre-fix. Post-fix, it carries a non-empty session_id (minted fresh for
    this one-shot run, mirroring make_turn_handler's ephemeral-id fallback)
    and the turn's turn_id, so child-turn tool/llm/execution telemetry is no
    longer dropped by the context-intelligence LoggingHandler's
    empty-session-id check.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="")

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    calls = captured["session"].coordinator.hooks.default_fields_calls
    assert len(calls) == 1
    stamped = calls[0]
    assert isinstance(stamped.get("session_id"), str) and stamped["session_id"]
    assert stamped["turn_id"] == "turn-1"


# ---------------------------------------------------------------------------
# Gap 6: parent_messages / context continuity (RED-proof) -- support#497
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parent_history_replayed_into_hosted_session_context(
    monkeypatch: pytest.MonkeyPatch,
):
    """RED-proof (support#497): against the pre-fix adapter, execute()'s
    ``context`` parameter was accepted and silently DROPPED -- the hosted
    amplifier-agent session booted with an empty transcript, so fidelity="full"
    cross-node continuity never reached the model (RECALL: NONE every turn).
    Post-fix, the adapter reads the parent's already-seeded history off
    ``context.get_messages()`` and replays it into the hosted session's own
    mounted context via ``coordinator.get("context").set_messages(history)``
    BEFORE the turn executes.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="ok")

    history = [
        {"role": "user", "content": "The secret code is ZEBRA-42."},
        {"role": "assistant", "content": "Understood, I will remember it."},
    ]
    parent_context = FakeContextManager(history)

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute(
        "What was the secret code?",
        parent_context,
        {},
        {},
        hooks,
        coordinator=None,
    )

    hosted_context = captured["session"].coordinator.get("context")
    assert hosted_context.set_messages_calls == [history], (
        "parent history was not replayed into the hosted session context -- "
        "support#497 regression: fidelity='full' continuity would be inert"
    )
    # The hosted context holds the prior turns before the turn executes, so
    # the model actually sees them.
    assert hosted_context.messages == history


@pytest.mark.asyncio
async def test_no_parent_context_replays_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    """A fresh turn (``context=None`` -- the first-iteration / non-full-
    fidelity case) must not call ``set_messages`` at all: nothing to replay,
    and the hosted session starts clean.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="ok")

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    hosted_context = captured["session"].coordinator.get("context")
    assert hosted_context.set_messages_calls == []


@pytest.mark.asyncio
async def test_empty_parent_context_replays_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    """An empty-but-present parent context (a real spawn that carried no
    ``parent_messages``) is treated exactly like "no history": its
    ``get_messages()`` returns ``[]``, which must NOT trigger a
    ``set_messages`` replay onto the hosted session.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="ok")

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute(
        "do the work",
        FakeContextManager([]),
        {},
        {},
        hooks,
        coordinator=None,
    )

    hosted_context = captured["session"].coordinator.get("context")
    assert hosted_context.set_messages_calls == []


class _RaisingContext:
    """A context whose ``get_messages()`` raises -- proves the history read
    lives INSIDE the completion-envelope ``try`` (support#497 review finding).
    """

    async def get_messages(self) -> list[dict[str, Any]]:
        raise RuntimeError("context store unavailable")


class _ContextNoSetMessages:
    """A hosted-side context module exposing ``get_messages`` but NOT
    ``set_messages`` -- proves the replay skips (warns) instead of crashing.
    """

    async def get_messages(self) -> list[dict[str, Any]]:
        return []


@pytest.mark.asyncio
async def test_context_get_messages_failure_still_emits_incomplete(
    monkeypatch: pytest.MonkeyPatch,
):
    """If reading parent history raises, execute() must STILL emit the
    ``ORCHESTRATOR_COMPLETE(incomplete)`` envelope it owes the spawn boundary
    (and re-raise) -- the history read is inside the same try/except that
    protects every other exit path, and never fabricates a verdict on the way
    out.
    """
    _install_fake_deps(monkeypatch, reply_text="ok")
    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()

    with pytest.raises(RuntimeError):
        await orchestrator.execute(
            "do the work", _RaisingContext(), {}, {}, hooks, coordinator=None
        )

    assert hooks.completion.get("status") == "incomplete"
    # fail-closed: no fabricated report_outcome on the incomplete path.
    assert hooks.completion.get("metadata", {}) == {}


@pytest.mark.asyncio
async def test_hosted_context_without_set_messages_warns_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """When the hosted session's context module lacks ``set_messages``, the
    replay is skipped with a WARNING and the turn still completes -- mirrors the
    library's own is_resumed guard, and this file's convention of a caplog test
    for every warn-and-continue branch.
    """
    captured = _install_fake_deps(monkeypatch, reply_text="ok")
    # Hosted session's mounted context has no set_messages (set before execute).
    captured["session"].coordinator.context_module = _ContextNoSetMessages()

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    parent_context = FakeContextManager(
        [
            {"role": "user", "content": "prior turn"},
            {"role": "assistant", "content": "ok"},
        ]
    )

    with caplog.at_level(logging.WARNING):
        await orchestrator.execute(
            "do the work", parent_context, {}, {}, hooks, coordinator=None
        )

    assert hooks.completion.get("status") == "success"
    assert any(
        "does not expose set_messages" in rec.getMessage() for rec in caplog.records
    )


class _WiringCoordinator:
    """Minimal coordinator mirroring what
    ``amplifier_core._session_exec.run_orchestrator`` reads: ``get(...)`` for
    ``orchestrator``/``context``/``providers``/``tools``, plus ``.hooks``,
    ``.config``, and ``get_capability(...)``. Lets a test drive the REAL kernel
    caller boundary (``run_orchestrator``) instead of calling ``execute()``
    directly -- the analogue of loop-pipeline's
    ``test_handler_backend_continuity_wiring`` driving the real
    ``CodergenHandler.execute`` caller.
    """

    def __init__(self, orchestrator: Any, context: Any, hooks: Any) -> None:
        self._orchestrator = orchestrator
        self._context = context
        self.hooks = hooks
        self.config: dict[str, Any] = {}

    def get(self, name: str) -> Any:
        return {
            "orchestrator": self._orchestrator,
            "context": self._context,
        }.get(name)

    def get_capability(self, name: str) -> Any:
        return None


@pytest.mark.asyncio
async def test_history_flows_through_the_real_run_orchestrator_boundary(
    monkeypatch: pytest.MonkeyPatch,
):
    """Wiring proof at the REAL kernel caller boundary (support#497): drive
    ``amplifier_core._session_exec.run_orchestrator`` -- the exact function the
    kernel uses to invoke a mounted orchestrator -- with a context seeded the
    way foundation's ``PreparedBundle.spawn`` seeds it (via ``set_messages``,
    NOT the constructor), and assert the prior-turn history reaches the hosted
    session's context. Mirrors loop-pipeline's
    ``test_handler_backend_continuity_wiring``, which exists specifically
    because backend-level hermetic tests proved the mechanism but not the
    caller->callee wiring. RED against the pre-fix adapter (``execute`` drops
    ``context``).
    """
    from amplifier_core._session_exec import run_orchestrator

    captured = _install_fake_deps(monkeypatch, reply_text="ok")

    history = [
        {"role": "user", "content": "First instruction"},
        {"role": "assistant", "content": "First output"},
    ]
    # Seed exactly as foundation's PreparedBundle.spawn does: via set_messages,
    # not the constructor.
    parent_context = FakeContextManager()
    await parent_context.set_messages(history)

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    coordinator = _WiringCoordinator(orchestrator, parent_context, hooks)

    await run_orchestrator(coordinator, "recall the first instruction")

    hosted_context = captured["session"].coordinator.get("context")
    assert hosted_context.set_messages_calls == [history], (
        "history did not reach the hosted session through the real "
        "run_orchestrator boundary -- support#497 wiring regression"
    )
