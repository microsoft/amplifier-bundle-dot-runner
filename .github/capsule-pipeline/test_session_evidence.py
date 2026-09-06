"""Tests for the session-evidence pipeline (issue #64): scrub coverage,
size capping, and the per-node timing table. Stdlib only; no pytest.

Run from this directory:

    python3 -m unittest test_session_evidence -v

THE THREE THINGS PROVED HERE, and why each needed proving:

1. **The scrub actually reaches a worker-session stream.** Once the Sec 26
   persister is mounted, agent nodes write
   `<logs>/<node>/sessions/<sid>/events.jsonl`, and a `tool:post` record
   carries that tool's output verbatim. That is the exact shape of the
   2026-08 incident scrub_secrets.py was written for -- but nothing in this
   repo asserted that the workflow's evidence root actually COVERS that new
   path. A planted (fake, shape-exact) key is written into a session stream
   at its real nested depth and must come back redacted, with `scan`
   agreeing the root is clean afterward.

2. **Session streams are size-bounded, and the drop is stated.** An
   unbounded `tool:post` payload can make one stream hundreds of megabytes.
   `cap_session_evidence.py` keeps the head and the tail and leaves a
   first-class `evidence:truncated` record naming exactly what went. The
   test pins the marker's presence AND the head/tail survival -- a cap that
   silently swallowed the tail would look identical in a size check.

3. **The timing table answers "84 minutes of what?".** Given a synthetic
   run whose session stream contains one long LLM call and one short tool
   call, the table must report both counts and identify the LONG one as the
   longest single call. It must also distinguish "no session recorded"
   (a tool/gate node -- expected) from "session recorded, stream missing"
   (capture failure -- the interesting case).
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cap_session_evidence  # noqa: E402
import node_timing_table  # noqa: E402
import scrub_secrets  # noqa: E402

# Deliberately fake but shape-exact (planted, never real) -- same convention
# as test_scrub_secrets.py's own fixtures.
PLANTED_OPENAI_KEY = "sk-proj-" + "Ab1" * 22 + "XYZq"


def _event(name: str, ts: datetime, data: dict) -> str:
    return json.dumps(
        {"event": name, "timestamp": ts.isoformat(), "data": data},
        ensure_ascii=False,
    )


def _write_session_stream(stage_dir: Path, session_id: str, lines: list[str]) -> Path:
    """Write a stream at the REAL nested depth the engine produces."""
    path = stage_dir / "sessions" / session_id / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_status(stage_dir: Path, session_id: str | None) -> None:
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "status.json").write_text(
        json.dumps({"outcome": "success", "session_id": session_id}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. The scrub reaches worker-session streams at their real depth
# ---------------------------------------------------------------------------


class ScrubReachesSessionStreams(unittest.TestCase):
    def test_planted_key_in_a_session_stream_is_redacted(self):
        """The load-bearing proof for shipping session dirs as evidence.

        A worker's `tool:post` payload is arbitrary tool output. This plants
        a shape-exact fake key inside one, at the exact nested path the
        engine writes (`<logs>/<node>/sessions/<sid>/events.jsonl`), and
        scrubs the LOGS ROOT -- which is precisely what the workflow's
        scrub step is pointed at. If the evidence root ever stops covering
        this path, this test goes red instead of a key going public.
        """
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "capsule-run" / "logs"
            now = datetime.now(timezone.utc)
            stream = _write_session_stream(
                logs / "author",
                "sid-plant-1",
                [
                    _event("session:start", now, {"session_id": "sid-plant-1"}),
                    _event(
                        "tool:post",
                        now,
                        {
                            "tool_name": "bash",
                            "result": f"OPENAI_API_KEY={PLANTED_OPENAI_KEY}\nHOME=/root",
                        },
                    ),
                ],
            )
            self.assertIn(PLANTED_OPENAI_KEY, stream.read_text(encoding="utf-8"))

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(scrub_secrets.cmd_scrub([str(logs)]), 0)

            after = stream.read_text(encoding="utf-8")
            self.assertNotIn(PLANTED_OPENAI_KEY, after)
            self.assertIn("[REDACTED:", after)
            # The surrounding structure survives -- the scrub is surgical.
            self.assertIn("HOME=/root", after)
            for line in after.splitlines():
                json.loads(line)  # still valid JSONL

    def test_scan_agrees_the_root_is_clean_after_the_scrub(self):
        """`scan` is the independent second opinion the workflow's residual
        gate is built on. If it still finds the planted key, the upload gate
        would (correctly) block -- so this pins that the scrub genuinely
        cleared it rather than merely mangling it."""
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            now = datetime.now(timezone.utc)
            _write_session_stream(
                logs / "void",
                "sid-plant-2",
                [_event("tool:post", now, {"result": PLANTED_OPENAI_KEY})],
            )
            with contextlib.redirect_stdout(io.StringIO()):
                scrub_secrets.cmd_scrub([str(logs)])
                findings = scrub_secrets.scan_text(
                    (logs / "void" / "sessions" / "sid-plant-2" / "events.jsonl").read_text(
                        encoding="utf-8"
                    ),
                    scrub_secrets._watched_literals(),
                )
            self.assertEqual(
                [f for f in findings if f != "high-entropy-token"],
                [],
                f"known-credential shape survived the scrub: {findings!r}",
            )


# ---------------------------------------------------------------------------
# 2. Size cap: bounded, and honest about what it dropped
# ---------------------------------------------------------------------------


class SessionEvidenceCap(unittest.TestCase):
    def _big_stream(self, root: Path, records: int = 300) -> Path:
        now = datetime.now(timezone.utc)
        lines = [_event("session:start", now, {"marker": "HEAD-SENTINEL"})]
        lines += [
            _event("tool:post", now, {"result": "x" * 200, "n": i})
            for i in range(records)
        ]
        lines.append(_event("session:end", now, {"marker": "TAIL-SENTINEL"}))
        return _write_session_stream(root / "author", "sid-big", lines)

    def test_oversized_stream_is_capped_with_a_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stream = self._big_stream(root)
            original = stream.stat().st_size

            reports, warnings = cap_session_evidence.cap_roots(
                [root], max_bytes=16 * 1024, keep_head_lines=5, keep_tail_lines=5
            )

            self.assertEqual(warnings, [])
            self.assertEqual(len(reports), 1)
            self.assertLess(stream.stat().st_size, original)
            self.assertGreater(reports[0]["dropped_lines"], 0)
            self.assertEqual(reports[0]["original_bytes"], original)

            records = [
                json.loads(line)
                for line in stream.read_text(encoding="utf-8").splitlines()
            ]
            markers = [
                r for r in records if r["event"] == cap_session_evidence.TRUNCATION_EVENT
            ]
            self.assertEqual(len(markers), 1, "exactly one truncation marker")
            self.assertEqual(
                markers[0]["data"]["dropped_lines"], reports[0]["dropped_lines"]
            )
            self.assertIn("max-bytes", markers[0]["data"]["reason"])

            # HEAD AND TAIL BOTH SURVIVE. A cap that kept only the head would
            # pass a size check and lose the only part that explains where a
            # runaway node actually stopped.
            self.assertEqual(records[0]["data"]["marker"], "HEAD-SENTINEL")
            self.assertEqual(records[-1]["data"]["marker"], "TAIL-SENTINEL")

    def test_small_stream_is_left_byte_identical(self):
        """Under the cap, nothing is touched -- no marker, no rewrite. A
        cap that rewrote every file would make "was this truncated?"
        unanswerable from the artifact."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime.now(timezone.utc)
            stream = _write_session_stream(
                root / "orient", "sid-small", [_event("session:start", now, {})]
            )
            before = stream.read_bytes()
            reports, _ = cap_session_evidence.cap_roots([root], max_bytes=1024 * 1024)
            self.assertEqual(reports, [])
            self.assertEqual(stream.read_bytes(), before)

    def test_capping_after_a_scrub_preserves_redaction_markers(self):
        """Ordering guarantee: the workflow scrubs, THEN caps. Capping only
        ever removes whole lines, so a redacted span it keeps stays
        redacted and it can never reintroduce a secret the scrub removed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime.now(timezone.utc)
            lines = [_event("session:start", now, {"k": PLANTED_OPENAI_KEY})]
            lines += [
                _event("tool:post", now, {"result": "y" * 200, "n": i})
                for i in range(300)
            ]
            lines.append(_event("session:end", now, {"k": PLANTED_OPENAI_KEY}))
            stream = _write_session_stream(root / "author", "sid-order", lines)

            with contextlib.redirect_stdout(io.StringIO()):
                scrub_secrets.cmd_scrub([str(root)])
            cap_session_evidence.cap_roots(
                [root], max_bytes=16 * 1024, keep_head_lines=3, keep_tail_lines=3
            )

            after = stream.read_text(encoding="utf-8")
            self.assertNotIn(PLANTED_OPENAI_KEY, after)
            self.assertIn("[REDACTED:", after)

    def test_missing_root_is_reported_not_fatal(self):
        reports, warnings = cap_session_evidence.cap_roots(
            [Path("/nonexistent/capsule-run")]
        )
        self.assertEqual(reports, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("does not exist", warnings[0])


# ---------------------------------------------------------------------------
# 3. The timing table
# ---------------------------------------------------------------------------


class NodeTimingTable(unittest.TestCase):
    def _run_dir(self, tmp: str) -> Path:
        logs = Path(tmp) / "logs"
        logs.mkdir(parents=True)
        t0 = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)

        # An agent node: one 40-minute LLM call, one 3-second tool call.
        _write_status(logs / "author", "sid-author")
        _write_session_stream(
            logs / "author",
            "sid-author",
            [
                _event("session:start", t0, {"session_id": "sid-author"}),
                _event("provider:request", t0, {"model": "claude-sonnet-4-5"}),
                _event(
                    "provider:response",
                    t0 + timedelta(minutes=40),
                    {"usage": {"output_tokens": 900}},
                ),
                _event(
                    "tool:pre", t0 + timedelta(minutes=41), {"tool_name": "bash"}
                ),
                _event(
                    "tool:post",
                    t0 + timedelta(minutes=41, seconds=3),
                    {"tool_name": "bash", "result": "ok"},
                ),
                _event("session:end", t0 + timedelta(minutes=42), {}),
            ],
        )
        # A gate node: no worker, no session id -- expected, uninteresting.
        _write_status(logs / "redgate", None)
        # An agent node whose capture was LOST: session id recorded, no stream.
        _write_status(logs / "critique", "sid-lost")

        (logs / "trace.jsonl").write_text(
            "\n".join(
                json.dumps(r)
                for r in (
                    {"iteration": 0, "node_id": "author", "status": "success",
                     "duration_ms": 2_520_000.0},
                    {"iteration": 0, "node_id": "redgate", "status": "success",
                     "duration_ms": 800.0},
                    {"iteration": 0, "node_id": "critique", "status": "success",
                     "duration_ms": 60_000.0},
                )
            )
            + "\n",
            encoding="utf-8",
        )
        return logs

    def test_table_reports_llm_and_tool_calls_and_the_longest(self):
        with tempfile.TemporaryDirectory() as tmp:
            markdown, rollup = node_timing_table.build_report(self._run_dir(tmp))

            author_row = next(
                line for line in markdown.splitlines() if line.startswith("| `author`")
            )
            cells = [c.strip() for c in author_row.strip("|").split("|")]
            # node, round, duration, outcome, llm, tools, longest
            self.assertEqual(cells[4], "1", f"expected 1 LLM call in {author_row!r}")
            self.assertEqual(cells[5], "1", f"expected 1 tool call in {author_row!r}")
            self.assertIn("40.0m", cells[6])
            self.assertIn("LLM", cells[6])
            self.assertEqual(rollup["nodes"], 3)

    def test_capture_failure_and_no_session_are_reported_differently(self):
        """The distinction that makes the footnote worth reading: a gate node
        with no session is normal; an agent node with a recorded session id
        and no stream is a broken capture."""
        with tempfile.TemporaryDirectory() as tmp:
            markdown, _ = node_timing_table.build_report(self._run_dir(tmp))
            self.assertIn("CAPTURE FAILURE", markdown)
            self.assertIn("`critique (round 0)`", markdown)
            self.assertIn("recorded no `session_id` at all", markdown)
            self.assertNotIn("`redgate (round 0)`", markdown)

    def test_missing_trace_is_reported_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            markdown, rollup = node_timing_table.build_report(Path(tmp))
            self.assertIn("No `trace.jsonl`", markdown)
            self.assertEqual(rollup["iterations_completed"], 0)

    def test_mean_round_cost_excludes_the_final_round(self):
        """A fuse-terminated final round is partial; averaging it in would
        UNDER-state per-round cost, which is exactly how `max_iterations`
        becomes a fiction."""
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir(parents=True)
            (logs / "trace.jsonl").write_text(
                "\n".join(
                    json.dumps(r)
                    for r in (
                        {"iteration": 0, "node_id": "a", "status": "success",
                         "duration_ms": 600_000.0},
                        {"iteration": 1, "node_id": "a", "status": "success",
                         "duration_ms": 60_000.0},
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            _, rollup = node_timing_table.build_report(logs)
            self.assertEqual(rollup["iterations_completed"], 1)
            self.assertAlmostEqual(rollup["mean_iteration_seconds"], 600.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
