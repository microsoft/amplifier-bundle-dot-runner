"""ToolHandler must reap its subprocess when its own task is cancelled (#34).

``ToolHandler.execute()`` used to kill its ``create_subprocess_shell`` child on
exactly ONE path -- its own internal ``asyncio.TimeoutError`` (the node's
``timeout=`` racing ``proc.communicate()``). An EXTERNAL cancellation of the
handler's task -- delivered while it awaited ``proc.communicate()`` -- raised
``CancelledError`` straight through the handler without ever calling
``proc.kill()`` / ``proc.wait()``. The OS child kept running, orphaned from the
engine's bookkeeping.

That path is no longer rare: attractor-674 made the graph-level
``max_pipeline_duration`` fuse bound each node's own await, so ANY tool node in
a pipeline with a fuse can now be cancelled mid-subprocess -- not just nodes
that opt into their own ``timeout=``.

Method: each test spawns a sleeper with a per-test-unique duration (e.g.
``sleep 120.481073``) so its PID can be resolved unambiguously with ``pgrep``
even while other suites on the same host run sleeps of their own. ``exec``
replaces the shell, so the resolved PID is BOTH the process the handler
spawned and the process actually sleeping -- no fork in between to make the
assertion lie. Liveness is then polled with ``os.kill(pid, 0)`` inside a
bounded window; a *reaped* child is fully gone, so not even a zombie (which
``os.kill(pid, 0)`` would still find) remains.

RED-proof: on the pre-fix handler the sleeper is alive for the whole window
and every cancellation test below fails on its "survived" assertion.
"""

from __future__ import annotations

import asyncio
import os
import random
import subprocess
import time

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.handlers.tool import ToolHandler
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise

# Long enough that the sleeper can only ever disappear because something
# killed it -- never because it finished on its own inside a window below.
_SLEEP_S = 120

# Bounded windows -- never a bare sleep.
_SPAWN_WINDOW_S = 10.0  # child must be observable within this
_REAP_WINDOW_S = 5.0  # child must be gone within this after cancellation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_marker() -> str:
    """A per-test-unique sleep duration, used as the process's own marker."""
    return f"{_SLEEP_S}.{random.randint(100000, 999999)}"


def _sleeper_command(marker: str) -> str:
    """A shell command that becomes a uniquely-identifiable sleeping process.

    ``exec`` replaces the shell with ``sleep``, so exactly one process carries
    the marker and its PID is the one the handler spawned (``proc.pid``).
    Contains no ``$`` -- the handler substitutes ``$``-tokens before exec.
    """
    return f"exec sleep {marker}"


def _empty_graph() -> Graph:
    """Minimal graph -- the tool handler only reads ``source_dir`` off it."""
    return Graph(name="t", nodes={}, edges=[])


