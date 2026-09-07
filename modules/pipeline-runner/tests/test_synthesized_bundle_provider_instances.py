"""The synthesized worker bundle mounts a configured provider INSTANCE.

Pins the second half of the ``ca-terra``/``ca-luna`` fix (node-matrix run
``20260907T043835Z``): resolving ``terra`` from settings is not enough --
the run must actually MOUNT it, under that id, and ROUTE to it, or the
failure just moves from the startup preflight to one of two deeper places:

* no ``profiles:`` entry -> ``backend.py``'s exact-or-nothing profile lookup
  refuses the node mid-walk ("no profile is mounted for that provider");
* no mounted provider under the id -> ``loop-agent``'s
  ``providers[llm_provider]`` lookup refuses it inside the spawned child
  ("Available providers: [...]").

Both halves are emitted from ONE resolved map so they cannot drift apart.
Hermetic: ``AMPLIFIER_HOME``/cwd point at tmp_path, and provider credential
env vars are cleared, so nothing depends on the developer's own host.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from amplifier_module_pipeline_runner import default_worker

_CREDENTIAL_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "COPILOT_AGENT_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)

_TERRA = {
    "id": "terra",
    "module": "provider-openai",
    "source": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
    "config": {"api_key": "sk-terra", "base_url": "https://example.invalid/v1"},
}

_DOT_TERRA = """\
digraph instance {
    start [shape=Mdiamond]
    author [shape=box, llm_provider="terra", prompt="write"]
    done [shape=Msquare]
    start -> author -> done
}
"""


@pytest.fixture
def isolated_host(monkeypatch, tmp_path: Path) -> Path:
    """A tmp Amplifier home configuring `terra` and `luna`, no real creds."""
    home = tmp_path / "amplifier-home"
    home.mkdir(parents=True)
    (home / "settings.yaml").write_text(
        yaml.safe_dump({"config": {"providers": [_TERRA, {**_TERRA, "id": "luna"}]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # openai-chatgpt probes an OAuth token cache on disk -- point it at an
    # absent path so this test cannot depend on the developer's own login.
    monkeypatch.setenv(
        "AMPLIFIER_OPENAI_CHATGPT_OAUTH_PATH_OVERRIDE", str(tmp_path / "absent.json")
    )
    monkeypatch.chdir(tmp_path)
    return home


def _bundle(dot_source: str | None, run_provider: str | None = None) -> dict:
    return yaml.safe_load(
        default_worker._synthesize_agent_bundle_yaml(
            "coding-agent", dot_source=dot_source, run_provider=run_provider
        )
    )


# ---------------------------------------------------------------------------
# Selection -- only what the run NAMES
# ---------------------------------------------------------------------------


def test_a_node_declaring_an_instance_id_selects_it(isolated_host: Path) -> None:
    selected = default_worker.selected_provider_instances(_DOT_TERRA)
    assert set(selected) == {"terra"}


def test_the_run_provider_flag_selects_an_instance(isolated_host: Path) -> None:
    """``--provider luna`` with a graph that declares nothing."""
    selected = default_worker.selected_provider_instances(
        "digraph g { a [shape=box, prompt=\"x\"] }", run_provider="luna"
    )
    assert set(selected) == {"luna"}


def test_unnamed_instances_are_not_mounted(isolated_host: Path) -> None:
    """Mounting all 15 of an operator's configured instances on every run
    would load a dozen provider modules the pipeline never asked for."""
    assert default_worker.selected_provider_instances(_DOT_TERRA) .keys() == {"terra"}


def test_a_module_name_is_never_reinterpreted_as_an_instance(
    isolated_host: Path, tmp_path: Path
) -> None:
    """An instance someone named ``openai`` must not silently take over the
    module address -- that would change what an existing graph means."""
    (tmp_path / "amplifier-home" / "settings.yaml").write_text(
        yaml.safe_dump(
            {"config": {"providers": [{**_TERRA, "id": "openai"}]}}
        ),
        encoding="utf-8",
    )
    selected = default_worker.selected_provider_instances(
        'digraph g { a [shape=box, llm_provider="openai", prompt="x"] }'
    )
    assert selected == {}


# ---------------------------------------------------------------------------
# Emission -- both halves, from one map
# ---------------------------------------------------------------------------


def test_instance_is_mounted_under_its_own_id(isolated_host: Path) -> None:
    """``instance_id`` is amplifier-core's own multi-instance seam: the
    provider self-mounts at ``openai`` and core remaps it to ``terra``, which
    is the exact key loop-agent looks the node's llm_provider up in."""
    entries = _bundle(_DOT_TERRA)["providers"]
    terra = [e for e in entries if e.get("instance_id") == "terra"]
    assert len(terra) == 1
    assert terra[0]["module"] == "provider-openai"
    assert terra[0]["source"] == _TERRA["source"]
    assert terra[0]["config"]["api_key"] == "sk-terra"
    assert terra[0]["config"]["base_url"] == "https://example.invalid/v1"


def test_instance_gets_a_profiles_route(isolated_host: Path) -> None:
    profiles = _bundle(_DOT_TERRA)["session"]["orchestrator"]["config"]["profiles"]
    assert profiles["terra"] == default_worker.DEFAULT_AGENT_NAME
    # the module table is untouched
    assert profiles["anthropic"] == default_worker.DEFAULT_AGENT_NAME


def test_instance_only_host_still_synthesizes_a_bundle(isolated_host: Path) -> None:
    """No module credential is set at all here. Before this change that was a
    hard NoProviderConfiguredError -- yet the host can serve `terra` fine."""
    entries = _bundle(_DOT_TERRA)["providers"]
    assert [e.get("instance_id") for e in entries] == ["terra"]


def test_instances_are_emitted_after_the_module_entries(
    isolated_host: Path, monkeypatch
) -> None:
    """A child that declares NO llm_provider falls back to
    ``next(iter(providers))``; prepending an instance would silently change
    the default provider of every node in an unrelated graph."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    entries = _bundle(_DOT_TERRA)["providers"]
    assert entries[0]["module"] == "provider-anthropic"
    assert entries[-1]["instance_id"] == "terra"


def test_no_instance_named_means_a_byte_identical_bundle(
    isolated_host: Path, monkeypatch
) -> None:
    """Existing graphs must be untouched: same bytes as before this feature."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    plain = 'digraph g { a [shape=box, llm_provider="anthropic", prompt="x"] }'
    with_flag = default_worker._synthesize_agent_bundle_yaml(
        "coding-agent", dot_source=plain, run_provider="anthropic"
    )
    without = default_worker._synthesize_agent_bundle_yaml(
        "coding-agent", dot_source=plain
    )
    assert with_flag == without
    assert "instance_id" not in without


def test_an_api_key_cannot_break_out_of_the_emitted_yaml(
    isolated_host: Path, tmp_path: Path
) -> None:
    """Config values are arbitrary text; they are dumped, never interpolated."""
    hostile = {**_TERRA, "config": {"api_key": "x\nproviders: []\n#"}}
    (tmp_path / "amplifier-home" / "settings.yaml").write_text(
        yaml.safe_dump({"config": {"providers": [hostile]}}), encoding="utf-8"
    )
    entries = _bundle(_DOT_TERRA)["providers"]
    terra = [e for e in entries if e.get("instance_id") == "terra"][0]
    assert terra["config"]["api_key"] == "x\nproviders: []\n#"
