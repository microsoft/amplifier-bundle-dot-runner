# CONTRACT: engine surface beyond the nlspec, v1

> **DRAFT.** Not FROZEN. Only the owner stamps FROZEN, by editing the `status:`
> line below and adding a dated Changelog entry. Freeze Bar evidence — condition
> by condition, clause by clause — is in the sibling
> `FREEZE-PACKET-engine-surface.v1.md`. This contract does not self-stamp, and
> until it is stamped nothing here binds a lane.

- **id:** `CONTRACT-engine-surface.v1`
- **version:** 1.0.0
- **status:** **DRAFT**
- **date:** 2026-09-02
- **owner:** maintainer
- **repo:** `microsoft/amplifier-bundle-dot-runner`

**Scope.** The behavior of this engine **beyond** the external nlspec vendored at
`contracts/external/attractor-spec-canonical.md`. The nlspec is governed
upstream and asserted clause-by-clause in `ledger/rows.yaml`; **this file governs
the surface the nlspec does not.** Core clauses are derived, one per live
extension surface, from `specs/EXTENSIONS.md` — the decision-record store that
has carried these surfaces "pending promotion to an owned contract
(`contracts/engine-surface.v1`, forthcoming)". This is that contract. Each
clause names the EXTENSIONS section it derives from; that `From` line **is** the
derivation, and a clause citing no live section is not a clause.

**Out of scope.** Anything the nlspec already governs. The five proposed engine
changes in `contracts/recipe-substrate.v1.md` (a separate, paused DRAFT — not
duplicated here). Anything EXTENSIONS marks REMOVED or ABSORBED.

**Census (the negative half, stated so it can be checked).** All 44 EXTENSIONS
sections were read. Excluded, with cause: **§1–§7** ABSORBED UPSTREAM @ `fb57a55`
(the nlspec is the normative text); **§16** `runs_on`/`continue_on_fail`, **§17**
`requires=`/`outputs=`, **§29** `feedback_from=` REMOVED (2026-08-30,
`feat/extensions-rip-3`); **§23** `response_schema` and **§18**'s
`k_of_n`/`quorum` REMOVED (2026-08-31, `feat/extensions-walkback-2`); **§35**'s
`report_outcome` transport REMOVED (2026-08-30, WAVE 5); the **T0-4** note is a
restoration of nlspec behavior, so it is `ledger/rows.yaml`'s (`ATX-10`), not
ours. Every other section is LIVE and appears in exactly one Core clause below.

---

## Core

