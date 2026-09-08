#!/usr/bin/env python3
"""Classify pipeline terminal artifacts and render their issue comments.

The workflow shell must not infer a terminal state from a process exit code:
some terminal states are intentionally successful refusals or escalations.
This small, dependency-free program makes that decision from the evidence
files the graph wrote, and gives every state a stable process code for tests
and callers that need to distinguish them.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class Outcome:
    name: str
    exit_code: int
    finding: Path | None = None
    capsule_id: str = ""


EXIT_CODES = {
    "converged": 0,
    "green_at_base": 10,
    "refused_unspecced": 11,
    "blocked_on_criteria": 12,
    "escalated": 13,
    "non_convergence": 20,
    "fuse": 21,
}


def first_capsule_id(out_dir: Path) -> str:
    capsules = sorted(out_dir.glob("*.verify.sh"))
    return capsules[0].name.removesuffix(".verify.sh") if capsules else ""


def checkpoint_outcome(checkpoint: Path) -> str:
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "absent"
    value = (payload.get("context") or {}).get("outcome")
    return value if isinstance(value, str) else "absent"


def classify(
    *,
    stage: str,
    ai_dir: Path,
    out_dir: Path,
    log_dir: Path,
    attractor_exit: str,
) -> Outcome:
    """Return the most specific terminal state represented by run evidence."""
    findings = ai_dir / "findings"
    candidates: Sequence[tuple[str, Path]] = (
        ("refused_unspecced", ai_dir / "criteria-refusal.md"),
        ("blocked_on_criteria", findings / "blocked-on-criteria.md"),
        ("escalated", ai_dir / "escalation.md"),
        ("green_at_base", findings / "green-on-main.md"),
    )
    for name, path in candidates:
        if path.is_file():
            return Outcome(name, EXIT_CODES[name], path)

    capsule_id = first_capsule_id(out_dir)
    if stage == "implement":
        recorded = checkpoint_outcome(log_dir / "checkpoint.json")
        if attractor_exit == "0" and recorded != "fail":
            return Outcome("converged", EXIT_CODES["converged"], capsule_id=capsule_id)
    elif capsule_id:
        return Outcome("converged", EXIT_CODES["converged"], capsule_id=capsule_id)

    stdout_log = log_dir / "engine-stdout.log"
    if stdout_log.is_file() and "exceeded max duration" in stdout_log.read_text(
        encoding="utf-8", errors="replace"
    ):
        return Outcome("fuse", EXIT_CODES["fuse"])
    return Outcome("non_convergence", EXIT_CODES["non_convergence"])


def write_github_output(path: Path, outcome: Outcome) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(f"kind={outcome.name}\n")
        output.write(f"id={outcome.capsule_id}\n")
        output.write(f"classifier_exit={outcome.exit_code}\n")


def read_finding(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").rstrip()
    except OSError as error:
        raise SystemExit(f"required finding is unreadable: {path}: {error}") from error


def render_comment(outcome: str, ai_dir: Path, run_url: str) -> str:
    """Render terminal-outcome comments whose evidence is a finding file."""
    if outcome == "refused_unspecced":
        finding = read_finding(ai_dir / "criteria-refusal.md")
        heading = (
            "**Feature specify stage: refused an unspecced feature request before "
            "spending the iteration budget.**"
        )
        action = (
            "**Next action:** a repository OWNER, MEMBER, or COLLABORATOR must answer "
            "in an authenticated issue comment inside a `## Acceptance criteria "
            "(feature-capsule)` block, include `Owned-by: @<their-login>`, `Scope:`, "
            "and one or more `AC-<n>:` lines, then re-apply `ready:feature-spec`."
        )
        label = "Criteria refusal (verbatim)"
    elif outcome == "green_at_base":
        finding = read_finding(ai_dir / "findings" / "green-on-main.md")
        heading = (
            "**Pipeline stage: its best executable check is already green at the "
            "current base commit.**"
        )
        action = (
            "**Next action:** a maintainer must review the finding below and decide "
            "whether the issue is already addressed or needs a more discriminating "
            "report, then re-apply the triggering label if another run is wanted."
        )
        label = "Green-at-base finding (verbatim)"
    elif outcome == "blocked_on_criteria":
        finding = read_finding(ai_dir / "findings" / "blocked-on-criteria.md")
        questions_path = ai_dir / "questions" / "blocking.md"
        questions = (
            "\n\n## Blocking questions (verbatim)\n\n" + read_finding(questions_path)
            if questions_path.is_file()
            else ""
        )
        heading = (
            "**Feature specify stage: blocked on maintainer acceptance criteria; "
            "the run will not decide them itself.**"
        )
        action = (
            "**Next action:** the criteria owner must answer the blocking questions in "
            "an authenticated issue comment inside their `## Acceptance criteria "
            "(feature-capsule)` block, then re-apply `ready:feature-spec`."
        )
        label = "Blocked-on-criteria finding (verbatim)"
        finding += questions
    elif outcome == "escalated":
        finding = read_finding(ai_dir / "escalation.md")
        heading = (
            "**Pipeline stage: escalated a decision to a maintainer instead of "
            "misreporting it as non-convergence.**"
        )
        action = (
            "**Next action:** resolve the decision recorded below, update the "
            "authenticated issue context if needed, then re-apply the triggering label."
        )
        label = "Escalation (verbatim)"
    else:
        raise SystemExit(f"no special-outcome comment template for: {outcome}")

    return (
        f"{heading}\n\n{action}\n\n## {label}\n\n{finding}\n\nWorkflow run: {run_url}\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    classify_parser = subparsers.add_parser("classify")
    classify_parser.add_argument(
        "--stage", choices=("specify", "feature", "implement"), required=True
    )
    classify_parser.add_argument("--ai-dir", type=Path, required=True)
    classify_parser.add_argument("--out-dir", type=Path, required=True)
    classify_parser.add_argument("--log-dir", type=Path, required=True)
    classify_parser.add_argument("--attractor-exit", default="")
    classify_parser.add_argument("--github-output", type=Path)

    comment_parser = subparsers.add_parser("comment")
    comment_parser.add_argument("--outcome", required=True)
    comment_parser.add_argument("--ai-dir", type=Path, required=True)
    comment_parser.add_argument("--run-url", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "classify":
        outcome = classify(
            stage=args.stage,
            ai_dir=args.ai_dir,
            out_dir=args.out_dir,
            log_dir=args.log_dir,
            attractor_exit=args.attractor_exit,
        )
        if args.github_output:
            write_github_output(args.github_output, outcome)
        else:
            print(outcome.name)
        return outcome.exit_code

    print(render_comment(args.outcome, args.ai_dir, args.run_url), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
