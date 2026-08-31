"""Superset provider detection for spawn workers (issue idea-transfer from
microsoft/amplifier-bundle-attractor#322, credited in this feature's commit).

THE ARCHITECTURAL SPLIT (maintainer ruling, non-negotiable):

``unified_llm`` (``modules/unified-llm-client``) is the PURE unified-llm-spec
client -- SDK-direct anthropic/openai/gemini, and nothing else. Its own
``PROVIDER_ENV_KEYS``/``detect_configured_providers`` are NOT touched by this
module and never grow a subscription-provider entry: teaching the pure client
providers it structurally cannot serve (no SDK adapter exists or ever will
for a GitHub-Copilot-proxied or ChatGPT-OAuth-proxied model family) would
defeat the whole point of "pure".

Spawn workers (``coding-agent`` / ``amplifier-agent``, hosted by
``loop-agent``/``loop-amplifier-agent``) are a different animal: they proxy
an arbitrary mounted PROVIDER MODULE, so a provider that has no unified_llm
SDK adapter at all -- because auth is a subscription/OAuth flow, not an API
key -- is still perfectly servable there. This module is the SUPERSET
detection table for exactly that seam: the NATIVE three (delegated verbatim
to ``unified_llm.client.detect_configured_providers`` -- never re-declared,
issue #338's root cause) plus the two new subscription probes.

THE THREE-TABLE SYNC GUARD (this feature's own drift class, same shape as
#338): a provider's (a) detection probe, (b) mounted MODULE source, and (c)
its ``profiles:`` routing key must never drift apart. Refactored to ONE
table (``PROVIDER_SPECS`` below) that every consumer
(``default_worker._PROVIDER_MODULE_SOURCES``, ``default_worker``'s profiles-
map key set, ``detect_configured_providers``) derives from, rather than
three independently maintained collections -- see
``tests/test_provider_detection.py::test_three_tables_derive_from_one_registry``
for the guard.

INTENT RULE (github-copilot only): ``COPILOT_AGENT_TOKEN``/
``COPILOT_GITHUB_TOKEN`` are copilot-specific env var NAMES -- their mere
presence already carries intent to use github-copilot, so they always count.
``GH_TOKEN``/``GITHUB_TOKEN`` are GENERIC tokens (GitHub Actions injects
``GITHUB_TOKEN`` into EVERY job; many developers already have ``GH_TOKEN``
set for unrelated `gh` CLI use) -- their presence carries NO intent by
itself. They count ONLY when the run EXPLICITLY asks for github-copilot,
i.e. some node in the DOT source actually declares
``llm_provider="github-copilot"``. Auto-mounting (and, worse, silently
attempting to authenticate) copilot into every ordinary CI lane just because
``GITHUB_TOKEN`` exists would be a surprise this rule exists to prevent.
``openai-chatgpt``'s probe has no equivalent ambiguity: the OAuth token
cache's mere existence already means a human deliberately ran the device-
code login flow for this module -- there is no "generic, always-present"
file it could be confused with.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# github-copilot token resolution order (mirrored from, and must stay in sync
# with, amplifier-module-provider-github-copilot's own sdk_adapter client
# ``_resolve_token`` -- see that module's README "Authentication" table).
# ---------------------------------------------------------------------------
_COPILOT_HIGH_INTENT_ENV_VARS: tuple[str, ...] = (
    "COPILOT_AGENT_TOKEN",
    "COPILOT_GITHUB_TOKEN",
)
_COPILOT_GENERIC_ENV_VARS: tuple[str, ...] = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
)

# openai-chatgpt OAuth token cache path (mirrored from, and must stay in sync
# with, amplifier-module-provider-openai-chatgpt's README "Authentication"
# section / its ``token_file_path`` config default).
DEFAULT_OPENAI_CHATGPT_TOKEN_FILE = "~/.amplifier/openai-chatgpt-oauth.json"
#: Override env var, exposed for hermetic tests (never required by the
#: provider module itself, which always uses the literal default path).
_OPENAI_CHATGPT_TOKEN_FILE_ENV = "AMPLIFIER_OPENAI_CHATGPT_OAUTH_PATH_OVERRIDE"


def _native_probe(canonical_name: str) -> Callable[[Mapping[str, str], bool], bool]:
    """Delegate to ``unified_llm.client.detect_configured_providers`` --
    the SAME single source of truth ``Client.from_env()`` uses. Never a
    second, hand-copied env-var list (issue #338)."""

    def probe(env: Mapping[str, str], explicit_ask: bool) -> bool:
        del explicit_ask  # native providers have no ambiguous generic signal
        from unified_llm.client import detect_configured_providers

        return canonical_name in detect_configured_providers(env)

    return probe


def _github_copilot_probe(env: Mapping[str, str], explicit_ask: bool) -> bool:
    """Env probe with the INTENT RULE (module docstring): high-intent token
    names always count; the generic GH_TOKEN/GITHUB_TOKEN count only when
    the run explicitly asked for github-copilot (a node declared
    ``llm_provider="github-copilot"``)."""
    if any(env.get(k) for k in _COPILOT_HIGH_INTENT_ENV_VARS):
        return True
    return explicit_ask and any(env.get(k) for k in _COPILOT_GENERIC_ENV_VARS)


def _openai_chatgpt_probe(env: Mapping[str, str], explicit_ask: bool) -> bool:
    """File probe: configured iff the OAuth token cache exists and is
    non-empty. No intent ambiguity (module docstring) -- ``explicit_ask``
    is accepted for signature parity but never consulted."""
    del explicit_ask
    raw_path = (
        env.get(_OPENAI_CHATGPT_TOKEN_FILE_ENV) or DEFAULT_OPENAI_CHATGPT_TOKEN_FILE
    )
    path = Path(raw_path).expanduser()
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


@dataclass(frozen=True)
class ProviderSpec:
    """One row of the single detection/module-source/profiles registry."""

    name: str
    #: git+https source for the reference provider MODULE (mirrors the
    #: ``module:``/``source:`` shape amplifier-bundle-attractor's own
    #: ``bundles/attractor-pipeline.yaml`` declared -- see
    #: ``default_worker._PROVIDER_MODULE_SOURCES``'s pre-existing docstring).
    module_source: str
    #: ``(env, explicit_ask) -> bool`` -- statically checkable, no network,
    #: no SDK import (mirrors ``unified_llm.detect_configured_providers``'s
    #: own contract).
    probe: Callable[[Mapping[str, str], bool], bool]
    #: Human-readable "how to configure this provider" -- used to build
    #: fail-loud messages (the NoProviderConfiguredError "supported"
    #: list, etc). Names the REAL detection mechanism, not just one env var.
    credential_hint: str
    #: None when the provider MODULE documents its own sensible
    #: ``default_model`` (so an absent ``llm_model`` is fine -- the module's
    #: own default stands, see EXTENSIONS.md's dated addendum). Otherwise a
    #: provider-specific hint appended to the "set llm_model explicitly"
    #: fail-loud message.
    model_required_hint: str | None = None


NATIVE_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "gemini")
SUBSCRIPTION_PROVIDERS: tuple[str, ...] = ("github-copilot", "openai-chatgpt")

