"""Event constants for the coding agent loop.

Maps spec EventKinds (Section 2.9) to event name strings used with hooks.emit().
Agent-specific events use the 'agent:' prefix. Standard Amplifier provider
events are re-exported from amplifier_core.events for convenience.

Spec coverage: EVENT-001 through EVENT-009.
"""

from amplifier_core.events import (
    PROVIDER_ERROR,
    PROVIDER_REQUEST,
    PROVIDER_RESPONSE,
)

# Agent session lifecycle
AGENT_SESSION_START = "agent:session_start"
AGENT_SESSION_END = "agent:session_end"
AGENT_AWAITING_INPUT = "agent:awaiting_input"

# User input
AGENT_USER_INPUT = "agent:user_input"

# Assistant output (streaming events: EVT-006, EVT-007, EVT-008)
AGENT_ASSISTANT_TEXT_START = "agent:assistant_text_start"
AGENT_ASSISTANT_TEXT_DELTA = "agent:assistant_text_delta"
AGENT_ASSISTANT_TEXT_END = "agent:assistant_text_end"

# Tool execution
AGENT_TOOL_CALL_START = "agent:tool_call_start"
AGENT_TOOL_CALL_END = "agent:tool_call_end"
AGENT_TOOL_CALL_OUTPUT_DELTA = "agent:tool_call_output_delta"

# Steering
AGENT_STEERING_INJECTED = "agent:steering_injected"

# Loop detection
AGENT_LOOP_DETECTION = "agent:loop_detection"

# Context window
AGENT_CONTEXT_WARNING = "agent:context_warning"

# Limits and errors
AGENT_TURN_LIMIT = "agent:turn_limit"
AGENT_ERROR = "agent:error"

__all__ = [
    "AGENT_ASSISTANT_TEXT_DELTA",
    "AGENT_ASSISTANT_TEXT_END",
    "AGENT_ASSISTANT_TEXT_START",
    "AGENT_AWAITING_INPUT",
    "AGENT_CONTEXT_WARNING",
    "AGENT_ERROR",
    "AGENT_LOOP_DETECTION",
    "AGENT_SESSION_END",
    "AGENT_SESSION_START",
    "AGENT_STEERING_INJECTED",
    "AGENT_TOOL_CALL_END",
    "AGENT_TOOL_CALL_OUTPUT_DELTA",
    "AGENT_TOOL_CALL_START",
    "AGENT_TURN_LIMIT",
    "AGENT_USER_INPUT",
    "PROVIDER_ERROR",
    "PROVIDER_REQUEST",
    "PROVIDER_RESPONSE",
]
