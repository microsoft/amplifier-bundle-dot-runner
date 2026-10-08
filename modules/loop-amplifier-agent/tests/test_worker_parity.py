"""Unchanged shared MUSTs; absent legacy TARGETs have public replacements."""

import amplifier_module_loop_amplifier_agent as adapter
import pytest
from worker_parity_kit.protocol import TurnResult
from worker_parity_kit.suite import *  # noqa: F403

from ._fakes import CapturingHooks, FakeContextManager, Handles, coordinator


class Harness:
    declared_absences = frozenset(
        {"tools_passthrough", "max_turns", "telemetry_provider_identity"}
    )

    async def run_turn(
        self, prompt, seeded_context_messages=None, orchestrator_config=None
    ):
        handles, hooks = Handles(), CapturingHooks()
        old = adapter.create_agent
        adapter.create_agent = handles.create_agent
        try:
            orch = adapter.AmplifierAgentOrchestrator(
                coordinator(), orchestrator_config or {}
            )
            reply = await orch.execute(
                prompt, FakeContextManager(seeded_context_messages), {}, {}, hooks
            )
        finally:
            adapter.create_agent = old
        messages = [
            {
                "role": message.role,
                "content": "".join(part.text for part in message.content),
            }
            for message in handles.input.history or []
        ]
        messages.append({"role": "user", "content": handles.input.content[0].text})
        return TurnResult(reply, messages, hooks.completion, [], provider_events=None)


@pytest.fixture
def worker_harness():
    return Harness()
