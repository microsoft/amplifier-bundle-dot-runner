"""Real public turn; only missing credentials allow a skip."""

import os
import secrets

import pytest
from amplifier_agent import AgentOptions  # noqa: F401 -- mandatory binding import
from amplifier_module_loop_amplifier_agent import AmplifierAgentOrchestrator

from ._fakes import CapturingHooks, FakeContextManager, coordinator

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"), reason="live Anthropic credentials absent"
)


@pytest.mark.asyncio
async def test_seeded_history_recalled(tmp_path):
    fact = secrets.token_hex(12)
    history = [
        {"role": "user", "content": f"Remember code {fact}."},
        {"role": "assistant", "content": "Noted."},
    ]
    orch = AmplifierAgentOrchestrator(
        coordinator(),
        {
            "llm_provider": "anthropic",
            "llm_model": os.getenv("AA_LIVE_MODEL_A", "claude-sonnet-5-5"),
            "working_dir": str(tmp_path),
        },
    )
    reply = await orch.execute(
        "Return the code from our conversation. No tools.",
        FakeContextManager(history),
        {},
        {},
        CapturingHooks(),
    )
    assert fact in reply
