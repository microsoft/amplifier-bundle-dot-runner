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

HONEST LIMIT, stated once and not softened: `contracts/engine-surface.v1.md` is
**DRAFT**, not FROZEN. These rows bind nothing on their own; they make drift
visible, which is what the freeze packet's condition 2 asked for. Only the owner
stamps FROZEN.
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
        "  One case is expected and is NOT a defect: the day the owner stamps this\n"
        "  contract FROZEN, both this probe and ESF-000's quote go red. That is the\n"
        "  re-review firing exactly when it should. Do the re-review, then re-pin.\n"
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


def _self_pinned_sources(node, path=()):
    """Every `source:` value self-pinning THIS repo, with its key path.

    Yields ``(key_path, value)``. A self-pin is a `git+…amplifier-bundle-dot-runner@<ref>`
    source -- the shape `specs/EXTENSIONS.md` section 37 made ref-free wherever
    foundation's resolution semantics allowed, and deliberately KEPT for
    `session.orchestrator` sources, which resolve against the composed root.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if (
                key == "source"
                and isinstance(value, str)
                and "amplifier-bundle-dot-runner@" in value
            ):
                yield path + (key,), value
            else:
                yield from _self_pinned_sources(value, path + (str(key),))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _self_pinned_sources(value, path + (str(i),))


def test_row_esf_017():
    """OPEN-PINNED: pin today's bundle-composition reality in all three directions.

    C17's three items name a root bundle with a `context:` key, an
    `agents/attractor-expert.md` registered by `behaviors/attractor-core.yaml`,
    and ref-free same-repo module/skill sources. None of those artifacts is in
    THIS repo -- they are the attractor bundle's, and the split left them there.
    This probe asserts that state rather than asserting the clause, because
    which of the three exits to take (re-scope / move / drop) is a contract
    edit, and PROTOCOL.md section 5 makes that the owner's call.

    The point of pinning it: the ruling cannot be quietly pre-empted. Port the
    expert agent in, add a `context:` key, or let a module source re-acquire a
    self-pinned ref, and this row goes red naming issue #48.
    """
    r = row("ESF-017")
    issue = r["decision"]["issue"]
    header = (
        f'SPEC-CONFORMANCE LEDGER FLIP -- row {r["id"]} "{r["title"]}"\n'
        f"  contract:    {CONTRACT_FILE}  clause C17\n"
        f"  disposition: OPEN-PINNED -- decision pending at issue #{issue}\n"
        "  direction:   UNDECIDED-MOVEMENT\n"
    )
    tail = (
        "\n"
        "  This row pins a state, not a behavior the contract requires. It moved.\n"
        "  Two legal exits -- in THIS change, not a follow-up:\n"
        f"    1. Revert the move, and take the ruling at issue #{issue} first.\n"
        "    2. Keep the move AND close the ruling with it: edit C17 (owner), then\n"
        "       re-row this clause against whatever C17 then says.\n"
        "  There is no third exit: re-pointing this probe at the new state without\n"
        "  the ruling silently decides an owner question a lane may not decide.\n"
        "  Doing neither means main carries a ledger that lies. That is drift."
    )

    # C17.1 -- this repo's root bundle serves no always-on guidance.
    root_bundle = BUNDLE_ROOT / "bundle.md"
    assert root_bundle.exists(), f"{header}  observed: bundle.md is gone entirely{tail}"
    front = _bundle_frontmatter(root_bundle)
    assert not front.get("context"), (
        f"{header}"
        f"  observed:    bundle.md now carries a `context:` key: {front.get('context')!r}\n"
        "  expected:    no `context:` key -- the always-on guidance C17.1 describes\n"
        "               belongs to the attractor bundle's root, not this one\n"
        f"{tail}"
    )
    assert not front.get("agents"), (
        f"{header}"
        f"  observed:    bundle.md now registers agents: {front.get('agents')!r}\n"
        "  expected:    no `agents:` key on this repo's root bundle\n"
        f"{tail}"
    )

    # C17.2 -- neither the expert agent nor the core behavior lives here.
    for missing in ("agents/attractor-expert.md", "behaviors/attractor-core.yaml"):
        assert not (BUNDLE_ROOT / missing).exists(), (
            f"{header}"
            f"  observed:    {missing} now exists in this repo\n"
            "  expected:    absent -- C17.2's subject is the attractor bundle's\n"
            "               registration, and that file was left in that repo by the split\n"
            f"{tail}"
        )

    # C17.3 -- every same-repo self-pin still confined to session.orchestrator,
    # the one class section 37 keeps deliberately.
    pins: list[tuple[str, str]] = []
    bundle_files = [root_bundle, *sorted((BUNDLE_ROOT / "behaviors").glob("*.yaml"))]
    for path in bundle_files:
        doc = (
            _bundle_frontmatter(path)
            if path.suffix == ".md"
            else yaml.safe_load(path.read_text(encoding="utf-8"))
        )
        for key_path, value in _self_pinned_sources(doc):
            if key_path[-3:-1] != ("session", "orchestrator"):
                pins.append(
                    (f"{path.relative_to(BUNDLE_ROOT)}:{'.'.join(key_path)}", value)
                )
    assert not pins, (
        f"{header}"
        f"  observed:    same-repo `@ref` self-pin outside a session.orchestrator\n"
        f"               source: {pins}\n"
        "  expected:    module and skill sources stay ref-free (C17.3). A self-pin at\n"
        "               @main makes a BRANCH install serve main's bytes, which is what\n"
        "               made branch regression-testing of guidance impossible.\n"
        f"{tail}"
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
    assert clauses["C17"] == "Bundle composition"
    # Backlogged entries (`- **B1 — ...**`) are NOT Core clauses and must not be
    # swept in by a looser pattern.
    assert not any(c.startswith("B") for c in clauses)
