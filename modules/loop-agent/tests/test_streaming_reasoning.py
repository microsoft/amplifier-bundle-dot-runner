"""Tests for streaming reasoning capture (1a5).

Verifies that _call_provider_streaming() captures:
1. ThinkingBlock content from stream chunks
2. Reasoning signature for multi-turn Anthropic conversations
3. Both text and reasoning when present together
4. Reasoning is included in AGENT_ASSISTANT_TEXT_END event
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_core.message_models import ChatRequest, Message

from amplifier_module_loop_agent.agent_session import AgentSession
from amplifier_module_loop_agent.config import SessionConfig


def _make_hooks():
    hooks = MagicMock()
    hooks._emitted = []

    async def _emit(event, data):
        hooks._emitted.append((event, data))
        return MagicMock(action="continue")

    hooks.emit = AsyncMock(side_effect=_emit)
    return hooks


def _make_streaming_provider(chunks: list[dict]):
    """Create a mock provider with a stream() async generator.

    Each dict in chunks is yielded from stream(). Keys:
      content, thinking, reasoning, reasoning_signature, signature,
      tool_calls, usage
    """
    provider = AsyncMock()

    async def _stream(request):
        for chunk in chunks:
            yield chunk

    provider.stream = _stream
    provider.complete = AsyncMock()
    return provider


def _make_session(provider, hooks=None):
    """Create an AgentSession configured for streaming."""
    hooks = hooks or _make_hooks()
    tools = {}
    session = AgentSession(
        config=SessionConfig(),
        provider=provider,
        tools=tools,
        hooks=hooks,
    )
    # Force streaming on (override detection since our mock uses a real async gen)
    session._use_streaming = True
    return session, hooks


@pytest.mark.asyncio
async def test_reasoning_captured_from_thinking_chunks():
    """Stream chunks with 'thinking' key are captured as reasoning."""
    provider = _make_streaming_provider(
        [
            {"thinking": "Let me analyze this..."},
            {"thinking": " The bug is in line 42."},
            {"content": "I found the issue."},
        ]
    )
    session, hooks = _make_session(provider)

    request = ChatRequest(messages=[Message(role="user", content="find the bug")])
    result = await session._call_provider_streaming(request)

    assert result["reasoning"] == "Let me analyze this... The bug is in line 42."
    assert result["text"] == "I found the issue."


@pytest.mark.asyncio
async def test_reasoning_captured_from_reasoning_chunks():
    """Stream chunks with 'reasoning' key are also captured."""
    provider = _make_streaming_provider(
        [
            {"reasoning": "Step 1: read the file."},
            {"reasoning": " Step 2: find the error."},
            {"content": "Here's my analysis."},
        ]
    )
    session, hooks = _make_session(provider)

    request = ChatRequest(messages=[Message(role="user", content="analyze")])
    result = await session._call_provider_streaming(request)

    assert result["reasoning"] == "Step 1: read the file. Step 2: find the error."


@pytest.mark.asyncio
async def test_reasoning_signature_captured():
    """Reasoning signature is captured for multi-turn thinking."""
    provider = _make_streaming_provider(
        [
            {"thinking": "Deep analysis..."},
            {"reasoning_signature": "sig_abc123"},
            {"content": "Done."},
        ]
    )
    session, hooks = _make_session(provider)

    request = ChatRequest(messages=[Message(role="user", content="think")])
    result = await session._call_provider_streaming(request)

    assert result["reasoning"] == "Deep analysis..."
    assert result["reasoning_signature"] == "sig_abc123"


@pytest.mark.asyncio
async def test_signature_key_also_captured():
    """The 'signature' key variant is also captured."""
    provider = _make_streaming_provider(
        [
            {"thinking": "Thinking..."},
            {"signature": "sig_xyz789"},
            {"content": "Result."},
        ]
    )
    session, hooks = _make_session(provider)

    request = ChatRequest(messages=[Message(role="user", content="think")])
    result = await session._call_provider_streaming(request)

    assert result["reasoning_signature"] == "sig_xyz789"


@pytest.mark.asyncio
async def test_reasoning_in_text_end_event():
    """AGENT_ASSISTANT_TEXT_END event includes reasoning when present."""
    provider = _make_streaming_provider(
        [
            {"thinking": "My reasoning here."},
            {"content": "The answer."},
        ]
    )
    session, hooks = _make_session(provider)

    request = ChatRequest(messages=[Message(role="user", content="q")])
    await session._call_provider_streaming(request)

    # Find the text_end event
    text_end_events = [
        (e, d) for e, d in hooks._emitted if e == "agent:assistant_text_end"
    ]
    assert len(text_end_events) == 1
    event_data = text_end_events[0][1]
    assert event_data["text"] == "The answer."
    assert event_data["reasoning"] == "My reasoning here."


@pytest.mark.asyncio
async def test_no_reasoning_returns_none():
    """When no thinking chunks, reasoning is None (not empty string)."""
    provider = _make_streaming_provider(
        [
            {"content": "Simple response."},
        ]
    )
    session, hooks = _make_session(provider)

    request = ChatRequest(messages=[Message(role="user", content="hi")])
    result = await session._call_provider_streaming(request)

    assert result["reasoning"] is None
    assert result["reasoning_signature"] is None


@pytest.mark.asyncio
async def test_reasoning_persisted_in_result_dict():
    """Reasoning from streaming is available in the result dict for AssistantTurn."""
    provider = _make_streaming_provider(
        [
            {"thinking": "Deep thought."},
            {"reasoning_signature": "sig_001"},
            {"content": "Answer."},
        ]
    )

    session, hooks = _make_session(provider)

    request = ChatRequest(messages=[Message(role="user", content="think hard")])
    result = await session._call_provider_streaming(request)

    # Verify the result dict has reasoning
    assert result["reasoning"] == "Deep thought."
    assert result["reasoning_signature"] == "sig_001"

    # When this result feeds into process_input's AssistantTurn creation,
    # reasoning and reasoning_signature will be set.
    from amplifier_module_loop_agent.turns import AssistantTurn

    turn = AssistantTurn(
        content=result["text"],
        reasoning=result["reasoning"],
        reasoning_signature=result["reasoning_signature"],
    )
    assert turn.reasoning == "Deep thought."
    assert turn.reasoning_signature == "sig_001"
