"""Attack #2 (spec-repair verification): per-node ``llm_provider``/``llm_model``
flow through the ``--worker coding-agent`` synthesis path, end to end.

THE HISTORICAL BUG THIS GUARDS (documented across the codebase as "Bug B" /
the "multi-lens silent-single-provider bug", see ``amplifier_module_loop_
pipeline.backend``'s ``_run_with_spawn`` comment on ``orchestrator_config``
and ``amplifier_module_loop_agent``'s "Bug B" tests): a node's own declared
``llm_provider`` (e.g. a critique node using ``openai`` while a sibling
critique node uses ``anthropic`` -- the "dual-lens" pattern) got merged into
the WRONG key somewhere between the pipeline node and the spawned child's
mounted orchestrator config, so every node silently converged on ONE
provider regardless of what it declared -- defeating dual-family critique
without any visible error.

This test proves the FULL, REAL chain survives WAVE 5's worker-names-not-
bundles repair (Part 2), where the bundle a ``--worker coding-agent`` run
spawns into is no longer authored by a human -- it is SYNTHESIZED by
``default_worker._synthesize_agent_bundle_yaml``, and that synthesized
bundle bakes in a STATIC ``llm_provider: anthropic`` default on the agent
entry (see that function). If the per-node dynamic value did not correctly
override that static default, this exact regression would silently return
-- now via the synthesized path instead of a hand-authored one.

Chain proven, using REAL production code at every link except the actual
network call:
  1. ``default_worker._synthesize_agent_bundle_yaml("coding-agent")`` -- the
     REAL synthesis, parsed by REAL ``amplifier_foundation.load_bundle``.
  2. ``amplifier_module_loop_pipeline.backend.AmplifierBackend.run()`` --
     the REAL node -> ``orchestrator_config`` construction (reads
     ``node.attrs["llm_provider"]`` / ``["llm_model"]``).
  3. A fake ``session.spawn`` capability that reproduces -- verbatim --
     amplifier_foundation's OWN merge line (``amplifier_foundation.bundle.
     _prepared.PreparedBundle.spawn``: ``child_mount_plan["orchestrator"]
     ["config"].update(orchestrator_config)``), starting from the
     REAL parsed agent bundle's own static config. This is the exact seam
     the historical bug lived in, reproduced from the real source rather
     than asserted by narrative.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from amplifier_module_pipeline_runner import default_worker

amplifier_foundation = pytest.importorskip("amplifier_foundation")


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    """Hermetic provider-key presence, matching test_default_worker.py's own
    ``_api_key`` fixture -- issue #338: ``_synthesize_agent_bundle_yaml`` now
    fails loud (``NoProviderConfiguredError``) when no provider API key is
    configured, rather than silently emitting a bundle with no mounted
    providers. Every test in this file synthesizes a bundle, so it must not
    depend on the ambient environment happening to export a real key.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")


async def _load_synthesized_agent_bundle(worker_name: str) -> dict[str, Any]:
    """Real synthesis + real parse -> plain dict of the agent's own config
    (mirrors what ``coordinator.config["agents"][name]`` holds in production:
    a bundle's raw ``agents:`` entry, read by loop-pipeline's backend)."""
    bundle_path = default_worker.write_agent_bundle(worker_name)
    loaded = await amplifier_foundation.load_bundle(str(bundle_path))
    # loaded.agents[name] is a plain dict -- the same shape
    # AmplifierBackend._run_with_spawn reads via `agent_configs.get(profile_name)`.
    return dict(loaded.agents[default_worker.DEFAULT_AGENT_NAME])


class _RealMergeSpawnCoordinator:
    """Fake coordinator whose ``session.spawn`` reproduces amplifier_
    foundation's REAL merge semantics verbatim (see module docstring), so
    this test cannot pass by accident just because a fake happens to be
    lenient. Records the FINAL merged orchestrator config per call for
    assertion -- this is the exact value that would reach the spawned
    child's ``mount_plan["orchestrator"]["config"]``, which is what
    ``coding-agent``'s own ``mount()`` hands to ``AgentOrchestrator.__init__``
    as ``config`` (proven by coding-agent's own
    ``test_node_llm_provider_selects_completion_and_base``).
    """

    def __init__(self, agent_entry: dict[str, Any]):
        self._agent_entry = agent_entry
        self.session = object()
        self.config: dict[str, Any] = {
            "agents": {default_worker.DEFAULT_AGENT_NAME: agent_entry}
        }
        self.merged_configs_by_call: list[dict[str, Any]] = []

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs) -> dict[str, Any]:
        agent_name = kwargs["agent_name"]
        orchestrator_config = kwargs.get("orchestrator_config") or {}
        agent_entry = self.config["agents"][agent_name]

        # --- verbatim reproduction of amplifier_foundation.bundle._prepared
        # .PreparedBundle.spawn's real merge (child_mount_plan built from the
        # resolved child bundle's OWN orchestrator config, THEN .update()'d
        # with the per-spawn orchestrator_config -- "recipe takes
        # precedence") ---
        child_mount_plan: dict[str, Any] = {
            "orchestrator": copy.deepcopy(
                agent_entry.get("session", {}).get("orchestrator", {})
            )
        }
        if "config" not in child_mount_plan["orchestrator"]:
            child_mount_plan["orchestrator"]["config"] = {}
        if orchestrator_config:
            child_mount_plan["orchestrator"]["config"].update(orchestrator_config)

        merged_config = child_mount_plan["orchestrator"]["config"]
        self.merged_configs_by_call.append(merged_config)

        return {
            "output": "done",
            "session_id": f"child-{len(self.merged_configs_by_call)}",
            "status": "success",
            "metadata": {},
        }


