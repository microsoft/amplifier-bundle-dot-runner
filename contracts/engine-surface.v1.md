# CONTRACT: engine surface beyond the nlspec, v1

> **DRAFT.** Only the owner stamps FROZEN, by editing `status:` below and adding a
> dated Changelog entry. Freeze Bar evidence — condition by condition, clause by
> clause — is in the sibling `FREEZE-PACKET-engine-surface.v1.md`. This contract
> does not self-stamp; until stamped, nothing here binds a lane.

- **id:** `CONTRACT-engine-surface.v1` · **version:** 1.0.0 · **status:** **DRAFT** · **date:** 2026-09-02
- **owner:** maintainer · **repo:** `microsoft/amplifier-bundle-dot-runner`

**Scope.** The behavior of this engine **beyond** the external nlspec vendored at
`contracts/external/attractor-spec-canonical.md`. That nlspec is governed upstream and
asserted clause-by-clause in `ledger/rows.yaml`; **this file governs the surface it does
not.** Core clauses are derived, one per live extension surface, from
`specs/EXTENSIONS.md` — the decision-record store that has carried these surfaces "pending
promotion to an owned contract (`contracts/engine-surface.v1`, forthcoming)". This is that
contract. Each clause names the sections it derives from; that `From` line **is** the
derivation, and a clause citing no live section is not a clause. Out of scope: anything the
nlspec already governs; `contracts/recipe-substrate.v1.md`'s five proposed engine changes
(a separate, paused DRAFT, not duplicated here); anything EXTENSIONS marks REMOVED/ABSORBED.

**Census, the negative half — stated so it can be checked.** All 44 EXTENSIONS sections were
read. Excluded, with cause: **§1–§7**, ABSORBED UPSTREAM @ `fb57a55` (the nlspec is the
normative text); **§16** `runs_on`/`continue_on_fail`, **§17** `requires=`/`outputs=`, **§29**
`feedback_from=`, REMOVED 2026-08-30 (`feat/extensions-rip-3`); **§23** `response_schema` and
**§18**'s `k_of_n`/`quorum`, REMOVED 2026-08-31 (`feat/extensions-walkback-2`); **§35**'s
`report_outcome` transport, REMOVED 2026-08-30 (WAVE 5); the **T0-4** note, a restoration of
nlspec behavior and so `ledger/rows.yaml`'s `ATX-10`, not ours. Every other section is LIVE
and appears in exactly one Core clause.

---

## Core

