"""Issue #338 regression proof: the synthesized ``--worker coding-agent`` /
``--worker amplifier-agent`` bundle must actually MOUNT a provider module,
not just declare a ``profiles:`` routing map.

THE GAP THIS CLOSES: ``test_llm_provider_flow_e2e.py`` proves per-node
``llm_provider`` routing survives the synthesized-bundle path, but its fake
spawn coordinator (``_RealMergeSpawnCoordinator``) never goes through
``amplifier_foundation`` module activation at all -- it reproduces one merge
line by hand. That test could (and did) stay green while the synthesized
bundle's mount plan carried zero ``providers:`` entries, because nothing in
the suite ever asked ``Bundle.to_mount_plan()`` what it actually mounts. This
file asks exactly that question, using REAL synthesis + REAL
``amplifier_foundation.load_bundle`` parsing (the same real chain
``test_default_worker.py``'s own
``test_synthesized_bundle_parses_via_real_amplifier_foundation`` already
uses) -- hermetic (fake API keys via monkeypatch, no network, no LLM call):
loading a bundle from a local temp YAML file and reading back its own
``providers`` list touches neither the network nor a provider SDK.

RED on main (pre-fix): ``_synthesize_agent_bundle_yaml`` never emitted a
top-level ``providers:`` section at all (issue #338's root cause) -- every
spawned loop-agent/loop-amplifier-agent child saw ``providers={}`` and died
on "Available providers: []" the instant any box node dispatched, even with
a valid API key configured.
"""

from __future__ import annotations

import asyncio

import pytest
from amplifier_module_pipeline_runner import default_worker
from amplifier_module_pipeline_runner import runner as runner_mod

amplifier_foundation = pytest.importorskip("amplifier_foundation")


def _clear_all_provider_keys(monkeypatch) -> None:
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        # Subscription-provider expansion (idea-transfer from microsoft/
        # amplifier-bundle-attractor#322): neutralize the two new probes too,
        # so this helper's contract ("nothing is configured") still holds.
        # COPILOT_*/GH_TOKEN/GITHUB_TOKEN clear the env probe; the override
        # points openai-chatgpt's FILE probe at a path guaranteed absent --
        # a real ~/.amplifier/openai-chatgpt-oauth.json on the host running
        # this suite must never leak into a "zero configured" test.
        "COPILOT_AGENT_TOKEN",
        "COPILOT_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(
        "AMPLIFIER_OPENAI_CHATGPT_OAUTH_PATH_OVERRIDE",
        "/nonexistent/openai-chatgpt-oauth.json",
    )


async def _load_synthesized_bundle(worker_name: str):
    """Real synthesis + real parse (no network -- a local temp file)."""
    bundle_path = default_worker.write_agent_bundle(worker_name)
    return await amplifier_foundation.load_bundle(str(bundle_path))


@pytest.mark.parametrize("worker_name", ["coding-agent", "amplifier-agent"])
def test_synthesized_bundle_mounts_a_provider_for_the_configured_key(
    monkeypatch, worker_name
):
    """The core issue #338 proof: a configured ANTHROPIC_API_KEY must land as
    a REAL top-level ``providers:`` entry in the synthesized bundle's own
    mount plan -- not merely a ``profiles:`` routing map naming an agent.
    """
    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    loaded = asyncio.run(_load_synthesized_bundle(worker_name))
    mount_plan = loaded.to_mount_plan()

    providers = mount_plan.get("providers")
    assert providers, (
        f"synthesized --worker {worker_name!r} bundle mounts NO providers "
        f"(mount_plan={mount_plan!r}) -- this is issue #338: a spawned child "
        'would see providers={} and die on "Available providers: []" the '
        "instant any box node dispatches, even with a valid API key present."
    )
    provider_modules = {p.get("module") for p in providers}
    assert "provider-anthropic" in provider_modules, (
        f"expected a mounted 'provider-anthropic' module, got {provider_modules!r}"
    )
    # Every mounted provider entry must carry a resolvable source (module
    # activation requires it -- see amplifier_foundation.bundle.Bundle.prepare,
    # which only activates providers/tools/hooks entries carrying 'source').
    for entry in providers:
        assert entry.get("source"), f"provider entry missing 'source': {entry!r}"


@pytest.mark.parametrize("worker_name", ["coding-agent", "amplifier-agent"])
def test_synthesized_bundle_mounts_a_provider_per_every_configured_key(
    monkeypatch, worker_name
):
    """Multiple configured keys -> multiple mounted provider modules (not just
    the first/default one) -- a dual-lens run (e.g. anthropic + openai nodes
    in the same graph) needs every configured provider actually mounted.
    """
    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")

    loaded = asyncio.run(_load_synthesized_bundle(worker_name))
    provider_modules = {
        p.get("module") for p in loaded.to_mount_plan().get("providers", [])
    }
    assert provider_modules == {"provider-anthropic", "provider-openai"}


@pytest.mark.parametrize("worker_name", ["coding-agent", "amplifier-agent"])
def test_synthesize_raises_loud_never_silent_empty_mount_when_zero_keys(
    monkeypatch, worker_name
):
    """Zero configured keys -> the existing loud no-provider error (the SAME
    ``NoProviderConfiguredError`` the ``direct`` worker's own bootstrap
    raises), never a silently-written bundle with an empty ``providers:``
    mount that only fails later, deep inside a spawned child.
    """
    _clear_all_provider_keys(monkeypatch)

    with pytest.raises(runner_mod.NoProviderConfiguredError):
        default_worker._synthesize_agent_bundle_yaml(worker_name)


