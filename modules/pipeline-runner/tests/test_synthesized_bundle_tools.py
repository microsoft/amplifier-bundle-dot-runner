"""Live-gate regression proof: the synthesized ``--worker loop-agent`` /
``--worker amplifier-agent`` bundle must actually MOUNT a tool surface, not
only providers.

THE GAP THIS CLOSES: ``test_synthesized_bundle_providers.py`` (issue #338)
asks ``Bundle.to_mount_plan()`` what the synthesis mounts -- but only ever
about ``providers``. It went green while the very same mount plan carried
ZERO ``tools`` entries, because nothing in the suite asked that half of the
question. This file asks exactly that half, with the same real chain (real
synthesis + real ``amplifier_foundation.load_bundle`` parsing, hermetic --
fake API keys via monkeypatch, no network, no LLM call).

RED before the fix, and NOT hypothetically: amplifier-bundle-attractor run
33296437678 drove this exact synthesized bundle against a real
ANTHROPIC_API_KEY. The providers half worked -- 210 seconds of real model
calls, no "Available providers: []". The tools half did not exist, and the
worker said so itself in ``logs/orient/response.md``:

    "My actual available tool set in this session is limited to
     `spawn_agent`, `send_input`, `wait`, and `close_agent` -- there is no
     `read_file`, `write_file`, `edit_file`, `shell`, `grep`, or `glob` tool
     actually exposed to me, despite the system prompt describing them as if
     available."

The pipeline's first maker node declared ``must_write=".ai/brief.md"``,
exhausted its retry budget without writing one byte, and the run died
pre-loop. Every ``must_write=`` contract is unsatisfiable by construction on
a tool-less worker, so this is not a nice-to-have mount: it is the
difference between a worker and a chatbot.
"""

from __future__ import annotations

import asyncio

import pytest
from amplifier_module_pipeline_runner import default_worker

amplifier_foundation = pytest.importorskip("amplifier_foundation")

#: The tool surface a maker node cannot do its job without. Named
#: individually (not just "len(tools) > 0") so a future synthesis that
#: mounts *some* tools but silently drops the filesystem or shell one still
#: reads RED -- dropping write access is exactly the failure this file
#: exists to catch, and it would be invisible to a bare count assertion.
_REQUIRED_TOOL_MODULES = {"tool-filesystem", "tool-bash", "tool-search"}


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


@pytest.mark.parametrize("worker_name", ["loop-agent", "amplifier-agent"])
def test_synthesized_bundle_mounts_a_real_tool_surface(monkeypatch, worker_name):
    """The core proof: the synthesized bundle's own mount plan must carry
    real ``tools`` entries, so a spawned box-node worker can read, write, and
    run things -- not merely talk to a model."""
    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    loaded = asyncio.run(_load_synthesized_bundle(worker_name))
    mount_plan = loaded.to_mount_plan()

    tools = mount_plan.get("tools")
    assert tools, (
        f"synthesized --worker {worker_name!r} bundle mounts NO tools "
        f"(mount_plan keys={sorted(mount_plan)!r}) -- a box-node worker would "
        "reach the model with only the spawn tools and could not read, write, "
        "or run anything, making every `must_write=` node contract "
        "unsatisfiable by construction (attractor run 33296437678)."
    )

    tool_modules = {t.get("module") for t in tools}
    missing = _REQUIRED_TOOL_MODULES - tool_modules
    assert not missing, (
        f"synthesized --worker {worker_name!r} bundle is missing required tool "
        f"module(s) {sorted(missing)!r}; mounted: {sorted(tool_modules)!r}"
    )


@pytest.mark.parametrize("worker_name", ["loop-agent", "amplifier-agent"])
def test_every_mounted_tool_entry_carries_a_resolvable_source(
    monkeypatch, worker_name
):
    """Module activation only activates providers/tools/hooks entries that
    carry a ``source`` (``amplifier_foundation.bundle.Bundle.prepare``). A
    ``tools:`` section whose entries lack one would parse fine, mount
    nothing, and reproduce the original bug with a green mount plan -- the
    most expensive possible way to be wrong."""
    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    loaded = asyncio.run(_load_synthesized_bundle(worker_name))
    for entry in loaded.to_mount_plan().get("tools", []):
        assert entry.get("source"), f"tool entry missing 'source': {entry!r}"


@pytest.mark.parametrize("worker_name", ["loop-agent", "amplifier-agent"])
def test_tool_mount_is_unconditional_not_keyed_off_provider_choice(
    monkeypatch, worker_name
):
    """Providers are mounted per configured API key; the tool surface is
    not conditional on anything. Whichever single provider a run happens to
    configure, the worker still needs to touch the tree it was pointed at --
    so switching the configured key must not change the tool mount."""
    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    anthropic_tools = {
        t.get("module")
        for t in asyncio.run(_load_synthesized_bundle(worker_name))
        .to_mount_plan()
        .get("tools", [])
    }

    _clear_all_provider_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    openai_tools = {
        t.get("module")
        for t in asyncio.run(_load_synthesized_bundle(worker_name))
        .to_mount_plan()
        .get("tools", [])
    }

    assert anthropic_tools == openai_tools == _REQUIRED_TOOL_MODULES
