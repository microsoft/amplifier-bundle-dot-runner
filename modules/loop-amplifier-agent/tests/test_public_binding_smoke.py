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
        assert "rev=v0.30.1#" in packages["amplifier-agent-engine"]["source"]["git"]
        assert packages["amplifier-core"]["version"].startswith("2.")
        assert "?rev=main#" in packages["amplifier-foundation"]["source"]["git"]


def test_published_foundation_source_matches_current_consumer_prerequisite():
    """The real --no-sources solve must not revive the old SHA/main conflict."""
    root = Path(__file__).resolve().parents[3]
    runner = tomllib.loads(
        (root / "modules/pipeline-runner/pyproject.toml").read_text()
    )
    requirement = (
        "amplifier-foundation @ "
        "git+https://github.com/microsoft/amplifier-foundation@main"
    )
    assert requirement in runner["project"]["dependencies"]
    assert runner["tool"]["uv"]["sources"]["amplifier-foundation"]["rev"] == "main"
    workflow = (root / ".github/workflows/ci.yml").read_text()
    root_install = workflow.split("  root-package-no-sources-install:", 1)[1].split(
        "  conformance-matrix:", 1
    )[0]
    assert requirement in root_install
    assert "21ad50fa40f7acff913cbf6615228ac359f7dedd" not in root_install
    assert (
        "Never replace external binding/engine/tool/Foundation sources." in root_install
    )
    # Root's legacy development correction must not become an external
    # Foundation override that hides the published-source conflict.
    manifest = tomllib.loads((root / "pyproject.toml").read_text())
    assert all(
        "amplifier-foundation" not in item
        for item in manifest["tool"]["uv"].get("override-dependencies", [])
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
