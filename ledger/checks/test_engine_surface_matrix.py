"""Conformance checks for the rows derived from `contracts/engine-surface.v1.md`.

The sibling module `test_spec_conformance_matrix.py` owns the ledger's
STRUCTURAL guards -- schema, quote verification, cite resolution, SYNC rows,
the probe cross-check -- for every row in `ledger/rows.yaml` regardless of which
contract it cites. Nothing here duplicates those; they already cover the `ESF-*`
rows the moment those rows exist.

What lives here is what is specific to the contract THIS repo owns:

  * the `ESF-000` SYNC probe, and
  * the coverage tripwires that join the ledger to the contract's own clause
    list in both directions -- no Core clause silently un-rowed, no row citing a
    clause the contract does not have.

Why a separate module rather than more of the matrix file: that file is written
against the EXTERNAL nlspec, and mixing an owned contract's probes into it would
blur exactly the boundary `engine-surface.v1` exists to draw. The structural
guards reach across both files by design (`probe_definitions()`), so a probe
here is cross-checked against a row there just as if it sat in one file.

STATUS, stated once: `contracts/engine-surface.v1.md` was **FROZEN on 2026-09-07**
on the owner's word, on the evidence in
`contracts/FREEZE-PACKET-engine-surface.v1.md`. Until that day these rows bound
nothing on their own -- they made drift visible, which is what the freeze
packet's condition 2 asked for; now they answer to a locked contract.

HONEST LIMIT, not softened by the stamp: 16 of the 17 clause rows are
`assertion.kind: indexed`, which proves a cited test EXISTS, not that it still
asserts what it was cited for, and 7-8 of 17 clauses (the packet's two counts)
have a worked end-to-end example. The stamp claims neither; see the contract
header's "What it does not claim".
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

import yaml

#: The sibling module owns the ONE ledger loader, and this module reuses it
#: rather than parsing `rows.yaml` a second time -- two loaders drift, and a
#: second reading of the ledger is exactly the kind of quiet divergence this
#: ledger exists to catch. It is loaded BY PATH for the same reason
#: `ledger/checks/conftest.py` loads loop-pipeline's conftest by path: these
#: checks run under `--import-mode=importlib` from the loop-pipeline module,
#: where `ledger/checks/` is not on `sys.path` and a plain
#: `import test_spec_conformance_matrix` raises ModuleNotFoundError at
#: collection time -- taking the whole suite down with it.
_MATRIX_PATH = Path(__file__).resolve().parent / "test_spec_conformance_matrix.py"


def _load_matrix_module():
    for existing in sys.modules.values():
        if getattr(existing, "__file__", None) == str(_MATRIX_PATH):
            return existing
    if not _MATRIX_PATH.exists():  # pragma: no cover - fails loud by design
        raise RuntimeError(
            f"{Path(__file__).name} cannot find the ledger loader it reuses: "
            f"{_MATRIX_PATH}\nPoint this path at the moved file -- do NOT fork the "
            "loader: a second reading of ledger/rows.yaml can disagree with the "
            "first, and a ledger that disagrees with itself asserts nothing."
        )
    spec = importlib.util.spec_from_file_location("_ledger_matrix", _MATRIX_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_matrix = _load_matrix_module()
BUNDLE_ROOT = _matrix.BUNDLE_ROOT
ROWS = _matrix.ROWS
row = _matrix.row
flip = _matrix.flip

#: The contract these rows answer to.
CONTRACT_FILE = "contracts/engine-surface.v1.md"
CONTRACT_PATH = BUNDLE_ROOT / CONTRACT_FILE

#: Rows derived from it. Identified by the contract they cite, never by an id
#: prefix -- an id is a label, the cited file is the fact.
ESF_ROWS = [r for r in ROWS if r["contract"]["file"] == CONTRACT_FILE]

#: `### C<n> — <heading>` is the contract's own Core-clause form. A clause that
#: is not written that way is not a Core clause, and this regex is the only
#: place that reading lives.
_CORE_CLAUSE = re.compile(r"^### (C\d+) — (.+)$", re.M)


def core_clauses() -> dict[str, str]:
    """Every Core clause id -> heading, read from the contract's own headings."""
    return {
        m.group(1): m.group(2)
        for m in _CORE_CLAUSE.finditer(CONTRACT_PATH.read_text(encoding="utf-8"))
    }


# ---------------------------------------------------------------------------
# The SYNC probe
# ---------------------------------------------------------------------------


