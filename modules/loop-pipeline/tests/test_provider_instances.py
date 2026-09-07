"""Configured provider INSTANCE resolution (EXTENSIONS.md Sec 36 addendum).

The defect these pin (node-matrix run ``20260907T043835Z``, rows ``ca-terra``
and ``ca-luna``): a node declaring ``llm_provider="terra"`` -- a real,
configured provider instance on the operator's host -- was refused at startup
with "no provider module or profile is mounted for it", because the engine
only ever knew the closed table of provider MODULE names and had never read
the user's ``config.providers[]`` instances at all.

Every test here is hermetic: ``AMPLIFIER_HOME`` and ``cwd`` are pointed at a
tmp_path, so nothing reads (or depends on the shape of) the developer's real
``~/.amplifier/settings.yaml``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from amplifier_module_loop_pipeline.preflight import (
    ProviderPreflightError,
    check_provider_preflight,
)
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.provider_instances import (
    ProviderInstance,
    load_provider_instances,
    provider_instance_ids,
    settings_scope_paths,
    validate_run_provider,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write_settings(path: Path, providers: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"config": {"providers": providers}}), encoding="utf-8"
    )


_TERRA = {
    "id": "terra",
    "module": "provider-openai",
    "source": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
    "config": {
        "api_key": "${MY_TEST_KEY}",
        "base_url": "https://example.invalid/v1",
        "default_model": "gpt-5.6-terra",
        "reasoning_effort": "high",
    },
}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """An isolated Amplifier home with a global settings.yaml declaring terra."""
    h = tmp_path / "amplifier-home"
    _write_settings(h / "settings.yaml", [_TERRA])
    return h


# ---------------------------------------------------------------------------
# Scope resolution -- must match the app CLI's own SettingsPaths.default()
# ---------------------------------------------------------------------------


def test_scope_paths_are_global_project_local_in_that_order(tmp_path: Path) -> None:
    """LOWEST priority first: global -> project -> local. Same three files, in
    the same order, ``amplifier_app_cli.lib.settings.SettingsPaths.default()``
    resolves; the CLI's session scope has no dot-runner analogue."""
    paths = settings_scope_paths(home=tmp_path / "h", cwd=tmp_path / "proj", env={})
    assert paths == [
        tmp_path / "h" / "settings.yaml",
        tmp_path / "proj" / ".amplifier" / "settings.yaml",
        tmp_path / "proj" / ".amplifier" / "settings.local.yaml",
    ]


def test_amplifier_home_env_var_wins_over_the_default(tmp_path: Path) -> None:
    """``$AMPLIFIER_HOME`` is the first rung of
    ``amplifier_foundation.paths.resolution.get_amplifier_home()``; resolving
    it anywhere else would read a DIFFERENT settings file than the CLI."""
    paths = settings_scope_paths(cwd=tmp_path, env={"AMPLIFIER_HOME": str(tmp_path / "custom")})
    assert paths[0] == (tmp_path / "custom").resolve() / "settings.yaml"


def test_missing_settings_tree_is_empty_not_an_error(tmp_path: Path) -> None:
    """A CI host with no Amplifier settings at all is ordinary, not broken."""
    assert load_provider_instances(home=tmp_path / "nope", cwd=tmp_path, env={}) == {}


# ---------------------------------------------------------------------------
# Instance identity
# ---------------------------------------------------------------------------


def test_instance_is_addressable_by_its_id(home: Path, tmp_path: Path) -> None:
    instances = load_provider_instances(home=home, cwd=tmp_path, env={})
    assert set(instances) == {"terra"}
    terra = instances["terra"]
    assert terra.module == "provider-openai"
    assert terra.canonical_module == "openai"
    assert terra.config["default_model"] == "gpt-5.6-terra"


def test_entry_without_an_id_is_not_an_addressable_instance(
    tmp_path: Path,
) -> None:
    """A bare module override is already addressable as ``openai``; inventing
    a second name for it would be an address no human ever typed."""
    h = tmp_path / "h"
    _write_settings(
        h / "settings.yaml",
        [{"module": "provider-openai", "config": {"default_model": "gpt-5"}}],
    )
    assert load_provider_instances(home=h, cwd=tmp_path, env={}) == {}


