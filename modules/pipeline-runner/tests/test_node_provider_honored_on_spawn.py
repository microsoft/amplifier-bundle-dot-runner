"""A node's declared provider/model/effort must REACH the spawned child.

Regression cover for the silent-substitution defect measured on this repo at
``5562a78``: three node-matrix variants declared ``llm_provider="openai"
llm_model="gpt-5" reasoning_effort=low|medium|high`` and all three ran on
Anthropic claude-sonnet-5, with no warning anywhere in the run.

The engine's backend was never the problem -- it computed and sent the right
values.  They were dropped one hop later:
``PreparedBundle.spawn(orchestrator_config=...)`` merges into a **top-level**
``mount_plan["orchestrator"]["config"]`` key, while the orchestrator a spawned
pipeline node actually mounts is declared at
``mount_plan["session"]["orchestrator"]``.  Nothing read the key the values
landed in, so the child ran on whatever the synthesized agent bundle declared
(``llm_provider: anthropic``).

These tests pin the repair at the seam this repo owns:
``runner.apply_orchestrator_config`` puts the per-node values on the child
bundle's OWN ``session.orchestrator.config`` -- the channel ``loop-agent``
(and ``loop-amplifier-agent``) actually read.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from amplifier_module_pipeline_runner.runner import (
    apply_orchestrator_config,
    make_spawn_fn,
)


class _FakeBundle:
    """Minimal stand-in for ``amplifier_foundation.Bundle``.

    Only the attributes the overlay touches: ``name`` and ``session``.  A
    plain object (not a dataclass) proves the overlay relies on nothing but
    ``copy.copy`` + attribute assignment.
    """

    def __init__(self, name: str, session: dict[str, Any]):
        self.name = name
        self.session = session


def _agent_bundle(provider: str = "anthropic") -> _FakeBundle:
    return _FakeBundle(
        "dot-runner-default-agent",
        {
            "orchestrator": {
                "module": "loop-agent",
                "source": "git+https://example.invalid#subdirectory=modules/loop-agent",
                "config": {"llm_provider": provider},
            }
        },
    )


# ---------------------------------------------------------------------------
# The overlay itself
# ---------------------------------------------------------------------------


def test_overlay_lands_on_session_orchestrator_config():
    """The node's values reach the key the child orchestrator reads."""
    bundle = _agent_bundle()

    out = apply_orchestrator_config(
        bundle,
        {"llm_provider": "openai", "reasoning_effort": "medium", "max_turns": 7},
    )

    cfg = out.session["orchestrator"]["config"]
    assert cfg["llm_provider"] == "openai", (
        "the node's declared llm_provider must override the agent bundle's own "
        f"declaration; got {cfg['llm_provider']!r}"
    )
    assert cfg["reasoning_effort"] == "medium"
    assert cfg["max_turns"] == 7
    # The orchestrator identity must survive untouched.
    assert out.session["orchestrator"]["module"] == "loop-agent"
    assert "source" in out.session["orchestrator"]


def test_overlay_does_not_mutate_the_cached_bundle():
    """One agent Bundle is cached and reused by EVERY node.

    Writing the overlay into it would leak one node's provider onto the next
    -- the same silent-substitution class, one layer down.
    """
    bundle = _agent_bundle()

    first = apply_orchestrator_config(bundle, {"llm_provider": "openai"})
    second = apply_orchestrator_config(bundle, {"llm_provider": "gemini"})

    assert bundle.session["orchestrator"]["config"]["llm_provider"] == "anthropic", (
        "the cached agent bundle was mutated -- a later node would inherit an "
        "earlier node's provider"
    )
    assert first.session["orchestrator"]["config"]["llm_provider"] == "openai"
    assert second.session["orchestrator"]["config"]["llm_provider"] == "gemini"


def test_overlay_is_a_noop_without_orchestrator_config():
    bundle = _agent_bundle()
    assert apply_orchestrator_config(bundle, None) is bundle
    assert apply_orchestrator_config(bundle, {}) is bundle


def test_overlay_refuses_an_agent_with_no_inline_orchestrator():
    """Fail loud rather than silently spawn a child that ignores the node."""
    bundle = _FakeBundle("no-orch", {})

    with pytest.raises(ValueError) as exc:
        apply_orchestrator_config(bundle, {"llm_provider": "openai"})

    message = str(exc.value)
    assert "no-orch" in message
    assert "session.orchestrator" in message
    assert "llm_provider" in message


# ---------------------------------------------------------------------------
# End-to-end through the spawn capability -- the seam the engine calls
# ---------------------------------------------------------------------------


class _RecordingPrepared:
    """Captures the child bundle ``make_spawn_fn`` hands to ``spawn``."""

    def __init__(self, agents: dict[str, Any]):
        self.bundle = type("_B", (), {"agents": agents})()
        self.spawn_calls: list[dict[str, Any]] = []

    async def spawn(self, **kwargs: Any) -> dict[str, Any]:
        self.spawn_calls.append(kwargs)
        return {"output": "ok", "session_id": "child-1"}


_AGENT_CONFIG = {
    "session": {
        "orchestrator": {
            "module": "loop-agent",
            "config": {"llm_provider": "anthropic"},
        }
    }
}