#: THE ONE TABLE. Insertion order is deliberate: native three (unified_llm's
#: own detection order) first, then the two subscription probes -- "native
#: three via the existing unified_llm detection + the two new probes".
PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        name="anthropic",
        module_source="git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        probe=_native_probe("anthropic"),
        credential_hint="ANTHROPIC_API_KEY",
    ),
    "openai": ProviderSpec(
        name="openai",
        module_source="git+https://github.com/microsoft/amplifier-module-provider-openai@main",
        probe=_native_probe("openai"),
        credential_hint="OPENAI_API_KEY",
    ),
    "gemini": ProviderSpec(
        name="gemini",
        module_source="git+https://github.com/microsoft/amplifier-module-provider-gemini@main",
        probe=_native_probe("gemini"),
        credential_hint="GEMINI_API_KEY (or GOOGLE_API_KEY)",
    ),
    "github-copilot": ProviderSpec(
        name="github-copilot",
        module_source=(
            "git+https://github.com/microsoft/amplifier-module-provider-github-copilot@main"
        ),
        probe=_github_copilot_probe,
        credential_hint=(
            "COPILOT_AGENT_TOKEN or COPILOT_GITHUB_TOKEN (GH_TOKEN/GITHUB_TOKEN "
            "also count, but ONLY when a node explicitly sets llm_provider="
            '"github-copilot" -- see the intent rule in provider_detection.py)'
        ),
        model_required_hint=(
            "github-copilot serves multiple model families -- set llm_model "
            'to an explicit id, e.g. llm_model="claude-sonnet-4.6" (a family '
            'token/glob like "sonnet" cannot be live-resolved for this '
            "provider). Omitting llm_model entirely also works: the mounted "
            "module defaults to its own configured default_model "
            '("claude-opus-4.5" unless overridden).'
        ),
    ),
    "openai-chatgpt": ProviderSpec(
        name="openai-chatgpt",
        module_source=(
            "git+https://github.com/microsoft/amplifier-module-provider-openai-chatgpt@main"
        ),
        probe=_openai_chatgpt_probe,
        credential_hint=(
            f"an OAuth token cache at {DEFAULT_OPENAI_CHATGPT_TOKEN_FILE} "
            "(run: amplifier provider login openai-chatgpt)"
        ),
        model_required_hint=(
            "openai-chatgpt serves multiple model families -- set llm_model "
            'to an explicit id, e.g. llm_model="gpt-5.5" (a family token/glob '
            'like "sonnet" cannot be live-resolved for this provider). '
            "Omitting llm_model entirely also works: the mounted module "
            'resolves its own default_model="latest" dynamically.'
        ),
    ),
}