def test_row_esf_000():
    """SYNC: the owned contract's sha256 still matches the recorded pin."""
    r = row("ESF-000")
    sync = r["sync"]
    actual = hashlib.sha256((BUNDLE_ROOT / sync["file"]).read_bytes()).hexdigest()
    pinned = sync["sha256"]
    assert actual == pinned, (
        f'SPEC-CONFORMANCE LEDGER FLIP -- row {r["id"]} "{r["title"]}"\n'
        f"  contract:    {sync['file']}  (OWNED by this repo -- no upstream)\n"
        f"  pinned to:   sha256={pinned}\n"
        f"  actual:      sha256={actual}\n"
        "  direction:   LEDGER-INTEGRITY -- the contract text has moved.\n"
        "\n"
        "  This is NOT a 'fix the hash' failure. Every ESF-* row quotes THIS file,\n"
        "  so a change to it is a MANDATORY RE-REVIEW of those rows\n"
        "  (LEDGER-FORMAT.md section 4 -- never a silent hash bump):\n"
        "    1. Re-read each ESF-* row against the new clause text. The quote checks\n"
        "       will already have failed on exactly the rows whose text moved --\n"
        "       that is your targeted diff.\n"
        "    2. Move the dispositions the new text invalidates. A clause that gained\n"
        "       a requirement nothing pins is a GAP row with a filed item, not a\n"
        "       CONFORMS row that happens to still parse.\n"
        "    3. THEN update `sha256:` in the SYNC row, in the same change.\n"
        "  That expected case has already happened once and was NOT a defect: on\n"
        "  2026-09-07 the owner stamped this contract FROZEN, both this probe and\n"
        "  ESF-000's quote went red, the re-review was done and both were re-pinned\n"
        "  in the same commit. It cannot happen that way again -- the contract is\n"
        "  now LOCKED, so bytes moving means either a proposal the owner ratified\n"
        "  (`engine-surface.v2-candidate.md`) landed, in which case do the re-review\n"
        "  above and re-pin, or someone edited a locked contract in place, which is\n"
        "  a protocol breach: revert it and write the proposal instead.\n"
        "  Doing neither means main carries a ledger that lies. That is drift."
    )


# ---------------------------------------------------------------------------
# Behavioral probes
# ---------------------------------------------------------------------------


def _bundle_frontmatter(path):
    """The YAML frontmatter of an Amplifier bundle `.md`, or {} if it has none."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    return yaml.safe_load(text[4:end]) or {}


#: The bundle surface this repo actually ships -- the only place a same-repo
#: `source:` can appear. Discovered by directory rather than by a hard-coded
#: file list, so a composition file added tomorrow is scanned tomorrow instead
#: of silently falling outside the probe. `bundles/`, `profiles/` and `agents/`
#: do not exist here today (the ruling at issue #48 removed the C17 items that
#: named them); they are listed anyway so the day one appears it is covered.
_BUNDLE_DIRS = ("behaviors", "bundles", "profiles", "agents")
_BUNDLE_SUFFIXES = {".yaml", ".yml", ".md"}

#: A same-repo source that carries a git ref -- the shape C17 forbids:
#: ``git+https://github.com/microsoft/amplifier-bundle-dot-runner@<ref>#subdirectory=...``.
#: The ref-FREE same-repo forms specs/EXTENSIONS.md section 37 moved to (a
#: relative ``../modules/X``, a namespaced ``"@dot-runner:skills"``) carry no
#: ``@`` after the repo slug and so never match.
_SELF_PIN_WITH_REF = re.compile(r"amplifier-bundle-dot-runner(?:\.git)?@(?P<ref>[^#\s]+)")

#: THE ALLOW-LIST -- one class, named explicitly rather than pattern-matched
#: loosely, because an exemption nobody can point at is indistinguishable from a
#: hole. Its authority is specs/EXTENSIONS.md section 37, change 3, verbatim:
#:
#:   "The remaining 34 are all `session.orchestrator` sources and are **kept
#:    deliberately**: foundation resolves those against the *composed root's*
#:    base_path -- the app's own bundle directory in a real session -- so no
#:    relative path written here can reach this snapshot, and there is no
#:    namespaced module-source form. Measured, not assumed: a build that made
#:    them relative failed to start in a clean DTU install."
#:
#: C17.2 restates that exemption inside the contract, so the clause and this
#: probe agree about it. `test_selfcheck_the_exemption_is_still_section_37s_own_words`
#: fails if that sentence ever leaves section 37.
_KEPT_DELIBERATELY = ("session", "orchestrator")


def bundle_surface_files() -> list[Path]:
    """Every bundle/behavior YAML or md file this repo ships."""
    files = [BUNDLE_ROOT / "bundle.md"]
    for name in _BUNDLE_DIRS:
        directory = BUNDLE_ROOT / name
        if not directory.is_dir():
            continue
        files.extend(
            path
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix in _BUNDLE_SUFFIXES
        )
    return [path for path in files if path.exists()]


def _load_bundle_doc(path: Path):
    """A bundle file's YAML: frontmatter for `.md`, the whole document otherwise."""
    if path.suffix == ".md":
        return _bundle_frontmatter(path)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _sources(node, path=()):
    """Every `source:` scalar in a bundle document, with its YAML key path."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "source" and isinstance(value, str):
                yield path + (str(key),), value
            else:
                yield from _sources(value, path + (str(key),))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _sources(value, path + (str(i),))


def classify_self_pins(doc) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split a bundle document's ref-carrying same-repo sources.

    Returns ``(offending, exempt)``; each entry is ``(key_path, value)``. A
    source is exempt ONLY when it sits at ``...session.orchestrator.source`` --
    the single class `_KEPT_DELIBERATELY` allow-lists. Everything else that
    carries a git ref back at this repo violates C17.1.
    """
    offending: list[tuple[str, str]] = []
    exempt: list[tuple[str, str]] = []
    for key_path, value in _sources(doc):
        if not _SELF_PIN_WITH_REF.search(value):
            continue
        bucket = exempt if key_path[-3:-1] == _KEPT_DELIBERATELY else offending
        bucket.append((".".join(key_path), value))
    return offending, exempt


