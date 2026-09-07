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


class FakeProvider:
    """Kernel-faithful double for a mounted PROVIDER MODULE.

    Authority: every provider module in this ecosystem carries a class-level
    ``name`` naming its own module family (``OpenAIProvider.name ==
    "openai"``) and resolves a ``default_model`` from its mount config in
    ``__init__``.  Those two attributes are not incidental -- they are what a
    worker reads to answer "who is about to serve this call", and therefore
    what the ``telemetry_provider_identity`` TARGET row needs present.

    An ``AsyncMock`` is NOT a substitute here, which is the whole reason this
    double exists: ``AsyncMock().name`` is Mock's own name attribute (a child
    mock, not a string), so a worker reading it correctly reports "unknown"
    and an identity assertion fails for a reason that has nothing to do with
    the worker.  Mount this instead when a harness's turn must produce
    identity-bearing provider events.

    ``instance_of`` is the shape a configured provider INSTANCE actually has
    in a spawned child: mounted under an alias (``terra``) while still
    declaring its own family (``openai``).  Pass ``name="openai"`` and mount
    it under ``"terra"``; nothing else changes.
    """

    def __init__(
        self,
        name: str = "anthropic",
        default_model: str = "claude-sonnet-5",
        reply_text: str = "ok",
    ) -> None:
        self.name = name
        self.default_model = default_model
        self.reply_text = reply_text
        #: Every ``ChatRequest`` this provider was asked to complete, in order.
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> Any:
        from amplifier_core.message_models import ChatResponse, Usage

        self.requests.append(request)
        return ChatResponse(
            content=[{"type": "text", "text": self.reply_text}],
            tool_calls=None,
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )


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
