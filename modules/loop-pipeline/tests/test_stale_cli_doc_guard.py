"""Doc guard: the DELETED `attractor` console script must not be taught as a live command.

The `attractor` entry point was removed entirely in 0.3.0 -- band-aid rip, no
alias, no shim, no deprecation window (README.md "Getting started"). This repo
ships exactly one CLI, `dot-runner`. An external consumer's source-verified doc
audit nonetheless found current-state docs still *printing* `attractor <sub>`
command lines, which read as live usage and send readers at a binary that does
not exist.

This guard closes that loop: in the current-state doc set below, a line may not
show `attractor <subcommand>` unless its paragraph carries an explicit
`<!-- historical-cli: ... -->` marker saying the stale name is deliberate.

Scope note -- this guard is about the *current-state* docs only. Files that
exist to be a dated record are excluded by name, with the reason, in
``_RECORD_FILES_NOT_GUARDED``: rewriting a record would be falsifying history,
not fixing drift.
"""

import re
from pathlib import Path


def _find_bundle_root() -> Path:
    """Walk up to the repo root (the dir holding both `docs/` and `modules/`)."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "docs").is_dir() and (
            candidate / "modules" / "loop-pipeline"
        ).is_dir():
            return candidate
    raise AssertionError(
        "Could not locate the bundle root from "
        f"{Path(__file__).resolve()}. This guard reads repo-root docs "
        "(README.md, MIGRATION.md, ...) and cannot run without them; it must "
        "fail loudly rather than silently skip."
    )


BUNDLE_ROOT = _find_bundle_root()

# The docs a reader treats as "how this repo works right now". Every one of
# these teaches current usage, so every command line in them must be runnable.
CURRENT_STATE_DOCS = (
    "README.md",
    "MIGRATION.md",
    "SPEC_CONFORMANCE.md",
    "docs/VISION.md",
    "docs/ISSUE_PIPELINE.md",
)

# Deliberately NOT guarded -- each is a dated record, quoted or frozen. The
# stale command names in them are the historical truth of what was written at
# the time; annotating beats rewriting.
_RECORD_FILES_NOT_GUARDED = {
    "docs/SPEC_CONFORMANCE_HISTORY.md": "ARCHIVED 2026-09-02, frozen by its own banner",
    "specs/EXTENSIONS.md": "dated decision-record entries, one per extension",
    "contracts/external/": "byte-pinned vendored upstream nlspec snapshots",
    "HISTORY-MAP.md": "pre-extraction commit-citation map",
}

# The five subcommands the removed `attractor` script exposed -- the same five
# `dot-runner` ships today (modules/pipeline-runner/.../cli.py:build_parser).
_SUBCOMMANDS = ("lint", "run", "resume", "trace", "doctor")

_STALE_CLI_RE = re.compile(r"\battractor\s+(?:" + "|".join(_SUBCOMMANDS) + r")\b")

# An explicit, greppable opt-out. Placed anywhere in a paragraph (a run of
# consecutive non-blank lines), it exempts that whole paragraph -- for prose
# whose subject IS the deleted command, e.g. README's "The `attractor` command
# is gone" notice. Invisible in rendered Markdown.
_HISTORICAL_MARKER = "<!-- historical-cli:"


def _unmarked_stale_hits(text: str) -> list[tuple[int, str]]:
    """Return (1-based line number, line) for stale hits whose paragraph is unmarked."""
    lines = text.splitlines()
    hits: list[tuple[int, str]] = []

    start = 0
    while start < len(lines):
        if not lines[start].strip():
            start += 1
            continue
        end = start
        while end < len(lines) and lines[end].strip():
            end += 1
        block = lines[start:end]
        if not any(_HISTORICAL_MARKER in line for line in block):
            for offset, line in enumerate(block):
                if _STALE_CLI_RE.search(line):
                    hits.append((start + offset + 1, line.strip()))
        start = end

    return hits


def test_current_state_docs_do_not_teach_the_deleted_attractor_command():
    """No current-state doc may print `attractor <subcommand>` as live usage.

    The `attractor` console script is gone (README "Getting started"). A doc
    line showing `attractor run ...` / `attractor lint ...` sends the reader at
    a binary that does not exist -- the exact drift an external audit caught in
    README.md, MIGRATION.md and the pre-split SPEC_CONFORMANCE.md.

    Fix a failure one of two ways:

    * It teaches a CURRENT command -> rename the token to `dot-runner`.
    * Its subject IS the deleted command (a removal notice, a dated record)
      -> add ``<!-- historical-cli: why this stale name is deliberate -->``
      on its own line inside the same paragraph.
    """
    failures: list[str] = []
    for rel in CURRENT_STATE_DOCS:
        path = BUNDLE_ROOT / rel
        assert path.is_file(), (
            f"{rel} is missing. This guard names it as a current-state doc; if "
            "it was renamed or retired, update CURRENT_STATE_DOCS in the same "
            "PR rather than letting the guard silently stop covering it."
        )
        for lineno, line in _unmarked_stale_hits(path.read_text(encoding="utf-8")):
            failures.append(f"{rel}:{lineno}: {line}")

    assert not failures, (
        "Current-state docs print the DELETED `attractor` command as if it were "
        "live usage:\n  " + "\n  ".join(failures) + "\n\n"
        "This repo ships exactly one CLI, `dot-runner` (README 'Getting "
        "started': no alias, no shim, no deprecation window). Either rename the "
        f"token to `dot-runner`, or -- if naming the dead command is the point "
        f"-- put `{_HISTORICAL_MARKER} ... -->` on its own line inside that "
        "paragraph."
    )


def test_historical_marker_exempts_only_its_own_paragraph():
    """The opt-out must not leak past a blank line (guard-the-guard).

    Without this, one `<!-- historical-cli: -->` near the top of README.md
    would silently disarm the whole file -- the failure mode that makes an
    escape hatch worse than no guard at all.
    """
    marked_then_unmarked = (
        f"{_HISTORICAL_MARKER} deliberate -->\n"
        "run it with `attractor run x.dot`\n"
        "\n"
        "and elsewhere `attractor lint x.dot`\n"
    )
    hits = _unmarked_stale_hits(marked_then_unmarked)
    assert [lineno for lineno, _ in hits] == [4], (
        "The marker must exempt only the paragraph it sits in. Got hits at "
        f"{[lineno for lineno, _ in hits]}, expected only line 4 (the "
        "paragraph after the blank line, which carries no marker)."
    )


_CLI_SOURCE_REL = "modules/pipeline-runner/amplifier_module_pipeline_runner/cli.py"
_ADD_PARSER_RE = re.compile(r"sub\.add_parser\(\s*\"([a-z][a-z0-9-]*)\"")


def test_guard_recognizes_every_shipped_subcommand():
    """The stale-name pattern must cover the CLI's whole subcommand surface.

    Anchored against the CLI's own source so a new `dot-runner` subcommand
    cannot quietly gain an unguarded `attractor <new-sub>` spelling in the
    docs. Read as text, not imported: `pipeline-runner` depends on
    `loop-pipeline`, never the reverse, so it is not importable from this
    module's test environment.
    """
    cli_source = BUNDLE_ROOT / _CLI_SOURCE_REL
    assert cli_source.is_file(), (
        f"{_CLI_SOURCE_REL} is missing -- this guard reads it to learn the "
        "shipped subcommand surface. If the CLI moved, re-anchor "
        "_CLI_SOURCE_REL in the same PR."
    )

    shipped = set(_ADD_PARSER_RE.findall(cli_source.read_text(encoding="utf-8")))
    assert shipped, (
        f'Found no `sub.add_parser("...")` calls in {_CLI_SOURCE_REL}. The '
        "parser is built some other way now; re-anchor _ADD_PARSER_RE rather "
        "than leaving this guard matching nothing."
    )
    assert shipped == set(_SUBCOMMANDS), (
        f"`dot-runner` now ships {sorted(shipped)} but this guard watches for "
        f"{sorted(_SUBCOMMANDS)}. Update _SUBCOMMANDS so a stale "
        "`attractor <new-subcommand>` line in the docs still fails."
    )
