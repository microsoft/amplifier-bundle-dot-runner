"""A checkpoint must carry the source directory its graph was resolved from.

The regression: a v2 checkpoint embeds the DOT *source* so a resume
is self-contained, but not the directory that source came from.  On resume the
engine reparses those bytes into a graph whose ``source_dir`` is empty, so
``resolve_dot_path`` (EXTENSIONS.md 10 / engine-surface C9 tier 2) skips the
source-directory tier entirely and falls through to ``context.target_dir``
(``--cwd``) -- a *different* directory on any resume run from its own workdir.
A relative ``dot_file=`` child that resolved correctly during the original run
then resolves somewhere else, or nowhere.

What this file pins:

* ``graph.source_dir`` round-trips through save/load as optional provenance.
* The fingerprint stays a function of the DOT bytes ALONE -- adding origin to
  the payload must not change graph identity, or every checkpoint written
  before this change would fail ladder rung 5.
* A v2 checkpoint with no origin stays valid and yields ``""`` (the old
  empty-anchor behaviour), so existing run directories still resume.
* A *present but malformed* origin fails loud at load rather than being
  coerced to ``""`` -- a silently-dropped anchor is the exact failure this
  change exists to end.
"""

from __future__ import annotations

import json

import pytest

from amplifier_module_loop_pipeline.checkpoint import (
    SCHEMA_VERSION,
    Checkpoint,
    CheckpointCorruptError,
    CheckpointFormatError,
    fingerprint_dot_source,
    load_checkpoint,
    load_checkpoint_for_resume,
    save_checkpoint,
)

_DOT = "digraph G { start [shape=Mdiamond]; done [shape=Msquare]; start -> done; }"


def _checkpoint(**graph_extra) -> Checkpoint:
    return Checkpoint(
        current_node="start",
        completed_nodes=["start"],
        context_snapshot={},
        timestamp="2026-09-16T00:00:00Z",
        node_outcomes={"start": {"status": "success", "is_explicit": True}},
        graph={
            "fingerprint": fingerprint_dot_source(_DOT),
            "dot_source": _DOT,
            **graph_extra,
        },
    )


# --- round trip -------------------------------------------------------------


def test_source_dir_round_trips(tmp_path):
    path = tmp_path / "checkpoint.json"
    save_checkpoint(_checkpoint(source_dir="/pkg/pipeline"), str(path))

    assert load_checkpoint(str(path)).graph_source_dir == "/pkg/pipeline"


def test_source_dir_is_written_into_the_existing_graph_block(tmp_path):
    """Provenance rides in the v2 ``graph`` payload -- no new top-level key,
    no schema bump."""
    path = tmp_path / "checkpoint.json"
    save_checkpoint(_checkpoint(source_dir="/pkg/pipeline"), str(path))

    data = json.loads(path.read_text())
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["graph"]["source_dir"] == "/pkg/pipeline"
    assert "source_dir" not in {k for k in data if k != "graph"}


def test_fingerprint_is_dot_bytes_only(tmp_path):
    """Origin is provenance, never identity.

    If origin reached the fingerprint, a checkpoint written before this change
    (or written from a relocated copy of the same bytes) would be refused at
    ladder rung 5 as "a different graph".
    """
    with_origin = tmp_path / "with.json"
    without_origin = tmp_path / "without.json"
    save_checkpoint(_checkpoint(source_dir="/pkg/pipeline"), str(with_origin))
    save_checkpoint(_checkpoint(), str(without_origin))

    a = load_checkpoint(str(with_origin))
    b = load_checkpoint(str(without_origin))
    assert a.graph_fingerprint == b.graph_fingerprint == fingerprint_dot_source(_DOT)


# --- backwards compatibility ------------------------------------------------


def test_v2_checkpoint_without_origin_still_resumes(tmp_path):
    """Every checkpoint written before this change has no ``source_dir``."""
    path = tmp_path / "checkpoint.json"
    save_checkpoint(_checkpoint(), str(path))

    checkpoint = load_checkpoint_for_resume(str(path))
    assert checkpoint.graph_source_dir == ""  # old empty-anchor behaviour


def test_empty_origin_is_treated_as_absent(tmp_path):
    path = tmp_path / "checkpoint.json"
    save_checkpoint(_checkpoint(source_dir=""), str(path))

    assert load_checkpoint_for_resume(str(path)).graph_source_dir == ""


# --- fail loud on a malformed origin ---------------------------------------


@pytest.mark.parametrize("bad", [123, ["/pkg"], {"dir": "/pkg"}, True])
def test_malformed_origin_fails_loud_rather_than_being_coerced(tmp_path, bad):
    path = tmp_path / "checkpoint.json"
    save_checkpoint(_checkpoint(), str(path))
    data = json.loads(path.read_text())
    data["graph"]["source_dir"] = bad
    path.write_text(json.dumps(data))

    with pytest.raises(CheckpointFormatError) as exc:
        load_checkpoint(str(path))
    assert "source_dir" in str(exc.value)


def test_malformed_origin_surfaces_as_a_named_resume_refusal(tmp_path):
    """On the resume ladder the same fault must arrive as rung 2, with the
    offending value quoted -- never as a silent fall back to no anchor."""
    path = tmp_path / "checkpoint.json"
    save_checkpoint(_checkpoint(), str(path))
    data = json.loads(path.read_text())
    data["graph"]["source_dir"] = 17
    path.write_text(json.dumps(data))

    with pytest.raises(CheckpointCorruptError) as exc:
        load_checkpoint_for_resume(str(path))
    message = str(exc.value)
    assert "source_dir" in message
    assert "17" in message
