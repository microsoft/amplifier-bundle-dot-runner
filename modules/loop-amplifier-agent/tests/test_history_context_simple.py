"""Translate real context-simple transcripts without modifying their history."""

import copy

import pytest
from amplifier_module_context_simple import SimpleContextManager
from amplifier_module_loop_amplifier_agent import AmplifierAgentOrchestrator


@pytest.mark.asyncio
async def test_real_restamped_history_preserves_roles_parts_and_inputs():
    incoming = [
        {"role": "system", "content": "stored system"},
        {"role": "developer", "content": "stored developer"},
        {"role": "user", "content": "remember"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "one"},
                {"type": "text", "text": ""},
                {"type": "text", "text": "two"},
            ],
            "metadata": {"timestamp": "2026-01-01T00:00:00Z", "_seq": 99},
        },
    ]
    original = copy.deepcopy(incoming)
    context = SimpleContextManager()
    await context.set_messages(incoming)
    # Foundation uses this factory for bundle instructions, not transcript data.
    async def factory():
        raise AssertionError("dynamic bundle prompt must not enter history")

    await context.set_system_prompt_factory(factory)
    stored = copy.deepcopy(await context.get_messages())
    assert [msg["metadata"]["_seq"] for msg in stored] == [0, 1, 2, 3]
    translated = await AmplifierAgentOrchestrator._history_from_context(context)
    assert [(msg.role, [part.text for part in msg.content]) for msg in translated] == [
        ("system", ["stored system"]),
        ("developer", ["stored developer"]),
        ("user", ["remember"]),
        ("assistant", ["one", "", "two"]),
    ]
    assert incoming == original
    assert await context.get_messages() == stored
    # Neither the projection nor a caller's subsequent edit aliases text parts.
    translated[3].content.clear()
    assert await context.get_messages() == stored


@pytest.mark.asyncio
async def test_real_add_message_timestamp_is_bookkeeping_only():
    context = SimpleContextManager()
    incoming = {"role": "user", "content": "text"}
    await context.add_message(incoming)
    stored = copy.deepcopy(await context.get_messages())
    assert set(stored[0]["metadata"]) == {"timestamp", "_seq"}
    translated = await AmplifierAgentOrchestrator._history_from_context(context)
    assert translated[0].content[0].text == "text"
    assert incoming == {"role": "user", "content": "text"}
    assert await context.get_messages() == stored


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"metadata": {"loaded_tools": ["read_file"]}},
        {"metadata": {"loaded_tool_state": {"read_file": {}}}},
        {"metadata": {"semantic": True}},
        {"metadata": ["not a mapping"]},
        {"tool_calls": []},
        {"tool_call_id": "call"},
    ],
)
async def test_semantic_and_tool_metadata_still_refused(extra):
    class Context:
        async def get_messages(self):
            return [{"role": "assistant", "content": "text", **extra}]

    with pytest.raises(ValueError, match="history"):
        await AmplifierAgentOrchestrator._history_from_context(Context())