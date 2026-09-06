#!/usr/bin/env python3
"""Per-node timing table for a finished pipeline run -- no LLM, no network.

WHY THIS EXISTS (issue #64). capsule-specify run 34039364352 spent 330
minutes and produced, per node, exactly three files: prompt.md, response.md,
status.json. The `author` node alone took 83.9 minutes in its second round.
Answering "83.9 minutes of what?" required downloading the run-evidence
artifact, unpacking it, and reading JSONL by hand -- so in practice nobody
answered it at all.

This script answers it in the job summary, from bytes the run already wrote:

  * `<logs-root>/trace.jsonl` -- one record per node visit, carrying
    `iteration`, `node_id`, `status` and `duration_ms` (the engine's own
    writer; see amplifier_module_loop_pipeline.engine).
  * `<logs-root>/**/sessions/<session_id>/events.jsonl` -- the worker's own
    persisted event stream (EXTENSIONS.md Sec 26), joined to the node by the
    `session_id` recorded in that node's status.json.

WHAT IT REPORTS, and what each column actually means:

  node / round    -- straight from trace.jsonl.
  duration        -- the node visit's own wall clock, from trace.jsonl.
  LLM calls       -- count of `provider:request` records in the joined
                     session stream. One per provider call the worker made.
  tool calls      -- count of `tool:pre` records. One per tool invocation.
  longest call    -- the single longest bracketed span in that stream:
                     request->response for an LLM call, pre->post for a tool
                     call. This is the "what was it DOING" column -- a node
                     whose longest call is 40 minutes was waiting on one
                     model call; a node whose longest call is 8 seconds was
                     doing many small things.

HONESTY RULES (the whole point of this file):

  * A node with no joinable session stream reports `-` in the three session
    columns and is counted in the footnote -- never 0, which would read as
    "made no calls" when the truth is "we did not capture them".
  * A malformed or unreadable events.jsonl line is skipped and counted, and
    the count is printed. It never silently reduces a total.
  * NOTHING from a node's `notes`, `response.md`, tool arguments or tool
    results is printed. This output lands in a public job summary; it
    carries structural data only (ids, counts, durations, statuses), so it
    is not a new exfiltration surface and does not need the scrubber.

Usage:

    node_timing_table.py --logs-root DIR [--summary-out FILE] [--json-out FILE]

`--summary-out` defaults to $GITHUB_STEP_SUMMARY when set (append mode), and
falls back to stdout. `--json-out` writes the machine-readable roll-up the
workflow's own honest-ceiling comment reads (iterations completed, mean
iteration cost, total wall clock).

Exit status is 0 unless the arguments themselves are wrong: a run with no
trace.jsonl (the pipeline died before writing one) reports that in the
summary and still exits 0 -- this is a reporting step, and it must never be
the thing that turns a run red.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

#: Bracketed spans: the START event, its matching END event, and the label
#: used when reporting the longest one. `provider:error` closes a request
#: too -- a call that failed still consumed wall clock, and dropping it
#: would under-report exactly the pathological case worth seeing.
_LLM_START = "provider:request"
_LLM_END = ("provider:response", "provider:error")
_TOOL_START = "tool:pre"
_TOOL_END = ("tool:post",)


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class SessionStats:
    """Counts and longest-span for one node's joined session stream."""

    def __init__(self) -> None:
        self.llm_calls = 0
        self.tool_calls = 0
        self.longest_seconds: float | None = None
        self.longest_label: str | None = None
        self.unparseable_lines = 0
        self.found = False

    def _close(self, open_ts: datetime | None, end_ts: datetime | None, label: str):
        if open_ts is None or end_ts is None:
            return
        span = (end_ts - open_ts).total_seconds()
        if span < 0:
            return
        if self.longest_seconds is None or span > self.longest_seconds:
            self.longest_seconds = span
            self.longest_label = label

    def ingest(self, path: Path) -> None:
        """Fold one events.jsonl into these stats."""
        self.found = True
        open_llm: datetime | None = None
        open_tool: datetime | None = None
        open_tool_name = "tool"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            self.unparseable_lines += 1
            return
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # Never silently absorbed: counted, and reported in the
                # footnote. A truncated tail (see cap_session_evidence.py)
                # can legitimately produce one of these.
                self.unparseable_lines += 1
                continue
            if not isinstance(record, dict):
                self.unparseable_lines += 1
                continue
            event = record.get("event")
            ts = _parse_ts(record.get("timestamp"))
            data = record.get("data") if isinstance(record.get("data"), dict) else {}
            if event == _LLM_START:
                self.llm_calls += 1
                open_llm = ts
            elif event in _LLM_END:
                model = data.get("model") if isinstance(data, dict) else None
                self._close(open_llm, ts, f"LLM ({model})" if model else "LLM")
                open_llm = None
            elif event == _TOOL_START:
                self.tool_calls += 1
                open_tool = ts
                name = data.get("tool_name") if isinstance(data, dict) else None
                open_tool_name = str(name) if name else "tool"
            elif event in _TOOL_END:
                self._close(open_tool, ts, f"tool ({open_tool_name})")
                open_tool = None