def test_entry_without_a_module_is_dropped(tmp_path: Path) -> None:
    """An id naming nothing mountable must not half-mount."""
    h = tmp_path / "h"
    _write_settings(h / "settings.yaml", [{"id": "ghost", "config": {"x": 1}}])
    assert load_provider_instances(home=h, cwd=tmp_path, env={}) == {}


# ---------------------------------------------------------------------------
# Scope merging -- identity key and deep config merge
# ---------------------------------------------------------------------------


def test_project_scope_deep_merges_over_global_by_id(home: Path, tmp_path: Path) -> None:
    """The CLI's ``_merge_provider_lists``/``merge_module_items`` rule: the
    higher scope overrides ONE config key without dropping the rest (the
    api_key the global scope supplied must survive)."""
    proj = tmp_path / "proj"
    _write_settings(
        proj / ".amplifier" / "settings.yaml",
        [{"id": "terra", "module": "provider-openai", "config": {"default_model": "gpt-5.6-override"}}],
    )
    terra = load_provider_instances(home=home, cwd=proj, env={"MY_TEST_KEY": "sk-live"})["terra"]
    assert terra.config["default_model"] == "gpt-5.6-override"
    assert terra.config["api_key"] == "sk-live"
    assert terra.config["base_url"] == "https://example.invalid/v1"


