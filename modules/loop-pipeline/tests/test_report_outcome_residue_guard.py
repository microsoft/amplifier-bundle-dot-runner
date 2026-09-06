"""Residue guard: `report_outcome` must never come back as LIVE behavior.

Owner ruling, 2026-09-06: the `report_outcome` ordering barrier is **residue,
not live behavior**. WAVE 5 (2026-08-30, `specs/EXTENSIONS.md` §35's `status:
REMOVED` note) deleted the tool module and the `metadata.report_outcome`
transport, but its deletion list missed three leftovers -- a batch-execution
gate and a sequential-batch barrier in `loop-agent`'s `agent_session.py` that
both keyed on a tool named `report_outcome`, and an orphan
`report_outcome_convergence.dot` fixture. Those are gone. This guard is what
keeps them gone.

**What this guard forbids, precisely: the NAME re-entering executable code.**
For every live module package (`modules/<m>/<package>/**/*.py`, tests
excluded) the source is parsed with `ast` and the token `report_outcome` is
rejected when it appears as:

  * an identifier -- a name, attribute, argument, keyword, function or class;
  * a non-docstring string literal (a dict key, a tool name, a comparison).

**What this guard deliberately permits: the historical record.** Comments are
not in the AST at all, and docstrings are recognized and skipped. That is the
point -- `backend.py`, `status_file.py`, `direct_worker.py` and their
neighbours carry WAVE 4/WAVE 5 notes explaining *why* the code has the shape
it has, and rewriting those would falsify the record rather than fix drift
(the same stance `test_stale_cli_doc_guard.py` takes on dated records).

Non-Python current-state surfaces -- `bundle.md` and everything under
`behaviors/` -- teach a reader what this bundle does *today*, so there any
occurrence at all is rejected.

Every exemption is named individually in `_ALLOWED`, with its reason. There is
no blanket escape hatch: a file is never exempted wholesale, only a specific
(path, line, snippet) pair.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TOKEN = "report_outcome"


def _find_bundle_root() -> Path:
    """Walk up to the repo root (the dir holding both `docs/` and `modules/`)."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "docs").is_dir() and (
            candidate / "modules" / "loop-pipeline"
        ).is_dir():
            return candidate
    raise AssertionError(
        "Could not locate the bundle root from "
        f"{Path(__file__).resolve()}. This guard sweeps repo-wide live code "
        "and cannot run without it; it must fail loudly rather than silently "
        "skip."
    )


BUNDLE_ROOT = _find_bundle_root()

# Individually named exemptions: (relative path, exact code snippet) -> reason.
# NOT a file-level opt-out -- only these exact snippets are permitted, and a
# second occurrence in the same file still fails.
_ALLOWED: dict[tuple[str, str], str] = {
    (
        "modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py",
        TOKEN,
    ): (
        "`_synthesize_outcome_marker`'s marker prefix. This is transcript "
        "vocabulary from EXTENSIONS.md §25/§35's body, not a dispatch on a "
        "tool: nothing branches on a tool named `report_outcome` here. It is "
        "OUT OF SCOPE for the 2026-09-06 ruling (which was about the ordering "
        "barrier) and is recorded as a named residual in that ruling's §35 "
        "addendum -- changing the marker vocabulary is a separate call."
    ),
}


