# Review — the shipped capsule pipelines against current doctrine and measured cost

**Status:** review only — no `.dot` file is modified by this PR (`git diff --stat` in the PR body
proves it); the proposed v2 is a fenced diff at the bottom, unapplied.

**Why this doc lives here.** The graphs are `.github/capsule-pipeline/*.dot` in *this* repo, whose
workflows drive them and whose engine executes them — every clause, worker name and attribute cited
below is defined here (`contracts/engine-surface.v1.md`, `specs/EXTENSIONS.md`,
`modules/loop-pipeline/`). **Attractor-side scope:** `microsoft/amplifier-bundle-attractor` ships the
same three graphs at the same path and their bodies are **byte-identical** — `diff` returns only this
repo's 14-line vendoring header (`capsule.dot:1-14`). Every finding applies verbatim to both; a fix
must land in attractor and be re-vendored per `.github/capsule-pipeline/README.md`. Attractor's
`examples/**/*.dot` are documentation, and its one other own-pipeline
(`.github/actions-key-smoke/actions-key-smoke.dot`, 3 nodes, no LLM node) has no findings.

**Assumed engine state.** `assumed_context_bounding_merged: false` — the work is `0c32135` +
`f6005e2` on `lane/agent-loop-context-bounding`; `git merge-base --is-ancestor 0c32135 main` says NO
and `git branch --contains` names only that lane. provider-anthropic #115 is treated as landed
(`d7355b6a`). Cost projections hold token counts at measured values and vary only the cache ratio.

## 1. The measured picture

Sources, read-only: run 2 = Actions `34064448082`,
`.amplifier/evaluation/capsule-64-run2/artifact/_temp/capsule-run/logs/` (`trace.jsonl`,
`timing-rollup.json`, 12 × `*/sessions/*/events.jsonl`); run 1 = Actions `34039364352`,
`.amplifier/evaluation/capsule-64/artifact/.../trace.jsonl` (no per-node sessions — run 2 is the
first with them, per `671e9da`).

| | run 1 | run 2 |
|---|---|---|
| node visits (`trace.jsonl`) | 28 | 28 |
| wall clock | **330.00 min = 19800s, exactly the fuse** | 188.4 min (`timing-rollup.json: total_seconds 11304.64`) |
| ended | fuse death mid-`critique` (round 1) | honest non-convergence → `postmortem` → `escalate` → `abandon` |
| complete rounds | 1 (185.6 min) | 1 (107.6 min; `mean_iteration_seconds 6456.15`) |

### 1.1 Per-worker-session cost, run 2

Derived from each session's `provider:*`/`tool:*` event pairs. `cost` sums the events' own
`usage.cost_usd`; `in` is `usage.input_tokens` (which **includes** `cache_read_input_tokens`);
`cache` is `cache_read/input`.

| node (visit) | session | calls | LLM min | tool min | tool calls | in | cache | out | cost |
|---|---|---|---|---|---|---|---|---|---|
| `author` r2 | `c2e6940c` | **164** | **59.9** | **0.19** | **166** (138 bash, 14 read, 12 edit, 2 write) | 24.70M (24,334→227,605, med 161,132) | 7.2% | 320,419 | **$74.10** |
| `author` r1 | `e1063bd1` | 92 | 20.4 | 0.24 | 94 | 9.33M (17,521→142,351) | 10.8% | 108,343 | $26.91 |
| `critique` | `2fd2064c` | 51 | 19.6 | 0.53 | 64 (61 bash) | 2.72M | 20.6% | 104,596 | $8.24 |
| `mutate_b` | `2549a832` | 56 | 17.3 | 0.05 | 60 | 2.85M | 21.4% | 88,635 | $8.24 |
| `void` | `0bb297ed` | 35 | 17.4 | 0.05 | 37 | 1.64M | 23.2% | 91,154 | $5.28 |
| `mutate` r1 | `577873de` | 57 | 16.8 | 0.07 | 56 | 2.13M | 29.6% | 85,478 | $5.98 |
| `rival` | `b1ba9a98` | 63 | 10.7 | 0.84 | 66 | 6.73M | 10.3% | 56,880 | $19.21 |
| `postmortem` | `115cbbfd` | 22 | 7.9 | 0.01 | 35 (18 read, 15 bash) | 1.30M | 18.5% | 41,490 | $3.88 |
| `mutate` r2 ×3 retries | `cb1d/f300/919e` | 66 | 12.6 | 0.14 | 70 | 2.59M | 28% | 66,643 | $6.82 |
| `orient` | `01fcaced` | 23 | 2.8 | 0.01 | 32 | 1.17M | 20.5% | 15,804 | $3.14 |
| **run total** | | **629** | **185.4** | **2.14** | **680** | **55.2M** | 12.5% | **979,442** | **$161.80** |

