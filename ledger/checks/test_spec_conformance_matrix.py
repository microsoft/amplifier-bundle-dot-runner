"""The conformance ledger checks -- Layer 2 of the drift defense.

Guards the incident class: **the ledger quietly becoming a lie.**

`docs/SPEC_CONFORMANCE_HISTORY.md` and `specs/EXTENSIONS.md` are human-maintained
decision records of every decided divergence from the vendored upstream nlspec
(the EXTERNAL contract at ``contracts/external/``).  They have had no executable
teeth: nothing failed when the engine moved away from what they say, and -- the
half people forget -- nothing failed when the engine moved *back toward the
contract* at a point where the ledger says we deliberately do not conform.  Both
directions leave `main` carrying a record that does not describe the shipped
engine.  That is drift.

This module makes the ledger load-bearing.  It reads ``ledger/rows.yaml`` -- a
reviewed document in the Converge ledger format (`docs/LEDGER-FORMAT.md`), a
top-level LIST of rows, one per normative statement cluster -- and for every row:

1. **Structural integrity** (parametrized per row): the row's VERBATIM contract
   quote is still present in the byte-pinned contract file; the decision record
   it cites still exists; every existing test it indexes still exists.
2. **Behavioral probes** (named ``test_row_<id>`` functions): in-process engine
   runs against backend doubles.  Deterministic, no LLM, no network, no
   subprocess.  A DIVERGED row proves BOTH that our ledgered behavior occurs AND
   that the contract's behavior does not -- the second half is what makes a
   silent re-alignment loud.
3. **The SYNC row** (`ATX-M-000`, first in the list, carrying ``sync:`` inline):
   the contract file's sha256 matches the recorded pin, so a re-vendor becomes a
   demanded full-ledger re-review instead of a quiet commit.
4. **The coverage tripwire**: every DIVERGES-bannered ``EXTENSIONS.md`` section
   and every DIVERGE-disposition ``ATX-*`` row in the retired-but-frozen
   `docs/SPEC_CONFORMANCE_HISTORY.md` must be cited by at least one ledger row.
   A decided divergence cannot be recorded without also being asserted.

Every failure -- behavioral or structural -- renders through one flip-message
helper, so ``grep "SPEC-CONFORMANCE MATRIX FLIP"`` over a CI log finds every
conformance event of any kind.  The message names the spec section, the ledger
entry, and the two legal exits.  There is no third exit: editing an assertion to
match new behavior without moving the ledger is the failure this file exists to
prevent.

Honest limits (inherited from the Layer 1 guard precedents):

- Quote verification proves the spec TEXT is still there, not that our reading
  of it is right.  A row is an argument; the quote is its citation.
- An AST-verified indexed cite proves the named test EXISTS, not that it still
  asserts what the row claims.  The paired suite's own green carries that.  A
  renamed test fails the cite check loudly -- that is designed, not incidental.
- Absence assertions are grep-shaped.  They prove the identifier does not appear
  in the named source roots, which is the same bar `ATX-3` itself uses
  ("grep `tool_hooks`=0").
- The ledger file is NOT optional.  Unlike the doc guards' skip-if-absent
  discipline for optional docs, a missing or unparseable ledger is a hard
  failure here: a silently-skipped ledger is a ledger that is not load-bearing.

Retirement condition: none for the mechanism.  Individual ROWS retire when
upstream absorbs the divergence (the EXTENSIONS "ABSORBED UPSTREAM" banner
protocol) or when a decision closes an OPEN-PINNED row -- in both cases the row
changes disposition rather than disappearing.
"""

from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.edge_selection import select_edge
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.pipeline_events import PIPELINE_ERROR
from amplifier_module_loop_pipeline.validation import SHAPE_TO_HANDLER, validate_or_raise

# ledger/checks/<this file> -> ledger/checks -> ledger -> repo root
BUNDLE_ROOT = Path(__file__).parent.parent.parent
LEDGER_PATH = BUNDLE_ROOT / "ledger" / "rows.yaml"
EXTENSIONS_LEDGER = BUNDLE_ROOT / "specs" / "EXTENSIONS.md"
#: The retired human ledger, frozen: still the dated decision record every
#: ATX-* / ULM-* / CAL-* id lives in, and still cited by rows via decision.history.
HISTORY_LEDGER = BUNDLE_ROOT / "docs" / "SPEC_CONFORMANCE_HISTORY.md"

#: PROTOCOL.md section 3.3's vocabulary, plus LEDGER-FORMAT.md section 3's
#: `DIVERGED` -- legal here ONLY because this contract is externally governed.
VALID_DISPOSITIONS = frozenset(
    {
        "CONFORMS",
        "DIVERGED",
        "GAP",
        "VIOLATION",
        "OPEN-PINNED",
        "NOT-ASSERTABLE",
        "EXCLUDED",
    }
)
VALID_ASSERTION_KINDS = frozenset({"probe", "indexed", "absence", "none"})

#: Dispositions whose rows MUST cite a decision record (or, for a row this
#: ledger itself surfaced, the issue that carries the pending decision).
DECISION_REQUIRED = frozenset({"DIVERGED", "OPEN-PINNED", "EXCLUDED"})
#: Dispositions whose rows MUST carry a written justification.
JUSTIFICATION_REQUIRED = frozenset({"OPEN-PINNED", "NOT-ASSERTABLE"})
#: Dispositions whose rows MUST carry a tracker ref -- a red row with no filed
#: item is a ledger that lies (LEDGER-FORMAT.md section 2).
WORK_REQUIRED = frozenset({"GAP", "VIOLATION"})


# ---------------------------------------------------------------------------
# Loading -- fail loud, never skip
# ---------------------------------------------------------------------------