def _live_package_files() -> list[Path]:
    """Every live module package's .py files. Tests are NOT live code."""
    files: list[Path] = []
    for module_dir in sorted((BUNDLE_ROOT / "modules").iterdir()):
        if not module_dir.is_dir():
            continue
        for package_dir in sorted(module_dir.iterdir()):
            if not package_dir.is_dir() or package_dir.name in {
                "tests",
                "__pycache__",
            }:
                continue
            if not (package_dir / "__init__.py").exists():
                continue
            files.extend(
                p
                for p in sorted(package_dir.rglob("*.py"))
                if "__pycache__" not in p.parts
            )
    assert files, (
        "The live-package sweep found no .py files at all -- the guard would "
        "pass vacuously. Check `modules/` layout before trusting this."
    )
    return files


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """`id()` of every string Constant that is a docstring (module/class/def)."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    ids.add(id(first.value))
    return ids


def find_live_occurrences(source: str, *, filename: str) -> list[tuple[int, str]]:
    """Return `(lineno, snippet)` for every EXECUTABLE use of the token.

    Comments never reach the AST, and docstrings are skipped explicitly, so
    only code that could actually *do* something with the name is reported.
    Exposed (not underscore-private) so the RED-proof below drives the real
    detector rather than a copy of it.
    """
    tree = ast.parse(source, filename=filename)
    doc_ids = _docstring_nodes(tree)
    hits: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in doc_ids:
                continue
            if TOKEN in node.value:
                hits.append((node.lineno, node.value.strip()))
        elif isinstance(node, ast.Name) and TOKEN in node.id:
            hits.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute) and TOKEN in node.attr:
            hits.append((node.lineno, node.attr))
        elif isinstance(node, ast.arg) and TOKEN in node.arg:
            hits.append((node.lineno, node.arg))
        elif isinstance(node, ast.keyword) and TOKEN in (node.arg or ""):
            hits.append((getattr(node, "lineno", 0), node.arg or ""))
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and TOKEN in node.name
        ):
            hits.append((node.lineno, node.name))
    return sorted(set(hits))


def test_report_outcome_is_absent_from_live_python_code():
    """No live module package may key on, emit, or name `report_outcome`."""
    violations: list[str] = []
    for path in _live_package_files():
        rel = path.relative_to(BUNDLE_ROOT).as_posix()
        for lineno, snippet in find_live_occurrences(
            path.read_text(encoding="utf-8"), filename=rel
        ):
            if _ALLOWED.get((rel, snippet)):
                continue
            violations.append(f"{rel}:{lineno}: {snippet!r}")

    assert not violations, (
        "`report_outcome` has re-entered executable code. It was ruled "
        "RESIDUE, NOT LIVE BEHAVIOR on 2026-09-06 (specs/EXTENSIONS.md §35's "
        "dated addendum); WAVE 5 removed the tool it names on 2026-08-30, so "
        "nothing can call it and any dispatch on it is dead by construction.\n"
        "Offending executable occurrences:\n  " + "\n  ".join(violations) + "\n"
        "Comments and docstrings are ALLOWED here -- move the mention into "
        "one if it is documenting history. If a live use is genuinely "
        "warranted, that is a contract amendment "
        "(contracts/engine-surface.v1.md's Reserved section), not a guard edit."
    )


def test_report_outcome_is_absent_from_current_state_bundle_surfaces():
    """`bundle.md` and `behaviors/` teach current behavior -- no mention at all."""
    targets = [BUNDLE_ROOT / "bundle.md"]
    behaviors = BUNDLE_ROOT / "behaviors"
    if behaviors.is_dir():
        targets.extend(sorted(behaviors.rglob("*.yaml")))
        targets.extend(sorted(behaviors.rglob("*.md")))

    assert targets, "No current-state bundle surfaces found -- guard is vacuous."

    violations: list[str] = []
    for path in targets:
        if not path.is_file():
            continue
        rel = path.relative_to(BUNDLE_ROOT).as_posix()
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if TOKEN in line:
                violations.append(f"{rel}:{lineno}: {line.strip()!r}")

    assert not violations, (
        "`report_outcome` appears in a current-state bundle surface. These "
        "files describe what this bundle does TODAY, and it does not do this "
        "(EXTENSIONS.md §35, REMOVED 2026-08-30; residue deleted 2026-09-06). "
        "The historical record lives in specs/EXTENSIONS.md and "
        "docs/SPEC_CONFORMANCE_HISTORY.md.\nOffending lines:\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.parametrize(
    ("label", "source"),
    [
        (
            "the deleted post-batch gate",
            'if any(tc.name == "report_outcome" and r.success for tc, r in z):\n'
            "    return last_text\n",
        ),
        (
            "the deleted sequential-batch barrier",
            'has_it = any(tc.name == "report_outcome" for tc in tool_calls)\n',
        ),
        (
            "the deleted metadata transport key",
            'metadata = {"report_outcome": verdict}\n',
        ),
        (
            "a re-added keyword parameter",
            "async def _emit_completion(self, *, report_outcome=None): ...\n",
        ),
        (
            "a re-added attribute read",
            "verdict = envelope.metadata.report_outcome\n",
        ),
    ],
)
def test_guard_actually_catches_each_deleted_shape(label: str, source: str):
    """RED-proof: every shape this PR deleted is caught by the real detector.

    Without this, a guard that silently matched nothing would pass forever.
    Each sample is a reduction of code that existed on `main` before the
    2026-09-06 ruling.
    """
    hits = find_live_occurrences(source, filename="<red-proof>")
    assert hits, f"the guard failed to catch {label}: {source!r}"


def test_guard_permits_the_historical_record():
    """Comments and docstrings keep the WAVE 4/5 record -- they must NOT fail."""
    source = (
        '"""WAVE 5 repair: the former ``metadata["report_outcome"]`` branch\n'
        'is removed -- report_outcome is gone repo-wide, no compat window."""\n'
        "\n"
        "# EXTENSIONS.md 35: the report_outcome ordering barrier that used to\n"
        "# sit here is DELETED (owner ruling 2026-09-06).\n"
        "def f():\n"
        '    """No report_outcome call is checked for anymore."""\n'
        "    return None\n"
    )
    assert find_live_occurrences(source, filename="<record-proof>") == []