Tool time is **1.2%** of LLM time across the whole run. The cost model is reconstructed exactly from
the events (`$3/Mtok` non-cached input, `$0.30/Mtok` cache read, `$15/Mtok` output — reproducing
every `cost_usd` to the cent, e.g. `mutate`'s first event: `(12660−10995)×3 + 10995×0.3 + 584×15`
per Mtok `= $0.0170535`, the recorded value).

### 1.2 The finding that governs everything below: **wall clock is an output-token budget**

| node (visit) | LLM min | out tokens | out/min | in (M) | in M/min |
|---|---|---|---|---|---|
| `author` r2 | 59.9 | 320,419 | 5,349 | 24.70 | 0.412 |
| `author` r1 | 20.4 | 108,343 | 5,311 | 9.33 | 0.457 |
| `void` (lowest input rate) | 17.4 | 91,154 | 5,239 | 1.64 | 0.094 |
| `rival` (highest input rate) | 10.7 | 56,880 | 5,316 | 6.73 | 0.629 |
| `orient` | 2.8 | 15,804 | 5,644 | 1.17 | 0.418 |

Across all 12 sessions: **out/min mean 5,293, stdev 144, CV 2.7%; Pearson(minutes, output tokens) =
0.9998.** Against input tokens: Pearson 0.9242, and the input rate spans 0.094–0.629 M/min — a 6.7×
spread. A node's wall clock is `output_tokens / 5,293` and is essentially blind to how large its
input payload is.

Three consequences, each load-bearing for findings 3 and 5:

1. **`max_pipeline_duration` is an output-token budget in disguise.** 19800s × 5,293 = **1,746,690
   output tokens**. Run 2 spent 979,442 (56%) and did not converge.
2. **provider-anthropic #115 is a cost lever, not a clock lever.** Holding the measured token counts
   fixed and varying only the hit ratio in the verified cost model: run 2 costs **$170.04** at the
   measured ~7%, **$47.62** at 89%, **$38.69** at 95% — a 72–77% cost cut. Its effect on the fuse is
   bounded above by TTFT, and TTFT is measurably small here: `orient` (median input 55K) is only 6%
   faster per output token than `author` r2 (median input 161K). **Ceiling on the clock benefit of
   caching: ~6%.**
3. **The tool-result retention window is also a cost lever, not a clock lever** — it removes input
   bytes, which buy no minutes. Its own entry discloses that a rolling window mutates the request
   prefix every call and so **makes #115's cache miss more often** (`specs/EXTENSIONS.md` §45,
   "Interaction with prompt caching"). The two landing fixes are partly in tension, and neither
   moves the fuse.

## 2. Node census