def test_spawn_capability_delivers_node_provider_to_the_child_bundle():
    """The whole hop: engine kwargs -> child bundle the session is built from."""
    prepared = _RecordingPrepared({"dot-runner-default-agent": _AGENT_CONFIG})
    spawn_fn = make_spawn_fn(prepared)

    asyncio.run(
        spawn_fn(
            agent_name="dot-runner-default-agent",
            instruction="do the thing",
            parent_session=None,
            agent_configs={},
            orchestrator_config={
                "llm_provider": "openai",
                "reasoning_effort": "medium",
            },
        )
    )

    (call,) = prepared.spawn_calls
    cfg = call["child_bundle"].session["orchestrator"]["config"]
    assert cfg["llm_provider"] == "openai", (
        "the child bundle handed to spawn still declares "
        f"llm_provider={cfg['llm_provider']!r} -- the node's declared provider "
        "was dropped in flight, exactly the measured defect"
    )
    assert cfg["reasoning_effort"] == "medium"


def test_max_agent_turns_reaches_the_child_bundle_as_max_turns():
    """A node's `max_agent_turns=` must survive the WHOLE hop, as `max_turns`.

    The two halves were each pinned already and the join was not:
    `test_attribute_passthrough.py::test_spawn_passes_max_agent_turns` proves
    the engine puts `max_turns` into the spawn's `orchestrator_config`
    (backend.py:402 -> :700), and the overlay tests above prove
    `apply_orchestrator_config` lands `llm_provider`/`reasoning_effort` on the
    key the child orchestrator reads. Nothing proved `max_turns` specifically
    makes it all the way onto the child bundle -- and `max_turns` is the only
    one of the three that is a BOUND: a value silently dropped in flight
    leaves the node running unbounded while the graph says it is capped.

    That is not hypothetical here. `capsule.dot`'s `author` and `critique`
    nodes now declare `max_agent_turns=` precisely to bound the largest fuse
    consumers (REVIEW-pipelines-2026-09.md Sec4 changes 1-2). If this hop
    breaks, those graphs advertise a ceiling they do not have.
    """
    prepared = _RecordingPrepared({"dot-runner-default-agent": _AGENT_CONFIG})
    spawn_fn = make_spawn_fn(prepared)

    asyncio.run(
        spawn_fn(
            agent_name="dot-runner-default-agent",
            instruction="write the capsule",
            parent_session=None,
            agent_configs={},
            # Exactly what AmplifierBackend._run_with_spawn builds for a node
            # carrying max_agent_turns="205" (int-converted at backend.py:403).
            orchestrator_config={"llm_provider": "anthropic", "max_turns": 205},
        )
    )

    (call,) = prepared.spawn_calls
    cfg = call["child_bundle"].session["orchestrator"]["config"]
    assert cfg.get("max_turns") == 205, (
        "the node's declared turn cap did not reach the child bundle's own "
        f"session.orchestrator.config -- got {cfg.get('max_turns')!r}. A graph "
        "declaring max_agent_turns= would run UNBOUNDED while claiming a ceiling."
    )


def test_two_nodes_with_different_providers_do_not_contaminate_each_other():
    """Sequential spawns off ONE cached agent bundle stay independent."""
    prepared = _RecordingPrepared({"dot-runner-default-agent": _AGENT_CONFIG})
    spawn_fn = make_spawn_fn(prepared)

    async def _run() -> None:
        for provider in ("openai", "gemini"):
            await spawn_fn(
                agent_name="dot-runner-default-agent",
                instruction="x",
                parent_session=None,
                agent_configs={},
                orchestrator_config={"llm_provider": provider},
            )

    asyncio.run(_run())

    seen = [
        call["child_bundle"].session["orchestrator"]["config"]["llm_provider"]
        for call in prepared.spawn_calls
    ]
    assert seen == ["openai", "gemini"], f"provider bled between nodes: {seen}"


def test_child_constraint_cannot_drop_the_overlay():
    """A caller-supplied constraint runs BEFORE the overlay, never after."""
    prepared = _RecordingPrepared({"dot-runner-default-agent": _AGENT_CONFIG})

    def _strip_everything(bundle: Any) -> Any:
        # A constraint that rebuilds the bundle from scratch -- if the overlay
        # ran first, this would silently discard the node's provider.
        return _FakeBundle(
            "constrained",
            {"orchestrator": {"module": "loop-agent", "config": {}}},
        )

    spawn_fn = make_spawn_fn(prepared, child_constraint=_strip_everything)

    asyncio.run(
        spawn_fn(
            agent_name="dot-runner-default-agent",
            instruction="x",
            parent_session=None,
            agent_configs={},
            orchestrator_config={"llm_provider": "openai"},
        )
    )

    (call,) = prepared.spawn_calls
    cfg = call["child_bundle"].session["orchestrator"]["config"]
    assert cfg["llm_provider"] == "openai"


def test_orchestrator_config_is_still_passed_to_spawn():
    """The upstream argument keeps being sent.

    It is inert today (it lands on a key nothing mounts), but passing it costs
    nothing and means both channels agree automatically if that seam is ever
    repaired upstream.
    """
    prepared = _RecordingPrepared({"dot-runner-default-agent": _AGENT_CONFIG})
    spawn_fn = make_spawn_fn(prepared)

    asyncio.run(
        spawn_fn(
            agent_name="dot-runner-default-agent",
            instruction="x",
            parent_session=None,
            agent_configs={},
            orchestrator_config={"llm_provider": "openai"},
        )
    )

    (call,) = prepared.spawn_calls
    assert call["orchestrator_config"] == {"llm_provider": "openai"}
