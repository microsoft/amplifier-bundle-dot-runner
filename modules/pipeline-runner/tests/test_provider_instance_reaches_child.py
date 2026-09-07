"""The mounted instance must ANSWER to its id in the child's mount plan.

EXTENSIONS.md Sec 36 addendum (2026-09-07).  Mounting an instance under its
own id (``test_synthesized_bundle_provider_instances.py``) makes
``providers["terra"]`` exist for ``loop-agent`` to select.  It does not, on
its own, deliver the node's declared MODEL to it -- and those are two
different layers reading two different keys:

* ``amplifier_core._session_init`` remaps the mount name from
  ``instance_id``;
* ``amplifier_foundation.spawn_utils`` indexes mount-plan entries by ``id``
  (``_build_provider_lookup``/``_find_provider_index``), and that is the
  index a ``ProviderPreference`` is matched against.

``backend.py`` sends the node's resolved ``llm_model`` as exactly such a
preference (``_ProviderPreference(provider=<llm_provider>, model=<model>)``),
so an entry carrying only ``instance_id`` is invisible to it: the preference
matches nothing, is silently dropped, and the instance runs on whatever
``default_model`` its settings file happens to carry -- no matter what the
node declared.  Emitting both keys from the one resolved field closes that,
and these tests hold it closed against the REAL foundation function rather
than a restatement of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from amplifier_foundation.spawn_utils import (
    ProviderPreference,
    apply_provider_preferences,
)

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

#: A fake instance, in the exact on-disk shape the app CLI writes.  Its id is
#: not a substring of any provider family name, so nothing can match it by
#: accident.
_FAKEINST = {
    "id": "fakeinst",
    "module": "provider-openai",
    "source": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
    "config": {
        "api_key": "sk-fakeinst",
        "base_url": "https://example.invalid/v1",
        "default_model": "gpt-fake-settings",
    },
}

_DOT = """\
digraph instance {
    start [shape=Mdiamond]
    author [shape=box, llm_provider="fakeinst", llm_model="gpt-fake-node",
            prompt="write"]
    done [shape=Msquare]
    start -> author -> done
}
"""


@pytest.fixture
def isolated_host(monkeypatch, tmp_path: Path) -> Path:
    """A tmp Amplifier home configuring `fakeinst`, no real credentials."""
    home = tmp_path / "amplifier-home"
    home.mkdir(parents=True)
    (home / "settings.yaml").write_text(
        yaml.safe_dump({"config": {"providers": [_FAKEINST]}}), encoding="utf-8"
    )
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(
        "AMPLIFIER_OPENAI_CHATGPT_OAUTH_PATH_OVERRIDE", str(tmp_path / "absent.json")
    )
    monkeypatch.chdir(tmp_path)
    return home


def _providers_section() -> list[dict]:
    bundle = yaml.safe_load(
        default_worker._synthesize_agent_bundle_yaml("coding-agent", dot_source=_DOT)
    )
    return bundle["providers"]


def _entry(providers: list[dict], instance_id: str) -> dict:
    matches = [p for p in providers if p.get("instance_id") == instance_id]
    assert len(matches) == 1, f"expected exactly one {instance_id} entry: {providers}"
    return matches[0]


# ---------------------------------------------------------------------------
# The two id keys, and why both
# ---------------------------------------------------------------------------


def test_the_entry_carries_both_id_keys(isolated_host: Path) -> None:
    """Two layers, two keys, one instance -- emitted from the same field so
    they cannot drift into naming different things."""
    entry = _entry(_providers_section(), "fakeinst")
    assert entry["instance_id"] == "fakeinst", "amplifier-core's mount remap"
    assert entry["id"] == "fakeinst", "amplifier-foundation's mount-plan index"


def test_the_nodes_model_reaches_the_instance(isolated_host: Path) -> None:
    """The load-bearing one, against foundation's OWN matcher.

    A node declaring ``llm_provider="fakeinst" llm_model="gpt-fake-node"``
    must end up calling ``gpt-fake-node`` -- not the ``gpt-fake-settings``
    default its settings entry carries.
    """
    plan = {"providers": _providers_section()}

    applied = apply_provider_preferences(
        plan, [ProviderPreference(provider="fakeinst", model="gpt-fake-node")]
    )

    entry = _entry(applied["providers"], "fakeinst")
    assert entry["config"]["default_model"] == "gpt-fake-node", (
        "the node's declared llm_model must reach the instance; a mount-plan "
        "entry foundation cannot index by the node's llm_provider silently "
        "drops the preference and runs the settings default instead"
    )


def test_the_preference_is_dropped_without_the_id_key(isolated_host: Path) -> None:
    """RED-proof, run as a test rather than described in a commit message.

    Strip ONLY the ``id`` key -- the exact pre-fix shape, where the instance
    is still correctly mounted and still correctly routed -- and the same
    preference silently vanishes.  Nothing raises; the run simply calls a
    different model than the graph declares.  That is the failure mode this
    key exists to remove, and it is why the assertion above is not merely a
    restatement of the emitter.
    """
    providers = _providers_section()
    stripped = [{k: v for k, v in p.items() if k != "id"} for p in providers]

    applied = apply_provider_preferences(
        {"providers": stripped},
        [ProviderPreference(provider="fakeinst", model="gpt-fake-node")],
    )

    entry = _entry(applied["providers"], "fakeinst")
    assert entry["config"]["default_model"] == "gpt-fake-settings", (
        "without the id key foundation must find no match at all -- if this "
        "now passes the preference through, the RED half of this proof has "
        "rotted and the test above proves nothing"
    )


def test_the_id_key_does_not_disturb_the_module_entries(isolated_host: Path) -> None:
    """Module entries are addressed by ``module`` and must stay untouched --
    adding an ``id`` to them would create a second address for a provider
    that already has a canonical one."""
    for entry in _providers_section():
        if entry.get("instance_id"):
            continue
        assert "id" not in entry, f"module entry grew an instance id: {entry}"


# ---------------------------------------------------------------------------
# WHICH COPY of the worker runs
# ---------------------------------------------------------------------------


def test_the_importability_probe_answers_about_this_environment() -> None:
    """The probe is a real ``find_spec``, not a constant: it says yes for a
    module that IS importable here and no for one that is not (including a
    malformed name, which ``find_spec`` raises on rather than returning
    None)."""
    assert default_worker._adapter_importable("amplifier_module_pipeline_runner")
    assert not default_worker._adapter_importable("amplifier_module_not_installed_xyz")
    assert not default_worker._adapter_importable("")


def test_no_source_pin_when_the_adapter_is_importable(
    isolated_host: Path, monkeypatch
) -> None:
    """The agent's orchestrator must not drag `@main`'s worker into a run
    whose own worker is already installed.

    A ``source:`` is a lazy-activation hint: foundation fetches that ref and
    core inserts the fetched path at ``sys.path[0]`` before importing the
    entry point.  With the pin present, an engine run from a checkout spawns
    its nodes on ``@main``'s worker code -- measured on this branch, where a
    worker-side fix installed in the run's venv did not execute and the run
    failed with ``@main``'s error text instead.  The adapter is an
    unconditional root dependency, so on a healthy install core's own
    entry-point discovery finds it and the hint is pure harm.

    The probe is patched rather than relied on: ``loop-agent`` is not a
    dependency of THIS module's test venv, so an unpatched assertion here
    would be measuring the venv's contents instead of the emitter's branch.
    ``test_the_importability_probe_answers_about_this_environment`` covers
    the probe itself.
    """
    monkeypatch.setattr(default_worker, "_adapter_importable", lambda _module: True)

    bundle = yaml.safe_load(
        default_worker._synthesize_agent_bundle_yaml("coding-agent", dot_source=_DOT)
    )
    orchestrator = bundle["agents"][default_worker.DEFAULT_AGENT_NAME]["session"][
        "orchestrator"
    ]
    assert orchestrator["module"] == "loop-agent", "the module name still names it"
    assert "source" not in orchestrator, (
        "the adapter is importable, so the run must mount the copy it "
        "actually ships -- not whatever @main happens to hold"
    )


def test_source_pin_survives_for_a_broken_install(
    isolated_host: Path, monkeypatch
) -> None:
    """The hint is narrowed, never deleted.  When the adapter genuinely is
    not importable -- an abnormal, broken-install state -- fetching a
    known-good copy beats failing to mount at all."""
    monkeypatch.setattr(default_worker, "_adapter_importable", lambda _module: False)

    bundle = yaml.safe_load(
        default_worker._synthesize_agent_bundle_yaml("coding-agent", dot_source=_DOT)
    )
    orchestrator = bundle["agents"][default_worker.DEFAULT_AGENT_NAME]["session"][
        "orchestrator"
    ]
    assert orchestrator["source"].startswith("git+https://github.com/microsoft/")
    assert orchestrator["source"].endswith("#subdirectory=modules/loop-agent")