def _stage_dirs(logs_root: Path, node_id: str, iteration: int) -> list[Path]:
    """Every directory this node's visit may have written under.

    The engine writes a node's stage dir at `<logs>/<node_id>`, and for
    looping graphs also mirrors per-iteration copies under
    `<logs>/iteration_<n>/<node_id>` (both shapes are present in run
    34039364352's own evidence). Both are checked; neither is assumed.
    """
    candidates = [logs_root / f"iteration_{iteration}" / node_id, logs_root / node_id]
    return [p for p in candidates if p.is_dir()]


def _recorded_session_id(logs_root: Path, node_id: str, iteration: int) -> str | None:
    """The `session_id` this node's own status.json recorded, if any.

    This is what separates the two very different reasons a node has no
    session stream: a tool/gate node never spawns a worker and legitimately
    records none (expected, uninteresting), while an agent node that DID
    record one and has no events.jsonl beside it is capture failure
    (interesting, and the thing worth naming). Collapsing both into one
    "not captured" list is what made the first draft of this table
    unreadable -- 27 names, 12 of them meaningless.
    """
    for stage_dir in _stage_dirs(logs_root, node_id, iteration):
        status_path = stage_dir / "status.json"
        if not status_path.is_file():
            continue
        try:
            data = json.loads(status_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("session_id"):
            return str(data["session_id"])
    return None


def _stats_for(logs_root: Path, node_id: str, iteration: int) -> SessionStats:
    stats = SessionStats()
    for stage_dir in _stage_dirs(logs_root, node_id, iteration):
        sessions = stage_dir / "sessions"
        if not sessions.is_dir():
            continue
        for events in sorted(sessions.glob("*/events.jsonl")):
            stats.ingest(events)
    return stats


def _fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 90:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}m"


def _read_trace(path: Path) -> tuple[list[dict], int]:
    rows: list[dict] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(record, dict) and "node_id" in record:
            rows.append(record)
        else:
            skipped += 1
    return rows, skipped