def test_detect_configured_providers_is_the_single_source_of_truth(monkeypatch):
    """``default_worker`` must delegate to ``unified_llm.client`` for the
    NATIVE three (issue #338: a second, hand-maintained copy of this list is
    exactly how a provider can silently stop being detected on one of the
    two paths without the other noticing), and to
    ``provider_detection.PROVIDER_SPECS`` for the full superset (this
    feature's own #338-shaped drift class -- see
    ``test_provider_detection.py::test_three_tables_derive_from_one_registry``
    for the dedicated three-table guard)."""
    from amplifier_module_pipeline_runner import provider_detection
    from unified_llm.client import PROVIDER_ENV_KEYS

    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real")

    assert default_worker._detect_configured_providers() == ["gemini"]
    # The module-source map is now a SUPERSET of unified_llm's pure native-3
    # table (github-copilot/openai-chatgpt have no unified_llm SDK adapter
    # at all, by architectural ruling) -- never equal to it any more; the
    # meaningful invariant is superset-containment plus "derives from the
    # one registry", not byte-equality with the pure client's own table.
    assert set(default_worker._PROVIDER_MODULE_SOURCES) >= set(PROVIDER_ENV_KEYS)
    assert set(default_worker._PROVIDER_MODULE_SOURCES) == set(
        provider_detection.PROVIDER_SPECS
    )


# ---------------------------------------------------------------------------
# Subscription-provider expansion (idea-transfer from microsoft/amplifier-
# bundle-attractor#322, credited in this feature's commit): github-copilot /
# openai-chatgpt mount-plan proofs -- the #26-style REAL load_bundle test,
# mirroring the native-provider proofs above exactly.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("worker_name", ["coding-agent", "amplifier-agent"])
def test_synthesized_bundle_mounts_github_copilot_provider(monkeypatch, worker_name):
    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("COPILOT_AGENT_TOKEN", "test-token-not-real")

    loaded = asyncio.run(_load_synthesized_bundle(worker_name))
    provider_modules = {
        p.get("module") for p in loaded.to_mount_plan().get("providers", [])
    }
    assert provider_modules == {"provider-github-copilot"}


@pytest.mark.parametrize("worker_name", ["coding-agent", "amplifier-agent"])
def test_synthesized_bundle_mounts_openai_chatgpt_provider(
    monkeypatch, worker_name, tmp_path
):
    _clear_all_provider_keys(monkeypatch)
    token_file = tmp_path / "openai-chatgpt-oauth.json"
    token_file.write_text('{"access_token": "x"}', encoding="utf-8")
    monkeypatch.setenv("AMPLIFIER_OPENAI_CHATGPT_OAUTH_PATH_OVERRIDE", str(token_file))

    loaded = asyncio.run(_load_synthesized_bundle(worker_name))
    provider_modules = {
        p.get("module") for p in loaded.to_mount_plan().get("providers", [])
    }
    assert provider_modules == {"provider-openai-chatgpt"}


@pytest.mark.parametrize("worker_name", ["coding-agent", "amplifier-agent"])
def test_synthesized_bundle_mounts_native_and_subscription_together(
    monkeypatch, worker_name, tmp_path
):
    """A dual-lens run: one native provider + one subscription provider,
    both mounted -- the two seams (unified_llm-backed detection and the new
    superset probes) compose without stepping on each other."""
    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    token_file = tmp_path / "openai-chatgpt-oauth.json"
    token_file.write_text('{"access_token": "x"}', encoding="utf-8")
    monkeypatch.setenv("AMPLIFIER_OPENAI_CHATGPT_OAUTH_PATH_OVERRIDE", str(token_file))

    loaded = asyncio.run(_load_synthesized_bundle(worker_name))
    provider_modules = {
        p.get("module") for p in loaded.to_mount_plan().get("providers", [])
    }
    assert provider_modules == {"provider-anthropic", "provider-openai-chatgpt"}


@pytest.mark.parametrize("worker_name", ["coding-agent", "amplifier-agent"])
def test_synthesized_bundle_never_mounts_copilot_from_generic_token_alone(
    monkeypatch, worker_name
):
    """INTENT RULE proof at the synthesis boundary: GITHUB_TOKEN alone
    (no explicit ask, no dot_source) must never silently mount
    provider-github-copilot -- the exact "surprise CI lane" this rule
    exists to prevent."""
    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_ci_injected_token")

    with pytest.raises(runner_mod.NoProviderConfiguredError):
        default_worker._synthesize_agent_bundle_yaml(worker_name)


@pytest.mark.parametrize("worker_name", ["coding-agent", "amplifier-agent"])
def test_synthesized_bundle_mounts_copilot_from_generic_token_with_explicit_ask(
    monkeypatch, worker_name
):
    """The other half of the intent rule: GITHUB_TOKEN DOES count once
    dot_source shows a node explicitly declaring
    llm_provider="github-copilot"."""
    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_ci_injected_token")
    dot_source = 'digraph { a [shape=box, llm_provider="github-copilot"]; }'

    bundle_path = default_worker.write_agent_bundle(worker_name, dot_source=dot_source)
    loaded = asyncio.run(amplifier_foundation.load_bundle(str(bundle_path)))
    provider_modules = {
        p.get("module") for p in loaded.to_mount_plan().get("providers", [])
    }
    assert provider_modules == {"provider-github-copilot"}