def _load_ledger() -> list[dict[str, Any]]:
    """Load the ledger. A missing or malformed ledger is a HARD failure.

    The guard-test precedents skip when an OPTIONAL doc is absent.  The ledger
    is not optional: silently skipping it would leave every decided divergence
    unasserted while CI stayed green, which is the exact condition this module
    exists to make impossible.

    Shape is enforced here, not merely assumed: LEDGER-FORMAT.md section 2 pins
    the top level as a LIST of rows with no wrapper mapping, because the first
    live implementation of that format wrapped its rows in ``{meta, rows}`` and
    the ambiguity was real.
    """
    if not LEDGER_PATH.exists():
        raise AssertionError(
            "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY\n"
            f"  The ledger file is missing: {LEDGER_PATH}\n"
            "  This file is NOT optional. It is the executable form of\n"
            "  docs/SPEC_CONFORMANCE_HISTORY.md and specs/EXTENSIONS.md; skipping\n"
            "  it would leave every ledgered divergence unasserted while CI stayed\n"
            "  green. Restore it, or -- if the ledger is being deliberately\n"
            "  retired -- remove these checks and docs/QUALITY_PROTOCOL.md's\n"
            "  Layer 2 claim in the same PR.\n"
            "  Doing neither means main carries a ledger that lies. That is drift."
        )
    try:
        doc = yaml.safe_load(LEDGER_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - exercised by the RED self-test
        raise AssertionError(
            "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY\n"
            f"  {LEDGER_PATH} does not parse as YAML: {exc}\n"
            "  Fix the file. An unparseable ledger asserts nothing."
        ) from exc
    if not isinstance(doc, list) or not doc:
        raise AssertionError(
            "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY\n"
            f"  {LEDGER_PATH} must parse as a NON-EMPTY top-level YAML LIST of\n"
            f"  rows (LEDGER-FORMAT.md section 2), got {type(doc).__name__}.\n"
            "  No wrapper mapping, no 'meta:' key, nothing above the list."
        )
    return doc


ROWS: list[dict[str, Any]] = _load_ledger()
ROWS_BY_ID: dict[str, dict[str, Any]] = {r["id"]: r for r in ROWS}
ROW_IDS: list[str] = [r["id"] for r in ROWS]

#: LEDGER-FORMAT.md section 4: the SYNC row is a row IN the list, by convention
#: the first, and it carries the contract file's path + content hash inline.
SYNC_ROW: dict[str, Any] = ROWS[0]
CONTRACT_PATH = BUNDLE_ROOT / SYNC_ROW["sync"]["file"]
CONTRACT_TEXT = CONTRACT_PATH.read_text(encoding="utf-8")

#: This ledger carries rows for MORE THAN ONE contract: the external nlspec
#: above, and `contracts/engine-surface.v1.md` -- the contract this repo OWNS.
#: Each contract is pinned by its own SYNC row, so "the synced contract" is a
#: set, not a singleton. Every checker below resolves a row against the
#: contract THAT ROW cites; a row citing a contract nothing pins is caught by
#: test_tripwire_every_row_cites_a_synced_contract.
SYNC_ROWS: list[dict[str, Any]] = [r for r in ROWS if "sync" in r]
SYNC_ROW_BY_CONTRACT: dict[str, dict[str, Any]] = {
    r["sync"]["file"]: r for r in SYNC_ROWS
}
CONTRACT_TEXTS: dict[str, str] = {
    f: (BUNDLE_ROOT / f).read_text(encoding="utf-8")
    for f in SYNC_ROW_BY_CONTRACT
    if (BUNDLE_ROOT / f).exists()
}

#: A contract is EXTERNALLY governed when its bytes are vendored from
#: elsewhere; only those owe a `sync.upstream` provenance string. An owned
#: contract's provenance is this repo's own history, and inventing an
#: `upstream:` for it would be decoration.
EXTERNAL_CONTRACT_PREFIX = "contracts/external/"


# ---------------------------------------------------------------------------
# The flip message -- generated, never hand-written per row
# ---------------------------------------------------------------------------


def _decision_cites(row: dict[str, Any]) -> list[str]:
    decision = row.get("decision") or {}
    cites: list[str] = []
    if decision.get("history"):
        cites.append(
            f"docs/SPEC_CONFORMANCE_HISTORY.md {decision['history']}"
        )
    if decision.get("extensions") is not None:
        cites.append(f"specs/EXTENSIONS.md section {decision['extensions']}")
    if decision.get("issue") is not None:
        cites.append(f"issue #{decision['issue']} (decision pending -- no record yet)")
    return cites or ["(none -- CONFORMS rows need no decision licence)"]


def _indent(text: str, pad: str = "               ") -> str:
    lines = [ln.rstrip() for ln in (text or "").strip().splitlines()]
    if not lines:
        return ""
    return ("\n" + pad).join(lines)


def flip(
    row: dict[str, Any],
    observed: str,
    expected: str,
    direction: str = "REGRESSION",
) -> str:
    """Render the failure contract for a row.

    Contract (design section 8), in order: banner, spec anchor with the verbatim
    quote, disposition + every ledger cite, direction, observed/expected, the two
    legal exits with the complete same-PR checklist, and the closing invariant.
    """
    disposition = row["disposition"]
    spec = row["contract"]
    cites = "; ".join(_decision_cites(row))

    direction_gloss = {
        "REGRESSION": (
            "the engine moved OFF the behavior the ledger records as decided."
        ),
        "UN-DIVERGENCE": (
            "the engine now matches the spec text the ledger says we deliberately\n"
            "               diverge from. Un-diverging silently is drift too."
        ),
        "UNDECIDED-MOVEMENT": (
            "an OPEN-PINNED behavior moved BEFORE its disposition was decided."
        ),
        "LEDGER-INTEGRITY": (
            "the ledger row itself no longer resolves against the contract or the\n"
            "               decision record."
        ),
    }.get(direction, "unclassified movement.")

    if disposition == "OPEN-PINNED":
        exit_two = (
            "    2. Keep the change AND make it the DECISION: move the open record\n"
            f"       ({cites}) to a decided disposition with a dated changelog line,\n"
            "       add or update the specs/EXTENSIONS.md entry if the decision is\n"
            "       DIVERGED, and update this ledger row (disposition + assertion).\n"
            "       Undecided is not the same as unpinned -- do not let an accident\n"
            "       become the decision by default."
        )
    elif disposition in {"DIVERGED", "EXCLUDED"}:
        exit_two = (
            "    2. Keep the change AND move the record with it, in THIS PR:\n"
            f"       update {cites}, rewrite the specs/EXTENSIONS.md entry body so it\n"
            "       describes the new behavior, update this ledger row (disposition +\n"
            "       assertion), and fix any paired doc guards named in the row's notes\n"
            "       below."
        )
    else:
        exit_two = (
            "    2. Keep the change AND record it as a deliberate divergence in THIS\n"
            "       PR: add a specs/EXTENSIONS.md entry per the Compatibility doctrine\n"
            "       in docs/VISION.md (name the safety property, cite the evidence,\n"
            "       fail loud), then move this ledger row to DIVERGED citing it.\n"
            "       A conformance that stops conforming without a decision record is\n"
            "       the unledgered-divergence class, which is how ATX-11 lived for\n"
            "       months."
        )

    parts = [
        f'SPEC-CONFORMANCE LEDGER FLIP -- row {row["id"]} "{row["title"]}"',
        f'  contract:    {spec["file"]}',
        f'  clause:      {spec["clause"]} {spec.get("heading", "")} --',
        f"               {_indent(spec['quote'])}",
        f"  disposition: {disposition}  (decided: {cites})",
        f"  direction:   {direction} -- {direction_gloss}",
        f"  observed:    {observed}",
        f"  expected:    {expected}",
    ]

    conflict = spec.get("conflict")
    if conflict:
        parts.append(
            f'  contract conflict: clause {conflict["clause"]} says --\n'
            f"               {_indent(conflict['quote'])}\n"
            f"               {_indent(conflict.get('note', ''))}"
        )

    if row.get("justification"):
        parts.append(f"  why pinned:  {_indent(row['justification'])}")
    if row.get("notes"):
        parts.append(f"  row note:    {_indent(row['notes'])}")

    parts.append("")
    parts.append("  Two legal exits -- in THIS PR, not a follow-up:")
    parts.append(
        "    1. Revert the behavior change. The ledger is the record of decided\n"
        "       behavior; the engine does not get to overrule it silently."
    )
    parts.append(exit_two)
    parts.append(
        "  There is no third exit. Editing this assertion to match the new behavior,\n"
        "  without moving the record, is the failure this ledger exists to prevent."
    )
    parts.append("  Doing neither means main carries a ledger that lies. That is drift.")
    return "\n".join(parts)


def integrity_flip(row: dict[str, Any], observed: str, expected: str) -> str:
    return flip(row, observed, expected, direction="LEDGER-INTEGRITY")


# ---------------------------------------------------------------------------
# Shared checker helpers -- also exercised directly by the RED/GREEN self-tests
# ---------------------------------------------------------------------------


def _normalize_block(text: str) -> str:
    """Collapse runs of intra-line whitespace; keep line structure.

    Line structure is preserved because the load-bearing quotes are pseudocode
    blocks whose shape carries meaning.  Intra-line runs are collapsed so a
    reflowed table cell or a tab/space swap does not produce a false flip.
    """
    return "\n".join(
        re.sub(r"[ \t]+", " ", line).strip() for line in text.strip("\n").splitlines()
    )


_CONTRACT_NORMALIZED = _normalize_block(CONTRACT_TEXT)

#: One normalized haystack per pinned contract. The default argument of
#: ``quote_is_present`` stays the external nlspec so every existing caller --
#: the row probes and the RED/GREEN self-tests below -- keeps its meaning.
_NORMALIZED_BY_CONTRACT: dict[str, str] = {
    f: _normalize_block(t) for f, t in CONTRACT_TEXTS.items()
}


def normalized_contract(contract_file: str) -> str:
    """The normalized bytes of one pinned contract, by its repo-relative path."""
    try:
        return _NORMALIZED_BY_CONTRACT[contract_file]
    except KeyError:  # pragma: no cover - exercised by the RED self-test
        raise AssertionError(
            "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY\n"
            f"  no SYNC row pins {contract_file!r}, so its quotes are unpinned text:\n"
            "  the contract could move without any row noticing. Add a SYNC row for\n"
            "  that contract (LEDGER-FORMAT.md section 4) before rowing against it."
        ) from None


def quote_is_present(quote: str, haystack_normalized: str = _CONTRACT_NORMALIZED) -> bool:
    return _normalize_block(quote) in haystack_normalized


def history_ledger_ids(text: str) -> set[str]:
    """Every ``ATX-*`` / ``ULM-*`` / ``CAL-*`` / ``SYNC-*`` row id in the record."""
    return {
        m.group(1)
        for m in re.finditer(r"^\|\s*((?:ATX|ULM|CAL|SYNC|DEAD)-\d+)\s*\|", text, re.M)
    }


def extensions_section_numbers(text: str) -> set[int]:
    """Every ``## N.`` heading number in specs/EXTENSIONS.md."""
    return {int(m.group(1)) for m in re.finditer(r"^## (\d+)\.\s", text, re.M)}


def module_symbols(path: Path) -> set[str]:
    """Top-level functions/classes and ``Class::method`` names, by AST parse.

    NEVER imports.  Indexed cites cross per-module venv boundaries (the
    pipeline-runner resume e2e test is not importable from this module's venv),
    so the only sound verification is a parse.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(f"{node.name}::{child.name}")
    return names


#: Where the ledger's probe functions live. More than one module now: this file
#: carries the external nlspec's probes, `test_engine_surface_matrix.py` carries
#: `contracts/engine-surface.v1.md`'s.
CHECKS_DIR = Path(__file__).resolve().parent


def probe_definitions() -> dict[str, str]:
    """Every ``test_row_*`` function defined anywhere under ``ledger/checks/``.

    Returns ``{function_name: repo-relative path}``. Discovered by AST parse for
    the same reason indexed cites are: a parse cannot be defeated by an import
    error in an unrelated module, and it does not care which venv is active.
    """
    found: dict[str, str] = {}
    for path in sorted(CHECKS_DIR.glob("test_*.py")):
        for name in module_symbols(path):
            if name.startswith("test_row_") and "::" not in name:
                found[name] = str(path.relative_to(BUNDLE_ROOT))
    return found


def resolve_indexed_cite(cite: str) -> tuple[bool, str]:
    """Return (ok, reason). A cite is ``path`` or ``path::symbol``."""
    file_part, _, symbol = cite.partition("::")
    path = BUNDLE_ROOT / file_part
    if not path.exists():
        return False, f"file does not exist: {file_part}"
    if not symbol:
        return True, ""
    if symbol not in module_symbols(path):
        return False, f"symbol '{symbol}' not found in {file_part}"
    return True, ""


_PY_SUFFIXES = {".py"}


def grep_source_roots(pattern: str, roots: list[str]) -> list[str]:
    """Return ``path:line`` hits for a regex over the .py files under roots."""
    rx = re.compile(pattern)
    hits: list[str] = []
    for root in roots:
        base = BUNDLE_ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in _PY_SUFFIXES or not path.is_file():
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if rx.search(line):
                    hits.append(f"{path.relative_to(BUNDLE_ROOT)}:{lineno}")
    return hits


#: A banner "states a divergence" only when it does so in a BOLD declarative --
#: the Entry Format's own shape ("**This extension DIVERGES from canonical spec
#: section X.**", "**REFILED ... ALSO records a DIVERGENCE**").  Matching bare
#: prose would sweep in every entry whose boilerplate merely uses the word.
_BOLD_DIVERGENCE = re.compile(r"\*\*[^*]*DIVERG(?:ES|ENCE)\b[^*]*\*\*", re.I)


def diverges_bannered_extension_sections(text: str) -> set[int]:
    lines = text.splitlines()
    heads = [
        (i, int(m.group(1)))
        for i, line in enumerate(lines)
        if (m := re.match(r"^## (\d+)\.\s", line))
    ]
    flagged: set[int] = set()
    for idx, (start, number) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        banner: list[str] = []
        for line in lines[start + 1 : end]:
            stripped = line.strip()
            if stripped.startswith(">"):
                banner.append(stripped.lstrip("> ").rstrip())
            elif stripped == "":
                continue
            else:
                break
        if _BOLD_DIVERGENCE.search(" ".join(banner)):
            flagged.add(number)
    return flagged


def diverge_disposition_atx_rows(text: str) -> set[str]:
    """``ATX-*`` ledger rows whose Disposition cell starts with DIVERGE."""
    found: set[str] = set()
    for line in text.splitlines():
        if not re.match(r"^\|\s*ATX-\d+\s*\|", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[-1].lstrip("* ").upper().startswith("DIVERGE"):
            found.add(cells[0])
    return found


# ---------------------------------------------------------------------------
# 1. Structural integrity -- parametrized per row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("row_id", ROW_IDS)
def test_row_schema_is_wellformed(row_id: str):
    """Vocabulary, required fields, and the ledger/justification obligations."""
    row = ROWS_BY_ID[row_id]

    assert row["disposition"] in VALID_DISPOSITIONS, (
        f"row {row_id}: unknown disposition {row['disposition']!r}. "
        f"Legal values: {sorted(VALID_DISPOSITIONS)}"
    )
    kind = row["assertion"]["kind"]
    assert kind in VALID_ASSERTION_KINDS, (
        f"row {row_id}: unknown assertion kind {kind!r}. "
        f"Legal values: {sorted(VALID_ASSERTION_KINDS)}"
    )
    assert row.get("title"), f"row {row_id}: title is required (it names the flip banner)"

    # LEDGER-FORMAT.md section 2: the quote is THE binding anchor and must live
    # nested under `contract:` -- a quote at row top level is malformed and
    # dodges verification entirely (a live finding, not a hypothetical).
    assert "quote" not in row, (
        f"row {row_id}: `quote` must be nested under `contract:`, never at row "
        "top level. A top-level quote is never verified against contract bytes, "
        "so the row's citation is decorative."
    )
    contract = row["contract"]
    assert contract.get("file"), f"row {row_id}: contract.file is required"
    assert contract.get("clause"), f"row {row_id}: contract.clause is required"
    assert contract.get("quote", "").strip(), f"row {row_id}: contract.quote is required"
    assert (BUNDLE_ROOT / contract["file"]).exists(), (
        f"row {row_id}: contract.file {contract['file']!r} does not exist"
    )

    if kind == "none":
        assert row["disposition"] == "NOT-ASSERTABLE", (
            f"row {row_id}: assertion.kind 'none' is legal ONLY for NOT-ASSERTABLE. "
            "Every other disposition is a claim about behavior, and a claim about "
            "behavior that asserts nothing is decoration."
        )

    if row["disposition"] in DECISION_REQUIRED:
        decision = row.get("decision") or {}
        assert any(decision.get(k) is not None for k in ("history", "extensions", "issue")), (
            f"row {row_id}: disposition {row['disposition']} requires a decision "
            "record cite (docs/SPEC_CONFORMANCE_HISTORY.md row, specs/EXTENSIONS.md "
            "section, or -- for a finding this ledger surfaced -- the issue carrying "
            "the pending decision). LEDGER-FORMAT.md section 3: a DIVERGED row "
            "without a decision record asserts a decision nobody made."
        )

    if row["disposition"] in WORK_REQUIRED:
        assert (row.get("work") or "").strip(), (
            f"row {row_id}: disposition {row['disposition']} requires a `work` "
            "tracker ref. A red row with no filed item is a ledger that lies."
        )

    if row["disposition"] in JUSTIFICATION_REQUIRED:
        assert (row.get("justification") or "").strip(), (
            f"row {row_id}: disposition {row['disposition']} requires a written "
            "justification. Pinning an undecided behavior without saying why is how a "
            "pin gets mistaken for a decision."
        )


@pytest.mark.parametrize("row_id", ROW_IDS)
def test_row_quote_is_verbatim_in_contract(row_id: str):
    """The row's contract quote(s) must still exist in the byte-pinned contract file."""
    row = ROWS_BY_ID[row_id]
    quotes: list[tuple[str, str]] = [("contract.quote", row["contract"]["quote"])]
    for extra in row["contract"].get("also") or []:
        quotes.append(("contract.also", extra["quote"]))
    conflict = row["contract"].get("conflict")
    if conflict:
        quotes.append(("contract.conflict", conflict["quote"]))

    haystack = normalized_contract(row["contract"]["file"])
    for field, quote in quotes:
        assert quote_is_present(quote, haystack), integrity_flip(
            row,
            observed=(
                f"{field} is no longer present in {row['contract']['file']}: "
                f"{_normalize_block(quote)[:120]!r}"
            ),
            expected=(
                "every ledger quote resolves verbatim against the contract bytes "
                "(whitespace-normalized within lines)"
            ),
        )


@pytest.mark.parametrize("row_id", ROW_IDS)
def test_row_decision_cites_exist(row_id: str):
    """A row may not cite a decision record that has been deleted or renumbered."""
    row = ROWS_BY_ID[row_id]
    decision = row.get("decision") or {}

    if decision.get("history"):
        known = history_ledger_ids(HISTORY_LEDGER.read_text(encoding="utf-8"))
        assert decision["history"] in known, integrity_flip(
            row,
            observed=(
                f"docs/SPEC_CONFORMANCE_HISTORY.md has no row "
                f"{decision['history']!r}"
            ),
            expected=(
                "the cited record still exists. That file is FROZEN and "
                "append-only-in-practice: ids do not vanish. If a row here cites "
                "an id that is gone, the absorption dropped a decision."
            ),
        )

    if decision.get("extensions") is not None:
        known_sections = extensions_section_numbers(
            EXTENSIONS_LEDGER.read_text(encoding="utf-8")
        )
        assert decision["extensions"] in known_sections, integrity_flip(
            row,
            observed=(
                f"specs/EXTENSIONS.md has no "
                f"'## {decision['extensions']}.' heading"
            ),
            expected=(
                "the cited EXTENSIONS section still exists. Renumbering or deleting an "
                "entry breaks every consumer that cites it -- which is exactly what "
                "test_extensions_ledger_integrity.py was written for after a "
                "'-Xtheirs' rebase silently discarded three merged entries."
            ),
        )

    if decision.get("issue") is not None:
        assert isinstance(decision["issue"], int) and decision["issue"] > 0, (
            f"row {row_id}: decision.issue must be a positive GitHub issue number"
        )
        assert row["disposition"] == "OPEN-PINNED", (
            f"row {row_id}: decision.issue is legal only on an OPEN-PINNED row. An "
            "issue records a PENDING decision; a decided disposition owes a real "
            "decision record."
        )


@pytest.mark.parametrize("row_id", ROW_IDS)
def test_row_indexed_cites_exist(row_id: str):
    """Every indexed test must still exist -- verified by AST parse, never import."""
    row = ROWS_BY_ID[row_id]
    for cite in row["assertion"].get("indexed") or []:
        ok, reason = resolve_indexed_cite(cite)
        assert ok, integrity_flip(
            row,
            observed=f"indexed cite does not resolve -- {reason} (cite: {cite})",
            expected=(
                "every indexed test still exists. The ledger INDEXES existing coverage "
                "rather than duplicating it, so a renamed or deleted test silently "
                "un-covers this contract clause unless this check catches it."
            ),
        )


@pytest.mark.parametrize("row_id", ROW_IDS)
def test_row_probe_function_exists(row_id: str):
    """A ``kind: probe`` row must name a probe function that actually exists here."""
    row = ROWS_BY_ID[row_id]
    if row["assertion"]["kind"] != "probe":
        pytest.skip("row is not probe-kind")
    probe = row["assertion"].get("ref")
    assert probe, f"row {row_id}: assertion.kind is 'probe' but no assertion.ref is named"
    here = callable(getattr(sys.modules[__name__], probe, None))
    elsewhere = probe_definitions().get(probe)
    assert here or elsewhere, integrity_flip(
        row,
        observed=(
            f"no probe function named {probe!r} in this module or anywhere under "
            f"{CHECKS_DIR.relative_to(BUNDLE_ROOT)}/"
        ),
        expected="every probe-kind row names a probe function that exists and runs",
    )


def test_row_ids_are_unique():
    seen: set[str] = set()
    for row in ROWS:
        assert row["id"] not in seen, (
            f"duplicate matrix row id {row['id']!r}. Ids are stable forever: never "
            "renumbered, never reused."
        )
        seen.add(row["id"])


def test_every_probe_function_is_claimed_by_a_row():
    """The other direction of the cross-check: no orphan probes.

    A probe with no row asserts behavior nobody wrote down a disposition for --
    which is a test, but not a conformance claim, and it will not appear in the
    matrix a reviewer reads.

    Scope widened with the second contract: probes for `engine-surface.v1` live
    in their own sibling module, so this now sweeps EVERY ``test_row_*`` under
    ``ledger/checks/`` rather than only this file's namespace. Nothing that was
    caught before stops being caught -- this module's own probes are still read
    from its live namespace, which also proves they import.
    """
    declared = {
        row["assertion"].get("ref")
        for row in ROWS
        if row["assertion"]["kind"] == "probe"
    }
    module = sys.modules[__name__]
    defined = {
        name
        for name in dir(module)
        if name.startswith("test_row_") and callable(getattr(module, name))
    } | set(probe_definitions())
    # Structural tests are parametrized over row ids and are not row probes.
    structural = {
        "test_row_schema_is_wellformed",
        "test_row_quote_is_verbatim_in_contract",
        "test_row_decision_cites_exist",
        "test_row_indexed_cites_exist",
        "test_row_probe_function_exists",
        "test_row_ids_are_unique",
        "test_row_absence_assertion_holds",
    }
    orphans = sorted(defined - declared - structural)
    assert not orphans, (
        "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY\n"
        f"  probe functions with no ledger row: {orphans}\n"
        "  Every probe must be claimed by a row, so the ledger a human reads is the\n"
        "  complete list of what is asserted. Add the row, or rename the function."
    )


# ---------------------------------------------------------------------------
# 2. The coverage tripwire -- a new ledgered divergence cannot go unasserted
# ---------------------------------------------------------------------------


def _cited_extension_sections() -> set[int]:
    return {
        row["decision"]["extensions"]
        for row in ROWS
        if (row.get("decision") or {}).get("extensions") is not None
    }


def _cited_history_rows() -> set[str]:
    return {
        row["decision"]["history"]
        for row in ROWS
        if (row.get("decision") or {}).get("history")
    }


def test_tripwire_every_diverges_bannered_extension_is_asserted():
    """A DIVERGES-bannered EXTENSIONS entry must be cited by at least one row."""
    bannered = diverges_bannered_extension_sections(
        EXTENSIONS_LEDGER.read_text(encoding="utf-8")
    )
    missing = sorted(bannered - _cited_extension_sections())
    assert not missing, (
        "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY (coverage tripwire)\n"
        f"  specs/EXTENSIONS.md sections {missing} carry a DIVERGES banner but no\n"
        "  ledger row cites them.\n"
        "  A divergence that is recorded but not asserted is a promise with no\n"
        "  enforcement: the engine can drift off it, or silently back onto the\n"
        "  contract, and CI stays green. Add a row to ledger/rows.yaml asserting\n"
        "  BOTH halves -- that our documented behavior occurs, and that the\n"
        "  contract's behavior does not.\n"
        "  Doing neither means main carries a ledger that lies. That is drift."
    )


def test_tripwire_every_diverge_atx_row_is_asserted():
    """A DIVERGE-disposition ``ATX-*`` record must be cited by a ledger row.

    Re-aimed (not weakened) at ``docs/SPEC_CONFORMANCE_HISTORY.md`` when
    ``SPEC_CONFORMANCE.md`` was retired.  That file is frozen, which makes this a
    permanent ABSORPTION check: every divergence the human ledger had decided is
    still carried by a row here.  A dropped decision turns this red.
    """
    history_text = HISTORY_LEDGER.read_text(encoding="utf-8")
    diverging = diverge_disposition_atx_rows(history_text)
    missing = sorted(diverging - _cited_history_rows())
    assert not missing, (
        "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY (coverage tripwire)\n"
        f"  docs/SPEC_CONFORMANCE_HISTORY.md rows {missing} carry a DIVERGE\n"
        "  disposition but no ledger/rows.yaml row cites them.\n"
        "  Recording a divergence in prose and leaving it unasserted is how ATX-11\n"
        "  lived for months: correct, load-bearing, and invisible to CI. Add a row to\n"
        "  ledger/rows.yaml in the same PR that decides it.\n"
        "  Doing neither means main carries a ledger that lies. That is drift."
    )


def test_tripwire_disposition_agrees_with_its_decision_record():
    """A row's disposition may not contradict the record it cites.

    This is the bidirectional-drift rule applied to the LEDGER ITSELF rather
    than to the engine.  Flipping a row from DIVERGED to CONFORMS while its
    decision record still records a decided divergence does not change one byte
    of behavior -- it just makes the ledger claim we conform where the record
    says we deliberately do not.  That is the "ledger quietly becoming a lie"
    incident class, arriving through the ledger's own front door.

    Both directions are checked: a row citing a DIVERGE-disposition record must
    be DIVERGED, and a row citing a record with any other disposition must not
    be.
    """
    history_text = HISTORY_LEDGER.read_text(encoding="utf-8")
    diverging = diverge_disposition_atx_rows(history_text)
    all_ids = history_ledger_ids(history_text)
    # Only ATX-<n> ids participate: that is the population
    # diverge_disposition_atx_rows() reads, so it is the only population whose
    # ABSENCE from that set is meaningful.
    atx_ids = {i for i in all_ids if i.startswith("ATX-")}

    wrong: list[str] = []
    for r in ROWS:
        cite = (r.get("decision") or {}).get("history")
        if not cite or cite not in atx_ids:
            continue
        if cite in diverging and r["disposition"] != "DIVERGED":
            wrong.append(
                f"    {r['id']}: disposition {r['disposition']} but {cite} is a "
                "decided DIVERGE"
            )
        if cite not in diverging and r["disposition"] == "DIVERGED":
            wrong.append(
                f"    {r['id']}: disposition DIVERGED but {cite} records no "
                "divergence"
            )

    assert not wrong, (
        "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY (coverage tripwire)\n"
        "  a ledger row's disposition contradicts the decision record it cites:\n"
        + "\n".join(wrong)
        + "\n"
        "  Two legal exits -- in THIS PR, not a follow-up:\n"
        "    1. Restore the row's disposition. The decision record is the record\n"
        "       of decided behavior; a row does not get to overrule it silently.\n"
        "    2. Keep the row AND move the record with it: docs/SPEC_CONFORMANCE_HISTORY.md\n"
        "       is FROZEN, so a genuine re-decision is a new specs/EXTENSIONS.md\n"
        "       entry (or an amendment) that the row cites instead.\n"
        "  There is no third exit. Editing the disposition to match a new opinion,\n"
        "  without moving the record, is the failure this ledger exists to prevent.\n"
        "  Doing neither means main carries a ledger that lies. That is drift."
    )


def test_tripwire_sync_row_is_first_and_pins_the_contract():
    """LEDGER-FORMAT.md section 4: a SYNC row opens the list and pins path + hash.

    Generalized to N contracts when this ledger grew rows for the repo's own
    ``contracts/engine-surface.v1.md`` alongside the vendored nlspec. The
    per-contract invariants are unchanged -- row 0 is still a SYNC row, a SYNC
    row id still ends in ``-000``, a contract is still pinned at most once --
    and one is ADDED: the recorded hash must actually match the file's bytes.
    That is what turns "never a silent hash bump" from a convention in the
    format doc into something a run can fail on.
    """
    assert SYNC_ROW["id"].endswith("-000"), (
        "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY\n"
        f"  the first ledger row is {SYNC_ROW['id']!r}, not a '<PREFIX>-000' SYNC row.\n"
        "  LEDGER-FORMAT.md section 4 pins the SYNC row as a row IN the list, by\n"
        "  convention the first. Run metadata never sits above the list."
    )
    assert SYNC_ROWS, (
        "no row in this ledger carries a `sync:` block, so every quote below is "
        "unpinned text: the contract could move and no row would notice."
    )

    seen: dict[str, str] = {}
    for sync_row in SYNC_ROWS:
        rid = sync_row["id"]
        assert rid.endswith("-000"), (
            f"SYNC row {rid}: a SYNC row id ends in '-000' (LEDGER-FORMAT.md "
            "section 4). One SYNC row per contract, each opening its own section."
        )
        sync = sync_row.get("sync") or {}
        for field in ("file", "sha256"):
            assert sync.get(field), (
                f"SYNC row {rid}: sync.{field} is required -- the row pins the\n"
                "contract file's path and content hash inline."
            )
        path = BUNDLE_ROOT / sync["file"]
        assert path.exists(), (
            f"SYNC row {rid}: sync.file {sync['file']!r} does not exist"
        )
        # `upstream:` is provenance for VENDORED bytes, and every external
        # contract owes one -- it is the only record of where those bytes came
        # from and what a re-sync would re-sync against. An OWNED contract has
        # no upstream; fabricating one would be decoration.
        if sync["file"].startswith(EXTERNAL_CONTRACT_PREFIX):
            assert sync.get("upstream"), (
                f"SYNC row {rid}: sync.upstream is required for a contract under "
                f"{EXTERNAL_CONTRACT_PREFIX} -- without it nothing records where "
                "those vendored bytes came from."
            )
        assert sync["file"] not in seen, (
            f"SYNC rows {seen.get(sync['file'])} and {rid} both pin "
            f"{sync['file']!r}. Exactly one SYNC row pins a contract's bytes."
        )
        seen[sync["file"]] = rid

        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == sync["sha256"], integrity_flip(
            sync_row,
            observed=(
                f"{sync['file']} hashes to {actual}, but its SYNC row records "
                f"{sync['sha256']}"
            ),
            expected=(
                "the pinned hash matches the contract's bytes. LEDGER-FORMAT.md "
                "section 4: a mismatch is a MANDATORY full-ledger re-review of every "
                "row citing this contract -- never a silent hash bump. Re-read those "
                "rows against the new text FIRST, then move the hash in the same "
                "change."
            ),
        )


def test_tripwire_every_row_cites_a_synced_contract():
    """Every row's contract.file must be a file some SYNC row actually pins.

    A row quoting a contract whose bytes nothing pins is a row whose citation
    cannot be trusted: the text could move without any SYNC row noticing.
    """
    pinned = set(SYNC_ROW_BY_CONTRACT)
    strays = sorted({r["id"] for r in ROWS if r["contract"]["file"] not in pinned})
    assert not strays, (
        "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY (coverage tripwire)\n"
        f"  rows {strays} cite a contract file no SYNC row pins\n"
        f"  (pinned: {sorted(pinned)}).\n"
        "  Add a SYNC row for that contract before adding rows against it, or the\n"
        "  quotes below it are unpinned text."
    )


# ---------------------------------------------------------------------------
# 3. RED/GREEN self-tests for the checker logic itself
#
# The checkers are the load-bearing part: a checker that cannot fail is a
# checker that proves nothing. These exercise each one against synthetic input
# that SHOULD be rejected, so a future refactor that neuters a check is caught
# by this file rather than by the next incident.
# ---------------------------------------------------------------------------


def test_selfcheck_quote_verification_rejects_text_not_in_the_spec():
    assert quote_is_present("The graph is the workflow: nodes are tasks")
    assert not quote_is_present(
        "The engine SHALL politely ask the model whether it feels converged."
    )


def test_selfcheck_quote_verification_is_whitespace_tolerant_not_content_tolerant():
    assert quote_is_present("The    graph  is the workflow:   nodes are tasks")
    assert not quote_is_present("The graph is the workflow; nodes are chores")


def test_selfcheck_ledger_shape_is_a_bare_top_level_list():
    """LEDGER-FORMAT.md section 2, asserted rather than assumed."""
    import yaml as _yaml

    raw = _yaml.safe_load(LEDGER_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, list), (
        f"ledger/rows.yaml parsed as {type(raw).__name__}, not a list. No wrapper "
        "mapping, no 'meta:' key, nothing above the list."
    )
    assert all(isinstance(r, dict) and "id" in r for r in raw)
    assert raw[0]["id"] == "ATX-M-000"


def test_selfcheck_indexed_cite_resolution_rejects_dangling_cites():
    ok, _ = resolve_indexed_cite(
        "modules/loop-pipeline/tests/test_engine.py::test_no_matching_edge_returns_fail"
    )
    assert ok
    ok, reason = resolve_indexed_cite(
        "modules/loop-pipeline/tests/test_engine.py::test_this_name_does_not_exist"
    )
    assert not ok and "not found" in reason
    ok, reason = resolve_indexed_cite("modules/loop-pipeline/tests/test_no_such_file.py")
    assert not ok and "does not exist" in reason


def test_selfcheck_ledger_id_extraction_finds_real_rows_and_not_invented_ones():
    ids = history_ledger_ids(HISTORY_LEDGER.read_text(encoding="utf-8"))
    assert {"ATX-11", "ATX-12", "ATX-5", "ATX-6"} <= ids
    assert "ATX-9999" not in ids


def test_selfcheck_extensions_heading_extraction_is_contiguous_and_real():
    numbers = extensions_section_numbers(EXTENSIONS_LEDGER.read_text(encoding="utf-8"))
    assert {24, 25, 33, 36} <= numbers
    assert max(numbers) not in {0}
    assert 9999 not in numbers


def test_selfcheck_diverges_banner_detection_is_precise():
    """The banner detector must read the BOLD declarative, not stray prose."""
    flagged = diverges_bannered_extension_sections(
        EXTENSIONS_LEDGER.read_text(encoding="utf-8")
    )
    # Real DIVERGES banners in the ledger today.
    assert {24, 25, 33} <= flagged
    synthetic_positive = (
        "## 99. Synthetic\n\n"
        "> **This extension DIVERGES from canonical spec section 1.2.**\n>\n"
        "> **depends-on:** none\n\ntext\n"
    )
    synthetic_negative = (
        "## 98. Synthetic\n\n"
        "> **This extension is NOT in the canonical attractor spec.**\n>\n"
        "> **upstream action:** declining, reason: the divergence is tracked here.\n\n"
    )
    assert diverges_bannered_extension_sections(synthetic_positive) == {99}
    assert diverges_bannered_extension_sections(synthetic_negative) == set()


def test_selfcheck_diverge_atx_row_detection_reads_the_disposition_cell():
    rows = diverge_disposition_atx_rows(HISTORY_LEDGER.read_text(encoding="utf-8"))
    assert {"ATX-4", "ATX-5", "ATX-11", "ATX-12"} <= rows
    assert "ATX-1" not in rows  # disposition ALIGN
    assert "ATX-2" not in rows  # disposition ALIGN


def test_selfcheck_flip_message_carries_the_full_contract():
    """The flip contract is the actual product. Assert its shape, not its prose."""
    row = ROWS_BY_ID["ATX-M-011"]
    message = flip(
        row,
        observed="dead end resolved to the spec's SUCCESS 'Pipeline completed'",
        expected="the spec-literal dead-end-to-SUCCESS behavior stays absent",
        direction="UN-DIVERGENCE",
    )
    assert message.startswith("SPEC-CONFORMANCE LEDGER FLIP -- row ATX-M-011")
    assert "contracts/external/attractor-spec-canonical.md" in message
    assert "clause:      3.2" in message
    assert 'RETURN Outcome(status=SUCCESS, notes="Pipeline completed")' in message
    assert "docs/SPEC_CONFORMANCE_HISTORY.md ATX-11" in message
    assert "specs/EXTENSIONS.md section 33" in message
    assert "UN-DIVERGENCE" in message
    assert "observed:" in message and "expected:" in message
    assert "Two legal exits" in message
    assert "1. Revert the behavior change" in message
    assert "2. Keep the change AND move the record with it" in message
    assert message.rstrip().endswith("That is drift.")


def test_selfcheck_open_pinned_flip_offers_the_decision_exit_not_the_ledger_exit():
    """An OPEN-PINNED row must not tell a developer to update a decision nobody made."""
    message = flip(
        ROWS_BY_ID["ATX-M-006o"],
        observed="a FAIL outcome was retried",
        expected="a FAIL outcome executes exactly once",
        direction="UNDECIDED-MOVEMENT",
    )
    assert "make it the DECISION" in message
    assert "Undecided is not the same as unpinned" in message
    assert "docs/SPEC_CONFORMANCE_HISTORY.md ATX-6" in message
    # the contract's self-contradiction is surfaced
    assert "clause 11.5" in message


# ---------------------------------------------------------------------------
# 4. Probe harness -- deterministic, in-process, no LLM, no network
# ---------------------------------------------------------------------------


class EventCollector:
    """Captures emitted pipeline events (the canonical hooks seam)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append((event_name, data))

    def get(self, event_name: str) -> list[dict[str, Any]]:
        return [data for name, data in self.events if name == event_name]


class ScriptedBackend:
    """Returns a scripted result per node id; records the call order."""

    def __init__(self, results: dict[str, Any] | None = None, default: Any = "ok") -> None:
        self._results = results or {}
        self._default = default
        self.calls: list[str] = []

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls.append(node.id)
        result = self._results.get(node.id, self._default)
        if callable(result):
            return result(len([c for c in self.calls if c == node.id]) - 1, context)
        return result


def build_engine(
    dot_source: str,
    backend: Any,
    tmp_path: Path,
    hooks: Any | None = None,
    validate: bool = True,
) -> PipelineEngine:
    graph = parse_dot(dot_source)
    if validate:
        validate_or_raise(graph)
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=str(tmp_path),
        hooks=hooks,
    )


def row(row_id: str) -> dict[str, Any]:
    return ROWS_BY_ID[row_id]


# ---------------------------------------------------------------------------
# 5. Absence assertions -- "we do not implement section X" as executable claim
# ---------------------------------------------------------------------------


_ABSENCE_ROW_IDS = [r["id"] for r in ROWS if r["assertion"]["kind"] == "absence"]


@pytest.mark.parametrize("row_id", _ABSENCE_ROW_IDS)
def test_row_absence_assertion_holds(row_id: str):
    """The named feature must remain absent from the named source roots."""
    r = row(row_id)
    spec = r["assertion"]["absence"]
    hits = grep_source_roots(spec["pattern"], spec["roots"])
    assert not hits, flip(
        r,
        observed=(
            f"pattern {spec['pattern']!r} now appears in the source roots "
            f"{spec['roots']}: {hits[:6]}"
        ),
        expected=(
            "the feature stays absent while its ledger disposition says it is "
            "not implemented"
        ),
        direction=(
            "UN-DIVERGENCE"
            if r["disposition"] in {"DIVERGED", "EXCLUDED"}
            else "UNDECIDED-MOVEMENT"
        ),
    )


# ---------------------------------------------------------------------------
# 6. Behavioral probes
# ---------------------------------------------------------------------------


def test_row_atx_m_000():
    """SYNC: the contract file's sha256 still matches the recorded pin."""
    r = row("ATX-M-000")
    sync = r["sync"]
    actual = hashlib.sha256((BUNDLE_ROOT / sync["file"]).read_bytes()).hexdigest()
    pinned = sync["sha256"]
    assert actual == pinned, (
        f"SPEC-CONFORMANCE LEDGER FLIP -- row {r['id']} \"{r['title']}\"\n"
        f"  contract:    {sync['file']}\n"
        f"  pinned to:   {sync['upstream']}  sha256={pinned}\n"
        f"  actual:      sha256={actual}\n"
        "  direction:   LEDGER-INTEGRITY -- the vendored contract has been re-synced.\n"
        "\n"
        "  This is NOT a 'fix the hash' failure. Every row in ledger/rows.yaml quotes\n"
        "  THIS file, so a re-sync is a MANDATORY FULL-LEDGER RE-REVIEW EVENT\n"
        "  (LEDGER-FORMAT.md section 4 -- never a silent hash bump):\n"
        "    1. Re-verify every row against the new upstream text -- the quote checks\n"
        "       will already have failed on exactly the rows whose normative text moved,\n"
        "       which is your targeted diff of what upstream touched.\n"
        "    2. Update the dispositions and decision records the new text ABSORBS or\n"
        "       INVALIDATES (see the specs/EXTENSIONS.md 'ABSORBED UPSTREAM' banner\n"
        "       protocol -- the fb57a55 sync retconned sections 1-7 that way, and found\n"
        "       section 18's k_of_n/quorum had been removed upstream entirely).\n"
        "    3. THEN update `sha256:` and `upstream:` in the SYNC row, in the same PR.\n"
        "  Quote verification proves the quoted text still exists; only the re-review\n"
        "  confirms each row's READING of it is still correct.\n"
        "  Doing neither means main carries a ledger that lies. That is drift."
    )


class _SuccessNoMatchBackend:
    """Explicit SUCCESS whose preferred_label matches no edge -> a dead end."""

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return Outcome(
            status=StageStatus.SUCCESS, preferred_label="nomatch", is_explicit=True
        )


@pytest.mark.asyncio
async def test_row_atx_m_011(tmp_path):
    """ATX-11 / EXTENSIONS section 33: a dead end ALWAYS hard-fails."""
    r = row("ATX-M-011")
    dot = """
    digraph M011 {
        start [shape=Mdiamond]
        exit  [shape=Msquare]
        work  [prompt="do work"]
        sink  [prompt="unreached"]
        start -> work
        work -> sink [condition="outcome=fail"]
        sink -> exit
    }
    """
    hooks = EventCollector()
    engine = build_engine(dot, _SuccessNoMatchBackend(), tmp_path, hooks=hooks)
    outcome = await engine.run()

    # (b) FIRST: the SPEC behavior must NOT occur. Checked before our own half
    # because the likeliest way this row flips is a well-meaning "align to
    # spec section 3.2" change, and a developer who just made that change is
    # owed the message that names it as an UN-DIVERGENCE -- not a generic
    # regression report.
    assert outcome.status != StageStatus.SUCCESS and "Pipeline completed" not in (
        outcome.notes or ""
    ), flip(
        r,
        observed="a dead end resolved to the spec's SUCCESS 'Pipeline completed'",
        expected="the spec-literal dead-end-to-SUCCESS behavior stays absent",
        direction="UN-DIVERGENCE",
    )

    # (a) our ledgered behavior: hard-fail, traceable, loud.
    assert outcome.status == StageStatus.FAIL, flip(
        r,
        observed=f"pipeline outcome {outcome.status} on a dead end",
        expected="a dead end ALWAYS terminates the pipeline with status=FAIL",
    )
    assert outcome.failure_reason, flip(
        r,
        observed="empty failure_reason on the dead-end termination",
        expected="terminate_pipeline() carries a traceable failure_reason",
    )
    assert any(
        e.get("error_type") == "no_matching_edge" for e in hooks.get(PIPELINE_ERROR)
    ), flip(
        r,
        observed="no PIPELINE_ERROR event with error_type=no_matching_edge",
        expected="the hard-fail emits PIPELINE_ERROR error_type=no_matching_edge",
    )


@pytest.mark.asyncio
async def test_row_atx_m_012(tmp_path):
    """ATX-12 / EXTENSIONS section 24: loop_restart resets in process."""
    r = row("ATX-M-012")
    seen_iterations: list[str] = []

    async def _run(node, prompt, context, incoming_edge=None, graph=None):
        iteration = str(context.get("iteration", ""))
        seen_iterations.append(iteration)
        if len(seen_iterations) == 1:
            return Outcome(
                status=StageStatus.SUCCESS,
                preferred_label="again",
                is_explicit=True,
                context_updates={"finding": "attempt-0-found-X"},
            )
        return Outcome(
            status=StageStatus.SUCCESS, preferred_label="done", is_explicit=True
        )

    backend = type("_LoopBackend", (), {"run": staticmethod(_run)})()
    dot = """
    digraph M012 {
        start [shape=Mdiamond]
        exit  [shape=Msquare]
        work  [prompt="iteration $iteration"]
        start -> work
        work -> work [label="again", loop_restart="true"]
        work -> exit [label="done"]
    }
    """
    logs_root = tmp_path / "run"
    logs_root.mkdir()
    engine = build_engine(dot, backend, logs_root)
    outcome = await engine.run()

    # (a) our ledgered behavior: in-process reset.
    assert len(seen_iterations) == 2, flip(
        r,
        observed=f"work executed {len(seen_iterations)} time(s)",
        expected="loop_restart clears completed nodes so the node executes again",
    )
    assert seen_iterations[1] != seen_iterations[0], flip(
        r,
        observed=f"$iteration did not advance across the restart: {seen_iterations}",
        expected="$iteration/$loop_count increment across a loop_restart",
    )
    assert engine.context.get("finding") == "attempt-0-found-X", flip(
        r,
        observed="context_updates from the pre-restart iteration were discarded",
        expected="context_updates SURVIVE a loop_restart (what convergence rides on)",
    )
    iteration_dirs = sorted(p.name for p in logs_root.glob("iteration_*") if p.is_dir())
    assert iteration_dirs, flip(
        r,
        observed=f"no iteration_N/ sub-tree under the run root {logs_root}",
        expected="the run directory is RETAINED and gains an iteration_N/ sub-tree",
    )

    # (b) the SPEC behavior must NOT occur: no fresh log-directory root, and
    # run() returned a normal final outcome instead of restart_run(); RETURN.
    siblings = [p for p in tmp_path.iterdir() if p.is_dir() and p != logs_root]
    assert not siblings, flip(
        r,
        observed=f"a fresh sibling run-root appeared beside the retained one: {siblings}",
        expected="the spec-literal 'fresh log directory' relaunch stays absent",
        direction="UN-DIVERGENCE",
    )
    assert outcome is not None and outcome.status in {
        StageStatus.SUCCESS,
        StageStatus.PARTIAL_SUCCESS,
    }, flip(
        r,
        observed=f"run() returned {outcome!r} after a loop_restart",
        expected=(
            "run() completes in process and returns a final outcome -- the spec's "
            "restart_run(...); RETURN stays absent"
        ),
        direction="UN-DIVERGENCE",
    )


@pytest.mark.asyncio
async def test_row_atx_m_025a(tmp_path):
    """EXTENSIONS section 25: plain prose cannot exit a goal gate -- but still
    wraps to SUCCESS everywhere else (the control that keeps this additive)."""
    r = row("ATX-M-025a")
    dot_gate = """
    digraph M025a {
        start [shape=Mdiamond]
        exit  [shape=Msquare]
        judge [prompt="judge the work", goal_gate=true]
        start -> judge
        judge -> exit
    }
    """
    engine = build_engine(dot_gate, ScriptedBackend(default="Looks good to me!"), tmp_path)
    outcome = await engine.run()

    # (b) the SPEC behavior must NOT occur on a goal gate.
    assert outcome.status != StageStatus.SUCCESS, flip(
        r,
        observed=(
            "a plain-prose response on a goal_gate=true node exited the pipeline "
            "SUCCESS -- the spec's unconditional string-to-SUCCESS wrap"
        ),
        expected=(
            "prose is not evidence of convergence: a goal gate fails closed on an "
            "unasserted verdict"
        ),
        direction="UN-DIVERGENCE",
    )

    # (a) the control: the spec's wrap is PRESERVED off the gate, which is what
    # keeps this divergence non-interfering for community graphs.
    dot_plain = """
    digraph M025aControl {
        start [shape=Mdiamond]
        exit  [shape=Msquare]
        work  [prompt="do work"]
        start -> work
        work -> exit
    }
    """
    control = build_engine(
        dot_plain, ScriptedBackend(default="Looks good to me!"), tmp_path / "control"
    )
    control_outcome = await control.run()
    assert control_outcome.status == StageStatus.SUCCESS, flip(
        r,
        observed=(
            f"a non-gate node's prose response produced {control_outcome.status} "
            "instead of the spec's SUCCESS wrap"
        ),
        expected=(
            "the section-25 divergence is SCOPED to goal_gate=true nodes; everywhere "
            "else the canonical prose-to-SUCCESS wrap is preserved (doctrine rule 3)"
        ),
    )


@pytest.mark.asyncio
async def test_row_atx_m_025b(tmp_path):
    """EXTENSIONS section 25: a status-only SUCCESS does not satisfy a gate."""
    r = row("ATX-M-025b")
    dot = """
    digraph M025b {
        start [shape=Mdiamond]
        exit  [shape=Msquare]
        judge [prompt="judge the work", goal_gate=true]
        start -> judge
        judge -> exit
    }
    """
    implicit = Outcome(status=StageStatus.SUCCESS, notes="done", is_explicit=False)
    engine = build_engine(dot, ScriptedBackend({"judge": implicit}), tmp_path / "implicit")
    outcome = await engine.run()

    # (b) the SPEC behavior (status in {SUCCESS, PARTIAL_SUCCESS} satisfies) must
    # NOT hold on its own.
    assert outcome.status == StageStatus.FAIL, flip(
        r,
        observed=(
            f"a status-only SUCCESS satisfied a goal gate (pipeline {outcome.status}) "
            "-- the spec's section 3.4 check_goal_gates behavior"
        ),
        expected=(
            "the gate additionally requires is_explicit: a defaulted SUCCESS is "
            "treated as unsatisfied and fails closed"
        ),
        direction="UN-DIVERGENCE",
    )
    assert "gate" in (outcome.failure_reason or "").lower(), flip(
        r,
        observed=f"failure_reason does not name the gate: {outcome.failure_reason!r}",
        expected="the fail-closed refusal is traceable to the unsatisfied gate",
    )

    # (a) the control: an EXPLICIT SUCCESS does satisfy it -- the gate is closed,
    # not welded shut.
    explicit = Outcome(status=StageStatus.SUCCESS, notes="done", is_explicit=True)
    control = build_engine(
        dot, ScriptedBackend({"judge": explicit}), tmp_path / "explicit"
    )
    control_outcome = await control.run()
    assert control_outcome.status == StageStatus.SUCCESS, flip(
        r,
        observed=(
            f"an EXPLICIT SUCCESS failed to satisfy the goal gate "
            f"({control_outcome.status})"
        ),
        expected=(
            "an asserted verdict still satisfies the gate -- fail-closed narrows what "
            "counts as evidence, it does not make gates unsatisfiable"
        ),
    )


@pytest.mark.asyncio
async def test_row_atx_m_006o(tmp_path):
    """ATX-6 (OPEN): a FAIL outcome executes exactly once, even with retries."""
    r = row("ATX-M-006o")
    dot = """
    digraph M006 {
        start [shape=Mdiamond]
        exit  [shape=Msquare]
        work  [prompt="do work", max_retries=2]
        start -> work
        work -> exit
    }
    """
    backend = ScriptedBackend(
        {"work": Outcome(status=StageStatus.FAIL, failure_reason="nope", is_explicit=True)}
    )
    engine = build_engine(dot, backend, tmp_path)
    await engine.run()

    executions = backend.calls.count("work")
    assert executions == 1, flip(
        r,
        observed=f"a FAIL outcome with max_retries=2 executed {executions} time(s)",
        expected=(
            "section 3.5's pseudocode returns a FAIL outcome immediately -- FAIL is a "
            "routing decision, not a flake. The Definition-of-Done checklist says the "
            "opposite; this engine follows section 3.5"
        ),
        direction="UNDECIDED-MOVEMENT",
    )


def test_row_atx_m_007o():
    """ATX-7 (OPEN): condition literals are compared WITH their quotes."""
    from amplifier_module_loop_pipeline.conditions import evaluate_condition

    r = row("ATX-M-007o")
    context = PipelineContext()
    context.set("mode", "fast")
    outcome = Outcome(status=StageStatus.SUCCESS)

    unquoted_matches = evaluate_condition("context.mode=fast", outcome, context)
    quoted_matches = evaluate_condition('context.mode="fast"', outcome, context)

    assert unquoted_matches, flip(
        r,
        observed="an UNQUOTED condition literal failed to match its context value",
        expected="unquoted literals compare by value (the common, working case)",
    )
    assert not quoted_matches, flip(
        r,
        observed=(
            "a double-quoted condition literal now matches -- parse_literal-style "
            "unquoting appears to have been implemented"
        ),
        expected=(
            "today's pinned behavior: quoted literals are compared RAW, including the "
            "quotes, so a canonical section 10.5 graph does not route as written"
        ),
        direction="UNDECIDED-MOVEMENT",
    )


def test_row_atx_m_f01():
    """ATX-13 / EXTENSIONS 38 (decided via issue #234, F1): an unknown shape
    hard-errors at dispatch; the section 4.2 default-handler fall-through does
    NOT occur.  Both halves asserted, per the DIVERGE-DECIDED contract."""
    r = row("ATX-M-F01")
    registry = HandlerRegistry(HandlerContext())

    # Half 1: the spec's behavior does not occur.  Section 4.2's resolve()
    # would fall through to the default codergen handler; if get() RETURNS
    # anything for an unknown shape, the engine has silently un-diverged.
    fell_through: object | None = None
    message = ""
    try:
        fell_through = registry.get(Node(id="broken_gate", shape="trapezium"))
    except ValueError as exc:
        message = str(exc)
    assert fell_through is None, flip(
        r,
        observed=(
            f"an unknown shape fell through to {type(fell_through).__name__} "
            "instead of raising -- the section 4.2 default-handler fallback is back"
        ),
        expected=(
            "the ledgered refusal: ValueError at dispatch (ATX-13, EXTENSIONS 38). "
            "A typo'd semantic shape must never silently become an LLM session"
        ),
        direction="UN-DIVERGENCE",
    )

    # Half 2: our ledgered behavior occurs, and is LOUD in the doctrine-rule-4
    # sense -- names the shape, lists the valid set (remediation, not just refusal).
    assert "trapezium" in message, flip(
        r,
        observed=f"the error does not name the offending shape: {message!r}",
        expected="the refusal names the bad shape so the author can fix the typo",
    )
    assert "box" in message, flip(
        r,
        observed="the error does not list the supported shapes",
        expected="the refusal lists the recognized shapes",
    )
    # The control: known shapes still dispatch. The divergence is a refusal on
    # the unknown case only -- it does not narrow the canonical table.
    handler = registry.get(Node(id="ok", shape="box"))
    assert handler is not None, flip(
        r,
        observed="a canonical shape failed to dispatch",
        expected="the section 2.8 table still resolves; only UNKNOWN shapes are refused",
    )


def test_row_atx_m_f04():
    """ATX-14 / EXTENSIONS 39 (decided via issue #234, F4): `reasoning_effort`
    has no engine-injected default -- Appendix A's "high" deliberately does not
    hold.  Unset stays unset through parse AND transforms (the stylesheet
    resolution point), so the provider's own default governs.  Any value
    appearing where the author wrote nothing is a hidden default -- the exact
    substitution EXTENSIONS 39 rules out -- whether it is the spec's "high" or
    anything else."""
    r = row("ATX-M-F04")
    graph = parse_dot(
        """
        digraph F04 {
            start [shape=Mdiamond]
            exit  [shape=Msquare]
            work  [prompt="do work"]
            start -> work -> exit
        }
        """
    )
    node = graph.nodes["work"]
    assert node.reasoning_effort is None, flip(
        r,
        observed=(
            f"a node omitting reasoning_effort now resolves to "
            f"{node.reasoning_effort!r} at parse time"
        ),
        expected=(
            "the ledgered behavior (ATX-14, EXTENSIONS 39): the attribute stays "
            "UNSET so the provider's own default applies -- the engine injects "
            "no value the author did not write"
        ),
        direction="UN-DIVERGENCE",
    )
    assert node.attrs.get("reasoning_effort") is None, flip(
        r,
        observed="node.attrs surfaced a default reasoning_effort",
        expected="the attrs proxy agrees with the first-class field: unset is unset",
        direction="UN-DIVERGENCE",
    )

    # Through the transform pipeline too: apply_transforms() is where the
    # stylesheet -- the spec's own centralizing surface for this attribute
    # (section 8) -- resolves values onto nodes.  With no stylesheet rule, the
    # engine must leave the attribute alone; this is the resolution point a
    # conforming implementation would have to inject "high" at.
    from amplifier_module_loop_pipeline.transforms import apply_transforms

    transformed = apply_transforms(graph, PipelineContext())
    assert transformed.nodes["work"].reasoning_effort is None, flip(
        r,
        observed=(
            "apply_transforms() resolved reasoning_effort to "
            f"{transformed.nodes['work'].reasoning_effort!r} with no stylesheet rule"
        ),
        expected=(
            "the transform pipeline injects nothing: node attr, model_stylesheet, "
            "and profile are the ONLY value sources (EXTENSIONS 39)"
        ),
        direction="UN-DIVERGENCE",
    )


@pytest.mark.asyncio
async def test_row_atx_m_102(tmp_path):
    """Section 3.2 step 4: context_updates merged; `outcome` always set;
    `preferred_label` set ONLY when the outcome carries a non-empty one."""
    r = row("ATX-M-102")
    seen: list[dict[str, Any]] = []

    async def _run(node, prompt, context, incoming_edge=None, graph=None):
        seen.append(
            {
                "node": node.id,
                "outcome": context.get("outcome"),
                "preferred_label": context.get("preferred_label"),
                "artifact": context.get("artifact_path"),
            }
        )
        if node.id == "first":
            return Outcome(
                status=StageStatus.SUCCESS,
                preferred_label="onward",
                context_updates={"artifact_path": "src/word_counter.py"},
                is_explicit=True,
            )
        return Outcome(status=StageStatus.SUCCESS, is_explicit=True)

    backend = type("_CtxBackend", (), {"run": staticmethod(_run)})()
    dot = """
    digraph M102 {
        start  [shape=Mdiamond]
        exit   [shape=Msquare]
        first  [prompt="one"]
        second [prompt="two"]
        start -> first
        first -> second [label="onward"]
        second -> exit
    }
    """
    engine = build_engine(dot, backend, tmp_path)
    await engine.run()

    assert len(seen) == 2, flip(
        r, observed=f"nodes executed: {[s['node'] for s in seen]}", expected="two nodes run"
    )
    assert seen[1]["artifact"] == "src/word_counter.py", flip(
        r,
        observed="context_updates from the first node were not visible to the second",
        expected="step 4 merges outcome.context_updates into the run context",
    )
    assert seen[1]["outcome"] == StageStatus.SUCCESS.value, flip(
        r,
        observed=f"context['outcome'] was {seen[1]['outcome']!r} at the second node",
        expected="step 4 sets context['outcome'] to the previous node's status",
    )
    assert seen[1]["preferred_label"] == "onward", flip(
        r,
        observed=f"context['preferred_label'] was {seen[1]['preferred_label']!r}",
        expected="step 4 sets context['preferred_label'] when the outcome carries one",
    )
    # The "only when non-empty" half: the second node emitted no preferred_label,
    # so the value must not be re-set by it (it stays as the first node left it,
    # never overwritten with an empty string).
    assert engine.context.get("preferred_label") == "onward", flip(
        r,
        observed=(
            "an outcome with an EMPTY preferred_label overwrote the context key "
            f"(now {engine.context.get('preferred_label')!r})"
        ),
        expected=(
            "step 4 sets preferred_label ONLY when non-empty -- an unconditional "
            "overwrite is the stale-label bug class"
        ),
    )


def test_row_atx_m_109():
    """Section 3.3: edge selection returns exactly ONE edge (ATX-10 restoration)."""
    r = row("ATX-M-109")
    graph = parse_dot(
        """
        digraph M109 {
            start [shape=Mdiamond]
            exit  [shape=Msquare]
            work  [prompt="work"]
            a     [prompt="a"]
            b     [prompt="b"]
            start -> work
            work -> a [condition="outcome=success"]
            work -> b [condition="outcome=success"]
            a -> exit
            b -> exit
        }
        """
    )
    outcome = Outcome(status=StageStatus.SUCCESS, is_explicit=True)
    selected = select_edge("work", outcome, PipelineContext(), graph)

    assert selected is not None, flip(
        r,
        observed="select_edge returned NONE with two matching conditional edges",
        expected="exactly one edge is selected",
    )
    assert not isinstance(selected, (list, tuple, set)), flip(
        r,
        observed=f"select_edge returned a collection of edges: {selected!r}",
        expected=(
            "ONE edge, never a fan-out from a non-component node. The multi-match "
            "fan-out dialect was an unledgered divergence, retired under ATX-10 (T0-4)"
        ),
    )
    assert selected.to_node == "a", flip(
        r,
        observed=f"tie broken to {selected.to_node!r}",
        expected="best_by_weight_then_lexical picks 'a' (equal weight, lexical order)",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rung,gate_attrs,graph_attrs,expected_target",
    [
        ("node retry_target", 'retry_target="fix_a"', "", "fix_a"),
        ("node fallback", 'fallback_retry_target="fix_b"', "", "fix_b"),
        ("graph retry_target", "", 'retry_target="fix_c"', "fix_c"),
        ("graph fallback", "", 'fallback_retry_target="fix_d"', "fix_d"),
    ],
)
async def test_row_atx_m_111(tmp_path, rung, gate_attrs, graph_attrs, expected_target):
    """Section 3.4 rule 3: the four-rung retry-target ladder, rung by rung."""
    r = row("ATX-M-111")
    gate_extra = f", {gate_attrs}" if gate_attrs else ""
    graph_line = f"    graph [{graph_attrs}]" if graph_attrs else ""
    dot = f"""
    digraph M111 {{
{graph_line}
        start [shape=Mdiamond]
        exit  [shape=Msquare]
        judge [prompt="judge", goal_gate=true{gate_extra}]
        fix_a [prompt="a"]
        fix_b [prompt="b"]
        fix_c [prompt="c"]
        fix_d [prompt="d"]
        start -> judge
        judge -> exit
        judge -> fix_a [condition="outcome=never_a"]
        judge -> fix_b [condition="outcome=never_b"]
        judge -> fix_c [condition="outcome=never_c"]
        judge -> fix_d [condition="outcome=never_d"]
        fix_a -> exit
        fix_b -> exit
        fix_c -> exit
        fix_d -> exit
    }}
    """
    engine = build_engine(
        dot,
        ScriptedBackend(
            {"judge": Outcome(status=StageStatus.FAIL, failure_reason="no", is_explicit=True)}
        ),
        tmp_path / rung.replace(" ", "_"),
    )
    gate_outcome = Outcome(status=StageStatus.FAIL, failure_reason="no", is_explicit=True)
    engine.node_outcomes["judge"] = gate_outcome
    engine.completed_nodes.append("judge")

    resolved = await engine._check_goal_gates()
    assert resolved.status == StageStatus.FAIL, flip(
        r,
        observed=f"an unsatisfied gate produced {resolved.status}",
        expected="an unsatisfied goal gate cannot let the pipeline exit",
    )
    assert resolved.suggested_next_ids == [expected_target], flip(
        r,
        observed=(
            f"rung '{rung}': gate retry resolved to {resolved.suggested_next_ids!r}"
        ),
        expected=(
            f"rung '{rung}': the ladder resolves to {expected_target!r} "
            "(node retry_target > node fallback > graph retry_target > graph fallback)"
        ),
    )


def test_row_atx_m_118():
    """Section 2.8 / Appendix B: the nine canonical shapes map to their handler types."""
    r = row("ATX-M-118")
    canonical_table = {
        "Mdiamond": "start",
        "Msquare": "exit",
        "box": "codergen",
        "hexagon": "wait.human",
        "diamond": "conditional",
        "component": "parallel",
        "tripleoctagon": "parallel.fan_in",
        "parallelogram": "tool",
        "house": "stack.manager_loop",
    }
    for shape, handler_type in canonical_table.items():
        assert SHAPE_TO_HANDLER.get(shape) == handler_type, flip(
            r,
            observed=(
                f"shape {shape!r} maps to {SHAPE_TO_HANDLER.get(shape)!r} "
                f"(canonical section 2.8 says {handler_type!r})"
            ),
            expected="the nine canonical shape rows map exactly as section 2.8 specifies",
        )
    # `folder` is ours, not the spec's -- asserted separately so a reader of this
    # probe cannot mistake an extension for canonical text.
    assert SHAPE_TO_HANDLER.get("folder") == "pipeline", flip(
        r,
        observed=f"the EXTENSION shape 'folder' maps to {SHAPE_TO_HANDLER.get('folder')!r}",
        expected="folder -> pipeline (specs/EXTENSIONS.md section 10, additive to section 2.8)",
    )


@pytest.mark.asyncio
async def test_row_atx_m_119(tmp_path):
    """Section 3.5: a handler exception becomes a FAIL outcome, never an escaped crash."""
    r = row("ATX-M-119")

    async def _boom(node, prompt, context, incoming_edge=None, graph=None):
        raise RuntimeError("backend exploded")

    backend = type("_RaisingBackend", (), {"run": staticmethod(_boom)})()
    dot = """
    digraph M119 {
        start [shape=Mdiamond]
        exit  [shape=Msquare]
        work  [prompt="do work"]
        start -> work
        work -> exit
    }
    """
    engine = build_engine(dot, backend, tmp_path)
    try:
        outcome = await engine.run()
    except RuntimeError as exc:  # pragma: no cover - the failure path
        raise AssertionError(
            flip(
                r,
                observed=f"the handler exception escaped run(): {exc!r}",
                expected=(
                    "section 3.5's CATCH turns an exception into "
                    "Outcome(status=FAIL, failure_reason=str(exception))"
                ),
            )
        ) from exc

    assert outcome.status == StageStatus.FAIL, flip(
        r,
        observed=f"a raising handler produced {outcome.status}",
        expected="an exception is caught and returned as a FAIL outcome",
    )
    assert "exploded" in (outcome.failure_reason or ""), flip(
        r,
        observed=f"failure_reason lost the exception text: {outcome.failure_reason!r}",
        expected="the failure_reason carries str(exception) so the crash stays traceable",
    )