def test_local_scope_beats_project_scope(home: Path, tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    _write_settings(
        proj / ".amplifier" / "settings.yaml",
        [{"id": "terra", "module": "provider-openai", "config": {"default_model": "from-project"}}],
    )
    _write_settings(
        proj / ".amplifier" / "settings.local.yaml",
        [{"id": "terra", "module": "provider-openai", "config": {"default_model": "from-local"}}],
    )
    terra = load_provider_instances(home=home, cwd=proj, env={})["terra"]
    assert terra.config["default_model"] == "from-local"


def test_a_malformed_scope_file_is_skipped_not_fatal(home: Path, tmp_path: Path) -> None:
    """A half-written settings.yaml must not take down every reader -- the
    CLI's own ``except Exception: pass`` behaviour."""
    proj = tmp_path / "proj"
    (proj / ".amplifier").mkdir(parents=True)
    (proj / ".amplifier" / "settings.yaml").write_text("{{{not yaml", encoding="utf-8")
    assert set(load_provider_instances(home=home, cwd=proj, env={})) == {"terra"}


# ---------------------------------------------------------------------------
# ${VAR} expansion -- environment first, then keys.env
# ---------------------------------------------------------------------------


def test_placeholder_resolves_from_the_environment(home: Path, tmp_path: Path) -> None:
    terra = load_provider_instances(home=home, cwd=tmp_path, env={"MY_TEST_KEY": "sk-env"})["terra"]
    assert terra.config["api_key"] == "sk-env"


def test_placeholder_resolves_from_keys_env_when_the_environment_is_silent(
    home: Path, tmp_path: Path
) -> None:
    """The CLI stores secrets in ``<amplifier-home>/keys.env`` and references
    them by placeholder; an engine that only read os.environ would mount a
    literal ``${MY_TEST_KEY}`` as the api_key."""
    (home / "keys.env").write_text('MY_TEST_KEY="sk-from-file"\n# comment\n', encoding="utf-8")
    terra = load_provider_instances(home=home, cwd=tmp_path, env={})["terra"]
    assert terra.config["api_key"] == "sk-from-file"


def test_environment_wins_over_keys_env(home: Path, tmp_path: Path) -> None:
    """Same precedence ``KeyManager._load_keys`` uses (it only sets a key that
    is not already in the environment)."""
    (home / "keys.env").write_text("MY_TEST_KEY=sk-from-file\n", encoding="utf-8")
    terra = load_provider_instances(home=home, cwd=tmp_path, env={"MY_TEST_KEY": "sk-env"})["terra"]
    assert terra.config["api_key"] == "sk-env"


def test_unresolvable_placeholder_is_left_verbatim_never_blanked(
    home: Path, tmp_path: Path
) -> None:
    """Blanking it would hand the provider module an empty credential and turn
    a nameable misconfiguration into a confusing auth error."""
    terra = load_provider_instances(home=home, cwd=tmp_path, env={})["terra"]
    assert terra.config["api_key"] == "${MY_TEST_KEY}"


# ---------------------------------------------------------------------------
# --provider: two address spaces, one check
# ---------------------------------------------------------------------------

_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def test_module_name_still_requires_its_credential() -> None:
    problem = validate_run_provider(
        "anthropic", key_env=_KEY_ENV, env={}, instances={}
    )
    assert problem is not None and "ANTHROPIC_API_KEY" in problem


def test_module_name_with_its_credential_passes() -> None:
    assert (
        validate_run_provider(
            "anthropic", key_env=_KEY_ENV, env={"ANTHROPIC_API_KEY": "k"}, instances={}
        )
        is None
    )


def test_instance_id_passes_without_the_modules_credential() -> None:
    """``--provider terra`` must not demand OPENAI_API_KEY: the instance
    carries its own api_key in the user's settings."""
    instances = {"terra": ProviderInstance(id="terra", module="provider-openai")}
    assert validate_run_provider("terra", key_env=_KEY_ENV, env={}, instances=instances) is None


def test_unknown_name_names_both_address_spaces() -> None:
    """"unknown provider 'terra'" listing only module names is the message
    that sends a human hunting for a typo they did not make."""
    instances = {"terra": ProviderInstance(id="terra", module="provider-openai")}
    problem = validate_run_provider("nope", key_env=_KEY_ENV, env={}, instances=instances)
    assert problem is not None
    assert "anthropic" in problem and "openai" in problem  # module address space
    assert "terra" in problem  # instance address space


# ---------------------------------------------------------------------------
# Preflight -- the actual refusal this feature exists to unblock
# ---------------------------------------------------------------------------

_DOT_TERRA = """\
digraph instance {
    graph [goal="instance routing"]
    start [shape=Mdiamond]
    author [shape=box, llm_provider="terra", prompt="write"]
    done [shape=Msquare]
    start -> author -> done
}
"""


def test_preflight_passes_when_the_instance_is_mounted() -> None:
    """The ca-terra row's failure, inverted: with the instance mounted (as a
    provider AND routed by a profile) the run starts."""
    graph = parse_dot(_DOT_TERRA)
    check_provider_preflight(
        graph,
        mounted_providers=("anthropic", "terra"),
        profiles={"anthropic": "w", "terra": "w"},
        resolvable_profiles={"w"},
        env={},
    )


def test_preflight_still_refuses_an_unknown_instance_id(monkeypatch, tmp_path: Path) -> None:
    """Issue #155's guarantee is kept: an id nothing serves refuses at startup,
    naming the node -- and now also naming the ids that WOULD have worked."""
    h = tmp_path / "h"
    _write_settings(h / "settings.yaml", [_TERRA, {**_TERRA, "id": "luna"}])
    monkeypatch.setenv("AMPLIFIER_HOME", str(h))
    monkeypatch.chdir(tmp_path)

    graph = parse_dot(_DOT_TERRA.replace('"terra"', '"tera"'))
    with pytest.raises(ProviderPreflightError) as exc:
        check_provider_preflight(
            graph,
            mounted_providers=("anthropic",),
            profiles={"anthropic": "w"},
            resolvable_profiles={"w"},
            env={},
        )
    message = str(exc.value)
    assert "tera" in message  # the name that failed
    assert "author" in message  # the node that declared it
    assert "terra" in message and "luna" in message  # what IS addressable


def test_preflight_message_says_so_when_no_instances_are_configured(
    monkeypatch, tmp_path: Path
) -> None:
    """Silence about instances would leave a human on a bare host guessing
    whether the feature exists at all."""
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "empty-home"))
    monkeypatch.chdir(tmp_path)
    graph = parse_dot(_DOT_TERRA)
    with pytest.raises(ProviderPreflightError) as exc:
        check_provider_preflight(
            graph,
            mounted_providers=("anthropic",),
            profiles={"anthropic": "w"},
            resolvable_profiles={"w"},
            env={},
        )
    assert "No provider instances are configured" in str(exc.value)


def test_provider_instance_ids_never_raises(tmp_path: Path) -> None:
    """A diagnostic helper must never be the thing that crashes the diagnosis."""
    assert provider_instance_ids(home=tmp_path / "absent", cwd=tmp_path, env={}) == []
