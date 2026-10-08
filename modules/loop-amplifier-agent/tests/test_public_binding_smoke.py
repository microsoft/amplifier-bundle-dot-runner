"""Missing public binding is a collection failure, never importorskip."""

import pytest
from amplifier_agent import AgentOptions, SessionOptions, create_agent
from pathlib import Path
import tomllib


def test_locks_keep_binding_main_but_engine_published_tag():
    root = Path(__file__).resolve().parents[3]
    for path in (root / "uv.lock", root / "modules/loop-amplifier-agent/uv.lock"):
        packages = {p["name"]: p for p in tomllib.loads(path.read_text())["package"]}
        assert "rev=main#" in packages["amplifier-agent"]["source"]["git"]
        assert "packages%2Fpython" in packages["amplifier-agent"]["source"]["git"]
        assert "rev=v0.22.0#" in packages["amplifier-agent-engine"]["source"]["git"]
        assert packages["amplifier-core"]["version"] == "2.0.1"
        assert (
            "21ad50fa40f7acff913cbf6615228ac359f7dedd"
            in packages["amplifier-foundation"]["source"]["git"]
        )


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