### C1 — Worker names, and `worker=` selection
*From §40 (LIVE, extension — implementor-level, below the nlspec's §4.5 backend boundary).*

1. The user-facing worker names are exactly `llm-direct`, `coding-agent`, `amplifier-agent`. `spawn` is a reserved sentinel the adapter resolves, not a `WorkerRegistry` key.
2. A node selects one with `worker="<name>"`; a run selects one with `--worker` (CLI) or `worker=` (library) / `orchestrator_config["worker"]`.
3. Selection precedence, highest first: the node's `worker=`; the run-level default; the capability fallback (`spawn` if `session.spawn` resolved, else `llm-direct`).
4. An unrecognized name — on a node or as a run default — raises `ValueError` **naming every known worker**. Never a silent fallback. A retired name (`direct`, `loop-agent`) fails loud with a `renamed: '<old>' -> '<new>'` hint; the hint is an error message, not an alias.
5. `WorkerRegistry.register` refuses a worker missing `clone()` or `close()`.

**Probe.** *Given* a graph node with `worker="direct"`, *When* the run resolves its worker, *Then* it raises `ValueError` whose message contains both `llm-direct` and `renamed`, and no node executes.

### C2 — The default-worker ladder fails loud on a broken install
*From §40 (2026-08-31 addendum, `fix/library-seam-default-worker`: "ONE behavior on both seams — fail loud, never silently degraded").*

1. A bare invocation — no `--worker`/`worker=`, no `bundle=` — resolves to `amplifier-agent` unconditionally, on **both** seams, through one shared resolver (`default_worker.resolve` / `resolve_for_library`).
2. A configured default worker whose module is absent or broken **fails loud**. Degrading to `llm-direct` with a stderr notice is deleted behavior, not deprecated behavior.
3. `worker=` and `bundle=` are mutually exclusive; passing both raises `ValueError` immediately rather than silently overriding one.
4. The two seams differ in exit mechanism only: the CLI exits non-zero; the library raises the catchable `default_worker.WorkerResolutionError`.

**Probe.** *Given* the library seam called with both `worker=` and `bundle=`, *When* `run_pipeline` is invoked, *Then* it raises `ValueError` before parsing the graph; and *Given* a configured-but-broken default worker, *Then* resolution raises `WorkerResolutionError` rather than returning `llm-direct`.

### C3 — `status.json` is the outermost verdict channel
*From §41 (LIVE, conformance restoration of a spec-native channel) and §35's WAVE 4 injected-contract half.*

1. After a node's handler runs, the engine re-reads that node's stage-directory `status.json` (`status_file.py::read_status_override`) — on every retry attempt, before the `must_write=` check, and immediately before each of `CodergenHandler`'s default-outcome writes.
2. Envelope fields: `outcome` (any `StageStatus`, including `skipped`), `preferred_label`, `suggested_next_ids`, `context_updates`, `notes`.
3. Precedence is divergence-gated: an envelope identical to the handler's outcome is a no-op; a **differing** well-formed envelope **wins**, and carries `is_explicit=True` — so it can satisfy a `goal_gate=true` node that bare prose would fail closed (C15).
4. Freshness floor: the file's mtime must postdate the node's execution start, so a stale file from an earlier attempt is never read.
5. Absent ⇒ no-op. Malformed — invalid JSON, non-object, missing/invalid `outcome`, wrong-typed `preferred_label`/`context_updates`/`notes` — is a **loud FAIL** regardless of what the handler returned.
6. Every SPAWN-capable worker is told, in its instruction, the absolute path of its `status.json` and the envelope shape (`status_contract`, via a ContextVar; the `CodergenBackend` protocol is untouched). A spawned process cannot discover that path otherwise.

**Probe.** *Given* a spawn node whose child writes a fresh `status.json` with `outcome: "fail"` while the handler returns SUCCESS, *When* the node completes, *Then* the recorded outcome is FAIL with `is_explicit=True`; *and* the same run with invalid JSON in that file also fails the node, distinct from the absent-file case, which leaves the handler's SUCCESS intact.

### C4 — The spawn lifecycle envelope never becomes a verdict
*From §35 (the half that survives WAVE 5's removal of `report_outcome`).*

1. `orchestrator:complete` carries `status` (`success` | `incomplete` | `cancelled`) and `turn_count`; its `metadata` is always `{}`.
2. The engine recovers **only** that lifecycle status from a spawn result — never an explicit verdict. Explicit verdicts arrive solely through C3.
3. A child producing no output whose lifecycle status is outside `_SPAWN_SUCCESS_STATUSES` is recorded FAIL (`"No output from child session"`), never a silent success.

**Probe.** *Given* a spawn child that returns empty output with lifecycle `status="incomplete"`, *When* the node completes, *Then* the outcome is FAIL with notes naming `No output from child session`, and no envelope field is fabricated.

### C5 — `must_write=` is a fail-closed artifact contract
*From §27 (LIVE, extension — the nlspec is silent on per-node artifact contracts).*

1. `must_write="<path>"` on a node asserts three axes after a non-FAIL handler outcome: the file **exists**; its mtime is **strictly greater** than the wall clock snapshotted immediately before the handler ran; its content has at least one non-whitespace byte.
2. Path resolution matches `requires=`: absolute as-is, relative against `context.target_dir` if set, else `os.getcwd()`.
3. A completed attempt that violates the contract consumes a retry attempt exactly like RETRY; a never-writing node invokes its handler exactly `1 + max_retries` times, then FAILs loudly with a `failure_reason` naming the violated axis, routed through normal failure edges.
4. Neither `allow_partial=true` nor `continue_on_fail=true` softens that FAIL; it carries `is_explicit=False`, so the node cannot satisfy its own `goal_gate`.
5. Presence only. Quality — schema, verdict structure, size — is graph policy, permanently.

**Probe.** *Given* a node with `must_write=".ai/out.md"` and `max_retries=2` whose handler returns SUCCESS but writes nothing, *When* it runs, *Then* the handler is invoked exactly 3 times and the node ends FAIL; *and* the same node whose artifact exists but was written **before** node start also FAILs (mtime equal to start time is a failure, not a pass).

### C6 — Graph-level `$name` params resolve at parse time, and cross the sub-pipeline boundary
*From §43 (LIVE, pure addition in a spec-silent area) and its dated addendum of 2026-09-02.*

1. `parse_dot()` accepts `params: dict[str, str]`. When a graph-level **duration** attribute — today only `max_pipeline_duration` — holds a bare `"$name"` token (the entire stripped value, never a substring), the parser substitutes `params[name]` and parses the result as if written literally. Every other value parses unchanged.
2. A `$name` with no supplied param raises `ValueError` **before any model call**, naming the missing param **and the mechanism that supplies it on the path in use**: `--param name=value` on the CLI, `config["params"]` when the orchestrator is mounted, or the parent graph's own params when composed as a child. There is no shell-style default, and none is planned: an absent fuse value must never become "no fuse".
3. Every parse site threads `params` — including child graphs (`shape=folder`/`dot_file=`, manager-loop child dotfiles), the mounted `PipelineOrchestrator`, the MATERIALIZE-time reference walk, and `dot-runner lint` (which accepts `--param`; since lint executes nothing, any parsing placeholder suffices).
4. **Params cross the sub-pipeline boundary, by design.** A child parses with the same mapping its own nodes expand at execution time, symmetric with node-level `$param` (C17.1). An LLM *session* deliberately does **not** cross that boundary (§11 / C13.3); a param mapping does, being declarative and serializable.
5. Standing rule: the default is now "crosses". **Any future graph-level param that must not cross requires its own decision record saying so.**
6. This is independent of node-level expansion: that resolves at execution time from `graph.params_values` and carries no fail-loud contract for a missing key.

**Probe.** *Given* a parent graph with `max_pipeline_duration="$max_duration"` and a `dot_file=` child declaring the same attribute, *When* parsed with `params={"max_duration": "10m"}`, *Then* both resolve; *and* omitting the param raises `ValueError` naming `max_duration` and the supplying mechanism, with zero nodes executed.

### C7 — Subscription providers, and the intent rule
*From §44 (LIVE, extension — the nlspec's `llm_provider` is an open set; "this entry is exactly that 'etc.'").*

1. Exactly two subscription providers exist: `github-copilot` and `openai-chatgpt`. Both are servable only by the spawn workers (`coding-agent`, `amplifier-agent`), never by `llm-direct`.
2. Detection lives in one registry (`provider_detection.PROVIDER_SPECS`); the three native providers delegate verbatim to `unified_llm.client.detect_configured_providers` and are never re-declared. `unified-llm-client` stays pure and never grows a subscription entry.
3. `github-copilot` is configured iff `COPILOT_AGENT_TOKEN` or `COPILOT_GITHUB_TOKEN` is set, **or** `GH_TOKEN`/`GITHUB_TOKEN` is set *and* the run explicitly asks for it.
4. **Intent rule.** Copilot-specific env names carry intent by their name and always count. Generic GitHub tokens carry none and count only when some node in the raw DOT source declares `llm_provider="github-copilot"` — detected by a conservative regex scan over the source, because worker resolution runs before the graph is parsed. `openai-chatgpt` needs no such rule: it is configured iff a non-empty OAuth cache exists at `~/.amplifier/openai-chatgpt-oauth.json` (overridable for tests via `AMPLIFIER_OPENAI_CHATGPT_OAUTH_PATH_OVERRIDE`), whose existence already means a human completed a login flow.
5. Declaring either provider under `llm-direct` fails loud **before** the generic no-adapter error, naming the fix: add `--worker coding-agent` (or `amplifier-agent`), or use `anthropic`/`openai`/`gemini`.
6. When exactly one provider is mounted, a node declaring no `llm_provider` defaults to it. Zero or multiple mounted preserves the literal `anthropic` fallback, unchanged.

**Probe.** *Given* an environment with only `GITHUB_TOKEN` set and a DOT source declaring no `llm_provider`, *When* providers are detected, *Then* `github-copilot` is **not** configured; *and* the same environment with a node declaring `llm_provider="github-copilot"` **does** configure it.

### C8 — Per-provider default model for `llm_provider`-alone nodes
*From §42 (LIVE, extension — implementor-level content for the nlspec's §8.5 rung 4, explicitly not a divergence).*

1. A node with an explicit `llm_provider` (raw or stylesheet-resolved) and no `llm_model` resolves a **family token**, never a literal model id, live via `resolve_latest_for`: `anthropic` → `sonnet` (stable only); `openai` → `gpt-5.*[0-9]` (stable only, anchored so `-mini`/`-codex` siblings cannot outrank the bare release); `gemini` → `gemini-3*pro*` (stable-only filtering disabled, because the flagship is itself preview-named).
2. Rung order is unchanged: explicit `llm_model`; `model_stylesheet`; graph-level default (unimplemented); then this rung.
3. A node with **neither** `llm_model` nor `llm_provider` still fails loud. An unknown provider fails loud naming the provider and the known-defaults set — never a silent guess.
4. The two subscription providers have no rung-4 row and none is planned; `_resolve_concrete_model` fails loud before attempting a live resolve, naming the real fix (an explicit concrete `llm_model`, or none at all).

**Probe.** *Given* a node with `llm_provider="anthropic"` and no `llm_model`, *When* the model is resolved, *Then* a concrete stable Sonnet id is returned; *and* `llm_provider="github-copilot"` with `llm_model="sonnet"` fails loud before any resolution attempt.

### C9 — The wall-clock fuse is enforced at node granularity
*From §15 (LIVE, extension — `max_pipeline_duration` is not in the nlspec; 2026-08-31 `attractor-674` update).*

1. Graph attribute `max_pipeline_duration` bounds the whole run against `time.monotonic()`.
2. Enforcement is not only between steps: each node dispatch is bounded by `min(remaining fuse budget, the node's own timeout=)` — the tighter wins, and the fuse always wins a tie, since whole-pipeline termination must never be masked as an ordinary node timeout.
3. A mid-node overrun terminates the node (`_terminate_fuse_mid_node`) after a bounded cancellation grace (`_FUSE_CANCEL_GRACE_S`, 5s) and ends the run `status=FAIL` with `failure_reason="max_pipeline_duration_exceeded"`.
4. Stated, not silently expanded: mid-node fuse termination exits directly and does not route through recovery/postmortem machinery.

**Probe.** *Given* a graph with `max_pipeline_duration` shorter than a single node's runtime, *When* run, *Then* the run ends FAIL with `failure_reason="max_pipeline_duration_exceeded"` while that node is still in flight — not after it completes.

### C10 — Provider preflight refuses before the first node
*From §36 (LIVE, extension — the nlspec is silent on provider mounting and credentials).*

1. Before the walk begins, on **both** entry points (`PipelineOrchestrator.execute()` and `drive_engine()`), every LLM-consuming node's declared `llm_provider` is checked for serviceability — statically, with no live API call: a mounted provider module, or a `profiles` entry whose credential env var is present.
2. A failure raises `ProviderPreflightError` naming **each** failing node, its provider, and the missing credential. Zero nodes execute; zero budget is spent.
3. A profile that names no adapter this run can resolve is refused at startup too, naming the node, the profile, and what **is** resolvable. Unknowability is honest: `None` means "not knowable on this path" and skips the clause; a discovery crash yields the empty set and refuses, never a false accept.
4. At dispatch, spawn-path profile resolution failure raises `ValueError` naming the node, the provider, the mounted profiles, and the credential to set — terminal in the retry ladder, never a crash loop.
5. Bounded by construction, and documented as such: presence is checked, never validity; the implicit default provider and nested child graphs are out of scope; an injected backend skips the preflight.

**Probe.** *Given* a graph whose node declares `llm_provider="openai"` with `OPENAI_API_KEY` unset, *When* run, *Then* `ProviderPreflightError` names that node, `openai`, and `OPENAI_API_KEY`, and the run's trace contains no node execution.

### C11 — Refusal, not degradation, at dispatch and at routing
*From §38 and §33 (both LIVE divergences from the nlspec, both `declining` upstream, safety property: no silent execution-class substitution, no silent green run).*

1. An unknown node shape hard-fails at dispatch: `HandlerRegistry.get()` raises `ValueError` naming the shape, the node id, the complete supported-shape list, and the remedy. The nlspec's `SHAPE_TO_HANDLER.get(shape, "codergen")` default is deliberately absent.
2. A main-loop dead end where outgoing edges exist but none matches terminates the pipeline `status=FAIL` with a `PIPELINE_ERROR` carrying `error_type=no_matching_edge`, rather than the nlspec's `Outcome(SUCCESS, "Pipeline completed")`.
3. A **designed** terminus — a node with no outgoing edges at all — is unaffected; the last outcome stands.
4. `run_subgraph()` returns `Outcome(FAIL, is_explicit=False)` on the same conditional mismatch.

**Probe.** *Given* a node with `shape=septagon`, *Then* the run raises naming the shape and listing supported shapes; *and given* a node with two `condition=`-bearing edges that both evaluate false, *Then* the run ends FAIL with `error_type=no_matching_edge`, while an edgeless terminal node ends with its own outcome.

### C12 — Sub-pipeline nodes: `shape=folder` / `dot_file=`
*From §10 (LIVE, extension — "additive and non-shadowing; `folder` is not a spec-assigned shape").*

1. A `shape=folder` node with `dot_file=` runs a child graph.
2. Path resolution has a fixed four-tier precedence: absolute; `graph.source_dir`; `context.target_dir` / `--cwd`; `os.getcwd()`.
3. Resolution is lazy — `validate()` performs no existence check — and an unresolvable child fails at **node entry** with `ChildDotResolutionError` (`error_type="child_dot_resolution"`), routable through normal failure edges rather than crashing the run.
4. The absent-child case is additionally surfaced ahead of time by lint rule `TOPO-010` at WARNING severity (C20).

**Probe.** *Given* a folder node whose `dot_file=` names a missing file, *When* run, *Then* the failure occurs at that node with `error_type="child_dot_resolution"` and a failure edge is taken; *and* `dot-runner lint` on the same graph emits exactly one `TOPO-010` warning and exits 0.

### C13 — Session and thread scoping
*From §8, §9, §11, §12, §13 (all LIVE; §9 and §13 resolve a self-contradiction in the nlspec, §11 and §12 sit in spec-silent space).*

1. Sessions are isolated per parallel branch: a branch runs on a branch-scoped engine with a cloned backend.
2. `thread_id` is **branch-local**. The same explicit `thread_id` in two sibling branches shares no history; the nlspec's §3.8 isolation is given precedence over its §5.4 thread reuse. Sequential same-`thread_id` reuse is unchanged.
3. A sub-pipeline or manager-loop child is a **fresh session boundary**: `thread_id` continuity does not cross it. Continuity does hold inline and across flattened `subgraph cluster_*`.
4. `fidelity=full` is realized by a `parent_messages` carrier at node-exchange granularity — one user/assistant pair per node; an empty output is carried as a synthesized marker rather than dropped.

**Probe.** *Given* two sibling parallel branches whose nodes both declare `thread_id="t"`, *When* both run, *Then* neither sees the other's messages; *and given* the same `thread_id` on two sequential nodes, *Then* the second sees the first's exchange.

### C14 — `reasoning_effort` has no engine-injected default
*From §39 (LIVE divergence from the nlspec's §2.6 / Appendix A, `declining` upstream; safety property: no hidden engine default on a provider-mode-switching surface).*

1. `Node.reasoning_effort` defaults to `None`; the parser injects nothing. Appendix A's `"high"` does not hold here.
2. The value is passed through as authored; on the spawn path, `None` keys are dropped so the child orchestrator uses its own default.
3. `model_stylesheet` is the only other source of a value.

**Probe.** *Given* a node declaring no `reasoning_effort` and no stylesheet rule, *When* the request is built, *Then* no `reasoning_effort` key is present — asserted as absence, not as `"high"`.

### C15 — The goal gate is fail-closed
*From §25 (LIVE divergence from the nlspec's §4.5, which returns SUCCESS unconditionally).*

1. `Outcome.is_explicit` defaults to `False`; plain prose parses to RETRY, not SUCCESS.
2. A `goal_gate=true` node is satisfied only when its outcome is both successful **and** explicit.
3. `is_explicit` is serialized into `status.json` and `trace.jsonl`, so the distinction is auditable after the run.

**Probe.** *Given* a `goal_gate=true` node whose worker returns prose containing the word "done", *When* the gate is checked, *Then* the outcome is RETRY; *and* the same node with a divergent `status.json` (C3) is satisfied.

### C16 — `outcome=` resolves `preferred_label` first
*From §22 (LIVE divergence from the nlspec's §10.4, which defines `outcome` as `outcome.status` only; ledgered historically as `ATX-5`).*

1. In an edge `condition="outcome=<x>"`, the `outcome` key resolves to `outcome.preferred_label` when one is set, falling back to `outcome.status`.
2. This is explicitly not behavior-neutral, and is why an explicit label can steer routing a bare status could not.

**Probe.** *Given* a node whose outcome is `status=success, preferred_label="needs_rework"` and two edges conditioned on `outcome=success` and `outcome=needs_rework`, *When* the edge is selected, *Then* the `needs_rework` edge wins.

### C17 — Additive graph vocabulary
*From §21, §20, §19, §14, and §18's retained half (all LIVE, all "additive — a graph that does not use it behaves as in canonical").*

1. **`$param` / `${key}`** (§21): prompt and attribute substitution accepts these alongside `$goal`, resolved against pipeline context by plain string replacement — substitution, never templating.
2. **Tool node** (§20): `shape=tool` accepts `parse_json` (stdout JSON into context) and `tool_env`, and exposes routing key `tool.last_line` alongside `tool.output`.
3. **`wait.human` freeform** (§19): a gate may accept open text and file attachments, not only accelerator-key choices; accelerator-key gates behave as in canonical.
4. **`allow_partial` on timeout** (§14): the canonical attribute gains a second trigger — a node timeout yields `PARTIAL_SUCCESS` rather than FAIL. Call sites accept `True` and `"true"` alike.
5. **`error_policy`** (§18, retained half): `fail_fast` / `continue` / `ignore` on a parallel node remain load-bearing. `k_of_n` / `quorum` are removed and reserved (below).

**Probe.** *Given* a graph exercising each of the five, *When* run, *Then* each behaves as stated; *and given* the same graph with each attribute removed, *Then* behavior is exactly the canonical baseline — the additive half asserted alongside the feature half.

### C18 — The run directory is the audit trail
*From §24, §26, §28, §30 (all LIVE; §24 additionally records a divergence — convergence iterations reset in-process rather than re-launching the run).*

1. **Per-iteration records** (§24): `logs_root/iteration_N/<node_id>/status.json` alongside the flat path; context keys `$iteration` / `$loop_count`; an append-only `logs_root/trace.jsonl` of `{iteration, node_id, status, preferred_label, duration_ms, ts}`, readable via the `trace` subcommand.
2. **Worker session observability** (§26): `Outcome.response_text` durably written to `<stage_dir>/response.md`; `session_id` in `status.json`; real session events under `<stage_dir>/sessions/<session_id>/events.jsonl`. Redaction is fail-loud: a redacted span is `[REDACTED:<shape>]` with a counted summary, and a redaction error withholds the payload rather than emitting it.
3. **Provenance** (§28): `manifest.json` carries `engine_version` and `engine_commit` (`"unknown"` when undeterminable, never fabricated); the standalone runner adds `runner_version`, `runner_commit`, `provider`.
4. **Attempt and cycle observability** (§30): `Outcome.attempt_count`; `attempt` on `pipeline:node_complete`; a bounded `failed_step` capture; `cycle_index` on manager-loop and subgraph records; `pipeline:stage_retrying` carrying `reason="exception:<Type>"`; `_branch_id` scoping.

**Probe.** *Given* a two-iteration convergence run with one retried node, *When* it completes, *Then* `trace.jsonl` has one record per node per iteration, `iteration_1/` and `iteration_2/` both exist, `manifest.json` carries a non-empty `engine_version`, and the retried node's `status.json` reports `attempt_count > 1`.

### C19 — Admission-time validation is narrower than the nlspec, and says so
*From §31 (LIVE; half conformance restoration, half a plainly-stated narrowing).*

1. `validate()` rejects a negative retry budget on `max_retries`, `default_max_retry`, `default_max_retries` — a restoration, since the nlspec already types these as Integer.
2. `validate()` rejects a node carrying `tool_command` whose effective handler is not the tool handler. This **refuses a graph the nlspec would admit and run**; the narrowing is recorded as such rather than filed as "tightening validation".

**Probe.** *Given* a graph with `max_retries=-1`, *Then* validation fails naming the attribute; *and given* a `shape=box` node carrying `tool_command`, *Then* validation fails naming the handler mismatch — both before execution.

### C20 — `lint` is a separate entry point with an exit contract
*From §32 (LIVE, extension — the nlspec's §7.4 explicitly permits additional lint rules).*

1. `dot-runner lint <file.dot>` runs a rule set independent of `validate()`: `TOPO-001`…`TOPO-010` and `VOCAB-001`.
2. Exit contract: errors exit 1; warnings exit 0 unless `--strict`. A WARNING never blocks execution.
3. `graph.source_dir` is seeded for `TOPO-010`, and `--param` is accepted (C6.3) so a parameterized graph lints without executing.

**Probe.** *Given* a graph whose only finding is a `TOPO-010` warning, *When* linted, *Then* exit code is 0 and the warning is reported; *When* linted `--strict`, *Then* exit code is 1.

### C21 — `suggested_next_ids` is coerced, and a dead end explains itself
*From §34 (LIVE — "a bug fix restoring intended behavior, not a new extension").*

1. At edge selection, a suggested id that is a string passes through and an `int` is coerced with `str()`; `bool`, `float`, `dict`, `list`, and `None` are rejected and logged rather than silently stringified.
2. The same coercion applies at the goal-gate retry lookup.
3. The no-matching-edge message (C11.2) keeps its `No matching edge from node 'X'` prefix and appends both the suggested ids and the existing edge targets.

**Probe.** *Given* an outcome with `suggested_next_ids=[3]` naming a node id `"3"`, *Then* that edge is selected; *and given* `suggested_next_ids=[True]`, *Then* it is rejected and the failure message lists the real edge targets.

### C22 — Bundle composition
*From §37 (LIVE — "a pure addition in an area the canonical spec does not address").*

1. The root bundle carries always-on guidance via its `context:` key.
2. `agents/attractor-expert.md` is registered as `attractor:attractor-expert` by the core behavior and the root bundle.
3. Same-repo module and skill sources are ref-free (`../modules/X`, `@attractor:skills`), because a ref-pinned same-repo source resolves against the installed app's bundle directory rather than this repo.

**Probe.** *Given* a fresh install of this bundle, *When* the agent roster is listed, *Then* `attractor:attractor-expert` is present; *and* no same-repo source in the bundle carries a git ref.

---

## Backlogged

Candidate clauses, each with a named promotion trigger. None binds today.

- **B1 — Preflight beyond the statically detectable class** (§36's honest residual). Today the implicit default provider, nested child graphs, and key *validity* are out of scope. **Trigger:** a measured run that drains budget on a misconfiguration this preflight could not have seen.
- **B2 — `must_write=` delayed-replant window and session attribution** (§27's residual, documented by `test_case4_delayed_replant_informational`). **Trigger:** a measured false pass caused by an external writer landing inside the window.
- **B3 — Retired worker-name hints** (`RENAMED_WORKER_NAMES`, C1.4). **Trigger:** the one-release migration window closes; the hint clause is then deleted, not softened.
- **B4 — Graph-level default model** (§42's rung 3, unimplemented by any layer). **Trigger:** a graph that needs one model for many nodes without a stylesheet.
- **B5 — The ambiguous multi-mount provider default** (§44.9: zero or multiple mounted providers preserve the literal `anthropic` fallback). **Trigger:** a measured wrong-provider run caused by that fallback.
- **Not backlogged here:** `foreach=`/`collect=`, `child_context=`, `--on-human-gate park` + `approve`, rate limiting, and the hexagon lint are `contracts/recipe-substrate.v1.md`'s C1–C5. They are proposed there and are not duplicated as clauses here.

---

## Reserved

Names and attributes held — removed or never built, and not to be reintroduced without an amendment.

- **Removed graph vocabulary:** `runs_on` and `continue_on_fail` as *routing* attributes (§16; `continue_on_fail`'s interaction with C5.4 is the only surviving mention), `requires=` / `outputs=` (§17), `response_schema` (§23), `feedback_from=` (§29), `join_policy=k_of_n` / `quorum` with `min_success` / `quorum_fraction` (§18). Reintroducing any of these names is an amendment, not a feature.
- **`report_outcome`** (§35, WAVE 5): the tool module is deleted and `metadata.report_outcome` is dead end-to-end. The name is reserved. **Open — needs an owner ruling:** `loop-agent`'s batch **ordering barrier** still keys on a tool named `report_outcome` (`agent_session.py`), and a `report_outcome_convergence.dot` fixture survives. WAVE 5's deletion list does not name the barrier and the EXTENSIONS body still asserts it, so this contract deliberately states no clause about it.
- **Retired worker names:** `direct`, `loop-agent` — reserved as fail-loud rename hints only (C1.4).
- **Edge-level fan-out** (T0-4): retired; `best_by_weight_then_lexical` is the sole non-parallel edge-selection path. That behavior belongs to the nlspec and is asserted at `ledger/rows.yaml`'s `ATX-10`, not here.
- **The nlspec's own surface:** no clause here may restate or contradict a clause of `contracts/external/attractor-spec-canonical.md`. Where this engine departs from it, the departure is a ledger row with a decision record — C11, C14, C15, C16, C18.1 and C19.2 are those departures, named as such.

---

## Conformance

Per-clause fixtures, checks, and the Freeze Bar's condition-by-condition state live in
`contracts/FREEZE-PACKET-engine-surface.v1.md`. Ledger rows deriving from these
clauses are a separate lane's work; **no `ledger/rows.yaml` row derives from this
contract yet**, and none may until it is stamped.

---

## Changelog

- **1.0.0 — 2026-09-02 — DRAFT.** Initial draft: the contract this repo owns for
  engine behavior beyond the external nlspec. Twenty-two Core clauses derived,
  one per live extension surface, from a full census of `specs/EXTENSIONS.md`
  §1–§44 — the promotion EXTENSIONS.md's own header has been pointing at
  ("pending promotion to an owned contract"). Recorded as decided-in-document:
  that `report_outcome`'s ordering barrier is **not** clause material until the
  owner rules on it (Reserved). Not stamped; not ledgered; binds nothing.