def _source_line(path: Path, value: str) -> str:
    """The 1-based line of the `source:` entry carrying ``value``, for file:line.

    Comment lines are skipped: `behaviors/dot-runner-amplifier-agent.yaml`
    documents the kept pin in prose directly above the pin itself, and pointing
    a reviewer at the comment instead of the code wastes the one thing a
    file:line is for.
    """
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("source:") and value in line:
            return str(number)
    return "?"


def test_row_esf_017():
    """CONFORMS: no same-repo module or skill source carries a git ref.

    C17 as originally drafted named three things, two of which the repo split
    left in `microsoft/amplifier-bundle-attractor`. The owner ruling at issue
    #48 removed those two and made section 37's third change the whole clause,
    which IS true of this repo and IS testable -- this probe is that test, and
    the reason the row could move off OPEN-PINNED.

    Hermetic: reads this repo's own bundle files, parses YAML, matches a regex.
    No engine, no network, no install. `test_selfcheck_the_scanner_*` below are
    the discriminating cases -- they prove the scanner separates a forbidden pin
    from an exempt one from a ref-free source, so a green run here means "looked
    and found nothing", never "never looked".
    """
    r = row("ESF-017")

    surface = bundle_surface_files()
    assert surface, (
        "ESF-017's probe found NO bundle files to scan. `bundle.md` and "
        f"{list(_BUNDLE_DIRS)} are all absent or unreadable, so this probe would "
        "pass vacuously -- which is worse than failing. Point "
        "bundle_surface_files() at wherever the bundle surface moved."
    )

    offending: list[str] = []
    for path in surface:
        rel = path.relative_to(BUNDLE_ROOT)
        bad, _kept = classify_self_pins(_load_bundle_doc(path))
        offending.extend(
            f"{rel}:{_source_line(path, value)}  ({key_path})\n                 {value}"
            for key_path, value in bad
        )

    assert not offending, flip(
        r,
        observed=(
            "same-repo `source:` carrying a git ref, outside the one exempt "
            "class:\n               " + "\n               ".join(offending)
        ),
        expected=(
            "module and skill sources stay ref-free. A self-pin at @main makes a "
            "BRANCH install serve main's bytes, which is what made branch "
            "regression-testing of guidance impossible (specs/EXTENSIONS.md "
            "section 37, change 3). Use a relative source (`../modules/X`) or a "
            "namespaced one (`\"@dot-runner:skills\"`). If this pin is a "
            "`session.orchestrator` source it is exempt -- but then it must "
            "actually SIT under `session.orchestrator`, not merely resemble one."
        ),
    )


