"""The child->parent `report_outcome` verdict transport, amplifier-agent-backed.

Mirrors ``modules/pipeline-runner/tests/test_spawn_report_outcome_transport.py``
(issue #285's transport test) with one substitution: the PRODUCER is this
module's ``AmplifierAgentOrchestrator`` (hosting a REAL amplifier-agent
Engine) instead of ``amplifier_module_loop_agent.AgentOrchestrator``.

All three parties are the REAL implementations:

  * PRODUCER  -- ``amplifier_module_loop_amplifier_agent.AmplifierAgentOrchestrator``,
    running a REAL ``amplifier_agent_lib.engine.Engine`` turn;
  * VERDICT TOOL -- ``amplifier_module_tool_report_outcome.ReportOutcomeTool``,
    mounted onto the live per-turn coordinator (the mechanism this whole
    module exists to prove -- see the package docstring);
  * CONSUMER  -- ``amplifier_module_loop_pipeline.backend.AmplifierBackend``,
    whose ``_outcome_from_spawn_result`` is the only place a spawn-path
    ``is_explicit=True`` outcome can come from.

Unlike loop-agent (whose ``AgentSession`` accepts an injected ``provider``
object -- a clean seam for a scripted, no-network double), amplifier-agent's
Engine boots its OWN bundle and mounts its OWN credentialed provider module.
There is no clean dependency-injection seam for a fake LLM here, so this test
is a genuine, network-calling, real-provider smoke test rather than a fully
offline one:

  * ``pytest.importorskip("amplifier_agent_lib")`` -- skips (not fails) when
    the peer library isn't installed (e.g. this module's own hermetic unit
    tests run fine without it; this file additionally requires it).
  * skip-if-no-provider-key -- skips (not fails) in CI, which carries no
    secrets, so this test never blocks the pipeline.

The hermetic equivalent of this same transport claim (real orchestrator,
real tool, real backend reader, DOUBLED amplifier-agent Engine/bundle
machinery) lives in ``tests/test_orchestrator.py::
test_envelope_shape_matches_backend_reader`` and runs unconditionally, in
CI, on every push.
"""

from __future__ import annotations

import os
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.outcome import StageStatus

from amplifier_module_loop_amplifier_agent import AmplifierAgentOrchestrator

pytest.importorskip(
    "amplifier_agent_lib",
    reason="amplifier-agent is a heavy, Python>=3.12-only peer dependency",
)

_HAS_PROVIDER_KEY = bool(
    os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
)

pytestmark = pytest.mark.skipif(
    not _HAS_PROVIDER_KEY,
    reason="no ANTHROPIC_API_KEY/OPENAI_API_KEY in the environment -- "
    "CI carries no secrets and must skip this live test honestly",
)


class _CapturingHooks:
    """Mirrors what foundation's `PreparedBundle.spawn` does at the real
    spawn boundary: registers a temporary `orchestrator:complete` subscriber
    and keeps the last payload.
    """

    def __init__(self) -> None:
        self.completion: dict[str, Any] = {}

    async def emit(self, event: str, data: dict) -> Any:
        from amplifier_core.events import ORCHESTRATOR_COMPLETE

        if event == ORCHESTRATOR_COMPLETE:
            self.completion.update(data)
        return None


async def _run_child(prompt: str) -> dict[str, Any]:
    """Run one REAL amplifier-agent invocation, foundation-spawn-shaped."""
    hooks = _CapturingHooks()
    orch = AmplifierAgentOrchestrator(coordinator=MagicMock(), config={})
    output = await orch.execute(prompt, MagicMock(), {}, {}, hooks, coordinator=None)

    return {
        "output": output,
        "session_id": "child-session-1",
        "status": hooks.completion.get("status", "success"),
        "turn_count": hooks.completion.get("turn_count", 1),
        "metadata": hooks.completion.get("metadata", {}),
    }


class _Session:
    config: ClassVar[dict[str, Any]] = {}


class _Coordinator:
    """Minimal coordinator exposing the `session.spawn` capability."""

    def __init__(self, spawn_result: dict[str, Any]) -> None:
        self._spawn_result = spawn_result
        self.session = _Session()
        self.config: dict[str, Any] = {
            "agents": {
                "attractor-anthropic": {
                    "session": {"orchestrator": {"module": "loop-amplifier-agent"}}
                }
            }
        }

    def get_capability(self, name: str):
        return self._spawn_fn if name == "session.spawn" else None

    async def _spawn_fn(self, **kwargs: Any) -> dict[str, Any]:
        return self._spawn_result


async def _parent_outcome(spawn_result: dict[str, Any], **node_attrs: Any):
    backend = AmplifierBackend(
        coordinator=_Coordinator(spawn_result),
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = Node(
        id="intake",
        prompt="Do the work",
        attrs={"llm_provider": "anthropic", **node_attrs},
    )
    return await backend.run(node, "Do the work", PipelineContext())


@pytest.mark.asyncio
async def test_real_amplifier_agent_verdict_survives_the_spawn_boundary():
    """A real amplifier-agent turn's report_outcome call reaches the parent
    as an EXPLICIT outcome -- the amplifier-agent-backed analogue of
    pipeline-runner's #285 regression test.
    """
    result = await _run_child(
        "Call the report_outcome tool right now with status='success' and "
        "notes='amplifier-agent transport probe ok'. Call exactly one tool "
        "and nothing else -- no other tool calls, no exploration, no file "
        "reads, no shell commands."
    )

    assert result["metadata"]["report_outcome"]["status"] == "success"

    outcome = await _parent_outcome(result)

    assert outcome.is_explicit is True
    assert outcome.status is StageStatus.SUCCESS
    assert outcome.notes == "amplifier-agent transport probe ok"