def _make_node(node_id: str, **attrs: Any):
    from amplifier_module_loop_pipeline.dot_parser import Node

    return Node(id=node_id, shape="box", attrs=attrs)


def _make_context():
    from amplifier_module_loop_pipeline.context import PipelineContext

    return PipelineContext()


@pytest.mark.asyncio
async def test_per_node_llm_provider_survives_synthesized_bundle_static_default():
    """THE regression proof: two sibling nodes on the SAME synthesized
    ``--worker coding-agent`` bundle, declaring DIFFERENT ``llm_provider``
    values, must reach the spawn boundary with THEIR OWN provider -- never
    both collapsing onto the bundle's static ``llm_provider: anthropic``
    default (the historical multi-lens silent-single-provider bug, now via
    the synthesized-bundle path Part 2 introduced).
    """
    from amplifier_module_loop_pipeline.backend import AmplifierBackend

    agent_entry = await _load_synthesized_agent_bundle("coding-agent")
    # Sanity: the synthesized bundle really does bake in a static default
    # that differs from at least one of the two nodes below -- otherwise
    # this test could pass vacuously.
    static_default = agent_entry["session"]["orchestrator"]["config"]["llm_provider"]
    assert static_default == "anthropic"

    coordinator = _RealMergeSpawnCoordinator(agent_entry)
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={
            "anthropic": default_worker.DEFAULT_AGENT_NAME,
            "openai": default_worker.DEFAULT_AGENT_NAME,
        },
    )

    node_openai = _make_node("critique-openai", llm_provider="openai")
    node_anthropic = _make_node("critique-anthropic", llm_provider="anthropic")

    await backend.run(node_openai, "critique from lens A", _make_context())
    await backend.run(node_anthropic, "critique from lens B", _make_context())

    assert len(coordinator.merged_configs_by_call) == 2
    openai_call_config, anthropic_call_config = coordinator.merged_configs_by_call

    assert openai_call_config["llm_provider"] == "openai", (
        "node declaring llm_provider='openai' must reach the spawned child's "
        f"merged orchestrator config as openai, got {openai_call_config!r} -- "
        "this is the multi-lens silent-single-provider regression"
    )
    assert anthropic_call_config["llm_provider"] == "anthropic", (
        f"got {anthropic_call_config!r}"
    )
    # The two sibling nodes must not have silently converged.
    assert openai_call_config["llm_provider"] != anthropic_call_config["llm_provider"]


@pytest.mark.asyncio
async def test_per_node_llm_model_flows_as_provider_preference_not_orchestrator_config():
    """``llm_model`` rides a SEPARATE channel (``provider_preferences``, per
    ``backend.py``'s own comment: "Provider SELECTION ... flows via
    orchestrator_config['llm_provider']" while "model delivery" rides
    ``provider_preferences``) -- proven against the real spawn kwargs
    construction, not a reimplementation of that split.
    """
    from amplifier_module_loop_pipeline.backend import AmplifierBackend

    agent_entry = await _load_synthesized_agent_bundle("coding-agent")
    coordinator = _RealMergeSpawnCoordinator(agent_entry)
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"openai": default_worker.DEFAULT_AGENT_NAME},
    )

    # Recover the actual spawn kwargs this run issues (not just the merged
    # config) by wrapping the coordinator's spawn_fn BEFORE the backend's
    # first call -- AmplifierBackend caches `session.spawn` lazily on first
    # use (`if not self._spawn_checked: self._spawn_fn = ...`), so a spy
    # installed after a first call would never be consulted again.
    calls: list[dict[str, Any]] = []
    orig = coordinator._spawn_fn

    async def _spy(**kwargs):
        calls.append(kwargs)
        return await orig(**kwargs)

    coordinator._spawn_fn = _spy  # type: ignore[method-assign]

    node = _make_node(
        "critique-openai-model", llm_provider="openai", llm_model="gpt-5-mini"
    )
    await backend.run(node, "critique with explicit model", _make_context())

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["orchestrator_config"]["llm_provider"] == "openai"
    prefs = kwargs.get("provider_preferences")
    assert prefs is not None and len(prefs) == 1
    assert prefs[0].provider == "openai"
    assert prefs[0].model == "gpt-5-mini", (
        f"llm_model must reach provider_preferences with the node's own model, got {prefs!r}"
    )
