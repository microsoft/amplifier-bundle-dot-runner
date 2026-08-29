"""The child->parent verdict transport, amplifier-agent-backed (WAVE 4).

Mirrors ``modules/pipeline-runner/tests/test_spawn_report_outcome_transport.py``
(issue #285's transport test) with one substitution: the PRODUCER is this
module's ``AmplifierAgentOrchestrator`` (hosting a REAL amplifier-agent
Engine) instead of ``amplifier_module_loop_agent.AgentOrchestrator``.

WAVE 4 (maintainer ruling 2026-08-29, ruling 5): this module no longer
mounts a ``report_outcome`` reach-in tool onto the hosted agent's
coordinator. The channel this test now proves end-to-end, with all REAL
parties, is the spec's own status-file contract (canonical Sec 4.5 /
Appendix C):

  * PRODUCER -- ``amplifier_module_loop_amplifier_agent.AmplifierAgentOrchestrator``,
    running a REAL ``amplifier_agent_lib.engine.Engine`` turn;
  * CONTRACT -- ``amplifier_module_loop_pipeline.status_contract.
    build_status_file_contract`` renders the exact same contract block
    ``backend.py`` injects into a real spawn's instruction;
  * VERDICT CHANNEL -- the hosted amplifier-agent's OWN file-editing tools
    (no mounting required -- writing a file is not a foreign capability);
  * CONSUMER -- plain ``os.path`` + ``json`` reads of the real file the
    turn actually wrote, standing in for
    ``handlers/codergen.py``'s ``read_status_override`` (which runs in the
    PARENT process, outside this adapter, and is proven separately by
    pipeline-runner's spawn e2e fixture).

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

The hermetic equivalent of this same "never fabricate a verdict" claim
(real orchestrator, DOUBLED amplifier-agent Engine/bundle machinery) lives
in ``tests/test_orchestrator.py::
test_envelope_shape_never_fabricates_report_outcome`` and runs
unconditionally, in CI, on every push.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from amplifier_module_loop_amplifier_agent import AmplifierAgentOrchestrator
from amplifier_module_loop_pipeline.status_contract import build_status_file_contract

from ._fakes import FakeContextManager

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
    # Adopted-PR fix: the original PR swapped an unused MagicMock() for
    # context=None here (correct direction -- execute() now READS context,
    # so an unused MagicMock would be awaited as get_messages() and fail).
    # But the kernel never actually passes context=None in a real spawn --
    # foundation's PreparedBundle.spawn always seeds a REAL, live
    # ContextManager instance (possibly empty, never bare None) before
    # execute() runs. An empty FakeContextManager() is the faithful "no
    # prior-turn history" value for a first turn: it exercises the real
    # read-path shape (``context.get_messages()`` returns ``[]`` ->
    # ``_history_from_context`` treats that as "nothing to replay") instead
    # of short-circuiting on ``context is None``.
    output = await orch.execute(
        prompt, FakeContextManager(), {}, {}, hooks, coordinator=None
    )

    return {
        "output": output,
        "session_id": "child-session-1",
        "status": hooks.completion.get("status", "success"),
        "turn_count": hooks.completion.get("turn_count", 1),
        "metadata": hooks.completion.get("metadata", {}),
    }


@pytest.mark.asyncio
async def test_real_amplifier_agent_writes_status_file_via_its_own_tools():
    """WAVE 4 (ruling 5): the hosted amplifier-agent's explicit verdict now
    travels via the status-file contract (canonical Sec 4.5 / Appendix C),
    written with the agent's OWN file-editing tools -- NOT a mounted
    report_outcome reach-in (retired). This is the amplifier-agent-backed,
    real-network analogue of pipeline-runner's #285 regression test, ported
    to the new channel: a real turn, given the exact contract block
    ``backend.py`` injects for every spawn worker, actually writes the file.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        status_path = os.path.join(tmpdir, "status.json")
        prompt = (
            "Do a trivial task: just confirm you are ready.\n"
            + build_status_file_contract(status_path)
        )

        result = await _run_child(prompt)

        # WAVE 4: metadata never carries a fabricated report_outcome -- this
        # module no longer mounts that reach-in tool (ruling 5).
        assert result["metadata"] == {}

        assert os.path.exists(status_path), (
            "the hosted amplifier-agent never wrote the status.json path "
            "it was given in its own instructions -- the status-file "
            "contract channel did not reach the model, or it has no "
            "usable file-write tool"
        )
        data = json.loads(await asyncio.to_thread(Path(status_path).read_text))
        assert data["outcome"] in {"success", "partial_success", "retry", "fail"}
