"""The hosted amplifier-agent session's events reach the run's evidence.

THE DEFECT (node-matrix run ``20260907T043835Z``, rows ``aa-anthropic-default``
and ``aa-gpt5-medium``): both PASSed their gate, and both were UNMEASURABLE --
no call count, no token counts, no cost. The collector reads
``<logs>/<node>/status.json``'s ``session_id`` and the matching
``<logs>/<node>/sessions/<id>/events.jsonl``; for an ``amplifier-agent`` node
that file held the adapter session's lifecycle brackets and nothing else,
because the session that actually calls the model is a SECOND coordinator this
adapter builds from amplifier-agent's own bundle, which nothing composed the
Sec 26 persister into.

These tests drive the REAL orchestrator against the REAL, SHIPPED
``SessionEventPersister`` (no reimplementation, no stub) with the module's
existing fake-Engine seam, and pin the three things the collector needs:

  1. a stream exists at ``<sessions_dir>/<id>/events.jsonl`` carrying >= 1
     provider event and >= 1 tool event;
  2. the usage block -- which the hosted orchestrator NEVER puts on
     ``provider:response``, because it never emits that event at all -- is
     there, VERBATIM, with its ``cost_usd``;
  3. ``<id>`` is the id the adapter reports upward, so ``status.json`` names
     the stream that exists rather than an empty sibling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import amplifier_module_loop_amplifier_agent as laa
import pytest
from amplifier_module_loop_pipeline.worker_observability import (
    current_worker_sessions_dir,
)

from ._fakes import CapturingHooks, FakeContextManager, make_fake_deps

#: One LLM call, verbatim in the shape the hosted runtime really emits it:
#: ``provider:request`` from loop-streaming (which imports PROVIDER_REQUEST and
#: PROVIDER_ERROR and *not* PROVIDER_RESPONSE), ``llm:response`` from the
#: provider module itself carrying the "#69 schema" usage block, and a tool
#: call bracketed by the kernel's own tool events.
_HOSTED_TURN_EVENTS: list[tuple[str, dict[str, Any]]] = [
    ("provider:request", {"provider": "anthropic", "iteration": 0}),
    (
        "llm:response",
        {
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "duration_ms": 4210,
            "status": "ok",
            "usage": {
                "input_tokens": 48123,
                "output_tokens": 902,
                "cache_read_tokens": 41000,
                "cache_write_tokens": 1200,
                "cost_usd": "0.043215",
            },
        },
    ),
    ("tool:pre", {"tool": "bash", "arguments": {"command": "pytest -q"}}),
    ("tool:post", {"tool": "bash", "result": "3 passed"}),
]


async def _run_turn_with_persistence(
    sessions_dir: Path,
    *,
    orchestrator_config: dict[str, Any] | None = None,
    emit_events: list[tuple[str, dict[str, Any]]] | None = None,
) -> tuple[CapturingHooks, Any]:
    """Drive the REAL orchestrator with the codergen handler's ContextVar set.

    ``current_worker_sessions_dir`` is the engine-side half of Sec 26's seam
    (``handlers/codergen.py`` sets it to ``<stage_dir>/sessions`` around each
    backend call); setting it here is what a real node execution does, not a
    test-only shortcut.
    """
    deps, captured = make_fake_deps(
        reply_text="done",
        emit_events=_HOSTED_TURN_EVENTS if emit_events is None else emit_events,
    )
    original = laa._load_dependencies
    laa._load_dependencies = lambda: deps
    token = current_worker_sessions_dir.set(str(sessions_dir))
    try:
        orchestrator = laa.AmplifierAgentOrchestrator(
            coordinator=object(), config=orchestrator_config or {}
        )
        hooks = CapturingHooks()
        await orchestrator.execute(
            "do the thing", FakeContextManager(), {}, {}, hooks
        )
    finally:
        current_worker_sessions_dir.reset(token)
        laa._load_dependencies = original
    return hooks, captured


def _read_stream(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]


@pytest.mark.asyncio
async def test_child_session_events_land_under_the_node(tmp_path: Path) -> None:
    """>= 1 provider event and >= 1 tool event, at the collector's own path."""
    hooks, _ = await _run_turn_with_persistence(tmp_path)

    worker_session_id = hooks.completion["metadata"]["worker_session_id"]
    stream = tmp_path / worker_session_id / "events.jsonl"
    assert stream.exists(), (
        f"no events.jsonl under {tmp_path}/{worker_session_id}/ -- the hosted "
        f"session's events were not persisted (present: "
        f"{sorted(p.name for p in tmp_path.iterdir())})"
    )

    records = _read_stream(stream)
    names = [r["event"] for r in records]
    provider_events = [n for n in names if n.startswith("provider:")]
    tool_events = [n for n in names if n.startswith("tool:")]
    assert provider_events, f"no provider events persisted (saw {names})"
    assert tool_events, f"no tool events persisted (saw {names})"


