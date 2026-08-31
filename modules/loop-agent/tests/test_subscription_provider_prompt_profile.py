"""RED-proofs for the ``prompt_profile`` rung + unknown-subscription-provider
default toolset (idea-transfer from microsoft/amplifier-bundle-attractor#322,
credited in this feature's commit).

The system-*.md files are TOOLSET prompts (which tool names/workflow the
agent should use), not model-FAMILY prompts. Two additions to
``_resolve_base_prompt``'s precedence:

  (3) an explicit ``prompt_profile`` config key selects a toolset prompt
      directly, independent of which provider is mounted.
  (5) a KNOWN subscription provider (github-copilot/openai-chatgpt -- not
      one of KNOWN_PROVIDERS by design) defaults to the generic "anthropic"
      toolset prompt instead of failing loud -- zero new config required.

A genuinely unknown/typo'd provider name still fails loud exactly as before
(``test_system_prompt_wiring.py::test_unknown_provider_with_no_base_raises_loud_error``,
unaffected -- that test uses provider name "test", never one of the two real
subscription providers this feature adds).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_core.message_models import ChatResponse, Usage
from amplifier_module_loop_agent import AgentOrchestrator


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _make_hooks():
    hooks = MagicMock()
    hooks._emitted: list[tuple[str, dict]] = []

    async def _recording_emit(event: str, data: dict):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_recording_emit)
    return hooks


def _make_provider():
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))
    return provider


def _make_coordinator():
    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()
    return coordinator


async def _system_content(orch: AgentOrchestrator, provider, mounted_name: str):
    context = MagicMock()
    hooks = _make_hooks()
    await orch.execute("hello", context, {mounted_name: provider}, {}, hooks)
    request = provider.complete.call_args[0][0]
    return request.messages[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription_provider", ["github-copilot", "openai-chatgpt"])
async def test_subscription_provider_defaults_to_generic_toolset_prompt(
    subscription_provider,
):
    """Rung (5): a KNOWN subscription provider gets a working system prompt
    with ZERO new config -- no prompt_profile, no system_prompt, nothing."""
    provider = _make_provider()
    orch = AgentOrchestrator(
        coordinator=_make_coordinator(),
        config={"max_tool_rounds_per_input": 1},  # no base configured at all
    )
    content = await _system_content(orch, provider, subscription_provider)
    assert "Anthropic Profile" in content or "Claude Code" in content


@pytest.mark.asyncio
async def test_explicit_prompt_profile_selects_toolset_regardless_of_provider():
    """Rung (3): prompt_profile="gemini" wins even though the mounted
    provider is a subscription provider that would otherwise default to
    anthropic's toolset prompt."""
    provider = _make_provider()
    orch = AgentOrchestrator(
        coordinator=_make_coordinator(),
        config={
            "prompt_profile": "gemini",
            "max_tool_rounds_per_input": 1,
        },
    )
    content = await _system_content(orch, provider, "github-copilot")
    assert "Gemini Profile" in content or "gemini-cli" in content


@pytest.mark.asyncio
async def test_prompt_profile_beats_subscription_default_for_openai_chatgpt():
    provider = _make_provider()
    orch = AgentOrchestrator(
        coordinator=_make_coordinator(),
        config={
            "prompt_profile": "openai",
            "max_tool_rounds_per_input": 1,
        },
    )
    content = await _system_content(orch, provider, "openai-chatgpt")
    assert "OpenAI Profile" in content or "codex-rs" in content


@pytest.mark.asyncio
async def test_explicit_system_prompt_still_beats_prompt_profile():
    """Precedence (1) still wins over the new rung (3)."""
    provider = _make_provider()
    sentinel = "EXPLICIT-BEATS-PROMPT-PROFILE-Q9"
    orch = AgentOrchestrator(
        coordinator=_make_coordinator(),
        config={
            "system_prompt": sentinel,
            "prompt_profile": "gemini",
            "max_tool_rounds_per_input": 1,
        },
    )
    content = await _system_content(orch, provider, "github-copilot")
    assert sentinel in content
    assert "Gemini Profile" not in content


@pytest.mark.asyncio
async def test_unrecognized_provider_still_fails_loud_not_defaulted():
    """Scope guard: the subscription-provider default is NOT a blanket
    "any unrecognized provider" policy -- a genuinely unknown/typo'd name
    must still fail loud (mirrors test_system_prompt_wiring.py's own
    "test" example, using a different made-up name to avoid coupling the
    two files together)."""
    provider = _make_provider()
    orch = AgentOrchestrator(
        coordinator=_make_coordinator(),
        config={"max_tool_rounds_per_input": 1},
    )
    context = MagicMock()
    hooks = _make_hooks()
    with pytest.raises(RuntimeError, match="not one of the known providers"):
        await orch.execute(
            "hello", context, {"totally-made-up-provider": provider}, {}, hooks
        )