# ---------------------------------------------------------------------------
# Coverage tripwires -- the join, asserted in both directions
# ---------------------------------------------------------------------------


def test_tripwire_every_core_clause_of_engine_surface_is_rowed():
    """LEDGER-FORMAT.md section 6.1, applied to this contract's Core clauses.

    This is the tripwire that makes the ledger's coverage claim falsifiable: add
    a Core clause to the contract and forget to row it, and this fails naming
    the clause. Without it, "one row per Core clause" is a statement about the
    day the section was written, not an invariant.
    """
    clauses = core_clauses()
    assert clauses, (
        f"no `### C<n> — ...` Core clause headings found in {CONTRACT_FILE}. Either "
        "the contract was restructured (in which case core_clauses() must move with "
        "it) or the file is not what this module thinks it is."
    )
    cited = {r["contract"]["clause"] for r in ESF_ROWS}
    missing = sorted(
        (c for c in clauses if c not in cited),
        key=lambda c: int(c[1:]),
    )
    assert not missing, (
        "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY (coverage tripwire)\n"
        f"  Core clauses of {CONTRACT_FILE} with no ledger row:\n"
        + "\n".join(f"    {c} -- {clauses[c]}" for c in missing)
        + "\n"
        "  Two legal exits -- in THIS change, not a follow-up:\n"
        "    1. Add a row for the clause. If nothing pins it, that row is a GAP with\n"
        "       a filed work item -- a GAP row is an honest row, an absent row is not.\n"
        "    2. Remove the clause from the contract, if it should never have been one.\n"
        "  There is no third exit. A Core clause with no row can drift with no\n"
        "  failure message naming this contract, which is the exact condition the\n"
        "  freeze packet recorded as the blocker on condition 2."
    )


def test_tripwire_no_engine_surface_row_cites_a_clause_that_does_not_exist():
    """The other direction: a row may not invent a clause the contract lacks.

    The SYNC row cites the contract header rather than a Core clause, so it is
    named as the one exemption rather than pattern-matched around.
    """
    clauses = set(core_clauses())
    exempt = {"Header"}
    invented = sorted(
        r["id"]
        for r in ESF_ROWS
        if r["contract"]["clause"] not in clauses
        and r["contract"]["clause"] not in exempt
    )
    assert not invented, (
        "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY (coverage tripwire)\n"
        f"  rows {invented} cite a clause {CONTRACT_FILE} does not have.\n"
        f"  Known Core clauses: {sorted(clauses, key=lambda c: int(c[1:]))}\n"
        "  A row citing a clause nobody wrote asserts a conformance claim against\n"
        "  nothing. Fix the clause id, or delete the row."
    )


def test_tripwire_engine_surface_rows_are_one_per_clause():
    """One row per Core clause, so the ledger reads as the contract's own table.

    Not a format requirement -- LEDGER-FORMAT.md permits several rows per clause
    -- but it IS this section's stated shape, and a second row appearing on a
    clause silently is how a table stops matching the contract it mirrors. When
    a clause genuinely needs splitting, split it here deliberately.
    """
    counts: dict[str, list[str]] = {}
    for r in ESF_ROWS:
        counts.setdefault(r["contract"]["clause"], []).append(r["id"])
    doubled = {c: ids for c, ids in counts.items() if len(ids) > 1}
    assert not doubled, (
        "SPEC-CONFORMANCE LEDGER FLIP -- LEDGER-INTEGRITY (coverage tripwire)\n"
        f"  more than one row cites the same clause: {doubled}\n"
        "  This section's shape is one row per Core clause. If a clause really does\n"
        "  need two rows, say so here and in the section's header comment -- do not\n"
        "  let the table quietly stop mirroring the contract."
    )


# ---------------------------------------------------------------------------
# RED/GREEN self-tests for the readings above
# ---------------------------------------------------------------------------


def test_selfcheck_core_clause_extraction_finds_the_real_clauses():
    clauses = core_clauses()
    assert {"C1", "C10", "C17"} <= set(clauses)
    assert "C99" not in clauses
    # Retitled 2026-09-06 with the narrowing (owner ruling, issue #48): the
    # clause is no longer "Bundle composition" -- two of that heading's three
    # subjects were another repo's.
    assert clauses["C17"] == "Ref-free same-repo sources"
    # Backlogged entries (`- **B1 — ...**`) are NOT Core clauses and must not be
    # swept in by a looser pattern.
    assert not any(c.startswith("B") for c in clauses)


