"""Session state machine for the coding agent loop.

Spec coverage: SESS-007 through SESS-015.

State transitions:
    IDLE -> PROCESSING          (submit)
    PROCESSING -> IDLE          (complete)
    PROCESSING -> AWAITING_INPUT (await_input)
    PROCESSING -> CLOSED        (fatal_error)
    AWAITING_INPUT -> PROCESSING (resume_input)
    IDLE -> CLOSED              (close)
    IDLE -> CLOSED              (abort)
    PROCESSING -> CLOSED        (abort)
    AWAITING_INPUT -> CLOSED    (abort)
"""

from __future__ import annotations

from enum import Enum


class SessionState(Enum):
    """Lifecycle states for a coding agent session."""

    IDLE = "idle"
    PROCESSING = "processing"
    AWAITING_INPUT = "awaiting_input"
    CLOSED = "closed"


class InvalidTransitionError(ValueError):
    """Raised when an invalid state transition is attempted."""


# Valid transitions: {from_state: {trigger: to_state}}
_TRANSITIONS: dict[SessionState, dict[str, SessionState]] = {
    SessionState.IDLE: {
        "submit": SessionState.PROCESSING,
        "close": SessionState.CLOSED,
        "abort": SessionState.CLOSED,
    },
    SessionState.PROCESSING: {
        "complete": SessionState.IDLE,
        "await_input": SessionState.AWAITING_INPUT,
        "fatal_error": SessionState.CLOSED,
        "abort": SessionState.CLOSED,
    },
    SessionState.AWAITING_INPUT: {
        "resume_input": SessionState.PROCESSING,
        "abort": SessionState.CLOSED,
    },
    SessionState.CLOSED: {},
}


class SessionStateMachine:
    """Enforces the session state transition rules."""

    def __init__(self) -> None:
        self._state = SessionState.IDLE

    @property
    def state(self) -> SessionState:
        return self._state

    def _transition(self, trigger: str) -> None:
        transitions = _TRANSITIONS.get(self._state, {})
        next_state = transitions.get(trigger)
        if next_state is None:
            raise InvalidTransitionError(
                f"Invalid transition: {trigger!r} from {self._state.value}"
            )
        self._state = next_state

    def submit(self) -> None:
        """IDLE -> PROCESSING."""
        self._transition("submit")

    def complete(self) -> None:
        """PROCESSING -> IDLE."""
        self._transition("complete")

    def await_input(self) -> None:
        """PROCESSING -> AWAITING_INPUT."""
        self._transition("await_input")

    def resume_input(self) -> None:
        """AWAITING_INPUT -> PROCESSING."""
        self._transition("resume_input")

    def fatal_error(self) -> None:
        """PROCESSING -> CLOSED."""
        self._transition("fatal_error")

    def abort(self) -> None:
        """any (non-CLOSED) -> CLOSED."""
        self._transition("abort")

    def close(self) -> None:
        """IDLE -> CLOSED."""
        self._transition("close")
