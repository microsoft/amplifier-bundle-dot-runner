"""Freshness floors must survive an mtime source coarser than ``time.time()``.

Two engine sites compare a file's mtime against ``node_start_wall`` -- a
``time.time()`` snapshot taken immediately before the node ran:

  * ``must_write.py`` (EXTENSIONS.md Sec 27): the artifact's mtime must
    strictly post-date node start, else the node FAILs ("planted").
  * ``status_file.py`` (EXTENSIONS.md Sec 41): a node-written
    ``status.json`` whose mtime does not post-date node start is treated
    as a stale leftover and ignored.

``time.time()`` is fine-grained. A file's mtime is stamped by the
filesystem, whose resolution may be **coarser** -- whole seconds on ext2,
vfat, HFS+, NFSv2 and several container/network mounts. On such a host a
file written *milliseconds after* the node started is stamped with an
mtime that does **not** strictly post-date ``node_start_wall``, and both
sites then reach the exact opposite of the truth:

    node_start_wall = 1000.4      (time.time(), fine-grained)
    file written at  1000.6       (genuinely during this execution)
    mtime stamped as 1000.0       (1-second-granularity filesystem)
    1000.0 <= 1000.4              -> "planted before node start"  # WRONG

That is issue #67's flaky pool: on such a host these two files' tests fail
nondeterministically (the race is lost only when ``node_start_wall`` is
sampled late enough in the tick), and in production the engine FAILs nodes
that did their work and silently discards valid ``status.json`` verdicts.

These tests are deterministic on every host. They do not depend on the
local filesystem's resolution: they use ``os.utime`` to stamp exactly what
a 1-second-granularity filesystem would have stamped, which is a faithful
emulation -- the engine cannot tell the two apart, it only ever sees the
resulting timestamp.

The anti-planting intent of both floors is pinned here too: the tolerance
is bounded by the granularity the timestamp itself evidences, so a file
genuinely planted before the node started still fails.
"""

from __future__ import annotations

import json
import math
import os
import time

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.freshness import (
    mtime_granularity_seconds,
    wrote_after_node_start,
)
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.must_write import check_must_write
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.status_file import read_status_override

_ONE_SECOND_NS = 1_000_000_000


# ---------------------------------------------------------------------------
# Unit level: the granularity derivation itself
# ---------------------------------------------------------------------------


def test_granularity_of_whole_second_timestamp_is_one_second():
    """A timestamp landing exactly on a second evidences a 1s-granular source."""
    assert mtime_granularity_seconds(1_700_000_000 * _ONE_SECOND_NS) == 1.0


def test_granularity_of_fine_grained_timestamp_is_zero():
    """A nanosecond-resolution stamp evidences no coarseness -- no tolerance."""
    assert mtime_granularity_seconds(1_700_000_000_123_456_789) == 0.0


def test_granularity_never_exceeds_one_second():
    """The tolerance is capped: a coarser-looking stamp buys at most 1s."""
    # 1000 whole seconds is divisible by 1e12 ns, but the cap holds.
    assert mtime_granularity_seconds(1_700_000_000_000_000_000) == 1.0


def test_fine_grained_equality_boundary_still_rejected():
    """On a fine-grained source the floor stays strictly-greater-than.

    Guards the adversarial ``os.utime``-to-exact-start bypass that
    must_write.py's docstring calls out (and that
    test_engine_must_write.py::test_case3b_mtime_equals_start_fails pins).
    """
    start = 1_700_000_000.123_456_7
    mtime_ns = int(start * 1e9)
    assert mtime_granularity_seconds(mtime_ns) == 0.0
    assert wrote_after_node_start(mtime_ns, start) is False


def test_equality_boundary_rejected_even_on_a_coarse_stamp():
    """Exact equality is never tolerated, whatever granularity is evidenced.

    A coarse filesystem's stamp is a floor of the real write time, so it
    lands strictly below a mid-tick ``node_start_wall`` -- never on it.
    Tolerating equality would forgive nothing real while reopening the
    ``os.utime``-to-exact-start bypass.
    """
    whole_second = 1_700_000_000
    assert mtime_granularity_seconds(whole_second * _ONE_SECOND_NS) == 1.0
    assert (
        wrote_after_node_start(whole_second * _ONE_SECOND_NS, float(whole_second))
        is False
    )


