"""worker-parity-kit wiring for loop-amplifier-agent.

See modules/worker-parity-kit/README.md ("Why this exists") for the three
incidents this kit's suite guards against. This file adds ONE thing: a
``WorkerHarness`` (``worker_parity_kit.protocol``) that drives THIS
module's REAL ``AmplifierAgentOrchestrator.execute()`` hermetically, reusing
the exact fake-Engine ``turn_handler`` seam ``tests/test_orchestrator.py``
already depends on (``tests/_fakes.py``'s ``make_fake_deps`` /
``FakeFactoryContextManager`` / ``CapturingHooks``) -- no new fakes, no
network, no credentials.

``from worker_parity_kit.suite import *`` below is what actually collects
the 3 MUST tests + the TARGET-tier parametrized test against the
``worker_harness`` fixture; nothing in this file re-implements any of them.
"""

from __future__ import annotations

import logging
from typing import Any

import amplifier_module_loop_amplifier_agent as laa
import pytest
from worker_parity_kit.protocol import TurnResult
from worker_parity_kit.suite import *

from ._fakes import (
    CapturingHooks,
    FakeContextManager,
    FakeFactoryContextManager,
    make_fake_deps,
)

#: README "Capability gaps vs the vendored CLI handler (v2)" +
#: AmplifierAgentOrchestrator's own class docstring: providers/tools are
#: accepted for Orchestrator protocol conformance, but amplifier-agent boots
#: its OWN bundle with its OWN provider mounting -- the PARENT session's
#: mounted tools are deliberately never used to drive the child turn. The
#: only TARGET capability this adapter openly does not honor.
DECLARED_ABSENCES = frozenset({"tools_passthrough"})


class _ListWarningHandler(logging.Handler):
    """Captures this module's own WARNING-level log records for a turn."""

    def __init__(self, records: list[str]) -> None:
        super().__init__(level=logging.WARNING)
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        self._records.append(record.getMessage())


class LoopAmplifierAgentHarness:
    """WorkerHarness wiring the REAL orchestrator to worker-parity-kit's
    shared suite via this module's existing hermetic fakes."""

    declared_absences: frozenset[str] = DECLARED_ABSENCES

    async def run_turn(
        self,
        prompt: str,
        seeded_context_messages: list[dict[str, Any]] | None = None,
        orchestrator_config: dict[str, Any] | None = None,
    ) -> TurnResult:
        hosted_context = FakeFactoryContextManager()
        deps, captured = make_fake_deps(reply_text="ok", context_module=hosted_context)

        original_load_deps = laa._load_dependencies
        laa._load_dependencies = lambda: (
            deps
        )  # same seam test_orchestrator.py monkeypatches
        warnings: list[str] = []
        handler = _ListWarningHandler(warnings)
        logger = logging.getLogger("amplifier_module_loop_amplifier_agent")
        logger.addHandler(handler)
        try:
            orchestrator = laa.AmplifierAgentOrchestrator(
                coordinator=object(), config=orchestrator_config or {}
            )
            parent_context = FakeContextManager(seeded_context_messages)
            hooks = CapturingHooks()
            reply = await orchestrator.execute(prompt, parent_context, {}, {}, hooks)
        finally:
            laa._load_dependencies = original_load_deps
            logger.removeHandler(handler)

        # Reconstruct "what reached the model boundary": the replayed
        # history (support#497 -- captured by FakeSession.execute() reading
        # the hosted context's get_messages_for_request()) plus the final
        # prompt text the fake session actually saw (carries user_instructions
        # + the report_outcome nudge, per _build_prompt).
        session = captured["session"]
        sent: list[dict[str, Any]] = list(session.messages_sent_to_provider or [])
        if session.prompt_seen is not None:
            sent.append({"role": "user", "content": session.prompt_seen})

        return TurnResult(
            reply=reply,
            messages_sent_to_provider=sent or None,
            completion_envelope=hooks.completion,
            warnings=warnings,
        )


@pytest.fixture
def worker_harness() -> LoopAmplifierAgentHarness:
    return LoopAmplifierAgentHarness()
