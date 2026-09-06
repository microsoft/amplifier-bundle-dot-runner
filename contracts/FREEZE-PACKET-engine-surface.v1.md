# FREEZE PACKET — `contracts/engine-surface.v1.md`

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
C17 ruling. The contract file itself has **not** moved — `ESF-000` pins its bytes
and is green. Nothing else this packet reads has changed.

---

## The bar, at a glance

| # | Condition (`PROTOCOL.md` §5) | Verdict |
|---|---|---|
| 1 | The spec is written | **MET** — unchanged. 17 Core clauses, each deriving from a named live `specs/EXTENSIONS.md` section, each carrying one executable Given/When/Then |
| 2 | A machine-checkable conformance kit exists, with ≥1 discriminating good/bad fixture pair | **MET — this is what changed.** 18 rows derive from this contract: `ESF-000` (SYNC, byte-pin) plus **one per Core clause C1–C17**, with 70 named test cites. 16 clause rows **CONFORMS**; `ESF-017` is **OPEN-PINNED** on the owner ruling at #48. Coverage tripwires in `ledger/checks/test_engine_surface_matrix.py` assert the join in both directions — no clause un-rowed, no row inventing a clause. Two limits, stated below, not softened |
| 3 | At least one real implementation passes it | **MET.** One implementation — this engine — and its suites are green. The residue is down to one named sliver (C14.3's byte-cap/unreadable-attachment path) plus C17, whose artifacts are not in this repo at all |
| 4 | A worked example exists end-to-end | **MET literally** — the three CI-executed capsule pipelines are a real end-to-end example. **Per clause: fully met for 7 of 17, partial for 6, absent for 4.** The corpus has not changed since 2026-09-02; the count has, because the counting rule is now stated (below) |

**Overall: conditions 1–3 are met, and 4 is met literally with named per-clause
residue.** Condition 2 was the structural blocker and it is gone. What blocks a
stamp today is **not lane work** — it is two owner-only edits to the clause text
(C17's referent, and a Conformance sentence that is now factually false), both of
which fall inside the single step the owner already owns.

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
C11, C12, C15 (**7**); partial — C1, C5, C9, C14, C16, C17 (**6**); absent — C2,
C8, C10, C13 (**4**). The 2026-09-02 packet's "11 of 17" folded partials in
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
| **C17** bundle composition | `ESF-017` **OPEN-PINNED** · probe `test_row_esf_017` pins today's state in all three directions · ruling at #48 | **Partial** — the shipped bundle is itself the example, but nothing checks the clause |

---

## Exactly what is missing

**1. C17 needs an owner ruling (#48) — and it is not a coverage gap.** As
written, C17's subject is **another repository's bundle**. It derives from
`specs/EXTENSIONS.md` §37, whose three changes landed on the attractor bundle;
after the split none of those artifacts is here — this repo's root `bundle.md`
has no `context:` key, there is no `agents/` directory, and its only same-repo
self-pin is a `session.orchestrator` source, exactly the class §37 keeps
deliberately. Re-scope to this repo's real bundle surface, move the clause to the
contract governing `amplifier-bundle-attractor`, or drop it: all three are
contract edits, and `PROTOCOL.md` §5 makes that the owner's call. `ESF-017`'s
probe pins the current state so the ruling cannot be quietly pre-empted — port
the expert agent in, add a `context:` key, or let a module source re-acquire a
ref, and the row goes red naming #48.

**2. The contract's own Conformance section is now factually false.** It says:

> **No `ledger/rows.yaml` row derives from this contract yet**, and none may
> until it is stamped; seeding them is a separate lane's work.

#49 falsified that sentence on the owner's instruction, following this packet's
own recommended sequence. It needs the owner's edit in the same pass as
ratification. It is recorded here as a residual rather than silently absorbed,
and `ledger/rows.yaml`'s own section header records it too.

**3. The kit's semantic reach is bounded, and the bound is real.** Sixteen of the
seventeen clause rows use `assertion.kind: indexed`, which proves the cited test
**exists** (AST parse), not that it still asserts the claim
(`LEDGER-FORMAT.md` §8). A cited test that is gutted while keeping its name goes
unnoticed. Only `ESF-000` (byte-pin) and `ESF-017` (state-pin) are executable
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
sweep arm skips), C17 (nothing checks the bundle).

**6. C14.3's last sliver.** `attachments_inline` / `attachments_ref`, globs, and
the missing-glob case are asserted, and #52 added the removed-attribute control.
Still unasserted: the byte-cap truncation marker and the unreadable-attachment
skip-with-warning (`handlers/human.py:118-121`, `:145`).

**7. One open question the contract deliberately refuses to answer.** Unchanged.
§35's `report_outcome` **ordering barrier**: WAVE 5 (2026-08-30) removed the tool
module and the `metadata.report_outcome` transport, but
`modules/loop-agent/amplifier_module_loop_agent/agent_session.py` still gates
batch execution on a tool named `report_outcome`, the EXTENSIONS body still
asserts the barrier as behavior, and
`modules/loop-pipeline/tests/fixtures/report_outcome_convergence.dot` plus
`modules/loop-amplifier-agent/tests/test_spawn_report_outcome_transport.py`
survive. WAVE 5's deletion list names none of them. Whether that barrier is live
behavior or unreachable residue is an **owner ruling**, not a lane's call, so
`engine-surface.v1` states no clause about it and reserves the name.

---

## Recommendation

Do not stamp yet. The honest sequence, with what has landed struck out:

1. **Ratify or amend the clause text (owner) — still the only step needing owner attention today.** It now carries three things: the clause text itself, C17's referent (#48), and the false Conformance sentence in item 2 above.
2. ~~Seed `ledger/rows.yaml` rows from these clauses~~ — **landed** (#49, #51, #52). Condition 2 is met.
3. Close condition 4's per-clause residue — the nine entries in item 5 above. Lane work, no owner input needed.
4. Rule on the `report_outcome` ordering barrier, then either add a clause or delete the residue.
5. Then, and only then, the stamp.

**What a stamp today would claim.** That the clause text is the owner's; that
drift on 16 of 17 clauses is machine-visible and fails naming this contract; that
one real implementation passes, green.

**What it would not claim.** That every clause has a worked end-to-end example —
7 of 17 do. That the cited tests still assert what they were cited for — 16 rows
prove existence, not semantics (item 3). That C17 has been ruled on — it has not,
and stamping the clause as written would freeze a clause whose subject is not in
this repo.

**One expected consequence, so it is not a surprise.** The moment the status line
changes, `ESF-000` goes red on both its hash and its quote. That is the mandatory
full-ledger re-review firing exactly when `LEDGER-FORMAT.md` §4 says it should —
not a defect, and not something to fix by bumping the hash.

---

## Changelog

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
