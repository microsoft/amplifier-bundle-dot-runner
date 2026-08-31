"""Hermetic tests for ``provider_detection`` -- the superset detection table
for spawn workers (idea-transfer from microsoft/amplifier-bundle-attractor
#322, credited in this feature's commit).

Covers: the github-copilot env probe (high-intent tokens, the intent-rule
gate on the generic GH_TOKEN/GITHUB_TOKEN), the openai-chatgpt file probe
(present/absent/empty), the explicit-ask text scan, and the THREE-TABLE SYNC
GUARD proving detection/module-source/profiles all derive from one registry.
"""

from __future__ import annotations

from amplifier_module_pipeline_runner import provider_detection


def _clear_env(monkeypatch) -> None:
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "COPILOT_AGENT_TOKEN",
        "COPILOT_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    # Point the chatgpt file probe at a path guaranteed absent -- hermetic
    # regardless of whether this HOST actually has a real OAuth cache.
    monkeypatch.setenv(
        "AMPLIFIER_OPENAI_CHATGPT_OAUTH_PATH_OVERRIDE",
        "/nonexistent/openai-chatgpt-oauth.json",
    )


# ---------------------------------------------------------------------------
# github-copilot env probe: high-intent tokens vs the generic-token intent rule
# ---------------------------------------------------------------------------