@pytest.mark.asyncio
async def test_usage_reaches_provider_response_verbatim(tmp_path: Path) -> None:
    """The collector's own contract: ``provider:response`` carries the usage.

    The hosted orchestrator emits ``provider:request`` but never
    ``provider:response`` -- so without the ``llm:response`` translation the
    stream can answer "how many calls?" and NOT "how many tokens, how much
    money?", which is exactly the state the two aa- rows were measured in.
    Every number here is forwarded, not derived: the assertion is equality
    with the provider module's own block.
    """
    hooks, _ = await _run_turn_with_persistence(tmp_path)

    worker_session_id = hooks.completion["metadata"]["worker_session_id"]
    records = _read_stream(tmp_path / worker_session_id / "events.jsonl")

    responses = [r for r in records if r["event"] == "provider:response"]
    assert len(responses) == 1, (
        "expected exactly one provider:response per LLM call, got "
        f"{len(responses)} (events: {[r['event'] for r in records]})"
    )
    assert responses[0]["data"]["usage"] == _HOSTED_TURN_EVENTS[1][1]["usage"]

    # And exactly one request per call -- the bridge must not also translate
    # llm:request, which would double every row's call count.
    requests = [r for r in records if r["event"] == "provider:request"]
    assert len(requests) == 1, f"call count double-counted: {len(requests)} requests"


@pytest.mark.asyncio
async def test_failed_call_is_not_bridged_as_a_zero_cost_call(
    tmp_path: Path,
) -> None:
    """An errored ``llm:response`` must not become a real-looking call.

    loop-streaming already emits ``provider:error`` for a failed call; a
    bridged ``provider:response`` with no usage would read to the collector as
    a call that succeeded and cost nothing.
    """
    hooks, _ = await _run_turn_with_persistence(
        tmp_path,
        emit_events=[
            ("provider:request", {"provider": "anthropic", "iteration": 0}),
            (
                "llm:response",
                {
                    "provider": "anthropic",
                    "model": "claude-sonnet-5",
                    "status": "error",
                    "error": "overloaded",
                },
            ),
        ],
    )

    worker_session_id = hooks.completion["metadata"]["worker_session_id"]
    records = _read_stream(tmp_path / worker_session_id / "events.jsonl")
    assert not [r for r in records if r["event"] == "provider:response"]


@pytest.mark.asyncio
async def test_reported_session_id_is_the_one_the_stream_is_filed_under(
    tmp_path: Path,
) -> None:
    """status.json's join key must name the directory that actually exists.

    The parent reads this id out of the completion envelope
    (``backend._session_id_from_spawn_result``) and writes it to status.json;
    the persister names the directory from the same id, stamped as an event
    default field. This pins the two to the same string -- the whole point of
    the key, since the spawn result's own ``session_id`` names the ADAPTER
    session, whose stream holds no telemetry.
    """
    hooks, captured = await _run_turn_with_persistence(
        tmp_path, orchestrator_config={"thread_key": "author::round-1"}
    )

    worker_session_id = hooks.completion["metadata"]["worker_session_id"]
    assert (tmp_path / worker_session_id).is_dir()
    # The same id the hosted session was actually created under.
    assert captured["engine"].boot_params["sessionId"] == worker_session_id
    # thread_key derivation still holds (WAVE 6 continuity, unchanged).
    assert worker_session_id == "dot-runner-thread-author--round-1"


@pytest.mark.asyncio
async def test_absent_sessions_dir_is_a_no_op_not_a_failure(tmp_path: Path) -> None:
    """Outside a worker-spawning node, there is nothing to persist to.

    Sec 26's seam is both-sides-optional. With the ContextVar unset the turn
    must still complete normally and write nothing.
    """
    deps, _ = make_fake_deps(reply_text="done", emit_events=_HOSTED_TURN_EVENTS)
    original = laa._load_dependencies
    laa._load_dependencies = lambda: deps
    try:
        orchestrator = laa.AmplifierAgentOrchestrator(coordinator=object(), config={})
        hooks = CapturingHooks()
        reply = await orchestrator.execute(
            "do the thing", FakeContextManager(), {}, {}, hooks
        )
    finally:
        laa._load_dependencies = original

    assert reply == "done"
    assert hooks.completion["status"] == "success"
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_failed_turn_still_reports_the_worker_session_id(
    tmp_path: Path,
) -> None:
    """The incomplete envelope carries the id too.

    A turn that raises still owes the spawn boundary an envelope; a node whose
    worker died mid-run is precisely when someone goes looking for the stream,
    so the join key must survive the failure path.
    """
    deps, _ = make_fake_deps(raise_on_execute=RuntimeError("boom"))
    original = laa._load_dependencies
    laa._load_dependencies = lambda: deps
    token = current_worker_sessions_dir.set(str(tmp_path))
    try:
        orchestrator = laa.AmplifierAgentOrchestrator(
            coordinator=object(), config={"thread_key": "author::round-2"}
        )
        hooks = CapturingHooks()
        with pytest.raises(RuntimeError):
            await orchestrator.execute(
                "do the thing", FakeContextManager(), {}, {}, hooks
            )
    finally:
        current_worker_sessions_dir.reset(token)
        laa._load_dependencies = original

    assert hooks.completion["status"] == "incomplete"
    assert (
        hooks.completion["metadata"]["worker_session_id"]
        == "dot-runner-thread-author--round-2"
    )
