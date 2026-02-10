"""Tests for session state machine.

Spec coverage: SESS-007 through SESS-015 (SessionState enum and transitions).
"""

import pytest
from amplifier_module_loop_agent.state import (
    SessionState,
    SessionStateMachine,
    InvalidTransitionError,
)


# --- State initialization ---


def test_initial_state_is_idle():
    sm = SessionStateMachine()
    assert sm.state == SessionState.IDLE


# --- Valid transitions ---


def test_submit_transitions_idle_to_processing():
    sm = SessionStateMachine()
    sm.submit()
    assert sm.state == SessionState.PROCESSING


def test_complete_transitions_processing_to_idle():
    sm = SessionStateMachine()
    sm.submit()
    sm.complete()
    assert sm.state == SessionState.IDLE


def test_fatal_error_transitions_processing_to_closed():
    sm = SessionStateMachine()
    sm.submit()
    sm.fatal_error()
    assert sm.state == SessionState.CLOSED


def test_awaiting_input_transition():
    sm = SessionStateMachine()
    sm.submit()
    sm.await_input()
    assert sm.state == SessionState.AWAITING_INPUT


def test_resume_from_awaiting():
    sm = SessionStateMachine()
    sm.submit()
    sm.await_input()
    sm.resume_input()
    assert sm.state == SessionState.PROCESSING


def test_close_from_idle():
    sm = SessionStateMachine()
    sm.close()
    assert sm.state == SessionState.CLOSED


# --- Abort from any non-closed state ---


def test_abort_from_idle():
    sm = SessionStateMachine()
    sm.abort()
    assert sm.state == SessionState.CLOSED


def test_abort_from_processing():
    sm = SessionStateMachine()
    sm.submit()
    sm.abort()
    assert sm.state == SessionState.CLOSED


def test_abort_from_awaiting_input():
    sm = SessionStateMachine()
    sm.submit()
    sm.await_input()
    sm.abort()
    assert sm.state == SessionState.CLOSED


# --- Invalid transitions ---


def test_complete_from_idle_raises():
    sm = SessionStateMachine()
    with pytest.raises(InvalidTransitionError, match="Invalid transition"):
        sm.complete()


def test_submit_from_processing_raises():
    sm = SessionStateMachine()
    sm.submit()
    with pytest.raises(InvalidTransitionError, match="Invalid transition"):
        sm.submit()


def test_submit_from_closed_raises():
    sm = SessionStateMachine()
    sm.close()
    with pytest.raises(InvalidTransitionError, match="Invalid transition"):
        sm.submit()


def test_abort_from_closed_raises():
    """Already closed — no transition, even abort."""
    sm = SessionStateMachine()
    sm.close()
    with pytest.raises(InvalidTransitionError, match="Invalid transition"):
        sm.abort()


def test_resume_input_from_idle_raises():
    sm = SessionStateMachine()
    with pytest.raises(InvalidTransitionError, match="Invalid transition"):
        sm.resume_input()


# --- InvalidTransitionError is a ValueError subclass ---


def test_invalid_transition_error_is_value_error():
    """InvalidTransitionError should be catchable as ValueError."""
    assert issubclass(InvalidTransitionError, ValueError)


# --- Full lifecycle round-trip ---


def test_full_lifecycle_idle_to_processing_to_idle():
    sm = SessionStateMachine()
    sm.submit()
    sm.complete()
    sm.submit()
    sm.complete()
    assert sm.state == SessionState.IDLE


def test_full_lifecycle_with_awaiting_input():
    sm = SessionStateMachine()
    sm.submit()
    sm.await_input()
    sm.resume_input()
    sm.complete()
    assert sm.state == SessionState.IDLE
