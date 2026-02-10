"""Steering and follow-up queues for the coding agent loop.

Spec coverage: STEER-001 through STEER-010.

The steering queue lets the host inject messages between tool rounds.
The follow-up queue lets the host queue messages for after the current
input completes, triggering recursive process_input() calls.

Both use asyncio.Queue for thread-safety.
"""

from __future__ import annotations

import asyncio


class SteeringQueue:
    """Queue for mid-task steering messages.

    Messages are injected between tool rounds as SteeringTurns
    in the conversation history, converted to user-role messages
    for the LLM.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    def steer(self, message: str) -> None:
        """Non-blocking enqueue of a steering message."""
        self._queue.put_nowait(message)

    def drain(self) -> list[str]:
        """Dequeue all pending steering messages.

        Returns a list of messages (possibly empty).
        """
        messages: list[str] = []
        while not self._queue.empty():
            try:
                messages.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages


class FollowUpQueue:
    """Queue for follow-up messages processed after the current input.

    Follow-up messages trigger recursive process_input() calls
    after the current agentic loop completes.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    def follow_up(self, message: str) -> None:
        """Enqueue a follow-up message."""
        self._queue.put_nowait(message)

    def drain(self) -> str | None:
        """Dequeue the next follow-up message, or None if empty."""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    @property
    def is_empty(self) -> bool:
        """True if no follow-up messages are pending."""
        return self._queue.empty()
