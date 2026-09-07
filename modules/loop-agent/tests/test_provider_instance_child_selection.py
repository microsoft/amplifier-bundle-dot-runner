"""A node's provider INSTANCE id must SELECT that instance in the child.

EXTENSIONS.md Sec 36 addendum (2026-09-07).  Half the instance story lives in
the engine (resolve the id from settings, mount it under that id, route a
profile to it -- ``pipeline-runner``'s own tests).  This file pins the OTHER
half, inside the spawned worker: given a mount named after an instance id,
``loop-agent`` must

  1. select THAT mounted object for the completion -- not the first mounted
     provider, and not a same-family module mount that happens to be there;
  2. resolve a Layer-1 base prompt for it, from the family the instance is an
     instance OF (an instance id is an alias, and ``canonical_provider`` can
     read no family out of ``"terra"``);
  3. NAME what served the call on the persisted provider events.

Point 3 is not decoration.  Measured: node-matrix run ``20260907T081003Z``,
row ``ca-terra``.  The node declared ``llm_provider="terra"
llm_model="gpt-5.6-terra"``, preflight passed, the node ran to completion in
34.3 minutes across 142 provider calls -- and every one of those calls was
served by Anthropic Sonnet.  Establishing that took reading Anthropic-shaped
usage KEYS off the events (``cache_creation_input_tokens``) and fitting
$11.19 against two price tables, because the events named no provider and no
model at all.  A misroute that costs $11 and 34 minutes must be readable
directly off the stream, so ``provider``/``provider_module``/``model`` now
ride both provider events.

Hermetic: no network, no settings file, no credentials -- the instance is a
fake object mounted under a fake id.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_core.message_models import ChatResponse, Usage
from amplifier_module_loop_agent import AgentOrchestrator
from amplifier_module_loop_agent.agent_session import (
    mounted_provider_default_model,
    mounted_provider_module_name,
    provider_instance_id,
)

#: The instance id a node would declare as ``llm_provider``.  Deliberately
#: NOT a substring of any known provider family, so nothing can resolve it by
#: accident -- ``canonical_provider("fakeinst")`` is None.
INSTANCE_ID = "fakeinst"
INSTANCE_MODEL = "gpt-fake-9.9-fakeinst"


def _text_response(text: str = "done") -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


class _FakeMountedProvider:
    """A provider module instance, shaped like the real ones.

    ``name`` is a CLASS attribute on every provider module in this ecosystem
    (``OpenAIProvider.name == "openai"``) and states the module family
    independently of the key it is mounted under; ``default_model`` is
    resolved from mount config in ``__init__`` and is the model the call will
    actually use, since loop-agent sends no model on the request.
    """

    def __init__(self, name: str, default_model: str) -> None:
        self.name = name
        self.default_model = default_model
        self.complete = AsyncMock(return_value=_text_response())


def _make_hooks():
    hooks = MagicMock()
    hooks._emitted: list[tuple[str, dict]] = []

    async def _recording_emit(event: str, data: dict):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_recording_emit)
    return hooks


def _make_coordinator():
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()
    return coordinator


def _providers() -> dict[str, Any]:
    """The mount shape an instance-routed run actually produces.

    ``anthropic`` is FIRST on purpose: it is what a run mounts from the
    module table, it is what ``next(iter(providers))`` would pick, and it is
    exactly what the measured ``ca-terra`` run was silently served by.
    """
    return {
        "anthropic": _FakeMountedProvider("anthropic", "claude-sonnet-5"),
        "openai": _FakeMountedProvider("openai", "gpt-5"),
        INSTANCE_ID: _FakeMountedProvider("openai", INSTANCE_MODEL),
    }


async def _run(config: dict[str, Any], providers: dict[str, Any]):
    orch = AgentOrchestrator(coordinator=_make_coordinator(), config=config)
    hooks = _make_hooks()
    await orch.execute("hello", MagicMock(), providers, {}, hooks)
    return orch, hooks


# ---------------------------------------------------------------------------
# 1. Selection -- the instance, not the first mount, not the family mount
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instance_id_selects_that_instance_for_the_completion():
    providers = _providers()
    await _run(
        {"llm_provider": INSTANCE_ID, "max_tool_rounds_per_input": 1}, providers
    )

    assert providers[INSTANCE_ID].complete.await_count == 1, (
        "the node declared llm_provider=%r and that mounted instance must be "
        "the object the completion was made against" % INSTANCE_ID
    )
    assert providers["anthropic"].complete.await_count == 0, (
        "the measured ca-terra misroute: the first mounted provider served "
        "the call while the run reported the node's declared provider"
    )
    assert providers["openai"].complete.await_count == 0, (
        "the instance's own FAMILY mount must not stand in for the instance "
        "-- they carry different base_url/default_model/effort"
    )


@pytest.mark.asyncio
async def test_session_reports_the_instance_and_its_family():
    providers = _providers()
    orch, _ = await _run(
        {"llm_provider": INSTANCE_ID, "max_tool_rounds_per_input": 1}, providers
    )

    session = orch.session
    assert session is not None
    assert session._provider_name == INSTANCE_ID
    assert session._provider_module_name == "openai", (
        "the family an instance is an instance OF comes off the live provider "
        "object, never off the mount alias"
    )
    assert session._model == INSTANCE_MODEL


# ---------------------------------------------------------------------------
# 2. Layer-1 base prompt -- resolved from the FAMILY, not the alias
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instance_gets_its_familys_base_prompt_with_no_extra_config():
    """Before this addendum an instance id could not run at all: with no
    system_prompt configured, ``_resolve_base_prompt`` fell through
    ``canonical_provider("fakeinst") is None`` straight to its fail-loud
    branch, so a correctly resolved, correctly mounted instance died on the
    base prompt instead."""
    providers = _providers()
    await _run(
        {"llm_provider": INSTANCE_ID, "max_tool_rounds_per_input": 1}, providers
    )

    request = providers[INSTANCE_ID].complete.call_args[0][0]
    system_content = request.messages[0].content
    assert system_content, "Layer-1 base prompt must not be empty"


@pytest.mark.asyncio
async def test_an_unresolvable_provider_still_fails_loud():
    """The fail-loud branch is narrowed, never removed: an id that answers to
    no family -- because nothing is mounted under it -- must still refuse."""
    providers = {"anthropic": _FakeMountedProvider("anthropic", "claude-sonnet-5")}
    with pytest.raises(RuntimeError) as exc:
        await _run(
            {"llm_provider": "not-mounted-anywhere", "max_tool_rounds_per_input": 1},
            providers,
        )
    assert "not-mounted-anywhere" in str(exc.value)


@pytest.mark.asyncio
async def test_a_mount_whose_family_is_unknowable_fails_loud_naming_both():
    """A mounted object that declares no family and no configured prompt has
    nothing to resolve a base prompt from. That must name BOTH the mount key
    and the module it reported, so the reader can see which one was missing."""
    providers = {"weird": _FakeMountedProvider("mystery-vendor", "m-1")}
    with pytest.raises(RuntimeError) as exc:
        await _run({"llm_provider": "weird", "max_tool_rounds_per_input": 1}, providers)
    message = str(exc.value)
    assert "'weird'" in message
    assert "'mystery-vendor'" in message


# ---------------------------------------------------------------------------
# 3. Telemetry -- the events NAME who served the call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_response_names_provider_module_and_model():
    providers = _providers()
    _, hooks = await _run(
        {"llm_provider": INSTANCE_ID, "max_tool_rounds_per_input": 1}, providers
    )

    responses = [d for event, d in hooks._emitted if event == "provider:response"]
    assert responses, "a completed call must emit provider:response"
    for data in responses:
        assert data["provider"] == INSTANCE_ID
        assert data["provider_module"] == "openai"
        assert data["model"] == INSTANCE_MODEL
        assert "usage" in data, "identity is ADDITIVE -- usage must survive"


@pytest.mark.asyncio
async def test_provider_request_carries_the_same_identity():
    providers = _providers()
    _, hooks = await _run(
        {"llm_provider": INSTANCE_ID, "max_tool_rounds_per_input": 1}, providers
    )

    requests = [d for event, d in hooks._emitted if event == "provider:request"]
    assert requests
    for data in requests:
        assert data["provider"] == INSTANCE_ID
        assert data["provider_module"] == "openai"
        assert data["model"] == INSTANCE_MODEL


@pytest.mark.asyncio
async def test_a_module_named_mount_reports_itself_unchanged():
    """The ordinary case must read identically: mount key and module agree,
    and the model is the one that mount was configured with."""
    providers = _providers()
    _, hooks = await _run(
        {"llm_provider": "anthropic", "max_tool_rounds_per_input": 1}, providers
    )

    responses = [d for event, d in hooks._emitted if event == "provider:response"]
    assert responses
    assert responses[0]["provider"] == "anthropic"
    assert responses[0]["provider_module"] == "anthropic"
    assert responses[0]["model"] == "claude-sonnet-5"


# ---------------------------------------------------------------------------
# 4. The two readers, on their own
# ---------------------------------------------------------------------------


def test_identity_readers_return_none_rather_than_guessing():
    """A bare double declares no family and no model. ``None`` is the honest
    answer; a guessed family is the substitution class this addendum removes,
    and a mock's repr persisted as a model name would be worse than silence."""
    double = MagicMock()
    assert mounted_provider_module_name(double) is None
    assert mounted_provider_default_model(double) is None


