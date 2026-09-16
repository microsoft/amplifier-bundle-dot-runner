"""Resume must keep resolving a relative ``dot_file=`` beside its root DOT.

The bug (dot_runner-4ws).  ``attractor run <file>`` seeds the root graph's
``source_dir`` from that file's directory, so a ``shape=folder`` node's
relative ``dot_file=`` resolves at tier 2 of the EXTENSIONS.md 10 /
engine-surface C9 precedence chain (absolute -> graph.source_dir ->
context.target_dir -> cwd).  A checkpoint embeds the DOT *bytes* but not that
directory, so ``attractor resume`` reparsed the graph with ``source_dir``
empty: tier 2 vanished and resolution silently slid down to
``context.target_dir`` -- i.e. ``--cwd``, a different directory entirely on any
resume run from its own workdir.

The interruption here is real: a child process in its own process group, killed
with SIGKILL while a node blocks, then a genuinely separate ``resume``
invocation.  Graphs are tool-only (no LLM, no network).

The oracle is deliberately a DECOY.  ``--cwd`` holds a *same-named* child DOT
whose tool node writes different bytes, so a fall-through to the wrong tier
cannot pass by accident -- it runs the decoy and the marker file says so.

Four cases, one per resume shape the fix has to keep honest:

1. ordinary resume from a distinct workdir  -> the package's child runs
2. relocated byte-identical ``--dot-file``  -> the EXPLICIT origin wins
3. a v2 checkpoint with no recorded origin  -> old empty-anchor behaviour
4. an absent child                          -> fails loud at node entry with
   ``child_dot_resolution``, and does NOT quietly execute the convenient
   same-named file sitting in ``--cwd``
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

CLI = [sys.executable, "-m", "amplifier_module_pipeline_runner"]

# start -> a -> gate -> sub(folder) -> exit
#
# gate blocks while ./BLOCK exists -- the interruption point.  It sits BEFORE
# the folder node so the child is reached only after the resume.
_ROOT_DOT = """digraph resume_source_dir {{
    graph [goal="relative child must survive a resume"]
    start [shape=Mdiamond]
    a    [shape=parallelogram, tool_command="echo ran >> a_runs.log; echo a-done"]
    gate [shape=parallelogram,
          tool_command="touch gate_started; while [ -f BLOCK ]; do sleep 0.2; done; echo gate-done"]
    sub  [shape=folder, dot_file="{child}"]
    exit [shape=Msquare]
    start -> a -> gate -> sub -> exit
}}
"""

_CHILD_DOT = """digraph child {{
    start [shape=Mdiamond]
    work  [shape=parallelogram, tool_command="printf '{marker}' > child_ran.txt"]
    done  [shape=Msquare]
    start -> work -> done
}}
"""


def _env():
    env = dict(os.environ)
    # Tool-only fixture: no provider is ever called, but the CLI preflights
    # the key's presence before running anything.
    env.setdefault("ANTHROPIC_API_KEY", "not-used-by-a-tool-only-graph")
    return env


def _cli(*args, timeout=300):
    return subprocess.run(
        [*CLI, *args],
        env=_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _wait_for(path: Path, timeout: float = 120.0, poll: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(poll)
    return False


@pytest.fixture
def interrupted(tmp_path):
    """Lay out package/ + work/, run for real, SIGKILL mid-``gate``.

    ``child_name`` names the ``dot_file=`` the root asks for; ``in_package``
    decides whether that file actually exists beside the root.  A same-named
    DECOY always exists in the work dir.
    """

    def _make(*, child_name: str = "child.dot", in_package: bool = True):
        package = tmp_path / "package"
        work = tmp_path / "work"
        logs = tmp_path / "logs"
        package.mkdir()
        work.mkdir()
        logs.mkdir()

        if in_package:
            (package / child_name).write_text(
                _CHILD_DOT.format(marker="package-child"), encoding="utf-8"
            )
        # The decoy: same name, different bytes, sitting in --cwd.
        (work / child_name).write_text(
            _CHILD_DOT.format(marker="decoy-child"), encoding="utf-8"
        )

        root = package / "root.dot"
        root.write_text(_ROOT_DOT.format(child=child_name), encoding="utf-8")
        (work / "BLOCK").write_text("")

        proc = subprocess.Popen(
            [
                *CLI,
                "run",
                str(root),
                "--logs-root",
                str(logs),
                "--cwd",
                str(work),
                "--worker",
                "llm-direct",
            ],
            env=_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            assert _wait_for(work / "gate_started"), (
                "gate never started; the run did not reach the interruption point"
            )
            checkpoint = json.loads((logs / "checkpoint.json").read_text())
            assert checkpoint["current_node"] == "a", checkpoint["current_node"]
            assert checkpoint["run_state"] == "in_flight"
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=60)
        finally:
            if proc.poll() is None:  # pragma: no cover - defensive
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=60)

        assert proc.returncode != 0, "the process was killed, not finished"
        (work / "BLOCK").unlink()  # what the crash was standing in for
        return root, package, work, logs

    return _make


# --- 1. ordinary resume from a distinct workdir -----------------------------


def test_ordinary_resume_resolves_the_child_beside_the_root_dot(interrupted):
    """No ``--dot-file``: the anchor can only come from the checkpoint."""
    _root, _package, work, logs = interrupted()

    resumed = _cli("resume", str(logs), "--cwd", str(work), "--worker", "llm-direct")
    assert resumed.returncode == 0, f"{resumed.stdout}\n{resumed.stderr}"

    marker = work / "child_ran.txt"
    assert marker.exists(), "the child pipeline never executed"
    assert marker.read_text() == "package-child", (
        "resume resolved the child against --cwd (the decoy) instead of the "
        "root DOT's own directory"
    )


# --- 2. relocated byte-identical --dot-file: explicit origin wins ------------


def test_explicit_dot_file_origin_wins_over_the_stored_one(interrupted, tmp_path):
    """A byte-identical copy elsewhere: resolution follows the COPY's dir.

    Fingerprint identity is DOT bytes only, so the relocated file passes ladder
    rung 5.  Its directory is then the caller's explicit, present-tense answer
    to "where does this graph live" and must outrank the recorded one.
    """
    root, _package, work, logs = interrupted()

    relocated = tmp_path / "relocated"
    relocated.mkdir()
    shutil.copy(root, relocated / "root.dot")
    # Same name, third set of bytes -- proves WHICH origin was used.
    (relocated / "child.dot").write_text(
        _CHILD_DOT.format(marker="relocated-child"), encoding="utf-8"
    )

    resumed = _cli(
        "resume",
        str(logs),
        "--dot-file",
        str(relocated / "root.dot"),
        "--cwd",
        str(work),
        "--worker",
        "llm-direct",
    )
    assert resumed.returncode == 0, f"{resumed.stdout}\n{resumed.stderr}"
    assert (work / "child_ran.txt").read_text() == "relocated-child"


# --- 3. a v2 checkpoint written before this change --------------------------


def test_checkpoint_without_a_recorded_origin_keeps_old_behaviour(interrupted):
    """Strip the origin: the resume must still run, on the old anchor rules.

    Old rules mean the source-directory tier is empty and ``context.target_dir``
    wins -- i.e. the decoy.  That is the PRE-EXISTING behaviour for a checkpoint
    that never recorded an origin, and it must stay working rather than start
    refusing.
    """
    _root, _package, work, logs = interrupted()

    checkpoint_path = logs / "checkpoint.json"
    data = json.loads(checkpoint_path.read_text())
    data["graph"].pop("source_dir", None)
    checkpoint_path.write_text(json.dumps(data, indent=2))

    resumed = _cli("resume", str(logs), "--cwd", str(work), "--worker", "llm-direct")
    assert resumed.returncode == 0, f"{resumed.stdout}\n{resumed.stderr}"
    assert (work / "child_ran.txt").read_text() == "decoy-child"


# --- 4. a missing child fails loud; no cwd fall-through ---------------------


def test_a_missing_child_fails_loud_instead_of_running_the_cwd_decoy(interrupted):
    """C9.3: unresolvable child -> ``child_dot_resolution`` at node ENTRY.

    With the anchor restored, tier 2 wins unconditionally (the chain has no
    existence check, by design -- that laziness is what makes write-then-run
    composition possible).  So an absent child must FAIL, not quietly execute
    the same-named file that happens to sit in ``--cwd``.
    """
    _root, _package, work, logs = interrupted(
        child_name="absent_child.dot", in_package=False
    )

    resumed = _cli("resume", str(logs), "--cwd", str(work), "--worker", "llm-direct")

    assert resumed.returncode != 0, (
        "an unresolvable child must fail the run, not fall through to --cwd"
    )
    assert not (work / "child_ran.txt").exists(), (
        "the decoy in --cwd was executed; resolution fell through a tier"
    )
    combined = resumed.stdout + resumed.stderr
    assert (
        "child_dot_resolution" in combined or "Child DOT file not found" in combined
    ), combined
