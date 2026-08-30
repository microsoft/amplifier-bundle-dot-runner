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
    ):
        monkeypatch.delenv(var, raising=False)


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
    """``default_worker`` must delegate to ``unified_llm.client`` for
    detection rather than re-declaring its own env-var list (issue #338: a
    second, hand-maintained copy of this list is exactly how a provider can
    silently stop being detected on one of the two paths without the other
    noticing)."""
    from unified_llm.client import PROVIDER_ENV_KEYS

    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real")

    assert default_worker._detect_configured_providers() == ["gemini"]
    assert set(default_worker._PROVIDER_MODULE_SOURCES) == set(PROVIDER_ENV_KEYS)
