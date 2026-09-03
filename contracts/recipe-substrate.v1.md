# CONTRACT: recipe-substrate engine changes, v1

> **DRAFT — PAUSED. The Freeze Bar is not met.**
>
> This contract is carried here under the Converge layout so it is reviewable in place, not
> because it is ready. `docs/PROTOCOL.md` §5's Freeze Bar demands, and this contract still owes:
>
> 1. a **discriminating good/bad fixture pair** — a graph that conforms and a graph that does not,
>    where the difference is exactly the clause under test;
> 2. a **passing implementation** of clauses C1–C5;
> 3. a **worked example** exercising the contract end to end.
>
> **Paused by owner decision, 2026-09-01.** Until those three land and the owner stamps the status
> line above, nothing in this file governs anything: no `ledger/rows.yaml` row derives from it, and
> no lane is held to it. Its clause text below is the proposal, not a ruling.
>
> **Vocabulary note.** The clause text was authored 2026-09-01, before the ledger was reshaped to
> the Converge format. Its planned-row table still speaks the old matrix vocabulary
> (`CONFORM`, `EXTENSION`, `ledger.extensions:`). The current vocabulary is
> `CONFORMS` / `DIVERGED` / `GAP` / `VIOLATION` / `OPEN-PINNED` / `NOT-ASSERTABLE` / `EXCLUDED`
> with `decision.extensions:` — and `EXTENSION` is no longer a disposition at all, because an
> extension is not a clause of the external contract (see `ledger/rows.yaml`'s header). The
> proposal text is left as the owner wrote it rather than silently modernised; translating it is
> part of the work this contract still owes before it can be stamped.

---

- **id:** `CONTRACT-recipe-substrate.v1`
- **version:** 1.0.0
- **status:** **DRAFT** — becomes FROZEN only when the owner stamps it (edit this line + Changelog).
- **date:** 2026-09-01
- **owner:** maintainer
- **repo at freeze time:** `microsoft/amplifier-bundle-dot-runner` (ships as `contracts/recipe-substrate.v1.md`; was `CONTRACT-recipe-substrate.v1.md` at repo root before the Converge layout landed)
- **scope:** clauses C1–C5 (five engine changes) + the wave-close reconcile event (§3.6). Nothing else.
- **spec baseline:** `contracts/external/attractor-spec-canonical.md` @ `strongdm/attractor` `fb57a55` (2090 lines). Every `§x.y:NNN` below is a line in THAT file, verified 2026-09-01. Decision records = `specs/EXTENSIONS.md` (currently §1–§44). Conformance ledger = `ledger/rows.yaml` (checks `ledger/checks/test_spec_conformance_matrix.py`; highest id in use `ATX-M-124`).

**Why.** The committed comparison harness (`recipe-ports/`, run `20260901T023905Z`) measured the dot-runner engine against the recipes engine over four ported flows and found exactly three substrate gaps, not a missing product: no native fan-out (pair 3, B 70.0s vs A 50.7s, sequential counter-cycle), no scoped child context (pair 4, `PARENT_ONLY_SECRET` reached both children — RESULTS.md §3.2, "the one clear recipes win"), and headless gate ergonomics that require a held-open FIFO to park (RESULTS.md §3.4). The maintainer decision recorded in work item `attractor-4c7` (2026-09-01) is therefore: **defer the recipes-wrapper, land five spec-verified engine changes.** This contract is what the build lanes are held to.

---

## C1 — `foreach=` on the parallel construct (`shape=component`)

**Binding behavior**

1. `foreach="<context-key>"` on a `shape=component` node (Appendix B:2046 `component`→`parallel`) expands N runtime branches from the list at that key; N=len(list). New Appendix-A node attribute.
2. Join is **only** `join_policy=wait_all|first_success` (§4.8:814 attr name; §4.8:846-851 table). `k_of_n`/`quorum` stay dead (EXTENSIONS §18 `status: REMOVED`). `max_parallel` (§4.8:815, default 4) still bounds concurrency.
3. ★ Each branch gets an isolated context clone and **is never merged back** (§3.8:577). Collect flows ONLY through the handler's outcome `context_updates`; the handler also writes `parallel.results` (mirrors §4.8:825).
4. Per-item keys are written into the branch clone only, under the spec's reserved prefix (§5.1:1070): `work.item`, `work.index`, `work.total`.
5. Optional `collect="<context-key>"`: an ordered list of `{index, item, status}` records, index-ordered (never completion-ordered). Under `first_success`, branches not run are recorded `status="not_run"`. Absent `collect=`, only `parallel.results` is written.
6. **No edge-level fan-out.** Non-component nodes keep single-edge selection (§3.3:408-418; matrix `ATX-M-109` / ledger `ATX-10` / T0-4). Any change that re-selects >1 edge for a non-component node violates this clause.

**Decided micro-calls (were open; decided here)**

- **Empty list (`N=0`)** is NOT an error. Zero branches execute; `collect` is written as `[]` and `parallel.results` as an empty serialization (the key is always present, never missing). Join outcome follows the spec pseudocode literally: `wait_all` → `SUCCESS` (`fail_count==0`, §4.8:831-835); `first_success` → `FAIL` with `failure_reason` naming the empty source key (`success_count==0`, §4.8:837-841). A `foreach.empty` record is written to `trace.jsonl`.
- **Missing / non-list source key** is a distinct, fail-loud case: `Outcome(FAIL)` naming the key. Absent ≠ empty.

**Acceptance (lane gate executes this)**

- *Given* a graph with `component` node `fan [foreach="work.items", collect="context.results", join_policy="wait_all"]` and `context.work.items` = a 3-item list, *When* run with the repo's in-test stub backend pattern (`modules/loop-pipeline/tests/test_parallel.py::Mock*Backend`), *Then* exactly 3 branch subgraph executions occur; a write performed inside any branch to key `context.leak` is absent from the parent context after the join; `context.results` has 3 entries ordered `index` 0,1,2; and `parallel.results` has 3 entries.
- *Negative A:* same graph, 0-item list → `SUCCESS`, `context.results == []`, 0 branch executions, `trace.jsonl` contains `foreach.empty`.
- *Negative B:* same graph with `join_policy="first_success"` and 0-item list → `FAIL`, `failure_reason` contains `work.items`.
- *Negative C:* source key absent → `FAIL` naming the key (asserted distinct from Negative A).
- *Guard:* a non-component node with two condition-matching edges still selects exactly one (existing T0-4/`ATX-M-109` assertion must still pass unmodified).

---

## C2 — Scoped child context for `folder` / `dot_file=` nodes

**Binding behavior**

1. New opt-in node attribute `child_context="<key|prefix.*>[,…]"` on `shape=folder` / `dot_file=` nodes (EXTENSIONS §10:249-267).
2. **Default is unchanged**: attribute absent → the child receives the full clone exactly as today. No shipped graph changes behavior.
3. When present, the child's initial context contains ONLY the listed keys/prefix globs, plus keys the engine itself mandates (`graph.*` mirroring, §3.1:331).
4. Spec-silent by construction: §9.4:1583-1585 defines sub-pipeline composition and says nothing about context inheritance; §3.8:577's clone mandate is scoped to `parallel`/`parallel.fan_in` only. This is an EXTENSION disposition, not a divergence.
5. Name must not collide with §2.6 or Appendix A (:2004-2022) — verified: `child_context` is unused there and does not shadow `stack.child_dotfile` / `stack.child_workdir` (:1997-1998).
6. Child→parent handoff is unchanged by this clause (`outputs=`, EXTENSIONS §17).

**Acceptance**

- *Given* a parent graph setting `context.parent_only="SECRET"` and `context.shared="OK"`, and a folder node `child [dot_file="child.dot", child_context="context.shared"]`, *When* the child probes both keys, *Then* `context.shared` resolves and `context.parent_only` is absent.
- *Given* the same graph with `child_context` REMOVED, *Then* both keys resolve (default-unchanged proof, asserted in the same test module).
- *Ledger:* new EXTENSIONS section extending §10, explicitly re-stating "additive and non-shadowing" (§10:261) for the new attribute.

---

## C3 — Headless gate park / approve / resume

**Binding behavior**

1. New interviewer mode `--on-human-gate park` on `dot-runner run` (joins `auto-approve|console|fail`): the run persists the pending question to the run dir and exits cleanly with `checkpoint.json` `run_state` recording a parked gate, without blocking on stdin.
2. New CLI verb `dot-runner approve <run_dir> [--choice <key>|--message <text>]`, consumed by the existing `dot-runner resume <run_dir>`.
3. Timeout semantics are honored verbatim: on timeout use `human.default_choice` if set, else `Outcome(RETRY)` (§6.5:1361-1367; §4.6:752-758). **Never silent auto-approve.**
4. Host-level only; spec-invisible. §6.1:1244 names "a programmatic queue" as a legitimate Interviewer frontend, and §9.5:1602-1603 already mandates `GET /questions` + `POST /…/answer` operability.
5. Refusal semantics unchanged: a skipped/refused gate remains `Outcome(FAIL, "human skipped interaction")` (§4.6:760-761).
6. No new graph vocabulary: zero node/edge/graph attributes are added by this clause.

**Acceptance**

- *Round trip:* *Given* `fixture_human_gate.dot`, *When* `dot-runner run … --on-human-gate park`, *Then* the process exits without blocking, `checkpoint.json` shows the pre-gate nodes complete and the gate pending, and no post-gate node appears in `trace.jsonl`. *When* `dot-runner approve <run_dir> --choice A` then `dot-runner resume <run_dir>` (a NEW process), *Then* the run completes, each pre-gate node appears exactly once in `trace.jsonl`, and the selected choice is recorded in `human.gate.selected` (§4.6:773).
- *Timeout with default:* gate node with `human.default_choice` set + an expired `timeout` → the default choice is taken and recorded; *without* `human.default_choice` → `Outcome(RETRY)`, asserted as RETRY (not SUCCESS, not silent approval).
- *Deny:* `approve --choice` naming the deny target → post-deny nodes provably absent from `trace.jsonl`.

---

## C4 — Rate limiting / backoff (operational config only)

**Binding behavior**

1. Engine-level operational config: max concurrent LLM calls, minimum inter-call delay, and 429-aware backoff. Configured out-of-band (CLI flag / config file / env), never in `.dot`.
2. **Zero graph-language surface.** No new node/edge/graph attribute; nothing added to Appendix A by this clause (§1.2:35 — the DOT file describes the graph, not engine operations).
3. Must NOT repurpose `max_retries` / `default_max_retries`: §3.6:528-538 governs retry attempts and delay only, and §3.6:562 already declares HTTP 429 retryable. Retry counts observed by a graph must be unchanged by throttling.
4. Throttling delays are not retries: a call delayed by the limiter consumes no retry budget and emits no `stage_retrying` event.

**Acceptance**

- *Given* a graph whose node has `max_retries=2` and a backend stub that returns HTTP 429 once, *When* run with the limiter enabled at concurrency 1, *Then* the node still records exactly the same attempt count as with the limiter disabled, and no `.dot` attribute was required to enable it.
- *Given* a `component` node fanning out 6 branches with limiter concurrency 2, *Then* observed max in-flight backend calls ≤ 2 and the run still completes.
- *Guard:* `git grep` over `contracts/external/…:2004-2033` shows no new Appendix-A row from this clause; the ledger row asserts `assertion.kind: absence` for graph-surface leakage.

---

## C5 — Hexagon-without-fail-edge WARNING lint

**Binding behavior**

1. New lint rule (§7.1 `Diagnostic`, `severity="WARNING"`, lint-only, no runtime change): a `shape=hexagon` / `wait.human` node with **no** outgoing edge carrying `condition="outcome=fail"` **and** no `retry_target` draws a WARNING.
2. The message must name the routing consequence concretely: gate refusal returns FAIL (§4.6:760-761), the main loop still calls `select_edge` (§3.2:388-393), and with no condition edge the plain highest-weight/lexically-first edge is taken (§3.3:416-418) — §3.7:566-571's failure ladder is reached only when `select_edge` returns NONE.
3. This behavior is **CONFORMANT**. The mitigation is lint + teaching. Any routing change is out of scope and violates this clause.
4. Severity is WARNING: it must never block execution (§7.1:1375 reserves refusal for ERROR).

**Acceptance + false-positive bound**

- *Fires:* hexagon with two plain `label=`-only edges → exactly 1 WARNING naming the node id and the winning plain edge target.
- *Does NOT fire (the calibration that matters):* (a) hexagon with an `outcome=fail` condition edge → 0 diagnostics; (b) hexagon with `retry_target` set → 0 diagnostics; (c) non-hexagon node with no fail edge → 0 diagnostics.
- *Repo calibration (honest baseline, verified 2026-09-01):* the lane enumerates every hexagon in `git ls-files '*.dot'` and asserts the fire set **exactly equals** the no-fail-edge/no-retry_target subset. That subset is currently non-empty — `modules/loop-pipeline/tests/fixtures/spec_human_gate.dot::review_gate` and `modules/pipeline-runner/tests/fixtures/fixture_human_gate.dot::approve_gate` both lack a fail edge, so both are **expected true positives**, not false positives. The "0 fires on shipped graphs" figure holds only for the WITH-fail-edge/retry_target subset (currently 0 members). The lane records the full inventory in the ledger entry.

---

## Conformance kit (per clause, all mechanically checkable)

| Clause | Tests (named by pattern) | Ledger | Matrix row | Docs |
|---|---|---|---|---|
| C1 | `modules/loop-pipeline/tests/test_foreach_*.py` (expansion, isolation, collect order, empty-list ×2, missing-key) | new EXTENSIONS §45 (`foreach=`/`collect=`, Appendix-A addition) | new `ATX-M-125` (`EXTENSION`, `ledger.extensions: 45`, `assertion.kind: probe`) + `ATX-M-109` must stay green | `context/dot-reference.md` fan-out row; Appendix-A delta table |
| C2 | `modules/loop-pipeline/tests/test_child_context_scope*.py` (scoped + default-unchanged) | EXTENSIONS §46, extending §10:249-267 | new `ATX-M-126` (`EXTENSION`, §9.4 anchor) | folder-node doc row: default = full clone |
| C3 | `modules/pipeline-runner/tests/test_park_approve_resume*.py`; `test_cli_on_human_gate.py` extended for `park` | EXTENSIONS §47 (host-level, spec-invisible) | new `ATX-M-127` (`CONFORM`, §6.5 + §4.6 anchors, timeout probe) | CLI docs: `run --on-human-gate park` → `approve` → `resume` |
| C4 | `modules/loop-pipeline/tests/test_rate_limit*.py` (429 backoff, concurrency cap, retry-budget non-interference) | EXTENSIONS §48 | new `ATX-M-128` (`EXTENSION`, §3.6:562 anchor, `absence` assertion on graph surface) | ops/config doc; explicitly "not a `.dot` attribute" |
| C5 | `modules/loop-pipeline/tests/test_lint_human_gate_fail_edge.py` (fire, 3 no-fire cases, repo inventory) | EXTENSIONS §49 (lint-only, WARNING, no routing change) | new `ATX-M-129` (`CONFORM`, §3.3:416-418 anchor) | lint-rule table row + Human Gate pattern teaching note |

**Wave-level reconcile (the close condition).** Re-run the committed harness — for each of `scenarios/multi_file_analysis.json`, `scenarios/comprehensive_review.json`, `scenarios/dependency_upgrade_staged.json`:

```
cd recipe-ports/evals && python3 run_pair.py --dtu-id recipes-vs-dotrunner \
  --scenario scenarios/<name>.json --out-dir <repo>/.amplifier/evaluation/recipes-vs-dotrunner/<UTC>/<pair>
```

Rows that must flip against the `20260901T023905Z` RESULTS.md baseline:

1. **§3.1 Parallelism** — "Mechanism / Side B" `sequential counter-cycle` → `foreach fan-out`; "Total wall-clock" B `70.0s` → **≤ Side A's 50.7s** (also flips the §1 mechanical-matrix pair-3 B wall-clock cell).
2. **§3.2 Sub-workflow context isolation** — both "child sees it?" B cells `YES — PARENT_ONLY_SECRET` → `NO`, and the heading "**the one clear recipes win**" must be retired in the re-run write-up.
3. **§3.3 Gate ergonomics / §3.4 Park + resume** — "Non-interactive drive" B and "How the park happens" B (`needs --on-human-gate console + a held-open FIFO`) → `--on-human-gate park` + `dot-runner approve` + `resume`, with "Resume re-executes prior work? **no**" preserved.

The wrapper decision re-opens **iff** all three reach parity-or-better. Anything short of that leaves the wrapper deferred.

---

## Reserved / explicitly out of scope

- **The recipes-wrapper itself** — upstream repo, separate decision. Nothing in this contract authorizes it.
- **`k_of_n` / `quorum`** — stay dead (removed upstream at `fb57a55`; EXTENSIONS §18 `status: REMOVED`). `error_policy` is untouched and remains in use.
- **`report_outcome`** — stays dead (EXTENSIONS §35 `status: REMOVED`, 2026-08-30 WAVE 5); `status.json` + the returned `Outcome` are the whole verdict channel.
- **No YAML/recipe vocabulary in the engine** (§1.2:35). No `stage`, `step`, `approval:`, or recipe-shaped attribute enters the DOT surface.
- **No routing change for C5** — the plain-edge outcome is conformant; only the lint and the docs move.
- **Edge-level fan-out** — not reintroduced under any clause (T0-4 / `ATX-10` / `ATX-M-109` remain binding).

---

## Changelog

- **1.0.0 — 2026-09-01 — DRAFT.** Initial draft for maintainer freeze. Scope = C1–C5 + reconcile, from the `attractor-4c7` decision. Open micro-calls decided in-document: C1 empty-list semantics (SUCCESS/`[]` under `wait_all`, FAIL under `first_success`, absent-key fail-loud as a distinct case) and C1 collect ordering (index-ordered records, `not_run` for uncompleted `first_success` branches). Spec cites corrected against `fb57a55` during drafting: `parallel.results` is §4.8:**825** (not :823); join table §4.8:**846-851**; human-gate timeout §4.6:**752-758**; refusal→FAIL §4.6:**760-761** (not :745); "programmatic queue" §6.1:**1244**; HTTP `/questions` + `/answer` §9.5:**1602-1603**; §6.5:**1361-1367**. C5's "0 fires expected" restated as a with-fail-edge-subset bound after finding two shipped hexagon fixtures that are legitimate true positives.