**101 nodes across the three shipped graphs**: `capsule.dot` 35, `feature-capsule.dot` 42,
`task-runner.dot` 24 — **26 LLM nodes** (`shape=box`), **3 human gates** (`shape=hexagon`), and 72
deterministic `shape=parallelogram` tool nodes with `max_retries=0` costing no model time (0.09 min
across the run's 16 glue visits). **Uniform facts, grepped across all three files:** zero `worker=`,
zero `max_agent_turns=`, zero per-node `timeout=` — every LLM node runs the run-level `--worker
coding-agent` (`capsule-specify.yml:381`, `capsule-implement.yml:334`, `feature-specify.yml:368`)
with an unbounded turn budget. Only two `fidelity=` values exist anywhere: the graph default
`compact`, and `full` on the one carry node (`capsule.dot:130,290`).

### 2.1 `capsule.dot` — LLM and gate nodes

`cost` is run 2 unless noted; "tools?" is what the prompt requires, not what the node used.

| node | L | purpose | worker today | needs tools? | fidelity / thread | measured | recommendation |
|---|---|---|---|---|---|---|---|
| `orient` | 208 | read issue, survey pinned tree, write `.ai/brief.md` | coding-agent | yes (read-only) | `compact` / — | 2.8 min, $3.14 | keep; add `max_agent_turns` |
| `rival` | 239 | gate-blind steelman fix, once per run | coding-agent | yes (writes+tests a patch) | `compact` / `rival` | 10.7 min ($19.21); **38.2 min run 1** | keep; bound turns |
| `author` | 290 | write `DEFINITION.md` + `.verify.sh` | coding-agent | yes | **`full` / `work`** | **59.9 min, $74.10**; 20.4 r1; **125.8 min over 2 visits run 1** | **bound turns** (§3.1); `full` is near-free, see §3.2 |
| `mutate` | 388 | crude hypothesis patch A | coding-agent | yes | `compact` / `mutate` | 16.8 min + 12.6 min of retries | keep |
| `mutate_b` | 395 | patch B, *different shape from A* | coding-agent | yes | `compact` / `mutate_b` | 17.3 min | keep — hard dep on A (§3.3) |
| `void` | 402 | adversarial dodge patch | coding-agent | yes | `compact` / `void` | 17.4 min; **44.4 min run 1** | keep, **fan out** (§3.3) |
| `critique` | 596 | THE one judge; `llm_provider="openai"` | coding-agent | yes (61 bash) | `compact` / — | 19.6 min; **47.0 min run 1** | keep on coding-agent (§3.1) |
| `diagnose` | 674 | root-cause after a repeated signature | coding-agent | read-only | `compact` / — | not reached | → `llm-direct` (§3.1) |
| `postmortem` | 731 | read the record, write the report | coding-agent | read-only | `compact` / — | 7.9 min, $3.88, 22 calls | → `llm-direct` (§3.1) |
| `escalate` | 737 | human gate `[A]/[C]/[K]` | — | — | — | 0 | keep |

Deterministic nodes (25, all `max_retries=0`): `setup` 193, `bad_input` 196, `dirty_tree` 199,
`orient_fail` 204, `rival_reset` 268, `author_reset` 340, `capsule_gate` 346, `leak_gate` 353,
`redgate` 378, `write_green_finding` 384, `nonvacuity_gate` 577, `reset_fail` 583, `verdict` 622,
`discrimination_check` 635, `package` 644, `triage` 652, `diagnose_gate` 710, `diagnose_fail` 716,
`budget_decision` 722, `write_cant_gate_finding` 727, `pm_gate` 734, `bump_budget` 740, `abandon`
743, `start` 138, `done` 139; five carry `goal_gate=true retry_target="author"`. **No recommendation
on any** — they are the shape `docs/VISION.md:140-146` demands; verdicts arrive by `tool.last_line`
+ exit code, and the review found no self-report gate in any of the three graphs.

### 2.2 `feature-capsule.dot` — the 35 above plus 7

Same 9 LLM nodes at the same settings (`orient` 424, `rival` 441, `author` 467 — again
`thread_id="work" fidelity="full"`, `mutate` 569, `mutate_b` 575, `void` 588, `critique` 669,
`diagnose` 786, `postmortem` 831, `escalate` 841). Seven extra deterministic nodes, `max_retries=0`,
none with a finding: `criteria_gate` 403, `write_unspecced_finding` 408, `criteria_fail` 413,
`leak_drift_fail` 506, `write_partial_finding` 548, `write_blocked_on_criteria` 559,
`critique_fail_class` 708. Every §4 recommendation transfers unchanged, and it runs at
`max_iterations=8` (`feature-specify.yml:379`) against the same 19800s fuse — so §3.5 bites hardest
here.

### 2.3 `task-runner.dot` — 24 nodes

| node | L | purpose | needs tools? | fidelity / thread | recommendation |
|---|---|---|---|---|---|
| `orient` | 210 | read task + doctrine, survey | read-only | `compact` | keep; bound turns |
| `attempt` | 214 | advance the task | yes | **`full` / `work`** | **bound turns** — the `author` analogue |
| `diagnose` | 239 | root cause after repeat | read-only | `compact` | → `llm-direct` |
| `critique` | 267 | judge vs the full DoD | yes | `compact` | keep |
| `critique_b` | 312 | second family, `llm_provider="openai"` | yes | `compact` | keep |
| `feedback` | 366 | **merge two critique files; names the disagreement** | **no — reads 2 files, writes 1** | `compact` | **→ `llm-direct`, the clearest case in any graph** |
| `package` | 370 | commit, exclude `.ai/`, write handoff | yes (git) | `compact` | keep |
| `postmortem` | 397 | write the non-convergence report | read-only | `compact` | → `llm-direct` |
| `escalate` | 474 | human gate | — | — | keep |

Deterministic (15, no findings): `setup` 203, `bad_input` 206, `verify` 226, `triage` 236,
`diagnose_gate` 245, `stamp_a` 300, `verdict` 361, `ship_check` 377, `pm_gate` 419, `forge_fail` 429,
`salvage` 471, `bump_budget` 479, `abandon` 482, `start` 199, `done` 200. `stamp_a`/`verdict`'s
sha256 forge guard is the only place in the three graphs where a gate proves an artifact was not
rewritten between write and read — worth copying into the capsule graphs.

## 3. Findings

### 3.1 Worker choice — three nodes should move, and moving them needs one extra attribute

Every LLM node in all three graphs runs `--worker coding-agent` with no turn cap. Two problems.

**(a) Three nodes never mutate anything.** `postmortem` and `diagnose` (all three graphs) and
task-runner's `feedback` read artifacts and write one document. `postmortem`'s measured shape
confirms it: 22 provider calls, 35 tool calls of which **18 are `read_file`**, 2 `write_file`.
`feedback` is stronger — its prompt is "read BOTH critique files … name the disagreement", reasoning
over two files. `llm-direct` bounds these: `_MAX_TOOL_LOOP_ROUNDS = 20`
(`modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py:84`) unless `max_agent_turns=`
raises it (`workers/direct_worker.py:206-218`). **The saving is a bound, not minutes:** all three sit
off the converging path (`postmortem` cost 7.9 min of a 188-min run), so the move buys **~0 min on a
converging run** and converts a salvage-path runaway into a declared ceiling. Claiming a wall-clock
win here would be dishonest.

**(b) What the worker can actually do — and it bites.** `llm-direct` refuses `github-copilot` and
`openai-chatgpt` (`workers/direct_worker.py:184-192`, **C1.1**/**C5.1,4**); the graphs use
`anthropic` and `openai`, so that is not the problem. **C5.6** is: *"A node with neither `llm_model`
nor `llm_provider` still fails loud"* — implemented at `backend.py:1117-1159`, where `_resolve_model`
reads `node.llm_provider` and raises when both are absent. **Every node in these graphs except
`critique`/`critique_b` declares neither.** So `worker="llm-direct"` alone would refuse at dispatch;
the move must also add `llm_provider="anthropic"`, which the v2 diff does. That is C1.4/C8's
refusal-not-degradation working as designed, and why this is a two-attribute change.

**(c) `critique` should NOT move**, despite being the obvious 20-min candidate: 61 of its 64 tool
calls were bash — it re-derives facts rather than reading a verdict — and the CHARTER
(`capsule.dot:62-67`) makes it irreplaceable (*"One judge rules the residue machines can't decide"*).
A 20-round cap on the one judge trades a bounded budget for a truncated ruling. Bound it generously.

### 3.2 Fidelity and thread carry — the obvious fix is worth 4.5% and would be the wrong lever

Current vocabulary (`contracts/external/attractor-spec-canonical.md:1140-1157`) is exactly six modes
— `full`, `truncate`, `compact`, `summary:low|medium|high` — **unchanged**. There are no newer
options; the graphs are not stale on vocabulary. What moved is what `full` *means* here: it is
realized by a `parent_messages` carrier at **node-exchange granularity** — one user/assistant pair
per node, **never the child's inner tool-loop turns** (`specs/EXTENSIONS.md` §12; **C10.4**) — and
the "Unbounded (uses compaction)" its spec table promises does not exist on the coding-agent path at
all ("does NOT perform automatic compaction", `coding-agent-loop` §5.5, quoted in EXTENSIONS §45).

The arithmetic on `author`'s `thread_id="work" fidelity="full"` (`capsule.dot:290`):

- round 1's first call: 17,521 input tokens. Round 2's first call: 24,334. **Difference ≈ 6,813
  tokens** — that is the entire inherited exchange.
- re-sent on all 164 calls: 6,813 × 164 ≈ **1.12M of the visit's 24.70M — at most 4.5%**, and
  `compact` substitutes its own preamble, so the real saving is less.
- in wall clock: **≈ 0%**, by §1.2.

**The 24K→228K curve is not inherited context. It is the visit's own accumulated tool results** —
166 of them totalling 382,222 chars, and not one exceeded its per-tool truncation limit (largest
17,112 vs bash's 30,000), measured on this exact session by the context-bounding lane
(`0c32135` commit body, `specs/EXTENSIONS.md` §45). Switching `author` to `compact` would look like
the fix and buy 4.5% of one node's cost and none of its minutes.

**Recommendation: leave every `fidelity=` exactly as it is** in all three graphs. `full` on
`author`/`attempt` is correct — the correction loop *should* remember its prior round, and that
costs ~6.8K tokens. The five `thread_id`s (`work`/`rival`/`mutate`/`mutate_b`/`void`) are correct and
required: under **C10.2** threads are branch-local, so §3.3's fan-out needs `void` on its own thread,
which it already has.

### 3.3 Do the adversarial nodes earn their cost?

Measured per complete round: `mutate` + `mutate_b` + `void` = **52.2 min** of run 2's 107.6-min round
(**49%**), 55.9 min of run 1's 185.6 (30%); `rival` adds 11.5 / 38.2 min once per run. The "1–1.5h
per iteration" estimate is confirmed: 63.7–94.2 min including `rival`.

**What a cheaper ordering would lose.** The CHARTER says *"Nothing with a measured miss record gets
acquittal power over an executed result"* (`capsule.dot:62-67`), and `nonvacuity_gate`'s comment
records that its three predecessor screens "all PASSED the dodge they were built to catch"
(`capsule.dot:404-415`). Deleting a maker does not save its minutes — it returns the pipeline to the
shape that shipped a vacuous gate. Specifically: **`mutate_b` cannot be dropped or parallelised with
`mutate`** — its prompt reads `.ai/hypothesis.patch` **by name** and demands "a genuinely DIFFERENT
mechanism than" it (`capsule.dot:395-400`), a hard data dependency whose removal deletes the only
reason B exists. **`void` is independent** — it reads only `DEFINITION.md`/`.verify.sh` and writes
`hypothesis_v.patch` (`capsule.dot:402-403`), naming A and B rhetorically and reading neither.
**`rival` is already outside the loop**, authored once (`capsule.dot:752-755`) — correctly.

**The one structural change with a double-digit clock saving:** fan `void` out beside the mutate
chain, joining at `nonvacuity_gate`. `max(17.47, 16.93+17.83) = 34.8 min` against 52.2 sequential —
**17.5 min/iteration, 16% of a complete round**, zero adversarial coverage lost. **The cost, stated
plainly:** the three makers share one `target_dir` and the engine isolates *sessions* per branch, not
*filesystems* (**C10.1**, EXTENSIONS §8/§13). They are contracted to write a diff rather than apply
one — and unlike `rival`/`author` they carry no reset node — but nothing enforces it, and two
concurrent makers self-testing in one tree would corrupt each other's measurement. Hence the fan-out
ships **commented out**, behind Q1.

### 3.4 Contract and vision conformance

**Exercised, correctly:** **C4** graph-level `$name` params — `max_pipeline_duration="$max_duration"`
(`capsule.dot:131`), declarative-only, no-silent-default note at `capsule.dot:113-124`, every
workflow passing one. **C6** node-granularity fuse — run 1 is the proof: it died *inside* `critique`
at exactly 19800s, and `capsule-specify.yml:887` classifies that as a wall-clock ceiling distinct
from non-convergence (C6.3/C6.4 read correctly). **C12** five `goal_gate=true` nodes. **C10.2** five
distinct `thread_id`s. **C15** the run directory *is* the audit trail — this review exists only
because `trace.jsonl`, `timing-rollup.json` and `sessions/*/events.jsonl` do, per C15.1/C15.2.

**Hand-rolled where a clause now exists:**

1. **C2 (`status.json` is the outermost verdict channel).** Not one node in any of the three graphs
   writes or reads `status.json`; every verdict travels by `tool.last_line` + exit code (`verdict`
   622, `capsule_gate` 346, `triage` 652 …). Legal — the graphs predate C2's restoration (§41) — and
   the idiom is hardened here (the `&& outcome=success` stale-label conjunction on every
   shared-source edge, `capsule.dot:102-105`). But C2.3 offers what last_line cannot: a
   divergence-gated envelope carrying `is_explicit=True`, satisfying a `goal_gate=true` node that
   bare prose fails closed. **Not proposed for v2** — it rewrites every routing edge in three graphs
   and the current mechanism works. Flagged as debt (Q4).
2. **C1 (`worker=` selection).** Unused; §3.1.
3. **C5.5/C5.6 (rung-4 default model).** `critique`'s `llm_model="gpt-[5-9]*"` glob predates rung 4;
   `llm_provider="openai"` alone now resolves `gpt-5.*[0-9]` live. Cosmetic; not proposed.
4. **`max_agent_turns`.** A real per-node attribute on both paths (`backend.py:402-404` → spawn's
   `max_turns` at `:700`; `direct_worker.py:206-218`). Unused in all three graphs. §3.1/§3.5.

**No contradiction with `docs/VISION.md` was found.** The graphs match its two sharpest resists:
no self-report gate (`VISION.md:140-146`) and no silent degradation — every failure edge routes to
`triage`/`escalate`/a LOUD halt, and `reset_fail` is a deliberate dead end (`capsule.dot:583-586`).

### 3.5 Budget honesty — re-derived

#69 set `max_iterations=2` from run 1's arithmetic (`capsule-specify.yml:329-357`): `C = 185.6 min`,
`F = 330 min`, `ceil(F/C) = 2`. Sound. Run 2 adds a second complete round at **107.6 min** — same
graph, same issue, **1.72× cheaper** — and `floor(330/107.6) = 3`. The honest bracket is
`max_iterations ∈ [2, 3]`, and **2 remains the defensible choice**: `ceil` against the *worse*
observed round is the fail-safe reading.

**Re-derived against the two landing fixes — and this is the point:**

- post-#115: **no change**. Caching cuts run 2 from $170.04 to $47.62–$38.69 and buys **≤6% of the
  clock** (§1.2, TTFT ceiling) — 20 min of 330, a fifth of a round, not a round.
- post-context-bounding (unmerged): **no change**, possibly slightly negative — the rolling window
  makes #115's cache miss more often (EXTENSIONS §45).
- **So: do not raise `max_iterations` on the strength of either fix.** That would re-advertise
  rounds the fuse cannot reach — the exact fiction #69 removed.

**What the arithmetic cannot tell you, and the measurement that would.** Round cost is **not
stationary**: within run 2, `author` went 92 → 164 calls and 108K → 320K output between rounds
(**2.96×**) while `mutate` went 16.8 → 12.6 min, and round 1 cost 80.8 min *and did not finish*. A
budget derived from round 0 systematically under-predicts. Honestly: **the per-iteration cost curve
is unknown after round 1, and no run has yet produced a round 2.** The settling measurement, in fuse
currency (§1.2):

> Run 3 with `max_iterations=2` unchanged and the §3.1 turn caps applied, then read
> `sessions/*/events.jsonl` for `output_tokens` per node visit and check whether round 1's total
> exceeds round 0's. If output per round is flat, `max_iterations = floor(1,746,690 / round₀_output)`
> is derivable directly — round 0 of run 2 was **550,890 output tokens**, giving 3. If it grows, the
> budget must be derived from the *worst* round, not the mean, and the turn caps become the control.

## 4. Proposed v2 — a real diff, **not applied**

Four changes to `capsule.dot`; the same shape applies to `feature-capsule.dot` (`author` 467,
`critique` 669, `diagnose` 786, `postmortem` 831) and `task-runner.dot` (`attempt` 214,
`critique`/`critique_b` 267/312, `diagnose` 239, `feedback` 366, `postmortem` 397). Every change adds
attributes or comments; no prompt, no existing edge and no topology is touched.

**Verified, not asserted:** this diff `git apply --check`s cleanly from the repo root (`-U1` context,
to keep this doc inside its line cap), and the resulting graph passes `dot-runner lint … --param
max_duration=19800s` with `OK (no findings)` — identical to the current file's own lint result.

```diff
diff --git a/.github/capsule-pipeline/capsule.dot b/.github/capsule-pipeline/capsule.dot
--- a/.github/capsule-pipeline/capsule.dot
+++ b/.github/capsule-pipeline/capsule.dot
@@ -289,3 +289,11 @@ digraph CapsulePipeline {
     // descent, so the structural channel is kept alongside continuity.
+    // v2 CHANGE 1 -- bound the largest fuse consumer. MEASURED (Actions
+    // 34064448082, session c2e6940c): round 2 = 164 turns / 320,419 output
+    // tokens / 59.9 min in ONE visit, and wall clock = output_tokens / 5,293
+    // (r=0.9998 over 12 sessions), so 110 turns caps this near 40 min.
+    // max_agent_turns reaches spawn as max_turns (backend.py:402->700).
+    // fidelity="full" KEPT: at node-exchange granularity (EXTENSIONS Sec12 /
+    // C10.4) it carries ~6,813 tokens -- <=4.5% of the visit, ~0% of its minutes.
     author [shape=box, class="maker", thread_id="work", fidelity="full",
+        max_agent_turns="110",
         must_write=".ai/capsule/DEFINITION.verify.sh",
@@ -595,3 +603,8 @@ digraph CapsulePipeline {
     // void_greened fact must not exit through any door without judgment.
+    // v2 CHANGE 2 -- bound the ONE judge, generously (51 turns / 19.6 min on run
+    // 34064448082 but 47.0 min on 34039364352). Stays on coding-agent: 61 of its
+    // 64 tool calls were bash -- it re-derives facts -- and the CHARTER makes it
+    // the node ruling on the residue machines cannot decide.
     critique [shape=box, class="gate", llm_provider="openai", llm_model="gpt-[5-9]*",
+        max_agent_turns="90",
         must_write=".ai/critique.md",
@@ -673,3 +686,10 @@ digraph CapsulePipeline {
     // structured crash->diagnose channel superseding the prose pointer).
+    // v2 CHANGE 3 -- read-only reasoning nodes move to the bounded worker
+    // (diagnose here, postmortem below). llm_provider= is REQUIRED, not
+    // decorative: C5.6 / backend.py:1117-1159 fails loud on a node carrying
+    // NEITHER llm_model NOR llm_provider, and every node here except critique
+    // carries neither. HONEST EFFECT: ~0 min on a converging run (both sit off
+    // it) -- a declared ceiling, not a saving.
     diagnose [shape=box, class="gate", must_write=".ai/diagnose-verdict",
+        worker="llm-direct", llm_provider="anthropic", max_agent_turns="30",
         prompt="A capsule-round stage failed twice with the SAME failure signature (see .ai/gate.log, and .ai/last-stage-fail for which stage: round/leak/redgate/nonvacuity/critique/discrimination -- if .ai/gate.log is STALE relative to the failure, the failing node was a MAKER crashing before any gate ran: a node/provider/contract failure, not a content defect). Stop iterating blindly -- find the root cause. READ THE ENGINE'S OWN RECORD FIRST, before any theory: the runner writes its logs OUTSIDE target_dir in a run-logs directory (default shape /tmp/attractor-run-*, or the --logs-root value; identify the live one by its trace.jsonl and per-node record dirs named after THIS graph's nodes, newest mtime). That directory carries trace.jsonl (the per-node status sequence) and per-node records <logs-dir>/<node_id>/status.json plus iteration-scoped copies <logs-dir>/iteration_<N>/<node_id>/status.json, whose failure_reason field is the ENGINE'S OWN statement of why a node failed -- e.g. a must_write artifact-contract violation names the declared artifact and the mtimes. That is an engine-level fact no amount of .ai/ reading can surface (.ai/gate.log is stale precisely when the failure was a maker crash). Read the failed node's failure_reason and QUOTE it verbatim in your diagnosis; if you cannot locate the record, say so explicitly in the diagnosis instead of theorizing. Then read .ai/gate.log, .ai/brief.md, the report at $issue_file, and the relevant code in $target_dir. Determine what is actually wrong: (a) DEFINITION.verify.sh's assertion approach, (b) a misread of the reported defect, (c) an environment/tooling gap or a repeatedly-crashing node -- including an engine artifact-contract (must_write) failure, which is a compliance defect of that maker's turn, never 'pipeline routing', (d) a genuine ambiguity in the report that cannot be resolved from inside this run. Write TWO files. FIRST, .ai/postmortem/diagnosis.md -- the human-facing analysis: root cause, evidence (quote the engine failure_reason when one exists), and the single change of course for the next attempt. Prose in that file has NO routing effect: the word BLOCKED anywhere in it -- quoted, conditional, hypothetical, emphatic -- routes nothing. SECOND, .ai/diagnose-verdict -- the machine verdict channel, read by exact match: EXACTLY one line containing EXACTLY one word, either CONTINUE (the next attempt should proceed with your change of course) or BLOCKED (the blocker cannot be resolved from inside this run -- including a node that keeps crashing for reasons no re-authoring can change). No punctuation, no explanation, no conditional constructions -- 'blocked only if X happens later' is CONTINUE now, by definition. Anything else in that file halts the run loud for a human."]
@@ -731,2 +751,3 @@ digraph CapsulePipeline {
     postmortem [shape=box, class="gate", must_write=".ai/postmortem/report.md",
+        worker="llm-direct", llm_provider="anthropic", max_agent_turns="30",
         prompt="This capsule run has hit a decision point without convergence. Read .ai/convergence.jsonl (the descent record), .ai/gate.log, .ai/last-stage-fail, .ai/brief.md, .ai/findings/, and the report at $issue_file. If .ai/findings/void-greened.md exists, LEAD WITH IT: an adversarial void patch greened the gate and no critic has ruled on it -- that fact must reach the human unmissed. Write .ai/postmortem/report.md: what was tried each round and at which stage it failed, whether the loop was DESCENDING, OSCILLATING, or WANDERING (cite the convergence record), your best hypothesis for why it has not converged, and -- the value salvaged from non-convergence -- the SPECIFIC, concrete, answerable questions this run could not resolve on its own that should go back to the person who filed the report at $issue_file. Be honest."]
@@ -792,2 +813,12 @@ digraph CapsulePipeline {
 
+    // v2 CHANGE 4 -- PROPOSED, SHIPPED COMMENTED OUT pending owner question Q1.
+    // void reads ONLY DEFINITION.md/.verify.sh and writes hypothesis_v.patch, so
+    // it can run beside the chain: max(17.47, 16.93+17.83) = 34.8 min vs 52.2
+    // sequential = -17.5 min/iteration, no coverage lost. mutate_b CANNOT move
+    // the same way -- its prompt reads .ai/hypothesis.patch BY NAME. BLOCKED ON:
+    // the makers share one target_dir and the engine isolates SESSIONS per
+    // branch, not filesystems (C10.1, EXTENSIONS Sec8/Sec13). Uncomment ONLY
+    // with a post-join reset-proof (rival_reset idiom) or per-branch worktrees.
+    //     mutate   -> void            [label="v2: fan out -- void is independent"]
+    //     mutate_b -> nonvacuity_gate [label="v2: join"]
     mutate -> mutate_b
```

### Expected effect, with the arithmetic

| change | wall clock / iteration | cost | what it actually buys |
|---|---|---|---|
| 1 — `author max_agent_turns=110` | caps at ~40 min (110 turns × 320,419/164 out ≈ 215K out ÷ 5,293) vs 59.9 measured → **−20 min worst case** | −$25 at measured cache; −$6 post-#115 | a *ceiling* on the largest fuse consumer, and a visible truncation instead of a silent 60-min visit |
| 2 — `critique max_agent_turns=90` | run 2 (51 turns) unaffected; **run 1's 47.0 min capped ~35** | −$0 typical | stops the judge from eating a round on a bad day |
| 3 — `diagnose`/`postmortem` → `llm-direct` | **≈ 0 min** (off the converging path) | ≈ −$2/run | honest: a bound, not a saving |
| 4 — `void` fan-out (commented) | **−17.5 min (−16% of a round)** | $0 | the only double-digit clock change; blocked on Q1 |
| #115, already merged | ≤ −6% (TTFT ceiling) | **$170 → $48–39, −72%** | cost, not clock |
| **all applied, run-2 profile** | 107.6 → **~90 min/round**, ~72 with change 4 | ~$45/run | `max_iterations=2` stays correct; a 3rd round becomes reachable only with change 4, and nothing here buys one outright |

## 5. Open questions for the owner

1. **Q1 — filesystem isolation for parallel makers.** Change 4 is the only structural saving found
   (16% of a round). It needs the makers proven not to touch `target_dir`, or per-branch worktrees.
   Is a "write diffs, never apply them" contract enforceable with a post-join reset-proof node (the
   `rival_reset`/`author_reset` idiom), or do you want per-branch filesystem isolation in the engine?
2. **Q2 — which tools does `llm-direct` expose on the Actions path?** Its tool set comes from the
   mount config (`loop_pipeline/__init__.py:97,298`) and this review could not prove what CI mounts.
   Cheap probe: one `worker="llm-direct"` node, then read `tool_names` off its `provider:request`
   event (`direct_worker.py:239`). If `read_file` is absent, change 3 is void.
3. **Q3 — are 110 and 90 the right turn caps?** They sit at ~1.7× the measured counts. A cap that
   truncates the author mid-capsule is worse than a long visit — prefer 2× (200/120) for one run?
4. **Q4 — `status.json` migration (C2).** Schedule as its own change, or leave the `tool.last_line`
   idiom alone while it works? Largest conformance gap found, and the most invasive to close.
5. **Q5 — attractor-side sequencing.** Should v2 land in `amplifier-bundle-attractor` first and be
   re-vendored per `.github/capsule-pipeline/README.md`, or land here and be back-ported?
