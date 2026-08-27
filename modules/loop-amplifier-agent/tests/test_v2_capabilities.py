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

from typing import Any

import amplifier_module_loop_amplifier_agent as laa
import pytest

from ._fakes import CapturingHooks, FakeSessionCoordinator, make_fake_deps


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
        outcome_to_set={"status": "success"},
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
    captured = _install_fake_deps(
        monkeypatch, reply_text="", outcome_to_set={"status": "success"}
    )
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
        outcome_to_set={"status": "success"},
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
    captured = _install_fake_deps(
        monkeypatch, reply_text="", outcome_to_set={"status": "success"}
    )
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
async def test_approval_defaults_to_deny(monkeypatch: pytest.MonkeyPatch):
    """RED-proof: the v1 adapter registered a hardcoded stub that ALWAYS
    returned ``{"action": "accept"}`` regardless of config. Against v1, this
    assertion (expecting a DECLINE with no config at all) fails. Post-fix,
    the safe default (``approval_policy`` unset -> "deny") makes the
    approval-gated tool call inside the child turn come back declined.
    """
    captured = _install_fake_deps(
        monkeypatch, reply_text="", outcome_to_set={"status": "success"}
    )

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert captured["session"].last_approval_response == {"action": "decline"}


@pytest.mark.asyncio
async def test_approval_policy_accept_forwards_accept(monkeypatch: pytest.MonkeyPatch):
    """Opting in via ``approval_policy: "accept"`` really does forward an
    accept decision through the REAL WireApprovalProvider/ctx.approval.request
    seam (not a bypassed hardcoded stub) -- and logs loudly (see next test).
    """
    captured = _install_fake_deps(
        monkeypatch, reply_text="", outcome_to_set={"status": "success"}
    )

    orchestrator = laa.AmplifierAgentOrchestrator(
        coordinator=None, config={"approval_policy": "accept"}
    )
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert captured["session"].last_approval_response == {"action": "accept"}


@pytest.mark.asyncio
async def test_approval_policy_accept_logs_a_loud_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """The dangerous opt-in must be loud, every time, not a silent default."""
    _install_fake_deps(monkeypatch, reply_text="", outcome_to_set={"status": "success"})

    orchestrator = laa.AmplifierAgentOrchestrator(
        coordinator=None, config={"approval_policy": "accept"}
    )
    hooks = CapturingHooks()
    with caplog.at_level("WARNING", logger="amplifier_module_loop_amplifier_agent"):
        await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert any("AUTO-APPROVED" in record.message for record in caplog.records), (
        "approval_policy='accept' must log a loud warning every turn"
    )


@pytest.mark.asyncio
async def test_approval_policy_invalid_value_fails_closed_to_deny(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """An unrecognized approval_policy value must never fail OPEN (accept) --
    it fails closed to "deny" and logs why.
    """
    captured = _install_fake_deps(
        monkeypatch, reply_text="", outcome_to_set={"status": "success"}
    )

    orchestrator = laa.AmplifierAgentOrchestrator(
        coordinator=None, config={"approval_policy": "sure-why-not"}
    )
    hooks = CapturingHooks()
    with caplog.at_level("WARNING", logger="amplifier_module_loop_amplifier_agent"):
        await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    assert captured["session"].last_approval_response == {"action": "decline"}
    assert any("unknown approval_policy" in record.message for record in caplog.records)


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
    captured = _install_fake_deps(
        monkeypatch, reply_text="", outcome_to_set={"status": "success"}
    )
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
    captured = _install_fake_deps(
        monkeypatch, reply_text="", outcome_to_set={"status": "success"}
    )
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
    captured = _install_fake_deps(
        monkeypatch, reply_text="", outcome_to_set={"status": "success"}
    )
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
    captured = _install_fake_deps(
        monkeypatch, reply_text="", outcome_to_set={"status": "success"}
    )
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
    captured = _install_fake_deps(
        monkeypatch, reply_text="", outcome_to_set={"status": "success"}
    )

    orchestrator = laa.AmplifierAgentOrchestrator(coordinator=None, config={})
    hooks = CapturingHooks()
    await orchestrator.execute("do the work", None, {}, {}, hooks, coordinator=None)

    calls = captured["session"].coordinator.hooks.default_fields_calls
    assert len(calls) == 1
    stamped = calls[0]
    assert isinstance(stamped.get("session_id"), str) and stamped["session_id"]
    assert stamped["turn_id"] == "turn-1"
