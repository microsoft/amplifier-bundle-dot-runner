"""Kernel-faithful shared doubles for worker harnesses.

These are deliberately the "fuller" reference doubles: a new worker harness
can use them directly instead of hand-rolling another narrow one. Compare
``modules/loop-amplifier-agent/tests/_fakes.py``'s own ``FakeContextManager``,
whose docstring explains it implements only 2-of-5 ``ContextManager`` methods
because that is all ITS tests exercise -- correct scoping for a single
adapter's own hermetic tests, but not what a SHARED kit double should do.
This module's doubles implement the full authority surface so any worker's
harness can lean on them without re-deriving faithfulness from scratch.
"""

from __future__ import annotations

from typing import Any


class FakeContextManager:
    """Kernel-faithful double for ``amplifier_core.interfaces.ContextManager``.

    Authority: ``amplifier_core.interfaces.ContextManager`` declares five
    async methods -- ``add_message``, ``get_messages_for_request``,
    ``get_messages``, ``set_messages``, ``clear``. This double implements
    all five (contrast with per-worker narrower fakes, which correctly scope
    down to what their own tests exercise). Suitable as the PARENT context a
    harness seeds with ``seeded_context_messages`` before driving a turn --
    exactly the seam M2 (fidelity=full continuity, EXTENSIONS.md sec12)
    exercises.
    """

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.messages: list[dict[str, Any]] = list(messages or [])
        self.set_messages_calls: list[list[dict[str, Any]]] = []

    async def add_message(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    async def get_messages_for_request(
        self, token_budget: int | None = None, provider: Any | None = None
    ) -> list[dict[str, Any]]:
        return list(self.messages)

    async def get_messages(self) -> list[dict[str, Any]]:
        return list(self.messages)

    async def set_messages(self, messages: list[dict[str, Any]]) -> None:
        self.set_messages_calls.append(list(messages))
        self.messages = list(messages)

    async def clear(self) -> None:
        self.messages = []


class CapturingHooks:
    """Records every emitted event; exposes the last ``ORCHESTRATOR_COMPLETE``
    payload, mirroring ``pipeline-runner``'s own ``_CapturingHooks`` test
    double and ``loop-amplifier-agent/tests/_fakes.py``'s ``CapturingHooks``.
    A harness's ``hooks`` argument to ``Orchestrator.execute()`` can be one
    of these directly.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.completion: dict[str, Any] = {}

    async def emit(self, event: str, data: dict[str, Any]) -> Any:
        self.events.append((event, data))
        from amplifier_core.events import ORCHESTRATOR_COMPLETE

        if event == ORCHESTRATOR_COMPLETE:
            self.completion = data
        return None
