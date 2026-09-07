# FREEZE PACKET — `contracts/engine-surface.v1.md`

> **STAMPED 2026-09-07 — the contract is FROZEN.** The owner ruled in one word,
> *"stamp"*, recorded by the manager session on the owner's behalf, on the evidence
> below exactly as it stands: conditions 1–3 MET, condition 4 met literally with
> named per-clause residue (7 of 17 worked end-to-end examples by the at-a-glance
> table, 8 of 17 by the counting rule stated under "The worked-example corpus" — the
> two differ over C17 alone, which moved after that table was written; 5 partial, 4
> absent). **Nothing below is re-assessed or rewritten by this record**, including
> the Recommendation's "do not stamp yet": that was this packet's honest advice on
> 2026-09-06 and it stands as the history it is. The owner stamped anyway, knowing
> the residue, and that ruling is recorded here and in the contract's own Changelog.
> The residue — items 3, 4, 5 and 6 under "Exactly what is missing" — is **not
> closed** by the stamp and stays this packet's open work; the contract's header
> states what the stamp does and does not claim. From 2026-09-07 the contract is
> locked: it changes only by a sibling `engine-surface.v2-candidate.md` proposal the
> owner ratifies, never by an edit in place.

**What this is.** The Freeze Bar evidence for `CONTRACT-engine-surface.v1`,
condition by condition and clause by clause, so the owner can decide from
artifacts rather than from a self-report.

**What this is not.** A stamp. `PROTOCOL.md` §5: *only the owner stamps FROZEN*,
and §7 resists self-ratified amendments "by lanes *or* by the orchestrator". The
contract stays **DRAFT** after this packet lands. Nothing here asks for less than
the whole bar; where a condition is unmet, this packet says so in as many words,
because pillar 5 holds that a missing artifact is a real result.

