"""Configured provider INSTANCE resolution (EXTENSIONS.md section 36 addendum).

THE GAP THIS CLOSES.  ``provider_detection.PROVIDER_SPECS`` is a closed table
of provider *module* names -- ``anthropic``, ``openai``, ``gemini``,
``github-copilot``, ``openai-chatgpt``.  A user's Amplifier settings can
configure MANY named INSTANCES of those same modules -- ``terra`` and ``luna``
are both ``provider-openai`` with different ``base_url``/``default_model``/
``reasoning_effort``.  Those instance ids are how a human addresses a model on
their own host (``amplifier provider list``), but the pipeline never learned
them: a node declaring ``llm_provider="terra"`` was refused at startup by the
issue-#155 preflight with "no provider module or profile is mounted for it",
because the synthesized worker bundle only ever mounted the closed table.

WHAT THIS MODULE DOES.  Reads the SAME settings files, in the SAME scope
order, with the SAME merge-by-identity rule the Amplifier CLI's
``AppSettings.get_provider_overrides()`` uses, and returns the addressable
instances keyed by their ``id``.  It deliberately does NOT import
``amplifier_app_cli``: the engine is installed as its own tool (see the root
``pyproject.toml``) and does not depend on the app CLI package, so an import
would be an environment-dependent, silently-absent code path -- exactly the
failure class this repo's preflight exists to prevent.  The behaviour is
pinned to the CLI's instead by ``tests/test_provider_instances.py``, which
asserts the scope order, the identity key, and the deep-merge rule against
fixtures written in the CLI's own on-disk shape.

WHAT AN "INSTANCE" IS HERE.  Only a ``config.providers[]`` entry carrying an
explicit ``id`` is an addressable instance.  An entry without one is a plain
module-level override (its provider is already addressable by the module's
canonical name, e.g. ``openai``) and is intentionally not returned -- adding
it under a synthesized name would invent an address no human ever typed.

SECRETS.  Instance ``config`` values are stored by the CLI as ``${VAR}``
placeholders backed by ``<amplifier-home>/keys.env`` (see the app CLI's
``key_manager``/``provider_config_utils``).  Those placeholders are expanded
here, from ``os.environ`` first and ``keys.env`` second -- the same
"environment wins over the file" precedence ``KeyManager._load_keys`` uses.
An UNRESOLVABLE placeholder is left verbatim rather than blanked, so a
mis-set key surfaces as the provider module's own loud auth failure naming
the literal ``${VAR}``, never as a silent empty credential.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "ProviderInstance",
    "load_provider_instances",
    "provider_instance_ids",
    "settings_scope_paths",
]


@dataclass(frozen=True)
class ProviderInstance:
    """One addressable provider instance from the user's Amplifier settings.

    Attributes:
        id: The instance id a node addresses via ``llm_provider="<id>"``
            (``config.providers[].id`` -- e.g. ``terra``).
        module: The provider MODULE this instance is an instance of (e.g.
            ``provider-openai``).  Kept in the settings' own ``provider-``
            prefixed form; :attr:`canonical_module` strips it.
        source: The module source URI, or ``None`` when the settings entry
            omits it (the loader then resolves the module by name).
        config: The instance's config block, with ``${VAR}`` placeholders
            already expanded (see the module docstring's SECRETS note).
    """

    id: str
    module: str
    source: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)

    @property
    def canonical_module(self) -> str:
        """``provider-openai`` -> ``openai`` -- the name amplifier-core mounts
        a provider under by default, and the name this instance is REMAPPED
        away from by its ``instance_id`` (see ``amplifier_core._session_init``)."""
        return (
            self.module.removeprefix("provider-")
            if self.module.startswith("provider-")
            else self.module
        )


def settings_scope_paths(
    *,
    home: Path | str | None = None,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> list[Path]:
    """The settings files to merge, LOWEST priority first.

    Mirrors ``amplifier_app_cli.lib.settings.SettingsPaths.default()`` and the
    scope order documented on ``AppSettings``: global -> project -> local
    (most specific wins).  The session scope is deliberately absent: it is
    keyed by a CLI session id that does not exist on a ``dot-runner`` run.

    Args:
        home: Amplifier home.  Defaults to ``$AMPLIFIER_HOME`` then
            ``~/.amplifier`` -- the same resolution order
            ``amplifier_foundation.paths.resolution.get_amplifier_home()``
            documents.  Exposed for hermetic tests.
        cwd: Project root for the project/local scopes.  Defaults to the
            process working directory.
        env: Environment mapping (defaults to ``os.environ``).
    """
    source = env if env is not None else os.environ
    if home is None:
        raw_home = source.get("AMPLIFIER_HOME")
        home_path = (
            Path(raw_home).expanduser().resolve()
            if raw_home
            else (Path.home() / ".amplifier").resolve()
        )
    else:
        home_path = Path(home).expanduser()
    root = Path(cwd) if cwd is not None else Path.cwd()
    return [
        home_path / "settings.yaml",
        root / ".amplifier" / "settings.yaml",
        root / ".amplifier" / "settings.local.yaml",
    ]


def _keys_env_path(
    *, home: Path | str | None = None, env: Mapping[str, str] | None = None
) -> Path:
    """``<amplifier-home>/keys.env`` -- the app CLI's ``KeyManager`` store."""
    return settings_scope_paths(home=home, cwd=Path("."), env=env)[0].parent / "keys.env"


def _load_keys_env(path: Path) -> dict[str, str]:
    """Parse a ``KEY=value`` keys.env file.  Mirrors ``KeyManager._load_keys``:
    ``#`` comments and blank lines skipped, surrounding quotes stripped.  A
    missing or unreadable file yields ``{}`` -- never raises, because an
    absent keys store is the normal case for a CI host that passes real env
    vars instead."""
    keys: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return keys
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        keys[name.strip()] = value.strip().strip('"').strip("'")
    return keys


def _expand(value: Any, lookup: Mapping[str, str]) -> Any:
    """Expand a whole-string ``${VAR}`` placeholder, recursively through
    dicts/lists.  Only the CLI's own placeholder shape is honoured -- a value
    that IS exactly ``${VAR}`` (see ``provider_config_utils.py``'s
    ``value.startswith("${") and value.endswith("}")`` test).  An unset VAR
    leaves the literal in place; see the module docstring."""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}") and len(value) > 3:
            name = value[2:-1]
            resolved = lookup.get(name)
            return resolved if resolved else value
        return value
    if isinstance(value, dict):
        return {k: _expand(v, lookup) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, lookup) for v in value]
    return value