# --- ESF-017's scanner: the discriminating triple -------------------------
#
# These are what make test_row_esf_017's green run mean something. Each case is
# an in-memory document -- nothing on disk is touched, nothing is restored --
# so the probe's detector is exercised even on the day the repo happens to
# contain no violation at all.


def _doc_with_source(key_path: tuple[str, ...], value: str):
    """Build a nested dict placing ``value`` at ``key_path`` + ``source``."""
    node: dict = {"source": value}
    for key in reversed(key_path):
        node = {key: node}
    return node


_A_REF_PIN = (
    "git+https://github.com/microsoft/amplifier-bundle-dot-runner@main"
    "#subdirectory=modules/loop-pipeline"
)


def test_selfcheck_the_scanner_catches_a_ref_pinned_module_source():
    """The RED case: a `tools:` module source that re-acquired a git ref."""
    doc = _doc_with_source(("tools", "0"), _A_REF_PIN)
    offending, exempt = classify_self_pins(doc)
    assert offending == [("tools.0.source", _A_REF_PIN)]
    assert exempt == []


def test_selfcheck_the_scanner_exempts_a_session_orchestrator_source():
    """The allow-listed case: identical value, different key path."""
    doc = _doc_with_source(("agents", "x", "session", "orchestrator"), _A_REF_PIN)
    offending, exempt = classify_self_pins(doc)
    assert offending == []
    assert exempt == [("agents.x.session.orchestrator.source", _A_REF_PIN)]


def test_selfcheck_the_scanner_ignores_ref_free_and_foreign_sources():
    """The GREEN cases: what section 37 moved TO, plus another repo's pin.

    Without this third case the scanner could pass the first two by flagging
    every `source:` it sees, which would make the probe unusable rather than
    correct.
    """
    for ref_free in (
        "../modules/loop-pipeline",
        '"@dot-runner:skills"',
        "git+https://github.com/microsoft/amplifier-bundle-attractor@main",
    ):
        offending, exempt = classify_self_pins(_doc_with_source(("tools", "0"), ref_free))
        assert offending == [], ref_free
        assert exempt == [], ref_free


def test_selfcheck_the_scanner_reads_the_real_bundle_surface():
    """The surface list is not empty, and the shipped root bundle is in it."""
    surface = bundle_surface_files()
    relative = {str(p.relative_to(BUNDLE_ROOT)) for p in surface}
    assert "bundle.md" in relative
    assert "behaviors/dot-runner-amplifier-agent.yaml" in relative


def test_selfcheck_source_line_points_at_the_pin_not_the_comment_above_it():
    """file:line must land on the `source:` line, not the prose describing it.

    `behaviors/dot-runner-amplifier-agent.yaml` documents its kept pin in a long
    comment block directly above the pin, and that comment contains the same URL.
    """
    path = BUNDLE_ROOT / "behaviors" / "dot-runner-amplifier-agent.yaml"
    _offending, exempt = classify_self_pins(_load_bundle_doc(path))
    assert len(exempt) == 1, exempt
    line = _source_line(path, exempt[0][1])
    assert line != "?"
    text = path.read_text(encoding="utf-8").splitlines()[int(line) - 1]
    assert text.lstrip().startswith("source:"), text


def test_selfcheck_the_exemption_is_still_section_37s_own_words():
    """The allow-list cites section 37. If that sentence goes, so does its authority.

    C17.2 and `_KEPT_DELIBERATELY` both rest on section 37 saying it keeps this
    class deliberately. Delete or reword that and the exemption becomes an
    unsourced carve-out, so this fails rather than letting the allow-list
    outlive its reason.
    """
    extensions = (BUNDLE_ROOT / "specs" / "EXTENSIONS.md").read_text(encoding="utf-8")
    sentence = (
        "The remaining 34 are all `session.orchestrator` sources and are "
        "**kept deliberately**"
    )
    assert " ".join(sentence.split()) in " ".join(extensions.split()), (
        "specs/EXTENSIONS.md section 37 no longer says it keeps `session.orchestrator` "
        "sources deliberately, but C17.2 and this module's `_KEPT_DELIBERATELY` "
        "allow-list both cite it. Re-source the exemption or remove it -- an "
        "allow-list whose justification has been deleted is a hole with a comment "
        "on it."
    )