**Assessed:** 2026-09-06, against `main` at `1dfc78b`. Guard suites re-run green
at assessment time: `ledger/checks` (**294 passed, 40 skipped** — was 214/24 on
2026-09-02; the growth is this contract's own rows and their guards) and the doc
guards `test_extensions_ledger_integrity` · `test_doc_consistency` ·
`test_engine_semantics_doc_guard` · `test_explainer_doc_guard` ·
`test_examples_lint_clean` (12 passed, 6 skipped, unchanged).

**What moved since the 2026-09-02 packet.** #49 seeded `ledger/rows.yaml` rows
from this contract's clauses and added the coverage tripwires; #51 closed C10's
one-sided case (ESF-010 GAP → CONFORMS, issue #46); #52 closed C14's two missing
removed-attribute controls (ESF-014 GAP → CONFORMS, issue #47); #48 opened the
C17 ruling. The contract file itself had **not** moved at assessment time.
Nothing else this packet reads has changed.

**Amended 2026-09-06, after that assessment — C17 only.** The owner ruled on #48
(narrow: keep §37's ref-free-same-repo-sources change, remove the two whose
artifacts the split left in `amplifier-bundle-attractor`), and that ruling is now
applied. So the contract file HAS moved, `ESF-000`'s `sha256:` is re-pinned as the
deliberate re-review `LEDGER-FORMAT.md` §4 requires, and `ESF-017` is CONFORMS on a
real behavioral probe. Every line below that named C17 is updated; nothing else in
this packet is re-assessed, and the 2026-09-06 assessment snapshot above (`main` at
`1dfc78b`, `ledger/checks` 294/40) stands as the history it is — that suite now runs
**300 passed, 40 skipped**, the six added being this probe's own self-checks.

---

## The bar, at a glance

| # | Condition (`PROTOCOL.md` §5) | Verdict |
|---|---|---|
| 1 | The spec is written | **MET** — unchanged. 17 Core clauses, each deriving from a named live `specs/EXTENSIONS.md` section, each carrying one executable Given/When/Then |
| 2 | A machine-checkable conformance kit exists, with ≥1 discriminating good/bad fixture pair | **MET — this is what changed.** 18 rows derive from this contract: `ESF-000` (SYNC, byte-pin) plus **one per Core clause C1–C17**, with 70 named test cites. **All 17 clause rows CONFORMS** — `ESF-017` moved OPEN-PINNED → CONFORMS when the owner ruled on #48 and C17 was narrowed to the half that is true of this repo. Coverage tripwires in `ledger/checks/test_engine_surface_matrix.py` assert the join in both directions — no clause un-rowed, no row inventing a clause. Two limits, stated below, not softened |
| 3 | At least one real implementation passes it | **MET.** One implementation — this engine — and its suites are green. The residue is down to one named sliver (C14.3's byte-cap/unreadable-attachment path); C17's — a clause whose artifacts were not in this repo at all — is closed by the #48 narrowing |
| 4 | A worked example exists end-to-end | **MET literally** — the three CI-executed capsule pipelines are a real end-to-end example. **Per clause: fully met for 7 of 17, partial for 6, absent for 4.** The corpus has not changed since 2026-09-02; the count has, because the counting rule is now stated (below) |

**Overall: conditions 1–3 are met, and 4 is met literally with named per-clause
residue.** Condition 2 was the structural blocker and it is gone. What blocks a
stamp today is **not lane work at all** — both owner-only edits named in the
2026-09-06 assessment have since been authorized and landed: C17's referent
(#48, narrowed) and the false Conformance sentence (item 2 below). What is
left is the owner's ratification of the clause text itself.

---

## The worked-example corpus

Unchanged since 2026-09-02, and stated so it is not re-counted as new: the three
graphs in `.github/capsule-pipeline/` — `capsule.dot`, `feature-capsule.dot`,
`task-runner.dot` — executed end-to-end by `.github/workflows/capsule-specify.yml`
and `capsule-implement.yml` as

```
dot-runner run … --worker coding-agent --param max_duration="$MAX_DURATION" …
```

with run artifacts uploaded as evidence. Between them they exercise `--worker`,
`--param` into `max_pipeline_duration="$max_duration"`, `must_write=`,
`goal_gate=`, `llm_provider="openai"` on a node, and the whole run directory.

**The counting rule, stated once.** A clause counts as **fully met** when a
shipped, CI-executed graph exercises the surface the clause governs. **Partial**
means one half of the clause is exercised and the other is not. **Absent** means
no shipped graph touches it. A refusal path that no green run can demonstrate
(C8) is counted absent, not excused. Under that rule: fully met — C3, C4, C6, C7,
C11, C12, C15, C17 (**8**); partial — C1, C5, C9, C14, C16 (**5**); absent — C2,
C8, C10, C13 (**4**). C17 moved partial → fully met on this packet's own stated
reason for calling it partial: *"the shipped bundle is itself the example, but
nothing checks the clause."* Something checks it now — `test_row_esf_017` scans the
shipped bundle surface in CI — and the shipped bundle was always the end-to-end
artifact. The 2026-09-02 packet's "11 of 17" folded partials in
inconsistently (C1's partial counted against, C5/C9/C14/C16's counted for); the
corpus is identical, only the arithmetic is honest now.

**A named erosion, still open.** Several EXTENSIONS entries cite exemplars under
`examples/` (`examples/pipelines/…`, `examples/patterns/task-runner.dot`,
`examples/objective/objective-runner.dot`). **That directory is still not in the
tree.** `test_examples_lint_clean.py` skips accordingly — by design, so not a red
test — but the corpus those entries point at is gone. Any freeze that leans on
those citations for condition 4 would be leaning on nothing.

---

## Per-clause evidence

Condition 2 is now the ledger's own column: each clause has exactly one row, and
that row carries the cites the 2026-09-02 packet listed inline. Read
`ledger/rows.yaml` for the cite sets; they are AST-verified to resolve.
Condition 4 — the worked example, or `—`.

| Clause | 2 — ledger row | 4 — worked example |
|---|---|---|
| **C1** worker names / selection / default ladder | `ESF-001` CONFORMS · 3 cites · control: `…runs_via_direct_unchanged` | **Partial.** `--worker coding-agent` runs in both capsule workflows. **No shipped `.dot` uses the `worker=` node attribute** |
| **C2** `status.json` verdict channel + spawn envelope | `ESF-002` CONFORMS · 4 cites · control: `test_sf005_matching_status_json_is_noop…` | **—** No shipped graph demonstrates a child writing a divergent `status.json` end-to-end |
| **C3** `must_write=` | `ESF-003` CONFORMS · 4 cites · control: `test_case6_no_attribute_control` | **Yes** — `capsule.dot`'s `critique` / `critique_b` and `task-runner.dot`'s postmortem node declare `must_write=` |
| **C4** graph-level `$name` params | `ESF-004` CONFORMS · 4 cites · pair: `…absent_param_fails_loud` / `…supplied_param_resolves` | **Yes** — all three capsule graphs carry `max_pipeline_duration="$max_duration"`, supplied by `--param` in CI |
| **C5** subscription providers + rung-4 default model | `ESF-005` CONFORMS · 4 cites · asserted both directions | **Partial.** `capsule.dot`'s `critique_b` declares `llm_provider="openai"`. **Neither subscription provider appears in a shipped graph** |
| **C6** fuse at node granularity | `ESF-006` CONFORMS · 3 cites · control: `…finishing_within_budget_completes_normally` | **Yes** — the capsule workflows set the fuse per invocation and their failure path reports it tripping |
| **C7** provider preflight | `ESF-007` CONFORMS · 4 cites · **no counter-case cited** (see below) | **Yes** — every capsule run passes the preflight before its first node |
| **C8** refusal not degradation | `ESF-008` CONFORMS · 3 cites · control: `test_known_shapes_still_dispatch_correctly` | **—** No shipped graph deliberately trips either refusal (correctly — they are refusals) |
| **C9** `shape=folder` / `dot_file=` | `ESF-009` CONFORMS · 3 cites · lazy-admission half cited directly | **Partial** — `fixtures/parent_with_child.dot` + `child_pipeline.dot` is the only end-to-end sub-pipeline; no shipped pipeline uses `dot_file=` |
| **C10** session and thread scoping | `ESF-010` CONFORMS · 5 cites · **discriminating pair** — same four nodes, same `thread_id`, sibling vs sequential topology (#51) | **—** The sibling-branch case runs through the real engine, hermetically, but no shipped graph turns on it |
| **C11** no `reasoning_effort` default | `ESF-011` CONFORMS · 4 cites · absence asserted on all three paths | **Yes** — every capsule run omits the attribute and no default appears |
| **C12** fail-closed goal gate | `ESF-012` CONFORMS · 4 cites · scoping control: `test_fc002_non_goal_gate…` | **Yes** — all three capsule graphs declare `goal_gate` |
| **C13** `outcome=` → `preferred_label` first | `ESF-013` CONFORMS · 3 cites · both rungs pinned | **—** Shipped graphs condition only on `outcome=success` / `outcome=fail`; **no shipped edge turns on a `preferred_label`** |
| **C14** additive graph vocabulary | `ESF-014` CONFORMS · 14 cites · **all five sub-items now paired** (#52 added §14 and §19's removed-attribute controls) | **Partial** — `fixture_tool_reads_param.dot` and `fixture_human_gate.dot` are end-to-end for §21/§19; §14, §18, §20 have no shipped example |
| **C15** run directory as audit trail | `ESF-015` CONFORMS · 4 cites · **no counter-case cited**, and the row's quote covers item 1 only (see below) | **Yes** — capsule runs upload the run directory as evidence |
| **C16** validation narrowing + lint | `ESF-016` CONFORMS · 4 cites · control: `…custom_handler_with_tool_command_is_not_blocked` | **Partial** — `test_examples_lint_clean.py`, the corpus-sweep arm, **skips: `examples/` is absent** |
| **C17** ref-free same-repo sources | `ESF-017` CONFORMS · probe `test_row_esf_017` scans the bundle surface and fails naming file:line · 4 in-memory self-checks discriminate forbidden / exempt / ref-free · narrowed by the #48 ruling | **Yes** — the shipped bundle is the example, and CI now checks it |

---

## Exactly what is missing

**1. C17's ruling — CLOSED (#48), and it was never a coverage gap.** As
originally written, C17's subject was **another repository's bundle**: it derives
from `specs/EXTENSIONS.md` §37, whose three changes landed on the attractor
bundle, and after the split none of the artifacts behind two of them was here.
The owner ruled to **narrow**: those two items are removed — they may become an
attractor-side contract's clauses, which is not this repo's to author — and §37's
third change, ref-free same-repo module and skill sources, becomes the whole of
C17, retitled and keeping its Given/Then. That half was always true here and is
testable, so `ESF-017` is CONFORMS on `test_row_esf_017`, which scans the bundle
surface for a `source:` naming this repo with a git ref and fails naming
file:line. One class is allow-listed, explicitly and with §37's own words:
`session.orchestrator` sources, which §37 *"keeps deliberately"*; C17.2 states
that exemption in the contract, and a self-check fails if that sentence ever
leaves §37. **What this closes:** condition 3's C17 residue, and condition 4's.
**What it does not close:** nothing — no residue moved from C17 to elsewhere.

**2. ~~The contract's own Conformance section is factually false.~~ RESOLVED**
in the PR carrying this update. It said:

> **No `ledger/rows.yaml` row derives from this contract yet**, and none may
> until it is stamped; seeding them is a separate lane's work.

#49 falsified that sentence on the owner's instruction, following this packet's
own recommended sequence. The owner authorized the correction on 2026-09-06 and
it has landed: the Conformance section now states today's truth — 18 rows,
`ESF-000` plus `ESF-001`…`ESF-017`; drift fails naming this contract; coverage
tripwires assert the clause↔row join in both directions — and states, rather
than softens, the two real limits (16 rows are existence-indexed, not semantic;
`ESF-017` is OPEN-PINNED on #48). `ledger/rows.yaml`'s section header is updated
to match. **No clause text moved and nothing was stamped**; the contract remains
DRAFT. `ESF-000`'s `sha256` is recomputed for the new bytes as the deliberate
re-review its own note requires; its quote is untouched because the status line
did not move.

**3. The kit's semantic reach is bounded, and the bound is real.** Sixteen of the
seventeen clause rows use `assertion.kind: indexed`, which proves the cited test
**exists** (AST parse), not that it still asserts the claim
(`LEDGER-FORMAT.md` §8). A cited test that is gutted while keeping its name goes
unnoticed. Only `ESF-000` (byte-pin) and `ESF-017` (a behavioral probe since the
#48 narrowing — it was a state-pin while the ruling was open) are executable
probes. This does not unmake condition 2 — the pairs are named and falsifiable by
reading — but a stamp should not be read as claiming more than it does.

**4. Two rows are thinner than their neighbours.**
`ESF-007` (C7) cites four refusal-side tests and no serviceable control, though
one exists uncited one file away
(`test_provider_preflight.py::test_orchestrator_execute_serviceable_graph_runs_unaffected`).
`ESF-015` (C15) likewise cites no counter-case, and its quote covers item 1 of
four; items 2–4 are covered by named suites but not separately rowed.

**5. Clauses with no worked end-to-end example.** C2, C8, C10, C13 outright; and
half of C1 (the `worker=` node attribute), C5 (subscription providers), C9
(`dot_file=` outside a test fixture), C14 (§14, §18, §20), C16 (the `examples/`
sweep arm skips). C17 has left this list: the shipped bundle is the example and
`test_row_esf_017` checks it in CI.

**6. C14.3's last sliver.** `attachments_inline` / `attachments_ref`, globs, and
the missing-glob case are asserted, and #52 added the removed-attribute control.
Still unasserted: the byte-cap truncation marker and the unreadable-attachment
skip-with-warning (`handlers/human.py:118-121`, `:145`).

**7. ~~One open question the contract deliberately refuses to answer.~~
RESOLVED — ruled 2026-09-06, residue deleted.** §35's `report_outcome`
**ordering barrier** was the one open question: WAVE 5 (2026-08-30) removed the
tool module and the `metadata.report_outcome` transport, but
`agent_session.py` still gated batch execution on a tool named
`report_outcome`, the EXTENSIONS body still asserted the barrier as behavior,
and two orphan artifacts survived. **The owner ruled: residue, not live
behavior — delete it, no new clause.** Landed: the post-batch gate and the
sequential-batch barrier are gone (ordinary canonical §3.2 batch semantics
restored); the orphan `report_outcome_convergence.dot` fixture is deleted;
`test_spawn_report_outcome_transport.py` is renamed
`test_spawn_status_file_transport.py` (its content has proven the LIVE
status-file channel since WAVE 4 — only its name was residue);
`loop-amplifier-agent`'s dead `report_outcome` completion parameter and its
unreachable metadata branch are deleted. `modules/tool-report-outcome/`, which
this packet previously listed alongside them, was in fact already fully removed
by WAVE 5 — the residue was narrower than stated here, and that correction is
part of the record. A hermetic guard
(`modules/loop-pipeline/tests/test_report_outcome_residue_guard.py`) now fails
naming `file:line` if the name re-enters live code, permitting comments and
docstrings so the historical record survives; it is RED-proofed against the
pre-change tree, where it names 9 live occurrences. The contract's Reserved
entry records the ruling; `specs/EXTENSIONS.md` §35 carries the dated,
append-only addendum, **including one named residual left deliberately out of
scope** — `_synthesize_outcome_marker`'s `[report_outcome: …]` transcript
marker prefix, which since WAVE 5 names a call that cannot have happened.
Changing transcript vocabulary is a separate call; it is exempted in the guard
individually, by exact snippet and reason, so it stays visible.

---

## Recommendation

Do not stamp yet. The honest sequence, with what has landed struck out:

1. **Ratify the clause text (owner) — still the only step needing owner attention today**, and now the whole of it. ~~C17's referent~~ — **landed** (#48, narrowed). ~~The false Conformance sentence~~ — **landed**, item 2 above.
2. ~~Seed `ledger/rows.yaml` rows from these clauses~~ — **landed** (#49, #51, #52). Condition 2 is met.
3. Close condition 4's per-clause residue — the nine entries in item 5 above. Lane work, no owner input needed.
4. ~~Rule on the `report_outcome` ordering barrier, then either add a clause or delete the residue~~ — **ruled and landed** (2026-09-06: residue, no clause). Item 7 above.
5. Then, and only then, the stamp.

**What a stamp today would claim.** That the clause text is the owner's; that
drift on **all 17** clauses is machine-visible and fails naming this contract; that
one real implementation passes, green.

**What it would not claim.** That every clause has a worked end-to-end example —
8 of 17 do. That the cited tests still assert what they were cited for — 16 rows
prove existence, not semantics (item 3). C17 is no longer on this list: it has
been ruled on (#48), narrowed to the half that is true of this repo, and asserted
by an executable probe.

**One expected consequence, so it is not a surprise.** The moment the status line
changes, `ESF-000` goes red on both its hash and its quote. That is the mandatory
full-ledger re-review firing exactly when `LEDGER-FORMAT.md` §4 says it should —
not a defect, and not something to fix by bumping the hash.

---

## Changelog

- **2026-09-07 — STAMPED. Record only; no re-assessment, no verdict moved.** The
  owner ruled *"stamp"* and `contracts/engine-surface.v1.md` went DRAFT → FROZEN in
  one commit: H1 and `status:` stamped, Changelog entry written, header pointer added
  saying the file now changes only by proposal. `ESF-000`'s `sha256` **and** its quote
  are re-pinned — both tripwires fired exactly as this packet said they would ("One
  expected consequence, so it is not a surprise"), the full-ledger re-review was done
  against unchanged clause text, and no clause row moved disposition. This entry is
  the only thing added to this packet besides the banner under its title: the
  assessment, the per-clause table, "Exactly what is missing" and the Recommendation
  are untouched, deliberately, so the evidence the owner ruled on stays readable as it
  was. **What the stamp did not close:** residue items 3 (existence-indexed rows), 4
  (`ESF-007`/`ESF-015` thin cites), 5 (clauses with no worked end-to-end example) and
  6 (C14.3's byte-cap / unreadable-attachment sliver). They remain this packet's open
  work and are lane work, needing no owner input.
- **2026-09-06 (third entry, same day) — two residuals closed, no re-assessment.**
  The Freeze Bar verdicts above are unchanged; this entry records only that two
  named residuals landed, after the C17 narrowing recorded immediately below. **Item 2** (the contract's false Conformance sentence)
  was corrected on the owner's authorization — the section now states 18 derived
  rows with tripwires both directions, and states its two real limits;
  `ledger/rows.yaml`'s section header and `ESF-000`'s `sha256` are updated to
  match, the latter as the deliberate re-review its own note requires (the quote
  is untouched — the status line did not move). **Item 7** (the `report_outcome`
  ordering barrier) was **ruled by the owner: residue, not live behavior** — the
  gate, the barrier, the orphan fixture and a dead completion parameter are
  deleted, the surviving transport test is renamed to the channel it actually
  proves, and a RED-proofed hermetic guard fails naming `file:line` if the name
  returns. One residual is deliberately left open and named rather than absorbed:
  the `[report_outcome: …]` transcript marker prefix. Correction to this packet's
  own prior text: `modules/tool-report-outcome/` was listed as surviving residue
  in item 7; it was already fully deleted by WAVE 5 and was never in the tree.
  **Still no stamp requested; the contract remains DRAFT.**
- **2026-09-06 — C17 lines only, after the #48 ruling.** Applied on top of the
  refresh below, touching **nothing but the lines that named C17**. The owner ruled
  to narrow: §37's two attractor-repo changes leave the clause, its third — ref-free
  same-repo module and skill sources — becomes the whole of it. Consequences recorded
  here: `ESF-017` OPEN-PINNED → **CONFORMS** on `test_row_esf_017`, so condition 2 now
  reads 17 of 17 clause rows CONFORMS; condition 3's C17 residue is closed; condition
  4's per-clause count moves **7 fully / 6 partial → 8 fully / 5 partial** (absent
  unchanged at 4), C17 moving on this packet's own stated reason for having called it
  partial; residue items 1 and 5 updated; the owner's remaining clause-text edits drop
  from three to two. `ledger/checks` runs **300 passed, 40 skipped** (was 294/40 — the
  six new ones are the probe's self-checks). The contract file has moved, so
  `ESF-000`'s hash is re-pinned; its quote is untouched because version, status and
  date did not move — a pre-freeze correction to a DRAFT mints no version. Still no
  stamp requested; the contract remains DRAFT.
- **2026-09-06 — refreshed to post-seeding truth.** Re-assessed all four
  conditions against `main` at `1dfc78b`. **Condition 2 moved NOT MET → MET**:
  #49 seeded 18 rows (`ESF-000` plus one per Core clause) with coverage tripwires
  both directions, #51 closed C10 (ESF-010 GAP → CONFORMS, issue #46), #52 closed
  C14 (ESF-014 GAP → CONFORMS, issue #47); 16 of 17 clause rows CONFORMS, C17
  OPEN-PINNED at #48. Condition 4's corpus is unchanged; its count was recomputed
  under a stated rule (7 fully / 6 partial / 4 absent, replacing "11 of 17").
  Per-clause table re-pointed at the ledger rows rather than duplicating cite
  lists. New residuals recorded: the contract's Conformance section is now
  factually false and needs the owner's edit; 16 rows are existence-indexed, not
  semantic; `ESF-007` and `ESF-015` cite no counter-case. Still no stamp
  requested; the contract remains DRAFT.
- **2026-09-02 — initial packet.** Assessed all four Freeze Bar conditions across
  the 17 Core clauses of `contracts/engine-surface.v1.md`. Verdict: **bar not
  met**, condition 2 blocking. No stamp requested; the contract remains DRAFT.