# ---------------------------------------------------------------------------
# "Explicit ask" detection -- a conservative text scan over the DOT source,
# NOT a full graph parse. This runs (CLI) BEFORE the worker/bundle is
# resolved, which is itself BEFORE the graph is ever parsed into Node
# objects (see cli.py: dot_source is read, then default_worker.resolve() is
# called, then run_pipeline() parses the graph) -- so a real parse is not
# available yet at the point this signal is needed. A regex over
# ``llm_provider=...`` attribute assignments is deliberately conservative:
# false negatives (missing a real declaration due to unusual formatting)
# only cost the generic-token signal being ignored -- exactly the safe
# direction for the intent rule -- never a false positive that widens it.
#
# KNOWN LIMITATIONS (adversarial review, feat/subscription-providers):
#
# 1. Comments: FIXED here. Without stripping, a `// llm_provider=
#    "github-copilot"` line comment or `/* ... */` block comment would
#    match -- a real false positive (mounts copilot for a node that never
#    asked for it). ``_strip_comments_for_scan`` below removes `//` and
#    `/* */` DOT comments (mirroring ``dot_parser._strip_comments``'s own
#    quote-aware algorithm, duplicated rather than imported -- this module
#    intentionally has no dependency on ``amplifier_module_loop_pipeline``'s
#    parser internals) before the regex ever runs.
#
# 2. Quoted string CONTENT (e.g. a node's own ``prompt="...talks about
#    llm_provider=github-copilot..."``) is NOT, and cannot cheaply be,
#    disambiguated from a real attribute assignment: telling "text inside a
#    string value" apart from "a real key=value pair" requires the same
#    tokenizer/parser this scan deliberately runs without (see above -- a
#    real parse is not available yet at this point in the CLI's call
#    order). This is an ACCEPTED, DOCUMENTED false-positive surface, not an
#    oversight: worst case it mounts github-copilot when nothing actually
#    uses it -- a wasted mount, never a wrong-routing or credential leak.
#    See ``test_explicit_ask_prompt_string_content_is_a_known_false_
#    positive`` (test_provider_detection.py) -- a test that PINS this
#    limitation (asserts the false positive still happens) rather than
#    silently hoping nobody notices.
#
# 3. Scope: this scans ONLY the ROOT ``dot_source`` the CLI was handed
#    (``--dot-source`` or the root ``dot_file``) -- never any transformed /
#    stylesheet-applied text, and NEVER a child graph reached through a
#    ``shape=folder``/``dot_file=`` sub-pipeline node (EXTENSIONS.md S10).
#    Child graphs are not read at all at this point in the CLI's call
#    order (worker/bundle resolution -> THIS scan -> run_pipeline's own
#    graph parse -> only later, if/when a folder node executes, does its
#    child .dot get read). A node buried in a child graph that declares
#    ``llm_provider="github-copilot"`` will NOT unlock the generic
#    GH_TOKEN/GITHUB_TOKEN signal by itself -- the run needs either a
#    high-intent token (COPILOT_AGENT_TOKEN/COPILOT_GITHUB_TOKEN, which
#    always count) or an explicit ask somewhere in the ROOT graph. This is
#    a real, structural limitation of running the intent scan pre-parse at
#    the root only; fixing it would require plumbing this scan through
#    every folder-node child-graph read (a synthesis-time architectural
#    change) -- out of scope here, named loudly rather than fixed.
# ---------------------------------------------------------------------------
_EXPLICIT_LLM_PROVIDER_RE = re.compile(r'llm_provider\s*=\s*"?([A-Za-z0-9_.-]+)"?')


