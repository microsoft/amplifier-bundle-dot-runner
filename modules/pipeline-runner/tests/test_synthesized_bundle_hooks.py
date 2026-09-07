"""Issue #64 regression proof: the synthesized named-worker bundle must MOUNT
the worker-session observability hooks, not just the providers and tools.

Sibling of ``test_synthesized_bundle_providers.py``, and the same class of
bug it closed. That file's header records the shape:

    ``_synthesize_agent_bundle_yaml`` never emitted a top-level
    ``providers:`` section at all (issue #338's root cause)

Then the live-gate finding recorded in ``_TOOL_MODULE_SOURCES`` closed the
same hole for ``tools:``. This file closes it for the third and last
section, ``hooks:`` -- and the consequence was quieter than either of the
other two, which is why it survived both fixes.

A missing provider kills the run instantly ("Available providers: []"). A
missing tool surface kills every ``must_write=`` contract within minutes. A
missing hooks section kills NOTHING -- the run completes, converges, and
ships a capsule. It just does so completely unobservably: EXTENSIONS.md
Sec 26's session-event persister only reaches a worker session by riding the
parent bundle through ``PreparedBundle.spawn``'s composition, so with no
``hooks:`` here, both halves of that seam no-op forever and no
``events.jsonl`` is ever written.

Measured (capsule-specify run 34039364352, issue #64): four agent nodes ran
42, 84, 47 and 38 minutes; the entire uploaded evidence per node was
prompt.md + response.md + status.json. "84 minutes of what?" was
unanswerable -- not because the telemetry was broken, but because the
shipped, tested telemetry module was never mounted.

Hermetic: real synthesis + real ``amplifier_foundation.load_bundle`` parsing
of a local temp YAML file (fake API keys via monkeypatch). No network, no
module activation, no LLM call.
"""

from __future__ import annotations

import asyncio

import pytest
from amplifier_module_pipeline_runner import default_worker

amplifier_foundation = pytest.importorskip("amplifier_foundation")

OBSERVABILITY_MODULE = "hooks-pipeline-observability"
TRUNCATION_MODULE = "hooks-tool-truncation"


def _with_one_provider_key(monkeypatch) -> None:
    """Synthesis refuses to run with zero configured providers (by design --
    see ``test_synthesize_raises_loud_never_silent_empty_mount_when_zero_keys``),
    so give it exactly one. Nothing here reaches a provider SDK."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")


async def _load_synthesized_bundle(worker_name: str):
    bundle_path = default_worker.write_agent_bundle(worker_name)
    return await amplifier_foundation.load_bundle(str(bundle_path))


@pytest.mark.parametrize("worker_name", ["coding-agent", "amplifier-agent"])
def test_synthesized_bundle_mounts_the_session_event_persister(
    monkeypatch, worker_name
):
    """The core issue #64 proof: the synthesized bundle's own mount plan
    carries a real top-level ``hooks:`` entry for the observability module.

    RED before the fix: ``mount_plan.get("hooks")`` was empty/absent for
    every named worker, so no spawned box-node worker ever had an observer
    attached and no run was forensically traceable.
    """
    _with_one_provider_key(monkeypatch)

    loaded = asyncio.run(_load_synthesized_bundle(worker_name))
    mount_plan = loaded.to_mount_plan()

    hooks = mount_plan.get("hooks")
    assert hooks, (
        f"synthesized --worker {worker_name!r} bundle mounts NO hooks "
        f"(mount_plan keys={sorted(mount_plan)!r}) -- this is issue #64: the "
        "Sec 26 worker-session persister is never composed into the spawned "
        "worker, so a multi-hour node produces no events.jsonl at all and "
        '"84 minutes of what?" stays unanswerable.'
    )
    hook_modules = {h.get("module") for h in hooks}
    assert OBSERVABILITY_MODULE in hook_modules, (
        f"expected a mounted {OBSERVABILITY_MODULE!r} module, got {hook_modules!r}"
    )


@pytest.mark.parametrize("worker_name", ["coding-agent", "amplifier-agent"])
def test_every_mounted_hook_entry_carries_a_resolvable_source(monkeypatch, worker_name):
    """Same discipline the providers test pins: ``Bundle.prepare`` only
    activates providers/tools/hooks entries that carry a ``source``. A hooks
    entry without one is a declaration that never becomes a mount -- exactly
    as silent as having no entry at all."""
    _with_one_provider_key(monkeypatch)

    loaded = asyncio.run(_load_synthesized_bundle(worker_name))
    for entry in loaded.to_mount_plan().get("hooks", []):
        assert entry.get("source"), f"hook entry missing 'source': {entry!r}"


@pytest.mark.parametrize("worker_name", ["coding-agent", "amplifier-agent"])
def test_synthesized_bundle_mounts_tool_output_truncation(monkeypatch, worker_name):
    """Second hook of the same class, quieter still than the first.

    ``hooks-tool-truncation`` implements coding-agent-loop spec Section
    5.1's MUST ("tool output ... MUST be truncated before being sent to the
    LLM"). It has shipped in this repo, fully tested, since the spec was
    vendored -- and was never mounted here either, so a spawned box-node
    worker sent every oversized `cat`/test-run output to the model whole.

    RED before the fix: ``hook_modules == {"hooks-pipeline-observability"}``.
    """
    _with_one_provider_key(monkeypatch)

    loaded = asyncio.run(_load_synthesized_bundle(worker_name))
    hook_modules = {h.get("module") for h in loaded.to_mount_plan().get("hooks", [])}

    assert TRUNCATION_MODULE in hook_modules, (
        f"synthesized --worker {worker_name!r} bundle does not mount "
        f"{TRUNCATION_MODULE!r} (mounted: {hook_modules!r}) -- spec Section "
        "5.1's truncation MUST is unenforced on the entire named-worker path"
    )


def test_hook_sources_table_is_not_empty():
    """The table itself is the contract -- an empty ``_HOOK_MODULE_SOURCES``
    would make every assertion above vacuously reachable only through the
    parametrized loads, and would silently reintroduce the bug."""
    assert default_worker._HOOK_MODULE_SOURCES
    assert OBSERVABILITY_MODULE in default_worker._HOOK_MODULE_SOURCES
    assert TRUNCATION_MODULE in default_worker._HOOK_MODULE_SOURCES


def test_truncation_hook_is_mounted_without_config_overrides(monkeypatch):
    """No ``config:`` block, deliberately.

    The hook's own defaults ARE spec Section 5.2's per-tool table
    (read_file 50,000 / bash 30,000 / grep 20,000 / ...). Emitting tighter
    limits here would be an engine author silently overruling a normative
    table on behalf of every consumer; the spec points at the operator for
    that. Accumulation -- the leak the measured evidence actually shows --
    is bounded by loop-agent's retention window instead (specs/EXTENSIONS.md
    Sec 45), not by shrinking this table.
    """
    _with_one_provider_key(monkeypatch)

    rendered = default_worker._synthesize_agent_bundle_yaml("coding-agent")
    hooks_block = rendered.split("\nhooks:\n", 1)[1].split("\nsession:", 1)[0]

    assert TRUNCATION_MODULE in hooks_block
    assert "config:" not in hooks_block, (
        "the synthesized hooks block carries a config override -- see this "
        f"test's docstring for why it must not:\n{hooks_block}"
    )