def test_coarse_stamp_within_its_own_granularity_counts_as_fresh():
    """The 1s-filesystem case: floored stamp, written after start -> fresh."""
    start = 1_700_000_000.4
    coarse_ns = math.floor(start) * _ONE_SECOND_NS  # what a 1s fs would stamp
    assert wrote_after_node_start(coarse_ns, start) is True


def test_coarse_stamp_beyond_its_own_granularity_is_still_stale():
    """The tolerance is bounded -- a genuinely older file stays stale."""
    start = 1_700_000_000.4
    old_ns = (math.floor(start) - 5) * _ONE_SECOND_NS
    assert wrote_after_node_start(old_ns, start) is False


# ---------------------------------------------------------------------------
# Site 1: must_write= artifact contract (EXTENSIONS.md Sec 27)
# ---------------------------------------------------------------------------


def _coarse_stamp(path, wall: float) -> None:
    """Stamp ``path`` exactly as a 1-second-granularity filesystem would."""
    whole = float(math.floor(wall))
    os.utime(path, (whole, whole))


def test_must_write_accepts_artifact_on_coarse_granularity_filesystem(tmp_path):
    """RED before the fix: a real artifact written during the node FAILs.

    The artifact is written after ``node_start_wall`` and carries content;
    the only thing "wrong" with it is that its filesystem stamps whole
    seconds. It must satisfy the contract.
    """
    artifact = tmp_path / "report.md"
    node_start_wall = time.time()
    artifact.write_text("# Real content written by this node\n")
    _coarse_stamp(artifact, node_start_wall)

    node = Node(id="maker", attrs={"must_write": str(artifact)})
    outcome = Outcome(status=StageStatus.SUCCESS)

    result = check_must_write(node, outcome, node_start_wall, PipelineContext())
    assert result is None, (
        "A genuinely fresh artifact on a 1-second-granularity filesystem must "
        f"satisfy must_write=, got {result!r}"
    )


def test_must_write_still_rejects_planted_artifact_on_coarse_filesystem(tmp_path):
    """The anti-planting floor survives the tolerance."""
    artifact = tmp_path / "report.md"
    node_start_wall = time.time()
    artifact.write_text("planted well before node start\n")
    whole = float(math.floor(node_start_wall) - 5)
    os.utime(artifact, (whole, whole))

    node = Node(id="maker", attrs={"must_write": str(artifact)})
    outcome = Outcome(status=StageStatus.SUCCESS)

    result = check_must_write(node, outcome, node_start_wall, PipelineContext())
    assert result is not None and result.status == StageStatus.FAIL, (
        "An artifact stamped 5 seconds before node start must still FAIL the "
        f"freshness floor, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Site 2: status.json read side (EXTENSIONS.md Sec 41)
# ---------------------------------------------------------------------------


def _node(node_id: str) -> Node:
    return Node(id=node_id, attrs={})


def test_status_override_honored_on_coarse_granularity_filesystem(tmp_path):
    """RED before the fix: a real, diverging status.json is ignored as stale."""
    node = _node("n")
    stage_dir = tmp_path / "n"
    stage_dir.mkdir()
    status_path = stage_dir / "status.json"

    node_start_wall = time.time()
    status_path.write_text(json.dumps({"outcome": "fail", "notes": "external"}))
    _coarse_stamp(status_path, node_start_wall)

    handler_outcome = Outcome(status=StageStatus.SUCCESS)
    result = read_status_override(node, str(tmp_path), node_start_wall, handler_outcome)
    assert result is not None, (
        "A status.json written during the node on a 1-second-granularity "
        "filesystem must be honored, not discarded as stale"
    )
    assert result.status == StageStatus.FAIL
    assert result.is_explicit is True


def test_status_override_still_ignores_genuinely_stale_file(tmp_path):
    """The stale-leftover guard survives the tolerance."""
    node = _node("n")
    stage_dir = tmp_path / "n"
    stage_dir.mkdir()
    status_path = stage_dir / "status.json"

    node_start_wall = time.time()
    status_path.write_text(json.dumps({"outcome": "fail"}))
    whole = float(math.floor(node_start_wall) - 5)
    os.utime(status_path, (whole, whole))

    handler_outcome = Outcome(status=StageStatus.SUCCESS)
    result = read_status_override(node, str(tmp_path), node_start_wall, handler_outcome)
    assert result is None, (
        f"A status.json stamped 5 seconds before node start is stale, got {result!r}"
    )
