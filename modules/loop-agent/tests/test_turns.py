"""Tests for turn types and session history.

Spec coverage: TURN-001 through TURN-005, SESS-003 through SESS-005.
"""

from datetime import datetime, timezone

from amplifier_module_loop_agent.turns import (
    UserTurn,
    AssistantTurn,
    ToolResultsTurn,
    SystemTurn,
    SteeringTurn,
    SessionHistory,
)


# --- UserTurn ---


def test_user_turn_has_required_fields():
    t = UserTurn(content="hello")
    assert t.content == "hello"
    assert t.timestamp is not None


def test_user_turn_auto_timestamp():
    before = datetime.now(timezone.utc)
    t = UserTurn(content="test")
    after = datetime.now(timezone.utc)
    assert before <= t.timestamp <= after


def test_user_turn_explicit_timestamp():
    ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    t = UserTurn(content="test", timestamp=ts)
    assert t.timestamp == ts


# --- AssistantTurn ---


def test_assistant_turn_stores_content():
    t = AssistantTurn(content="I'll help")
    assert t.content == "I'll help"


def test_assistant_turn_stores_tool_calls():
    t = AssistantTurn(
        content="I'll help",
        tool_calls=[{"id": "1", "name": "read_file", "arguments": {}}],
    )
    assert len(t.tool_calls) == 1
    assert t.tool_calls[0]["name"] == "read_file"


def test_assistant_turn_defaults():
    t = AssistantTurn(content="hello")
    assert t.tool_calls == []
    assert t.reasoning is None
    assert t.usage is None
    assert t.response_id is None
    assert t.timestamp is not None


def test_assistant_turn_with_usage():
    from amplifier_core.message_models import Usage

    usage = Usage(input_tokens=10, output_tokens=5, total_tokens=15)
    t = AssistantTurn(content="done", usage=usage)
    assert t.usage is not None
    assert t.usage.input_tokens == 10


def test_assistant_turn_with_reasoning():
    t = AssistantTurn(content="answer", reasoning="I thought about it")
    assert t.reasoning == "I thought about it"


def test_assistant_turn_with_response_id():
    t = AssistantTurn(content="ok", response_id="resp_abc123")
    assert t.response_id == "resp_abc123"


# --- ToolResultsTurn ---


def test_tool_results_turn():
    from amplifier_core.models import ToolResult

    results = [
        ToolResult(success=True, output="file contents"),
        ToolResult(success=False, error={"message": "not found"}),
    ]
    t = ToolResultsTurn(results=results)
    assert len(t.results) == 2
    assert t.results[0].success is True
    assert t.results[1].success is False
    assert t.timestamp is not None


def test_tool_results_turn_empty_list():
    t = ToolResultsTurn(results=[])
    assert len(t.results) == 0


# --- SteeringTurn ---


def test_steering_turn():
    t = SteeringTurn(content="try a different approach")
    assert t.content == "try a different approach"
    assert t.timestamp is not None


# --- SystemTurn ---


def test_system_turn():
    t = SystemTurn(content="You are a coding agent.")
    assert t.content == "You are a coding agent."
    assert t.timestamp is not None


# --- SessionHistory ---


def test_session_history_starts_empty():
    h = SessionHistory()
    assert len(h) == 0
    assert h.turn_count == 0


def test_session_history_append_and_len():
    h = SessionHistory()
    h.append(UserTurn(content="hello"))
    h.append(AssistantTurn(content="hi"))
    assert len(h) == 2
    assert h.turn_count == 2


def test_session_history_iter():
    h = SessionHistory()
    h.append(UserTurn(content="a"))
    h.append(AssistantTurn(content="b"))
    turns = list(h)
    assert len(turns) == 2
    assert isinstance(turns[0], UserTurn)
    assert isinstance(turns[1], AssistantTurn)


def test_session_history_getitem():
    h = SessionHistory()
    h.append(UserTurn(content="first"))
    h.append(AssistantTurn(content="second"))
    turn0 = h[0]
    turn1 = h[1]
    turn_last = h[-1]
    assert isinstance(turn0, UserTurn) and turn0.content == "first"
    assert isinstance(turn1, AssistantTurn) and turn1.content == "second"
    assert isinstance(turn_last, AssistantTurn) and turn_last.content == "second"


def test_session_history_last_turn():
    h = SessionHistory()
    assert h.last_turn is None
    h.append(UserTurn(content="hello"))
    last = h.last_turn
    assert last is not None
    assert isinstance(last, UserTurn) and last.content == "hello"


def test_session_history_last_assistant_turn():
    h = SessionHistory()
    assert h.last_assistant_turn is None
    h.append(UserTurn(content="hello"))
    assert h.last_assistant_turn is None
    h.append(AssistantTurn(content="response"))
    assert h.last_assistant_turn is not None
    assert h.last_assistant_turn.content == "response"


def test_session_history_preserves_order():
    h = SessionHistory()
    h.append(UserTurn(content="1"))
    h.append(AssistantTurn(content="2"))
    h.append(SteeringTurn(content="3"))
    h.append(AssistantTurn(content="4"))
    contents = [getattr(t, "content", None) for t in h]
    assert contents == ["1", "2", "3", "4"]


def test_session_history_clear():
    h = SessionHistory()
    h.append(UserTurn(content="a"))
    h.append(AssistantTurn(content="b"))
    h.clear()
    assert len(h) == 0
    assert h.turn_count == 0