def build_report(logs_root: Path) -> tuple[str, dict]:
    """Return (markdown, rollup). Never raises on a malformed/absent run."""
    trace_path = logs_root / "trace.jsonl"
    if not trace_path.is_file():
        return (
            "## Per-node timing\n\n"
            f"No `trace.jsonl` under `{logs_root}` -- the pipeline did not get "
            "far enough to write one. Nothing to report; this is not itself a "
            "failure.\n",
            {"iterations_completed": 0, "mean_iteration_seconds": None,
             "total_seconds": 0.0, "nodes": 0},
        )

    rows, skipped_trace = _read_trace(trace_path)
    lines = [
        "## Per-node timing",
        "",
        "| node | round | duration | outcome | LLM calls | tool calls | longest single call |",
        "| --- | ---: | ---: | --- | ---: | ---: | --- |",
    ]

    per_iteration: dict[int, float] = {}
    total_seconds = 0.0
    capture_failures: list[str] = []
    no_session_recorded = 0
    unparseable_total = 0

    for record in rows:
        node_id = str(record.get("node_id", "?"))
        iteration = record.get("iteration")
        iteration_i = iteration if isinstance(iteration, int) else 0
        duration_ms = record.get("duration_ms")
        seconds = float(duration_ms) / 1000.0 if isinstance(duration_ms, (int, float)) else 0.0
        status = str(record.get("status", "?"))

        total_seconds += seconds
        per_iteration[iteration_i] = per_iteration.get(iteration_i, 0.0) + seconds

        stats = _stats_for(logs_root, node_id, iteration_i)
        unparseable_total += stats.unparseable_lines
        if stats.found:
            llm = str(stats.llm_calls)
            tools = str(stats.tool_calls)
            longest = (
                f"{_fmt_duration(stats.longest_seconds)} {stats.longest_label}"
                if stats.longest_seconds is not None
                else "-"
            )
        else:
            # NOT zero. "We did not capture it" and "it made no calls" are
            # different facts and must not share a rendering.
            llm = tools = longest = "-"
            if _recorded_session_id(logs_root, node_id, iteration_i):
                capture_failures.append(f"{node_id} (round {iteration_i})")
            else:
                no_session_recorded += 1

        lines.append(
            f"| `{node_id}` | {iteration_i} | {_fmt_duration(seconds)} | {status} "
            f"| {llm} | {tools} | {longest} |"
        )

    # WHICH ROUNDS COUNT TOWARD THE MEAN. The final round is where the run
    # stopped -- by the fuse, by convergence, or by a failure -- so on a
    # fuse-terminated run it is a partial round and averaging it in would
    # UNDER-state the true per-round cost, which is the exact direction that
    # produces a fictional `max_iterations`. So when there is more than one
    # round, the last is excluded from the mean (its cost is still listed).
    # With a single round there is nothing else to average, and it is
    # reported as-is with that stated plainly.
    rounds = sorted(per_iteration)
    measured_rounds = rounds[:-1] if len(rounds) > 1 else rounds
    complete_iterations = len(measured_rounds)
    mean_iteration = (
        sum(per_iteration[i] for i in measured_rounds) / complete_iterations
        if complete_iterations
        else None
    )

    lines += [
        "",
        f"**Total wall clock across {len(rows)} node visit(s): "
        f"{_fmt_duration(total_seconds)}.**",
        "",
    ]
    for iteration in rounds:
        marker = "  _(the run ended in this round)_" if iteration == rounds[-1] else ""
        lines.append(
            f"- round {iteration}: {_fmt_duration(per_iteration[iteration])}{marker}"
        )
    if mean_iteration is not None:
        basis = (
            f"over {complete_iterations} round(s), excluding the final round "
            "where the run ended"
            if len(rounds) > 1
            else "this run's only round, which may itself be partial"
        )
        lines += [
            "",
            f"Mean cost of one round: {_fmt_duration(mean_iteration)} ({basis}). "
            "This is the number `max_iterations` has to be honest against.",
        ]
    if capture_failures or no_session_recorded:
        note = [
            "",
            "> `-` in the last three columns means **not captured**, never "
            '"made no calls".',
        ]
        if capture_failures:
            shown = capture_failures[:8]
            more = len(capture_failures) - len(shown)
            note.append(
                "> **CAPTURE FAILURE** -- these node visits recorded a "
                "`session_id` but no `events.jsonl` was found beside it: "
                + ", ".join(f"`{n}`" for n in shown)
                + (f", and {more} more" if more else "")
                + ". That is a broken capture, not an idle worker."
            )
        if no_session_recorded:
            note.append(
                f"> {no_session_recorded} node visit(s) recorded no `session_id` "
                "at all -- expected for tool/gate nodes, which spawn no worker."
            )
        lines += note
    if unparseable_total or skipped_trace:
        lines += [
            "",
            f"> Skipped {unparseable_total} unreadable session-event line(s) and "
            f"{skipped_trace} unreadable trace line(s). Counts above exclude them.",
        ]

    rollup = {
        "iterations_completed": complete_iterations,
        "mean_iteration_seconds": mean_iteration,
        "total_seconds": total_seconds,
        "nodes": len(rows),
    }
    return "\n".join(lines) + "\n", rollup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-root", required=True)
    parser.add_argument("--summary-out", default=None)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    markdown, rollup = build_report(Path(args.logs_root))

    destination = args.summary_out or os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    else:
        sys.stdout.write(markdown)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rollup, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
