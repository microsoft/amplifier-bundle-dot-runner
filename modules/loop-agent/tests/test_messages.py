"""Tests for history-to-messages conversion (Task 1.8).

Spec coverage: LOOP-010, STEER-003, STEER-010.
Verifies that typed Turn history is correctly converted to Message
objects for ChatRequest, with system-first ordering, content blocks,
ThinkingBlock preservation, and edge case handling.
"""


from amplifier_core.message_models import TextBlock, ThinkingBlock
from amplifier_core.models import ToolResult

from amplifier_module_loop_agent.messages import convert_history_to_messages
from amplifier_module_loop_agent.turns import (
    AssistantTurn,
    SteeringTurn,
    SystemTurn,
    ToolResultsTurn,
    UserTurn,
)


# ---------------------------------------------------------------------------
# Basic turn type conversions
# ---------------------------------------------------------------------------


def test_user_turn_becomes_user_message():
    turns = [UserTurn(content="hello")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content == "hello"


def test_system_turn_becomes_system_message():
    turns = [SystemTurn(content="You are a coding agent.")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "system"
    assert msgs[0].content == "You are a coding agent."


def test_steering_turn_becomes_user_message():
    """SteeringTurn is converted to user-role message (STEER-003, STEER-010)."""
    turns = [SteeringTurn(content="try differently")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert "try differently" in msgs[0].content


def test_assistant_turn_text_only():
    """AssistantTurn with text only → assistant message."""
    turns = [AssistantTurn(content="Hello!")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"
    # Content should contain the text
    content = msgs[0].content
    if isinstance(content, list):
        text_blocks = [b for b in content if isinstance(b, TextBlock)]
        assert len(text_blocks) == 1
        assert text_blocks[0].text == "Hello!"
    else:
        assert content == "Hello!"


def test_assistant_turn_with_tool_calls():
    """AssistantTurn with tool calls → tool_calls on message."""
    turns = [
        AssistantTurn(
            content="Let me read that file.",
            tool_calls=[
                {"id": "tc1", "name": "read_file", "arguments": {"path": "x.py"}},
            ],
        )
    ]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"
    # tool_calls should be present (via extra fields)
    assert hasattr(msgs[0], "tool_calls") or "tool_calls" in (msgs[0].model_extra or {})


# ---------------------------------------------------------------------------
# System-first ordering
# ---------------------------------------------------------------------------


def test_system_messages_placed_first():
    """System messages appear before all other messages."""
    turns = [
        UserTurn(content="hello"),
        SystemTurn(content="system prompt"),
        AssistantTurn(content="hi"),
    ]
    msgs = convert_history_to_messages(turns)
    assert msgs[0].role == "system"
    assert msgs[0].content == "system prompt"
    assert msgs[1].role == "user"
    assert msgs[2].role == "assistant"


def test_multiple_system_messages_all_first():
    """Multiple system messages all placed at the beginning."""
    turns = [
        UserTurn(content="hello"),
        SystemTurn(content="system 1"),
        AssistantTurn(content="hi"),
        SystemTurn(content="system 2"),
    ]
    msgs = convert_history_to_messages(turns)
    assert msgs[0].role == "system"
    assert msgs[1].role == "system"
    assert msgs[2].role == "user"
    assert msgs[3].role == "assistant"


# ---------------------------------------------------------------------------
# ToolResultsTurn conversion
# ---------------------------------------------------------------------------


def test_tool_results_turn_basic():
    """ToolResultsTurn → tool messages with correct call_id."""
    turns = [
        AssistantTurn(
            content="",
            tool_calls=[
                {"id": "tc1", "name": "read_file", "arguments": {}},
            ],
        ),
        ToolResultsTurn(results=[ToolResult(success=True, output="file contents")]),
    ]
    msgs = convert_history_to_messages(turns)
    # assistant message + 1 tool message
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "tc1"
    assert "file contents" in tool_msgs[0].content


def test_tool_results_multiple_tools():
    """Multiple tool calls → multiple tool result messages with matching IDs."""
    turns = [
        AssistantTurn(
            content="",
            tool_calls=[
                {"id": "tc1", "name": "read_file", "arguments": {}},
                {"id": "tc2", "name": "write_file", "arguments": {}},
            ],
        ),
        ToolResultsTurn(
            results=[
                ToolResult(success=True, output="read output"),
                ToolResult(success=True, output="write output"),
            ]
        ),
    ]
    msgs = convert_history_to_messages(turns)
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0].tool_call_id == "tc1"
    assert tool_msgs[1].tool_call_id == "tc2"


def test_tool_results_with_errors():
    """Tool results with errors are properly serialized."""
    turns = [
        AssistantTurn(
            content="",
            tool_calls=[
                {"id": "tc1", "name": "bad_tool", "arguments": {}},
            ],
        ),
        ToolResultsTurn(results=[ToolResult(success=False, output="Tool error: oops")]),
    ]
    msgs = convert_history_to_messages(turns)
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "error" in tool_msgs[0].content.lower() or "oops" in tool_msgs[0].content


# ---------------------------------------------------------------------------
# ThinkingBlock preservation
# ---------------------------------------------------------------------------


def test_assistant_turn_with_reasoning():
    """AssistantTurn with reasoning → content includes ThinkingBlock."""
    turns = [AssistantTurn(content="Answer", reasoning="Let me think...")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    content = msgs[0].content
    assert isinstance(content, list), "Content should be a list of blocks"
    thinking_blocks = [b for b in content if isinstance(b, ThinkingBlock)]
    text_blocks = [b for b in content if isinstance(b, TextBlock)]
    assert len(thinking_blocks) == 1
    assert thinking_blocks[0].thinking == "Let me think..."
    assert len(text_blocks) == 1
    assert text_blocks[0].text == "Answer"


def test_thinking_block_preserves_signature():
    """ThinkingBlock signature is preserved for multi-turn."""
    turns = [
        AssistantTurn(
            content="Answer",
            reasoning="Let me think...",
            reasoning_signature="sig123abc",
        )
    ]
    msgs = convert_history_to_messages(turns)
    content = msgs[0].content
    assert isinstance(content, list)
    thinking_blocks = [b for b in content if isinstance(b, ThinkingBlock)]
    assert len(thinking_blocks) == 1
    assert thinking_blocks[0].signature == "sig123abc"


def test_thinking_block_before_text_block():
    """ThinkingBlock appears before TextBlock in content (provider convention)."""
    turns = [AssistantTurn(content="Answer", reasoning="Thinking...")]
    msgs = convert_history_to_messages(turns)
    content = msgs[0].content
    assert isinstance(content, list)
    # Find positions
    thinking_idx = next(
        i for i, b in enumerate(content) if isinstance(b, ThinkingBlock)
    )
    text_idx = next(i for i, b in enumerate(content) if isinstance(b, TextBlock))
    assert thinking_idx < text_idx


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_content_assistant():
    """AssistantTurn with empty content → still produces a message."""
    turns = [AssistantTurn(content="")]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"


def test_none_content_assistant():
    """AssistantTurn with None-ish content → message with empty string."""
    turns = [AssistantTurn(content="", tool_calls=[])]
    msgs = convert_history_to_messages(turns)
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"


def test_empty_history():
    """Empty history → empty messages list."""
    msgs = convert_history_to_messages([])
    assert msgs == []


def test_tool_results_without_preceding_assistant():
    """ToolResultsTurn without preceding AssistantTurn → tool_call_id is None."""
    turns = [
        ToolResultsTurn(results=[ToolResult(success=True, output="orphan result")])
    ]
    msgs = convert_history_to_messages(turns)
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id is None


def test_full_conversation_round_trip():
    """Full conversation with system, user, assistant, tools, steering."""
    turns = [
        SystemTurn(content="You are helpful."),
        UserTurn(content="Read x.py"),
        AssistantTurn(
            content="I'll read it.",
            tool_calls=[
                {"id": "tc1", "name": "read_file", "arguments": {"path": "x.py"}}
            ],
        ),
        ToolResultsTurn(results=[ToolResult(success=True, output="print('hello')")]),
        SteeringTurn(content="Also check y.py"),
        AssistantTurn(content="Sure, let me check y.py too."),
    ]
    msgs = convert_history_to_messages(turns)

    # System first
    assert msgs[0].role == "system"
    # Then user, assistant, tool, steering(=user), assistant
    roles = [m.role for m in msgs]
    assert roles == ["system", "user", "assistant", "tool", "user", "assistant"]
