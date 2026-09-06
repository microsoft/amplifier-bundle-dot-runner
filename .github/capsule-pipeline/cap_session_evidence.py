#!/usr/bin/env python3
"""Bound the size of worker-session evidence, and SAY what was dropped.

WHY THIS EXISTS. Once EXTENSIONS.md Sec 26's session-event persister is
actually mounted (issue #64), every worker node writes
`<stage>/sessions/<session_id>/events.jsonl` -- and those files carry a
`tool:post` record per tool call, each containing that tool's full serialized
output. On a multi-hour node that is unbounded: a single `bash` call that
cats a large file, or a search across a big tree, lands in the stream
verbatim. Uploaded as a run-evidence artifact, that is both slow and
pointless: nobody reads 400 MB of tool output.

WHAT IT DOES. For each `events.jsonl` over `--max-bytes`, keep the HEAD and
the TAIL (both whole lines) and replace the middle with ONE marker record:

    {"event": "evidence:truncated", "timestamp": ..., "data": {
       "dropped_lines": N, "dropped_bytes": B, "original_bytes": T,
       "kept_head_lines": H, "kept_tail_lines": L,
       "reason": "cap_session_evidence.py --max-bytes ..."}}

Head AND tail, not just head: the beginning of a stream is the session start
and the first calls (what the node set out to do), and the end is where it
actually stopped (what it was doing when the fuse tripped) -- and on a
runaway node the end is the only part that explains anything.

The marker is a real JSONL record with the same envelope shape as its
neighbours, so a reader that walks the file gets a first-class "the record
you are looking for was deliberately removed here" instead of an unexplained
gap. Silence about a drop is the failure mode this file exists to avoid.

ORDERING (load-bearing). This runs AFTER the secret scrub and BEFORE the
residual-secret gate. After the scrub, so the bytes it keeps are already
redacted and truncation can never strip the marker off a half-redacted span.
Before the gate, so the gate scans EXACTLY the bytes that get uploaded --
never a superset it approved and never a subset it did not see. It only ever
REMOVES whole lines and appends a structural marker; it cannot introduce
secret-shaped material that the scrub had not already seen.

Usage:

    cap_session_evidence.py ROOT [ROOT ...] [--max-bytes N]
                            [--keep-head-lines N] [--keep-tail-lines N]
                            [--summary-out FILE]

Exit status is 0 unless the arguments are wrong. A root that does not exist
is reported and skipped: this is an evidence-shaping step and must never be
the thing that turns a run red.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

#: 4 MiB per session stream. Sized to comfortably hold a long agent node's
#: own event envelope (a few thousand records of session/provider/tool
#: brackets) while cutting off the pathological case: one tool call that
#: returned tens of megabytes. Overridable via --max-bytes.
DEFAULT_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_KEEP_HEAD_LINES = 400
DEFAULT_KEEP_TAIL_LINES = 400

TRUNCATION_EVENT = "evidence:truncated"


def _marker_line(
    *,
    dropped_lines: int,
    dropped_bytes: int,
    original_bytes: int,
    kept_head: int,
    kept_tail: int,
    max_bytes: int,
) -> str:
    return json.dumps(
        {
            "event": TRUNCATION_EVENT,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "dropped_lines": dropped_lines,
                "dropped_bytes": dropped_bytes,
                "original_bytes": original_bytes,
                "kept_head_lines": kept_head,
                "kept_tail_lines": kept_tail,
                "reason": (
                    "cap_session_evidence.py --max-bytes "
                    f"{max_bytes}: the middle of this stream was removed to "
                    "bound the uploaded run-evidence artifact. The head "
                    "(what the node set out to do) and the tail (where it "
                    "actually stopped) are intact."
                ),
            },
        },
        ensure_ascii=False,
    )


def cap_file(
    path: Path,
    *,
    max_bytes: int,
    keep_head_lines: int,
    keep_tail_lines: int,
) -> dict | None:
    """Cap one events.jsonl. Returns a report dict, or None if untouched."""
    try:
        original_bytes = path.stat().st_size
    except OSError:
        return None
    if original_bytes <= max_bytes:
        return None

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:  # unreadable -> reported, never silently skipped
        return {
            "path": str(path),
            "error": f"{type(exc).__name__}",
            "original_bytes": original_bytes,
        }

    if len(lines) <= keep_head_lines + keep_tail_lines:
        # Too few lines to cap by line count -- a handful of enormous
        # records. Keep the head and the tail we can, still marked.
        keep_head_lines = max(1, len(lines) // 2)
        keep_tail_lines = max(1, len(lines) - keep_head_lines - 1)

    head = lines[:keep_head_lines]
    tail = lines[-keep_tail_lines:] if keep_tail_lines else []
    dropped = lines[keep_head_lines : len(lines) - keep_tail_lines]
    dropped_bytes = sum(len(line.encode("utf-8")) + 1 for line in dropped)

    marker = _marker_line(
        dropped_lines=len(dropped),
        dropped_bytes=dropped_bytes,
        original_bytes=original_bytes,
        kept_head=len(head),
        kept_tail=len(tail),
        max_bytes=max_bytes,
    )
    path.write_text("\n".join([*head, marker, *tail]) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "original_bytes": original_bytes,
        "new_bytes": path.stat().st_size,
        "dropped_lines": len(dropped),
        "dropped_bytes": dropped_bytes,
    }


def cap_roots(
    roots: list[Path],
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    keep_head_lines: int = DEFAULT_KEEP_HEAD_LINES,
    keep_tail_lines: int = DEFAULT_KEEP_TAIL_LINES,
) -> tuple[list[dict], list[str]]:
    """Cap every events.jsonl under *roots*. Returns (reports, warnings)."""
    reports: list[dict] = []
    warnings: list[str] = []
    for root in roots:
        if not root.exists():
            warnings.append(f"root does not exist, skipped: {root}")
            continue
        for path in sorted(root.rglob("events.jsonl")):
            report = cap_file(
                path,
                max_bytes=max_bytes,
                keep_head_lines=keep_head_lines,
                keep_tail_lines=keep_tail_lines,
            )
            if report is not None:
                reports.append(report)
    return reports, warnings


def render(reports: list[dict], warnings: list[str], max_bytes: int) -> str:
    if not reports and not warnings:
        return (
            "## Session-evidence size cap\n\n"
            f"No worker-session stream exceeded {max_bytes} bytes. Nothing "
            "was dropped; the uploaded evidence is complete.\n"
        )
    lines = ["## Session-evidence size cap", ""]
    if reports:
        lines += [
            f"{len(reports)} worker-session stream(s) exceeded {max_bytes} "
            "bytes and had their MIDDLE removed (head and tail kept, one "
            "`evidence:truncated` marker record left in place):",
            "",
            "| stream | original | kept | lines dropped | bytes dropped |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for report in reports:
            if "error" in report:
                lines.append(
                    f"| `{report['path']}` | {report['original_bytes']} | "
                    f"UNREADABLE ({report['error']}) | - | - |"
                )
                continue
            lines.append(
                f"| `{report['path']}` | {report['original_bytes']} | "
                f"{report['new_bytes']} | {report['dropped_lines']} | "
                f"{report['dropped_bytes']} |"
            )
    for warning in warnings:
        lines += ["", f"> {warning}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--keep-head-lines", type=int, default=DEFAULT_KEEP_HEAD_LINES)
    parser.add_argument("--keep-tail-lines", type=int, default=DEFAULT_KEEP_TAIL_LINES)
    parser.add_argument("--summary-out", default=None)
    args = parser.parse_args(argv)

    reports, warnings = cap_roots(
        [Path(r) for r in args.roots],
        max_bytes=args.max_bytes,
        keep_head_lines=args.keep_head_lines,
        keep_tail_lines=args.keep_tail_lines,
    )
    markdown = render(reports, warnings, args.max_bytes)

    destination = args.summary_out or os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    # Always echo to the log too -- the job summary is easy to miss, and
    # "what was dropped" must not be discoverable in only one place.
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