def test_github_copilot_configured_via_copilot_agent_token(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("COPILOT_AGENT_TOKEN", "tok")
    assert "github-copilot" in provider_detection.detect_configured_providers()


def test_github_copilot_configured_via_copilot_github_token(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "tok")
    assert "github-copilot" in provider_detection.detect_configured_providers()


def test_github_copilot_not_configured_from_generic_token_alone(monkeypatch):
    """INTENT RULE: GH_TOKEN/GITHUB_TOKEN carry no intent by themselves --
    GitHub Actions injects GITHUB_TOKEN into every job; auto-mounting
    copilot into every CI lane would be a surprise."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    assert "github-copilot" not in provider_detection.detect_configured_providers()


def test_github_copilot_not_configured_from_gh_token_alone(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "ghp_xxx")
    assert "github-copilot" not in provider_detection.detect_configured_providers()


def test_github_copilot_generic_token_counts_with_explicit_ask(monkeypatch):
    """INTENT RULE, other half: the generic token DOES count once a node in
    the DOT source explicitly declares llm_provider="github-copilot"."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    dot = 'digraph { a [shape=box, llm_provider="github-copilot"]; }'
    assert "github-copilot" in provider_detection.detect_configured_providers(
        dot_source=dot
    )


def test_github_copilot_generic_token_ignored_when_ask_is_for_other_provider(
    monkeypatch,
):
    """A node explicitly asking for a DIFFERENT provider must not itself
    unlock github-copilot's generic-token signal."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    dot = 'digraph { a [shape=box, llm_provider="anthropic"]; }'
    assert "github-copilot" not in provider_detection.detect_configured_providers(
        dot_source=dot
    )


def test_high_intent_token_counts_even_without_explicit_ask(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("COPILOT_AGENT_TOKEN", "tok")
    dot = 'digraph { a [shape=box, llm_provider="anthropic"]; }'
    assert "github-copilot" in provider_detection.detect_configured_providers(
        dot_source=dot
    )


# ---------------------------------------------------------------------------
# openai-chatgpt file probe
# ---------------------------------------------------------------------------


def test_openai_chatgpt_not_configured_when_file_absent(monkeypatch):
    _clear_env(monkeypatch)
    assert "openai-chatgpt" not in provider_detection.detect_configured_providers()


def test_openai_chatgpt_configured_when_file_present_and_nonempty(
    monkeypatch, tmp_path
):
    _clear_env(monkeypatch)
    token_file = tmp_path / "openai-chatgpt-oauth.json"
    token_file.write_text('{"access_token": "x"}', encoding="utf-8")
    monkeypatch.setenv("AMPLIFIER_OPENAI_CHATGPT_OAUTH_PATH_OVERRIDE", str(token_file))
    assert "openai-chatgpt" in provider_detection.detect_configured_providers()


def test_openai_chatgpt_not_configured_when_file_empty(monkeypatch, tmp_path):
    """An empty file is not a real, usable OAuth cache."""
    _clear_env(monkeypatch)
    token_file = tmp_path / "openai-chatgpt-oauth.json"
    token_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("AMPLIFIER_OPENAI_CHATGPT_OAUTH_PATH_OVERRIDE", str(token_file))
    assert "openai-chatgpt" not in provider_detection.detect_configured_providers()


def test_openai_chatgpt_has_no_intent_ambiguity(monkeypatch, tmp_path):
    """Unlike github-copilot, the file probe needs no explicit-ask gate --
    the file's mere existence already means a human ran the login flow."""
    _clear_env(monkeypatch)
    token_file = tmp_path / "openai-chatgpt-oauth.json"
    token_file.write_text('{"access_token": "x"}', encoding="utf-8")
    monkeypatch.setenv("AMPLIFIER_OPENAI_CHATGPT_OAUTH_PATH_OVERRIDE", str(token_file))
    # No dot_source at all -- still configured.
    assert "openai-chatgpt" in provider_detection.detect_configured_providers()


# ---------------------------------------------------------------------------
# Native three: unchanged, delegated verbatim to unified_llm
# ---------------------------------------------------------------------------


def test_native_three_delegate_to_unified_llm(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    result = provider_detection.detect_configured_providers()
    assert result == ["anthropic"]


def test_native_and_subscription_can_combine(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    monkeypatch.setenv("COPILOT_AGENT_TOKEN", "tok")
    result = provider_detection.detect_configured_providers()
    assert result == ["anthropic", "github-copilot"]


def test_zero_configured_is_empty_list(monkeypatch):
    _clear_env(monkeypatch)
    assert provider_detection.detect_configured_providers() == []


# ---------------------------------------------------------------------------
# explicitly_requested_providers -- the text-scan mechanism itself
# ---------------------------------------------------------------------------


def test_explicitly_requested_providers_empty_for_none_or_blank():
    assert provider_detection.explicitly_requested_providers(None) == frozenset()
    assert provider_detection.explicitly_requested_providers("") == frozenset()


def test_explicitly_requested_providers_scans_multiple_nodes():
    dot = (
        "digraph {\n"
        '  a [shape=box, llm_provider="github-copilot"];\n'
        "  b [shape=box, llm_provider=anthropic];\n"
        "}\n"
    )
    assert provider_detection.explicitly_requested_providers(dot) == frozenset(
        {"github-copilot", "anthropic"}
    )


# ---------------------------------------------------------------------------
# ★ THE THREE-TABLE SYNC GUARD
# ---------------------------------------------------------------------------


def test_three_tables_derive_from_one_registry():
    """Detection table, module-source map, and profiles-map key set must
    never drift apart (issue #338's own drift class, applied to this
    feature's 2 new providers). Refactored to ONE table
    (PROVIDER_SPECS) every consumer derives from -- this test is the
    structural guard that a future edit cannot silently add a provider to
    one collection without the others."""
    from amplifier_module_pipeline_runner import default_worker

    spec_names = set(provider_detection.PROVIDER_SPECS)
    module_source_names = set(provider_detection.module_source_map())
    default_worker_source_names = set(default_worker._PROVIDER_MODULE_SOURCES)

    assert spec_names == module_source_names == default_worker_source_names
    assert spec_names == {
        "anthropic",
        "openai",
        "gemini",
        "github-copilot",
        "openai-chatgpt",
    }

    # Every module source is a real git+https reference string.
    for name, source in default_worker._PROVIDER_MODULE_SOURCES.items():
        assert source.startswith("git+https://"), f"{name}: {source!r}"


def test_registry_is_the_single_edit_point_for_new_providers():
    """A guard-of-the-guard: PROVIDER_SPECS is the only place a name is
    declared -- module_source_map()/credential_hint()/model_required_hint()
    all read through it rather than shadowing their own copies."""
    for name, spec in provider_detection.PROVIDER_SPECS.items():
        assert provider_detection.module_source_map()[name] == spec.module_source
        assert provider_detection.credential_hint(name) == spec.credential_hint
        assert provider_detection.model_required_hint(name) == spec.model_required_hint


def test_native_providers_have_no_model_required_hint():
    """The native three resolve a model live via unified_llm -- no special
    per-provider hint is needed for them."""
    for name in provider_detection.NATIVE_PROVIDERS:
        assert provider_detection.model_required_hint(name) is None


def test_subscription_providers_have_model_required_hints():
    for name in provider_detection.SUBSCRIPTION_PROVIDERS:
        hint = provider_detection.model_required_hint(name)
        assert hint is not None
        assert "llm_model" in hint