def _strip_comments_for_scan(source: str) -> str:
    """Remove ``//`` line comments and ``/* */`` block comments, preserving
    the content of double-quoted strings verbatim (mirrors
    ``amplifier_module_loop_pipeline.dot_parser._strip_comments``'s
    algorithm -- duplicated, not imported, to keep this module free of a
    dependency on that parser's internals). Used ONLY to make the
    explicit-ask text scan comment-blind; never applied to the DOT source
    actually handed to the real parser."""
    result: list[str] = []
    i = 0
    length = len(source)
    while i < length:
        if source[i] == '"':
            j = i + 1
            while j < length:
                if source[j] == "\\" and j + 1 < length:
                    j += 2
                    continue
                if source[j] == '"':
                    j += 1
                    break
                j += 1
            result.append(source[i:j])
            i = j
        elif source[i : i + 2] == "//":
            j = source.find("\n", i)
            if j == -1:
                break
            i = j
        elif source[i : i + 2] == "/*":
            j = source.find("*/", i + 2)
            if j == -1:
                break
            i = j + 2
        else:
            result.append(source[i])
            i += 1
    return "".join(result)


def explicitly_requested_providers(dot_source: str | None) -> frozenset[str]:
    """Provider names a DOT source's own node attributes explicitly declare
    via ``llm_provider=...`` -- a conservative text scan (see module
    docstring's KNOWN LIMITATIONS), lower-cased. Empty when ``dot_source``
    is falsy. Comments are stripped before scanning (limitation 1); a
    mention inside a quoted string's own content is a known, accepted
    false-positive surface (limitation 2); only the root dot_source is ever
    seen, never a child graph (limitation 3)."""
    if not dot_source:
        return frozenset()
    scanned = _strip_comments_for_scan(dot_source)
    return frozenset(
        m.group(1).lower() for m in _EXPLICIT_LLM_PROVIDER_RE.finditer(scanned)
    )


def detect_configured_providers(
    env: Mapping[str, str] | None = None,
    *,
    dot_source: str | None = None,
) -> list[str]:
    """Return the SUPERSET of provider names servable by a spawn worker
    whose credential/config this environment/run satisfies.

    Args:
        env: Optional mapping to read env vars from (defaults to
            ``os.environ``). Exposed for hermetic tests.
        dot_source: Optional raw DOT source text for this run, used ONLY to
            compute the "explicit ask" intent signal (see
            :func:`explicitly_requested_providers`) -- never parsed as a
            graph here.

    Returns:
        Provider names (subset of ``PROVIDER_SPECS``) in registry order,
        e.g. ``["anthropic", "github-copilot"]``.
    """
    source = env if env is not None else os.environ
    requested = explicitly_requested_providers(dot_source)
    return [
        name
        for name, spec in PROVIDER_SPECS.items()
        if spec.probe(source, name in requested)
    ]


def module_source_map() -> dict[str, str]:
    """The module-source map every synthesized bundle mounts from --
    derived from :data:`PROVIDER_SPECS`, never hand-maintained separately."""
    return {name: spec.module_source for name, spec in PROVIDER_SPECS.items()}


def credential_hint(provider: str) -> str | None:
    """Human-readable "how to configure this provider" for fail-loud
    messages, or ``None`` for an unregistered provider name."""
    spec = PROVIDER_SPECS.get(provider)
    return spec.credential_hint if spec else None


def model_required_hint(provider: str) -> str | None:
    """Per-provider ``llm_model`` fail-loud hint, or ``None`` when the
    provider has no such hint (native three, or an unregistered name)."""
    spec = PROVIDER_SPECS.get(provider)
    return spec.model_required_hint if spec else None
