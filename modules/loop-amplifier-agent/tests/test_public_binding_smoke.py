"""Missing public binding is a collection failure, never importorskip."""

import pytest
from amplifier_agent import AgentOptions, SessionOptions, create_agent


@pytest.mark.asyncio
async def test_credential_free_construct_close(tmp_path):
    # Local Ollama construction needs no credentials, service or model lookup.
    # No turn is started: this proves installation/lifecycle, not inference.
    agent = await create_agent(
        AgentOptions(
            provider="ollama",
            model="smoke-model",
            working_directory=tmp_path,
            sessions_directory=tmp_path / "state",
            environment={"OLLAMA_HOST": "http://127.0.0.1:11434"},
        )
    )
    try:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        await session.close()
    finally:
        await agent.close()
