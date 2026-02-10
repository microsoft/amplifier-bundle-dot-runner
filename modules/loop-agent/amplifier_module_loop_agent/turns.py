"""Turn types and session history for the coding agent loop.

Spec coverage: TURN-001 through TURN-005, SESS-003 through SESS-005.

Turn types model the conversation history as a typed sequence of entries,
each representing a single participant's contribution to the conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Union

from amplifier_core.message_models import Usage
from amplifier_core.models import ToolResult


def _now() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


@dataclass
class UserTurn:
    """User input turn."""

    content: str
    timestamp: datetime = field(default_factory=_now)


@dataclass
class AssistantTurn:
    """LLM assistant response turn."""

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str | None = None
    reasoning_signature: str | None = None
    usage: Usage | None = None
    response_id: str | None = None
    timestamp: datetime = field(default_factory=_now)


@dataclass
class ToolResultsTurn:
    """Results from executing one or more tool calls."""

    results: list[ToolResult]
    timestamp: datetime = field(default_factory=_now)


@dataclass
class SteeringTurn:
    """Injected steering message (mid-task redirection)."""

    content: str
    timestamp: datetime = field(default_factory=_now)


@dataclass
class SystemTurn:
    """System-level message (e.g., system prompt, warnings)."""

    content: str
    timestamp: datetime = field(default_factory=_now)


# Union of all turn types
Turn = Union[UserTurn, AssistantTurn, ToolResultsTurn, SteeringTurn, SystemTurn]


class SessionHistory:
    """Ordered list of conversation turns with query helpers.

    Holds the full typed turn history for a session. Provides
    append, iteration, indexing, and convenience accessors.
    """

    def __init__(self) -> None:
        self._turns: list[Turn] = []

    def append(self, turn: Turn) -> None:
        """Append a turn to the history."""
        self._turns.append(turn)

    @property
    def turn_count(self) -> int:
        """Total number of turns."""
        return len(self._turns)

    @property
    def last_turn(self) -> Turn | None:
        """Most recent turn, or None if empty."""
        return self._turns[-1] if self._turns else None

    @property
    def last_assistant_turn(self) -> AssistantTurn | None:
        """Most recent AssistantTurn, or None."""
        for turn in reversed(self._turns):
            if isinstance(turn, AssistantTurn):
                return turn
        return None

    def clear(self) -> None:
        """Remove all turns."""
        self._turns.clear()

    def __len__(self) -> int:
        return len(self._turns)

    def __iter__(self) -> Iterator[Turn]:
        return iter(self._turns)

    def __getitem__(self, index: int) -> Turn:
        return self._turns[index]