def _provider_key(entry: Mapping[str, Any]) -> str | None:
    """Identity key for scope merging -- ``id`` if present, else ``module``.
    Mirrors ``amplifier_app_cli.lib.merge_utils._provider_key``."""
    return entry.get("id") or entry.get("module")


def _deep_merge_entry(
    base: Mapping[str, Any], overlay: Mapping[str, Any]
) -> dict[str, Any]:
    """Deep-merge one provider entry over another (overlay wins), merging the
    nested ``config`` block key-by-key rather than replacing it -- the
    behaviour ``merge_module_items`` gives the CLI, so a project scope can
    override just ``default_model`` without dropping the global scope's
    ``api_key``."""
    merged = dict(base)
    for key, value in overlay.items():
        if (
            key == "config"
            and isinstance(value, Mapping)
            and isinstance(merged.get(key), Mapping)
        ):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _merge_scope_entries(
    scopes: Iterable[list[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Merge each scope's ``config.providers`` list, lowest priority first."""
    result: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for entries in scopes:
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            key = _provider_key(entry)
            if key and key in index:
                pos = index[key]
                result[pos] = _deep_merge_entry(result[pos], entry)
            else:
                result.append(dict(entry))
                if key:
                    index[key] = len(result) - 1
    return result


def _read_scope(path: Path) -> list[Mapping[str, Any]]:
    """``config.providers`` from one settings file.  A missing or malformed
    file yields ``[]`` -- the CLI's own ``except Exception: pass`` behaviour
    (a half-written settings.yaml must not take down every reader)."""
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(content, Mapping):
        return []
    config = content.get("config")
    if not isinstance(config, Mapping):
        return []
    providers = config.get("providers")
    if not isinstance(providers, list):
        return []
    return [p for p in providers if isinstance(p, Mapping)]


def load_provider_instances(
    *,
    home: Path | str | None = None,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, ProviderInstance]:
    """Addressable provider instances from the user's Amplifier settings.

    Args:
        home: Amplifier home override (see :func:`settings_scope_paths`).
        cwd: Project root override (see :func:`settings_scope_paths`).
        env: Environment mapping for ``${VAR}`` expansion and
            ``$AMPLIFIER_HOME`` resolution (defaults to ``os.environ``).

    Returns:
        ``{instance_id: ProviderInstance}`` for every merged
        ``config.providers[]`` entry that carries BOTH an ``id`` and a
        ``module`` (an entry without ``module`` names nothing mountable and
        is dropped rather than half-mounted).  Empty when no settings file
        exists -- an ordinary, non-error state on a CI host.
    """
    source = dict(env if env is not None else os.environ)
    paths = settings_scope_paths(home=home, cwd=cwd, env=source)
    merged = _merge_scope_entries(_read_scope(p) for p in paths)

    lookup = dict(_load_keys_env(_keys_env_path(home=home, env=source)))
    lookup.update({k: v for k, v in source.items() if v})  # environment wins

    instances: dict[str, ProviderInstance] = {}
    for entry in merged:
        instance_id = entry.get("id")
        module = entry.get("module")
        if not isinstance(instance_id, str) or not instance_id:
            continue
        if not isinstance(module, str) or not module:
            continue
        raw_config = entry.get("config")
        config = _expand(dict(raw_config), lookup) if isinstance(raw_config, Mapping) else {}
        source_uri = entry.get("source")
        instances[instance_id] = ProviderInstance(
            id=instance_id,
            module=module,
            source=source_uri if isinstance(source_uri, str) and source_uri else None,
            config=config,
        )
    return instances


def validate_run_provider(
    provider: str,
    *,
    key_env: Mapping[str, str],
    env: Mapping[str, str] | None = None,
    instances: Mapping[str, ProviderInstance] | None = None,
) -> str | None:
    """Validate a run-level ``--provider`` value.  Returns ``None`` when it is
    serviceable, else a complete, user-facing failure sentence.

    Three outcomes, in order:

    1. A MODULE name in *key_env* -- the pre-existing path, unchanged: the
       named credential env var must be present.
    2. A configured INSTANCE id -- accepted WITHOUT the module credential
       check.  The instance carries its own ``api_key`` in settings (usually
       a ``${VAR}`` backed by ``keys.env``), so demanding e.g.
       ``OPENAI_API_KEY`` for ``--provider terra`` would refuse a run the
       host can serve perfectly well.  A credential that is genuinely absent
       still fails loud -- at the provider module's own auth, naming the
       unresolved placeholder (see this module's SECRETS note).
    3. Neither -- refused, naming BOTH address spaces, because "unknown
       provider 'terra'" that lists only module names is the message that
       sent this feature's own reporter looking for a typo they had not made.
    """
    if provider in key_env:
        source = env if env is not None else os.environ
        var = key_env[provider]
        if not source.get(var):
            return f"missing API key -- set {var} for provider {provider!r}"
        return None
    available = (
        instances if instances is not None else load_provider_instances(env=env)
    )
    if provider in available:
        return None
    known_instances = ", ".join(sorted(available)) or "none configured"
    return (
        f"unknown provider {provider!r}. Known provider modules: "
        f"{', '.join(sorted(key_env))}. Configured provider instances: "
        f"{known_instances}."
    )


def provider_instance_ids(
    *,
    home: Path | str | None = None,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Sorted configured instance ids -- the list a fail-loud message shows a
    human who typed one the run cannot serve.  Never raises: a settings tree
    that cannot be read yields ``[]``, so a diagnostic message can never be
    the thing that crashes the diagnosis."""
    try:
        return sorted(load_provider_instances(home=home, cwd=cwd, env=env))
    except Exception:  # noqa: BLE001 -- a message helper must never raise
        return []