def test_identity_readers_read_the_real_shape():
    provider = _FakeMountedProvider("openai", INSTANCE_MODEL)
    assert mounted_provider_module_name(provider) == "openai"
    assert mounted_provider_default_model(provider) == INSTANCE_MODEL


@pytest.mark.asyncio
async def test_explicit_config_model_outranks_the_mounts_default():
    """``model`` in orchestrator config is an explicit override and stays
    authoritative -- the mount's own default is a FALLBACK for reporting,
    never a replacement for a value the caller actually set."""
    providers = _providers()
    _, hooks = await _run(
        {
            "llm_provider": INSTANCE_ID,
            "model": "explicitly-configured-model",
            "max_tool_rounds_per_input": 1,
        },
        providers,
    )
    responses = [d for event, d in hooks._emitted if event == "provider:response"]
    assert responses[0]["model"] == "explicitly-configured-model"


# ---------------------------------------------------------------------------
# 5. The residual two: INSTANCE id and REASONING EFFORT
# ---------------------------------------------------------------------------
#
# `provider` + `provider_module` + `model` (sections 3-4 above) left two
# facts still un-evidenced, both of which a reader had to infer:
#
#   * WAS this an instance at all?  A reader had to compare the two names AND
#     know the naming-variant rule (`provider-openai` is the openai family,
#     not an instance called "provider-openai").  Inference, on the exact
#     question the addendum exists to answer.
#   * At what EFFORT?  Nowhere on the stream.  The node-matrix varies effort
#     across rows, so `high` vs `low` could only be told apart by trusting
#     the matrix file -- not the run's own evidence.