def _sleeper_pids(marker: str) -> list[int]:
    """PIDs whose command line carries ``marker`` (``pgrep -f``)."""
    proc = subprocess.run(
        ["pgrep", "-f", f"sleep {marker}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [int(line) for line in proc.stdout.split() if line.isdigit()]


def _pid_alive(pid: int) -> bool:
    """True while ``pid`` still exists (a not-yet-reaped zombie counts)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by someone else
        return True
    return True


async def _await_sleeper_pid(marker: str) -> int:
    """Poll until the sleeper is observable; fail loudly on timeout."""
    deadline = time.monotonic() + _SPAWN_WINDOW_S
    while time.monotonic() < deadline:
        pids = _sleeper_pids(marker)
        if pids:
            assert len(pids) == 1, f"expected one sleeper for {marker}, got {pids}"
            return pids[0]
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"tool subprocess 'sleep {marker}' never appeared within {_SPAWN_WINDOW_S}s"
    )


async def _await_pid_gone(pid: int, window_s: float = _REAP_WINDOW_S) -> bool:
    """Poll up to ``window_s`` for ``pid`` to disappear."""
    deadline = time.monotonic() + window_s
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        await asyncio.sleep(0.05)
    return not _pid_alive(pid)


def _kill_if_alive(pid: int) -> None:
    """Test-local safety net so a RED run never leaks a 120s sleeper."""
    try:
        os.kill(pid, 9)
    except (ProcessLookupError, PermissionError):
        pass


# ---------------------------------------------------------------------------
# Handler level: external cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_cancellation_reaps_the_subprocess(tmp_path):
    """Cancelling the handler's task kills AND reaps the child (issue #34)."""
    marker = _new_marker()
    node = Node(
        id="sleeper",
        shape="parallelogram",
        attrs={"tool_command": _sleeper_command(marker)},
    )
    handler = ToolHandler()

    task = asyncio.ensure_future(
        handler.execute(node, PipelineContext(), _empty_graph(), str(tmp_path))
    )
    pid = await _await_sleeper_pid(marker)

    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The cancellation must not merely propagate -- the child it left
        # behind must be gone. On the pre-fix handler it is still sleeping.
        assert await _await_pid_gone(pid), (
            f"tool subprocess pid={pid} survived cancellation of its handler "
            f"task for {_REAP_WINDOW_S}s -- it was never killed/reaped"
        )
    finally:
        _kill_if_alive(pid)


@pytest.mark.asyncio
async def test_internal_timeout_still_reaps_the_subprocess(tmp_path):
    """The node's own ``timeout=`` path still kills and reaps (no regression).

    Guards the refactor that folded both paths into ONE cleanup routine: the
    pre-existing timeout behaviour (FAIL outcome naming the timeout, child
    gone) must be exactly what it was.
    """
    marker = _new_marker()
    node = Node(
        id="sleeper",
        shape="parallelogram",
        timeout=300,  # ms -- the handler divides by 1000
        attrs={"tool_command": _sleeper_command(marker)},
    )
    handler = ToolHandler()

    task = asyncio.ensure_future(
        handler.execute(node, PipelineContext(), _empty_graph(), str(tmp_path))
    )
    pid = await _await_sleeper_pid(marker)

    try:
        outcome = await asyncio.wait_for(task, timeout=_SPAWN_WINDOW_S)
        assert outcome.status == StageStatus.FAIL
        assert "Timeout after 0.3s" in (outcome.failure_reason or "")
        assert await _await_pid_gone(pid), (
            f"tool subprocess pid={pid} survived its own node timeout"
        )
    finally:
        _kill_if_alive(pid)


@pytest.mark.asyncio
async def test_successful_command_is_unaffected(tmp_path):
    """The ordinary success path is untouched by the cleanup routine."""
    node = Node(
        id="echo",
        shape="parallelogram",
        attrs={"tool_command": "printf reaped_nothing"},
    )
    context = PipelineContext()
    outcome = await ToolHandler().execute(node, context, _empty_graph(), str(tmp_path))
    assert outcome.status == StageStatus.SUCCESS
    assert context.get("tool.output") == "reaped_nothing"
    assert context.get("tool.last_line") == "reaped_nothing"


# ---------------------------------------------------------------------------
# Engine level: the fuse path that made this reachable for every tool node
# ---------------------------------------------------------------------------


_FUSE_DOT = """
digraph {{
    max_pipeline_duration="{budget_ms}"
    start   [shape=Mdiamond]
    sleeper [shape=parallelogram, tool_command="{command}"]
    exit    [shape=Msquare]
    start -> sleeper -> exit
}}
"""


@pytest.mark.asyncio
async def test_fuse_expiring_during_tool_node_leaves_no_orphan(tmp_path):
    """A fuse that expires mid-``tool_command`` leaves no orphaned child.

    End-to-end over the real seam attractor-674 opened: the engine bounds the
    node's await by the remaining ``max_pipeline_duration`` budget and cancels
    the handler task mid-subprocess. The handler is what must translate that
    cancellation into a dead child.
    """
    marker = _new_marker()
    graph = parse_dot(_FUSE_DOT.format(budget_ms=300, command=_sleeper_command(marker)))
    validate_or_raise(graph)
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=None)),
        logs_root=str(tmp_path),
    )

    run_task = asyncio.ensure_future(engine.run())
    pid = await _await_sleeper_pid(marker)

    try:
        outcome = await asyncio.wait_for(run_task, timeout=30.0)
        assert outcome.status == StageStatus.FAIL
        assert outcome.failure_reason == "max_pipeline_duration_exceeded"

        assert await _await_pid_gone(pid), (
            f"tool subprocess pid={pid} outlived the pipeline: the fuse "
            "cancelled its node but the child was never killed/reaped"
        )
    finally:
        _kill_if_alive(pid)
