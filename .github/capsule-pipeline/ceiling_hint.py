#!/usr/bin/env python3
"""One honest sentence for the wall-clock-ceiling comment posted on an issue.

WHY THIS EXISTS (issue #64). When the engine's fuse trips mid-work, the
workflow comments on the issue. That comment used to read, in effect:

    1 iteration(s) completed against a starting budget of 6
    (max_iterations=6) when the wall-clock fuse tripped mid-work.

Every number in it is true and the sentence as a whole is misleading: it
invites the reader to conclude the run "only managed 1 of 6", when the real
finding is that 6 was never reachable -- one round of this graph costs about
185 minutes and the fuse is 330. The budget was the fiction, not the run.

So the sentence now carries the arithmetic that makes the ceiling legible:

    N of M iterations, fuse F, mean iteration cost C -- at that cost the
    fuse affords ~K complete iteration(s).

with K derived, not asserted. A reader can check it in their head, and the
next person to change `max_iterations` has the number they need in the same
sentence as the number they are changing.

Inputs are both optional and both may be absent or malformed; every missing
piece degrades to a named gap in the sentence rather than a crash or a
confident guess. This runs on the failure path of an expensive job -- it
must never be the thing that breaks it.

Usage:

    ceiling_hint.py --fuse 19800s --max-iterations 2
                    [--checkpoint PATH] [--timing PATH]

Prints one line to stdout. Always exits 0.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def parse_fuse_seconds(fuse: str) -> float | None:
    """`"19800s"` / `"19800"` -> 19800.0. Anything else -> None (named gap)."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*s?\s*", fuse or "")
    return float(match.group(1)) if match else None


def _load_json(path: str | None) -> dict:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _fmt_minutes(seconds: float) -> str:
    return f"{seconds / 60:.0f} min"


def build_hint(
    *,
    fuse: str,
    max_iterations: int,
    checkpoint: str | None = None,
    timing: str | None = None,
) -> str:
    checkpoint_data = _load_json(checkpoint)
    timing_data = _load_json(timing)

    engine_state = checkpoint_data.get("engine_state")
    completed = None
    if isinstance(engine_state, dict):
        raw = engine_state.get("iteration_count")
        if isinstance(raw, int):
            completed = raw
    if completed is None:
        raw = timing_data.get("iterations_completed")
        completed = raw if isinstance(raw, int) else None

    mean_seconds = timing_data.get("mean_iteration_seconds")
    if not isinstance(mean_seconds, (int, float)) or mean_seconds <= 0:
        mean_seconds = None
    fuse_seconds = parse_fuse_seconds(fuse)

    completed_text = (
        f"{completed} of {max_iterations} iteration(s) completed"
        if completed is not None
        else f"iteration count unreadable (budget was {max_iterations})"
    )
    parts = [completed_text, f"fuse --param max_duration=\"{fuse}\""]

    if mean_seconds is not None:
        parts.append(f"mean cost of a complete iteration {_fmt_minutes(mean_seconds)}")
        if fuse_seconds is not None:
            affordable = int(fuse_seconds // mean_seconds)
            parts.append(
                f"at that cost the fuse affords ~{affordable} complete "
                "iteration(s)"
            )
    else:
        parts.append(
            "mean iteration cost unavailable (no timing roll-up was written -- "
            "see the per-node timing table in the job summary, and the "
            "uploaded run evidence)"
        )

    return "; ".join(parts) + ". The fuse tripped mid-work."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fuse", required=True)
    parser.add_argument("--max-iterations", type=int, required=True)
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--timing", default=None)
    args = parser.parse_args(argv)
    print(
        build_hint(
            fuse=args.fuse,
            max_iterations=args.max_iterations,
            checkpoint=args.checkpoint,
            timing=args.timing,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