@pytest.mark.asyncio
async def test_provider_response_names_the_instance_id_as_an_instance():
    providers = _providers()
    _, hooks = await _run(
        {
            "llm_provider": INSTANCE_ID,
            "reasoning_effort": "high",
            "max_tool_rounds_per_input": 1,
        },
        providers,
    )

    responses = [d for event, d in hooks._emitted if event == "provider:response"]
    assert responses
    for data in responses:
        assert data["provider_instance"] == INSTANCE_ID, (
            "the mount key is an instance alias and the event must SAY so -- "
            "not leave a reader to derive it from provider != provider_module"
        )
        assert data["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_provider_request_carries_instance_and_effort_too():
    providers = _providers()
    _, hooks = await _run(
        {
            "llm_provider": INSTANCE_ID,
            "reasoning_effort": "high",
            "max_tool_rounds_per_input": 1,
        },
        providers,
    )

    requests = [d for event, d in hooks._emitted if event == "provider:request"]
    assert requests
    for data in requests:
        assert data["provider_instance"] == INSTANCE_ID
        assert data["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_the_effort_reported_is_the_effort_the_request_carries():
    """Not a re-echo of config: the value on the event must equal the value
    that actually rode on the ``ChatRequest`` to the provider. One source of
    truth, proven against the object the provider received."""
    providers = _providers()
    _, hooks = await _run(
        {
            "llm_provider": INSTANCE_ID,
            "reasoning_effort": "low",
            "max_tool_rounds_per_input": 1,
        },
        providers,
    )

    request = providers[INSTANCE_ID].complete.call_args[0][0]
    responses = [d for event, d in hooks._emitted if event == "provider:response"]
    assert request.reasoning_effort == "low"
    assert responses[0]["reasoning_effort"] == request.reasoning_effort


@pytest.mark.asyncio
async def test_a_module_named_mount_reports_no_instance():
    """The ordinary case must report ``None``, never the module name dressed
    up as an instance -- an invented instance id is the same substitution
    class as an invented family."""
    providers = _providers()
    _, hooks = await _run(
        {"llm_provider": "anthropic", "max_tool_rounds_per_input": 1}, providers
    )

    responses = [d for event, d in hooks._emitted if event == "provider:response"]
    assert responses
    assert responses[0]["provider_instance"] is None
    assert responses[0]["reasoning_effort"] is None, (
        "a node that declared no effort must report None, never a default it "
        "did not ask for"
    )


def test_provider_instance_id_rules():
    """The reader on its own, over the four cases that matter."""
    # A real instance: the key answers to no family.
    assert provider_instance_id("terra", "openai") == "terra"
    # The ordinary mount: key IS the module name.
    assert provider_instance_id("openai", "openai") is None
    # A naming variant of the same family is NOT a configured instance.
    assert provider_instance_id("provider-openai", "openai") is None
    assert provider_instance_id("Provider-OpenAI", "openai") is None
    # Either half unknown -> say nothing.
    assert provider_instance_id(None, "openai") is None
    assert provider_instance_id("terra", None) is None