### C1 — Worker names, `worker=` selection, and the default ladder
*From §40 (LIVE, extension — implementor-level, below the nlspec's §4.5 backend boundary), with its 2026-08-30 rename note and 2026-08-31 library-seam addendum.*

1. The worker names are exactly `llm-direct`, `coding-agent`, `amplifier-agent`. `spawn` is a reserved sentinel the adapter resolves, not a `WorkerRegistry` key.
2. A node selects one with `worker="<name>"`; a run selects one with `--worker` (CLI) or `worker=` (library) / `orchestrator_config["worker"]`.
3. Precedence, highest first: the node's `worker=`; the run-level default; the capability fallback (`spawn` when `session.spawn` resolved, else `llm-direct`).
4. An unrecognized name — on a node or as a run default — raises `ValueError` **naming every known worker**; never a silent fallback. A retired name (`direct`, `loop-agent`) fails loud with a `renamed: '<old>' -> '<new>'` hint, which is an error message, not a working alias.
5. A bare invocation — no `worker=`, no `bundle=` — resolves to `amplifier-agent` unconditionally on **both** seams, through one shared resolver.
6. A configured default worker whose module is absent or broken **fails loud**. Degrading to `llm-direct` with a stderr notice is deleted behavior, not deprecated behavior. `worker=` and `bundle=` are mutually exclusive: passing both raises immediately rather than silently overriding one. The seams differ only in mechanism — the CLI exits non-zero, the library raises the catchable `WorkerResolutionError`. `WorkerRegistry.register` refuses a worker missing `clone()` or `close()`.

**Probe.** *Given* a node with `worker="direct"`, *When* its worker is resolved, *Then* a `ValueError` naming both `llm-direct` and `renamed` is raised and no node executes; *and given* a broken configured default, *Then* resolution fails loud rather than returning `llm-direct`.

### C2 — `status.json` is the outermost verdict channel; the lifecycle envelope is never a verdict
*From §41 (LIVE, conformance restoration of a spec-native channel) and §35's surviving half (LIVE) after WAVE 5 removed `report_outcome`.*

1. After a node's handler runs, the engine re-reads that node's stage-directory `status.json` — on every retry attempt, before the `must_write=` check, and immediately before each of `CodergenHandler`'s default-outcome writes, so the handler's own mandated write cannot clobber an external one.
2. Envelope fields: `outcome` (any `StageStatus`, including `skipped`), `preferred_label`, `suggested_next_ids`, `context_updates`, `notes`.
3. Precedence is divergence-gated: an envelope identical to the handler's outcome is a no-op; a **differing** well-formed envelope **wins**, carrying `is_explicit=True` — so it can satisfy a `goal_gate=true` node that bare prose would fail closed (C12).
4. Freshness floor: the file's mtime must postdate the node's execution start, so a stale file from an earlier attempt is never read.
5. Absent ⇒ no-op. Malformed — invalid JSON, non-object, missing or invalid `outcome`, wrong-typed `preferred_label` / `context_updates` / `notes` — is a **loud FAIL** regardless of what the handler returned.
6. Every SPAWN-capable worker is told, in its instruction, the absolute path of its `status.json` and the envelope shape. A spawned process cannot discover that path otherwise; the `CodergenBackend` protocol itself is untouched.
7. `orchestrator:complete` carries `status` (`success` | `incomplete` | `cancelled`) and `turn_count`, and its `metadata` is always `{}`. The engine recovers **only** that lifecycle status from a spawn result, never an explicit verdict. A child producing no output whose lifecycle status is not a success status is recorded FAIL (`"No output from child session"`), never a silent success.

**Probe.** *Given* a spawn node whose child writes a fresh `status.json` with `outcome: "fail"` while the handler returns SUCCESS, *Then* the outcome is FAIL with `is_explicit=True`; *and* the same run with invalid JSON in that file also fails the node — distinct from the absent-file case, which leaves SUCCESS intact.

### C3 — `must_write=` is a fail-closed artifact contract
*From §27 (LIVE, extension — the nlspec is silent on per-node artifact contracts).*

1. `must_write="<path>"` asserts three axes after a non-FAIL handler outcome: the file **exists**; its mtime is **strictly greater** than the wall clock snapshotted immediately before the handler ran; its content has at least one non-whitespace byte.
2. Path resolution matches `requires=`: absolute as-is, relative against `context.target_dir` if set, else `os.getcwd()`.
3. A completed attempt that violates the contract consumes a retry attempt exactly like RETRY; a never-writing node invokes its handler exactly `1 + max_retries` times, then FAILs loudly with a `failure_reason` naming the violated axis, routed through normal failure edges.
4. Neither `allow_partial=true` nor `continue_on_fail=true` softens that FAIL. It carries `is_explicit=False`, so the node cannot satisfy its own `goal_gate`.
5. Presence only. Quality — schema, verdict structure, size — is graph policy, permanently.

**Probe.** *Given* a node with `must_write=".ai/out.md"` and `max_retries=2` whose handler returns SUCCESS but writes nothing, *Then* the handler is invoked exactly 3 times and the node ends FAIL; *and* a node whose artifact exists but predates node start also FAILs — an mtime equal to start time is a failure, not a pass.

### C4 — Graph-level `$name` params resolve at parse time, and cross the sub-pipeline boundary
*From §43 (LIVE, pure addition in a spec-silent area) and its dated addendum of 2026-09-02.*

1. `parse_dot()` accepts `params`. When a graph-level **duration** attribute — today only `max_pipeline_duration` — holds a bare `"$name"` token (the entire stripped value, never a substring), the parser substitutes and parses the result as if written literally. Every other value parses unchanged.
2. A `$name` with no supplied param raises **before any model call**, naming the missing param **and the mechanism that supplies it on the path in use**: `--param name=value` on the CLI, `config["params"]` when the orchestrator is mounted, or the parent graph's own params when composed as a child. There is no shell-style default and none is planned — an absent fuse value must never become "no fuse".
3. Every parse site threads `params`: child graphs (`shape=folder` / `dot_file=`, manager-loop child dotfiles), the mounted orchestrator, the materialize-time reference walk, and `lint` (which accepts `--param`; since lint executes nothing, any parsing placeholder suffices).
4. **Params cross the sub-pipeline boundary, by design** — a child parses with the same mapping its own nodes expand at execution time, symmetric with node-level `$param` (C14.1). An LLM *session* deliberately does **not** cross that boundary (C10.3); a param mapping does, being declarative and serializable. The default is now "crosses": **any future graph-level param that must not cross requires its own decision record saying so.**
5. Independent of node-level expansion, which resolves at execution time from `graph.params_values` and carries no fail-loud contract for a missing key.

**Probe.** *Given* a parent with `max_pipeline_duration="$max_duration"` and a `dot_file=` child declaring the same attribute, *When* parsed with that param supplied, *Then* both resolve; *and* omitting it raises, naming `max_duration` and the supplying mechanism, with zero nodes executed.

### C5 — Provider resolution: subscription providers, the intent rule, and the rung-4 default model
*From §44 (LIVE, extension — the nlspec's `llm_provider` is an open set, "this entry is exactly that 'etc.'") and §42 (LIVE, implementor-level content for the nlspec's §8.5 rung 4, explicitly not a divergence).*

1. Exactly two subscription providers exist: `github-copilot` and `openai-chatgpt`, servable only by the spawn workers, never by `llm-direct`. Detection lives in one registry; the three native providers delegate verbatim to `unified_llm`'s own detection and are never re-declared, and `unified-llm-client` stays pure — it never grows a subscription entry.
2. `github-copilot` is configured iff `COPILOT_AGENT_TOKEN` or `COPILOT_GITHUB_TOKEN` is set, **or** `GH_TOKEN` / `GITHUB_TOKEN` is set *and* the run explicitly asks for it.
3. **Intent rule.** Copilot-specific env names carry intent by their name and always count. Generic GitHub tokens carry none, and count only when some node in the raw DOT source declares `llm_provider="github-copilot"` — detected by a conservative regex scan over the source, because worker resolution runs before the graph is parsed. `openai-chatgpt` needs no such rule: it is configured iff a non-empty OAuth cache exists at its documented path, whose existence already means a human completed a login flow.
4. Declaring either provider under `llm-direct` fails loud **before** the generic no-adapter error, naming the fix: add `--worker coding-agent` (or `amplifier-agent`), or use `anthropic` / `openai` / `gemini`.
5. A node with an explicit `llm_provider` and no `llm_model` resolves a **family token**, never a literal model id, live: `anthropic` → `sonnet` (stable only); `openai` → `gpt-5.*[0-9]` (stable only, anchored so `-mini` / `-codex` siblings cannot outrank the bare release); `gemini` → `gemini-3*pro*` (stable-only filtering disabled, because the flagship is itself preview-named). Rung order is otherwise unchanged: explicit `llm_model`, then `model_stylesheet`, then the unimplemented graph-level default, then this.
6. A node with **neither** `llm_model` nor `llm_provider` still fails loud. An unknown provider fails loud naming the provider and the known-defaults set — never a silent guess. The two subscription providers have no rung-4 row and none is planned: a family token for either fails loud before any live resolve, naming the fix (an explicit concrete id, or none at all).
7. When exactly one provider is mounted, a node declaring no `llm_provider` defaults to it; zero or multiple mounted preserves the literal `anthropic` fallback, unchanged.

**Probe.** *Given* an environment with only `GITHUB_TOKEN` set and a DOT source declaring no `llm_provider`, *Then* `github-copilot` is **not** configured; *and* the same environment with a node declaring it **does** configure it; *and* `llm_provider="anthropic"` with no `llm_model` resolves a concrete stable Sonnet id.

### C6 — The wall-clock fuse is enforced at node granularity
*From §15 (LIVE, extension — `max_pipeline_duration` is not in the nlspec; 2026-08-31 `attractor-674` update).*

1. Graph attribute `max_pipeline_duration` bounds the whole run against a monotonic clock.
2. Enforcement is not only between steps: each node dispatch is bounded by `min(remaining fuse budget, the node's own timeout=)`. The tighter wins, and the fuse wins a tie — whole-pipeline termination must never be masked as an ordinary node timeout.
3. A mid-node overrun terminates the node after a bounded cancellation grace (5s) and ends the run FAIL with `failure_reason="max_pipeline_duration_exceeded"`.
4. Stated, not silently expanded: mid-node fuse termination exits directly and does not route through recovery or postmortem machinery.

**Probe.** *Given* a graph whose `max_pipeline_duration` is shorter than one node's runtime, *Then* the run ends FAIL with that exact `failure_reason` **while the node is still in flight** — not after it completes.

### C7 — Provider preflight refuses before the first node
*From §36 (LIVE, extension — the nlspec is silent on provider mounting and credentials).*

1. Before the walk begins, on **both** entry points, every LLM-consuming node's declared `llm_provider` is checked for serviceability — statically, with no live API call: a mounted provider module, or a `profiles` entry whose credential env var is present.
2. A failure names **each** failing node, its provider, and the missing credential. Zero nodes execute; zero budget is spent.
3. A profile naming no adapter this run can resolve is refused at startup too, naming the node, the profile, and what **is** resolvable. Unknowability is honest: "not knowable on this path" skips the clause, while a discovery crash yields the empty set and refuses — never a false accept.
4. At dispatch, spawn-path profile resolution failure fails loud naming the node, the provider, the mounted profiles, and the credential to set — terminal in the retry ladder, never a crash loop.
5. Bounded by construction and documented as such: presence is checked, never validity; the implicit default provider and nested child graphs are out of scope; an injected backend skips the preflight.

**Probe.** *Given* a node declaring `llm_provider="openai"` with `OPENAI_API_KEY` unset, *Then* startup fails naming that node, `openai`, and `OPENAI_API_KEY`, and the trace contains no node execution.

### C8 — Refusal, not degradation, at dispatch and at routing
*From §38 and §33 (LIVE divergences, both `declining` upstream — no silent execution-class substitution, no silent green run) and §34 (LIVE, a restoration).*

1. An unknown node shape hard-fails at dispatch, naming the shape, the node id, the complete supported-shape list, and the remedy. The nlspec's default-to-codergen fallback is deliberately absent.
2. A main-loop dead end where outgoing edges exist but none matches terminates the run FAIL with `error_type=no_matching_edge`, rather than the nlspec's `Outcome(SUCCESS, "Pipeline completed")`. `run_subgraph()` returns `Outcome(FAIL, is_explicit=False)` on the same mismatch.
3. A **designed** terminus — a node with no outgoing edges at all — is unaffected; the last outcome stands.
4. That failure message keeps its `No matching edge from node 'X'` prefix and appends both the suggested ids and the existing edge targets.
5. At edge selection a suggested id that is a string passes through and an `int` is coerced; `bool`, `float`, `dict`, `list`, and `None` are rejected and logged, never silently stringified. The same coercion applies at the goal-gate retry lookup.

**Probe.** *Given* a node with `shape=septagon`, *Then* the run refuses, naming the shape and listing supported shapes; *and given* two `condition=`-bearing edges that both evaluate false, *Then* the run ends FAIL with `error_type=no_matching_edge`, while an edgeless terminal node ends with its own outcome.

### C9 — Sub-pipeline nodes: `shape=folder` / `dot_file=`
*From §10 (LIVE, extension — "additive and non-shadowing; `folder` is not a spec-assigned shape").*

1. A `shape=folder` node with `dot_file=` runs a child graph.
2. Path resolution has a fixed four-tier precedence: absolute; the graph's source directory; `context.target_dir` / `--cwd`; the process working directory.
3. Resolution is lazy — `validate()` performs no existence check — and an unresolvable child fails at **node entry** with `error_type="child_dot_resolution"`, routable through normal failure edges rather than crashing the run.
4. The absent-child case is additionally surfaced ahead of time by lint rule `TOPO-010` at WARNING severity (C16.3).

**Probe.** *Given* a folder node whose `dot_file=` names a missing file, *Then* the failure occurs at that node with `error_type="child_dot_resolution"` and a failure edge is taken; *and* linting the same graph emits exactly one `TOPO-010` warning and exits 0.

### C10 — Session and thread scoping
*From §8, §9, §11, §12, §13 (all LIVE; §9 and §13 resolve a self-contradiction in the nlspec, §11 and §12 sit in spec-silent space).*

1. Sessions are isolated per parallel branch: a branch runs on a branch-scoped engine with a cloned backend.
2. `thread_id` is **branch-local**. The same explicit `thread_id` in two sibling branches shares no history — the nlspec's §3.8 isolation is given precedence over its §5.4 thread reuse. Sequential same-`thread_id` reuse is unchanged.
3. A sub-pipeline or manager-loop child is a **fresh session boundary**: `thread_id` continuity does not cross it. Continuity does hold inline and across flattened `subgraph cluster_*`.
4. `fidelity=full` is realized by a `parent_messages` carrier at node-exchange granularity — one user/assistant pair per node; an empty output is carried as a synthesized marker rather than dropped.

**Probe.** *Given* two sibling parallel branches whose nodes both declare `thread_id="t"`, *Then* neither sees the other's messages; *and given* the same `thread_id` on two sequential nodes, *Then* the second sees the first's exchange.

### C11 — `reasoning_effort` has no engine-injected default
*From §39 (LIVE divergence from the nlspec's §2.6 and Appendix A, `declining` upstream — no hidden engine default on a provider-mode-switching surface).*

1. `Node.reasoning_effort` defaults to `None` and the parser injects nothing. Appendix A's `"high"` does not hold here.
2. The value is passed through as authored; on the spawn path, `None` keys are dropped so the child orchestrator uses its own default.
3. `model_stylesheet` is the only other source of a value.

**Probe.** *Given* a node declaring no `reasoning_effort` and no stylesheet rule, *Then* no `reasoning_effort` key is present in the request — asserted as absence, not as `"high"`.

### C12 — The goal gate is fail-closed
*From §25 (LIVE divergence from the nlspec's §4.5, which returns SUCCESS unconditionally).*

1. `Outcome.is_explicit` defaults to `False`; plain prose parses to RETRY, not SUCCESS.
2. A `goal_gate=true` node is satisfied only when its outcome is both successful **and** explicit.
3. `is_explicit` is serialized into `status.json` and `trace.jsonl`, so the distinction is auditable after the run.

**Probe.** *Given* a `goal_gate=true` node whose worker returns prose containing the word "done", *Then* the outcome is RETRY; *and* the same node with a divergent `status.json` (C2) is satisfied.

### C13 — `outcome=` resolves `preferred_label` first
*From §22 (LIVE divergence from the nlspec's §10.4, which defines `outcome` as `outcome.status` only).*

1. In an edge `condition="outcome=<x>"`, the `outcome` key resolves to `outcome.preferred_label` when one is set, falling back to `outcome.status`.
2. This is explicitly not behavior-neutral, and is why an explicit label can steer routing that a bare status could not.

**Probe.** *Given* an outcome of `status=success, preferred_label="needs_rework"` and edges conditioned on `outcome=success` and `outcome=needs_rework`, *Then* the `needs_rework` edge wins.

### C14 — Additive graph vocabulary
*From §21, §20, §19, §14, and §18's retained half (all LIVE, all "additive — a graph that does not use it behaves as in canonical").*

1. **`$param` / `${key}`** (§21): prompt and attribute substitution accepts these alongside `$goal`, resolved against pipeline context by plain string replacement — substitution, never templating.
2. **Tool node** (§20): `shape=tool` accepts `parse_json` and `tool_env`, and exposes routing key `tool.last_line` alongside `tool.output`.
3. **`wait.human` freeform** (§19): `mode="freeform"` accepts open text rather than only accelerator-key choices, and `description` / `attachments_inline` / `attachments_ref` enrich the question with files (each attachment read up to a byte cap, an unreadable one skipped with a warning rather than failing the gate). Accelerator-key gates behave as in canonical.
4. **`allow_partial` on timeout** (§14): the canonical attribute gains a second trigger — a node timeout yields `PARTIAL_SUCCESS` rather than FAIL. Call sites accept `True` and `"true"` alike.
5. **`error_policy`** (§18, retained half): `fail_fast` / `continue` / `ignore` on a parallel node remain load-bearing; `k_of_n` and `quorum` are removed and reserved.

**Probe.** *Given* a graph exercising all five, *Then* each behaves as stated; *and given* the same graph with each attribute removed, *Then* behavior is exactly the canonical baseline — the additive half asserted alongside the feature half.

### C15 — The run directory is the audit trail
*From §24, §26, §28, §30 (all LIVE; §24 additionally records a divergence — convergence iterations reset in-process rather than re-launching the run).*

1. **Per-iteration records** (§24): `logs_root/iteration_N/<node_id>/status.json` alongside the flat path; context keys `$iteration` / `$loop_count`; an append-only `trace.jsonl` of `{iteration, node_id, status, preferred_label, duration_ms, ts}`, readable via the `trace` subcommand.
2. **Worker session observability** (§26): `response_text` durably written to `<stage_dir>/response.md`; `session_id` in `status.json`; real session events under `<stage_dir>/sessions/<session_id>/events.jsonl`. Redaction is fail-loud — a redacted span is `[REDACTED:<shape>]` with a counted summary, and a redaction error withholds the payload rather than emitting it.
3. **Provenance** (§28): `manifest.json` carries `engine_version` and `engine_commit` (`"unknown"` when undeterminable, never fabricated); the standalone runner adds `runner_version`, `runner_commit`, `provider`.
4. **Attempt and cycle observability** (§30): `attempt_count` on the outcome and `attempt` on `pipeline:node_complete`; a size-bounded `failed_step` capture; `cycle_index` on manager-loop and subgraph records; `pipeline:stage_retrying` carrying `reason="exception:<Type>"`; `_branch_id` scoping.

**Probe.** *Given* a two-iteration convergence run with one retried node, *Then* `trace.jsonl` has one record per node per iteration, `iteration_1/` and `iteration_2/` both exist, `manifest.json` carries a non-empty `engine_version`, and the retried node reports `attempt_count > 1`.

### C16 — Admission-time validation is narrower than the nlspec, and lint is a separate entry point
*From §31 (LIVE — half restoration, half a plainly-stated narrowing) and §32 (LIVE, extension — the nlspec's §7.4 explicitly permits additional lint rules).*

1. `validate()` rejects a negative retry budget on `max_retries`, `default_max_retry`, `default_max_retries` — a restoration, since the nlspec already types these as Integer.
2. `validate()` rejects a node carrying `tool_command` whose effective handler is not the tool handler. This **refuses a graph the nlspec would admit and run**; the narrowing is recorded as a narrowing, not as "tightening validation".
3. `lint <file.dot>` runs a rule set independent of `validate()`: `TOPO-001`…`TOPO-010` and `VOCAB-001`.
4. Exit contract: errors exit 1; warnings exit 0 unless `--strict`. A WARNING never blocks execution.
5. The graph's source directory is seeded for `TOPO-010`, and `--param` is accepted (C4.3) so a parameterized graph lints without executing.

**Probe.** *Given* `max_retries=-1`, *Then* validation fails naming the attribute; *and given* a `shape=box` node carrying `tool_command`, *Then* validation fails naming the handler mismatch; *and given* a graph whose only finding is `TOPO-010`, *Then* lint exits 0, and `--strict` exits 1.

### C17 — Bundle composition
*From §37 (LIVE — "a pure addition in an area the canonical spec does not address").*

1. The root bundle carries always-on guidance through its `context:` key.
2. `agents/attractor-expert.md` is registered as `attractor:attractor-expert` by the core behavior and the root bundle.
3. Same-repo module and skill sources are ref-free, because a ref-pinned same-repo source resolves against the installed app's bundle directory rather than this repo.

**Probe.** *Given* a fresh install of this bundle, *Then* `attractor:attractor-expert` is present in the agent roster, and no same-repo source in the bundle carries a git ref.

---

## Backlogged

*Candidate clauses with named promotion triggers; none binds today.*
- **B1 — Preflight beyond the statically detectable class** (§36's own residual): the implicit default provider, nested child graphs, and key *validity* are out of scope. **Trigger:** a measured run that drains budget on a misconfiguration this preflight could not have seen.
- **B2 — `must_write=`'s delayed-replant window and session attribution** (§27's residual, documented by `test_case4_delayed_replant_informational`). **Trigger:** a measured false pass caused by an external writer landing inside the window.
- **B3 — Retired worker-name hints** (C1.4). **Trigger:** the one-release migration window closes; the hint is then deleted, not softened.
- **B4 — A graph-level default model** (§42's rung 3, unimplemented by any layer). **Trigger:** a graph needing one model across many nodes without a stylesheet.
- **B5 — The ambiguous multi-mount provider default** (C5.7). **Trigger:** a measured wrong-provider run caused by that fallback.
- **Not backlogged here:** `foreach=` / `collect=`, `child_context=`, `--on-human-gate park` + `approve`, rate limiting, and the hexagon lint belong to `contracts/recipe-substrate.v1.md`'s C1–C5. They are proposed there and are not duplicated as clauses here.

---

## Reserved

*Names held — removed or never built — not reintroduced without an amendment.*
- **Removed graph vocabulary:** `runs_on` and `continue_on_fail` as *routing* attributes (§16; `continue_on_fail`'s interaction with C3.4 is the only surviving mention), `requires=` / `outputs=` (§17), `response_schema` (§23), `feedback_from=` (§29), and `join_policy=k_of_n` / `quorum` with `min_success` / `quorum_fraction` (§18).
- **`report_outcome`** (§35, WAVE 5): the tool module is deleted and `metadata.report_outcome` is dead end-to-end; the name is reserved. **Open — needs an owner ruling:** `loop-agent`'s batch **ordering barrier** still keys on a tool named `report_outcome` (`agent_session.py`), and a `report_outcome_convergence.dot` fixture survives. WAVE 5's deletion list does not name the barrier and the EXTENSIONS body still asserts it, so this contract deliberately states no clause about it.
- **Retired worker names** `direct` and `loop-agent`: reserved as fail-loud rename hints only (C1.4).
- **Edge-level fan-out** (T0-4): retired; that behavior belongs to the nlspec and is asserted at `ledger/rows.yaml`'s `ATX-10`, not here.
- **The nlspec's own surface:** no clause here may restate or contradict a clause of `contracts/external/attractor-spec-canonical.md`. Where this engine departs from it, the departure is a ledger row with a decision record — C8.1, C8.2, C11, C12, C13, C15.1 and C16.2 are those departures, named as such.

## Conformance

Per-clause fixtures and checks, and the Freeze Bar's condition-by-condition state, are in
`contracts/FREEZE-PACKET-engine-surface.v1.md`. **No `ledger/rows.yaml` row derives from this
contract yet**, and none may until it is stamped; seeding them is a separate lane's work.

## Changelog

- **1.0.0 — 2026-09-02 — DRAFT.** Initial draft: the contract this repo owns for engine
  behavior beyond the external nlspec. Seventeen Core clauses derived, one per live extension
  surface, from a full census of `specs/EXTENSIONS.md` §1–§44 — the promotion that file's own
  header has been pointing at. Decided in-document: `report_outcome`'s ordering barrier is
  **not** clause material until the owner rules on it (Reserved). Not stamped, not ledgered.
