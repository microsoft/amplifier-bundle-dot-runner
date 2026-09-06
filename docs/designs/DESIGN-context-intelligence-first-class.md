# DESIGN: context intelligence first-class across dot-runner runs

- **status:** PROPOSAL — for owner review. Nothing here binds a lane until ratified.
- **date:** 2026-09-06 · **tracker:** `attractor-soo` · **repo:** `microsoft/amplifier-bundle-dot-runner`
- **scope:** design only. No code, no contract clause, no `specs/EXTENSIONS.md` entry lands with this doc.

**Why this doc lives here, not in `amplifier-bundle-attractor`.** All four surfaces it touches are
owned by this repo and by nothing else: the node-visit boundary (only the engine knows when one
opens and closes — `engine.py:862-866`); the telemetry seam the engine already exposes to observers
(`worker_observability.py:33-35`); the parity kit that pins per-worker obligations
(`modules/worker-parity-kit/`); and `contracts/engine-surface.v1.md` C15. `amplifier-bundle-attractor`
consumes this engine and owns none of them. Per `docs/VISION.md`, context intelligence (CI) is
implementor-level observability **below** the nlspec's §4.5 backend boundary — the tier of C1's
worker names.

---

## 1. Problem, and today's reality

The maintainer directive (2026-08-29) asks that the `llm-direct` worker stop being a declared
telemetry absence. The finding of this design pass is that patching `llm-direct` would fix the
wrong layer. **No dot-runner run is reconstructible today, for any worker**, because the engine
mints no identity at all: not for the run, not for the node visit, not for the worker session.
What identity exists is whatever a worker happens to carry, recorded passively.

### 1.1 Engine-level: three missing identities

| Identity | State today | Evidence |
|---|---|---|
| **run id** | Never minted. Run identity is the run *directory path*; the default path is a fixed constant, so two runs in one process overwrite each other's `manifest.json`. `internal.run_id` is **read and never written** outside a test. | `__init__.py:443-447`; `fidelity.py:219-221`; `manifest.json` fields at `engine.py:1871-1878` carry `start_time`, not an id |
| **node-visit id** | `execution_index` exists — on the event bus (`pipeline:node_start`, `pipeline:node_complete`) and in the checkpoint — but is **absent from `status.json` and `trace.jsonl`**, the two durable records. | minted `engine.py:862-866`; emitted `engine.py:874`, `:1155`; checkpointed `engine.py:1815`; *not* in the status payload `engine.py:1905-1928` nor the trace record `engine.py:1949-1958` |
| **worker-session id** | Set on `Outcome` **only** on the spawn path, read out of the child's result dict. The registry-worker path never sets it and structurally cannot: the seam forbids it. | set `backend.py:863-865`; registry path `backend.py:911-930` returns `outcome` untouched; `Outcome.session_id` default `outcome.py:52-53` |

The seam rule is deliberate, and the design must not break it — `workers/worker_protocol.py:6-9`:

> A worker is stateless per node visit: `(prompt, context, replayed history)` in,
> `(output, outcome)` out. **No session identity ever crosses the seam.**

### 1.2 Per-worker: what each emits, what is missing

| Worker | Session id it holds | Does the engine see it? | Events it emits | Reaches `sessions/<id>/events.jsonl`? |
|---|---|---|---|---|
| `llm-direct` | **none, by design** (`worker_protocol.py:6-9`) | no — `outcome.session_id` stays `None` | `provider:request` / `:response` / `:error`, keyed by **`node_id`** via a ContextVar (`direct_worker.py:233-242`, `:273-298`, node scoping `:231`) | **no.** The persister returns early on a missing `session_id` (`session_events.py:147-149`), so no session directory is ever created for an `llm-direct` node |
| `coding-agent` | a private `uuid4` (`agent_session.py:163`) | **no** — `execute()` returns a bare `str` (`loop-agent/.../__init__.py:514`, `:580`); `orchestrator:complete` carries no `session_id` (`__init__.py:627-637`) | 16 `agent:*`, plus kernel `tool:pre`/`tool:post` and `provider:*`. Only `agent:session_start` (`agent_session.py:391-393`) and `agent:awaiting_input` (`:567-570`) carry the uuid | **no.** `agent:*` is not in `PERSISTED_SESSION_EVENTS` (`session_events.py:81-90`), so the one event carrying the uuid is dropped |
| `amplifier-agent` | `dot-runner-thread-<slug>` or `engine-<uuid4>` (`__init__.py:673-677`), stamped onto the hosted session as a default field (`__init__.py:586-591`) | **no** — it is a local in `_run_turn`; the engine records the *spawn-boundary* id instead (`backend.py:863-865`). Two ids name one node visit | whatever the hosted kernel emits, attributed by the `set_default_fields` stamp | **yes** — the only worker for which the C15.2 forensic path resolves |

`set_default_fields` — the mechanism that makes the `amplifier-agent` path work — is stamped on
the **hosted inner session's** hooks, one turn at a time (`__init__.py:588-591`). It is the
correct mechanism. It is applied in exactly one worker, by that worker, from an id the engine
never learns.

### 1.3 The parity kit asserts nothing here

`telemetry_session_id` is TARGET #7 (`suite.py:245`) with an **empty probe config**
(`suite.py:271`), so the check reduces to: skip if declared absent (`suite.py:322-326`); survive
being asked (`suite.py:328-332`); no warning naming it (`suite.py:341`). Of the three wired
workers, `llm-direct` skips by declared absence (`loop-pipeline/tests/test_worker_parity.py:55-57`)
and `coding-agent` passes green — a green its own test file calls **"VACUOUSLY"**, adding that
"the kit's TARGET bar doesn't distinguish 'handled' from 'nothing here to mishandle'"
(`loop-agent/tests/test_worker_parity.py:56-72`). **No worker has ever been positively proven to
stamp anything.** The kit cannot fix this at its current seam: `TurnResult` has four fields and
none can carry telemetry (`worker_parity_kit/protocol.py:58-61`).

Secondary defect: the shipped `DirectWorker` declares one absence (`direct_worker.py:101`)
while its test harness declares three (`test_worker_parity.py:55-57`).

### 1.4 Context intelligence never sees a run

CI ingests exactly one on-disk shape —
`~/.amplifier/projects/{project_slug}/sessions/{session_id}/context-intelligence/{events.jsonl, metadata.json}`
— discovered by finding a `metadata.json` whose parent directory is named `context-intelligence` and
whose `format` is `"context-intelligence"`. Its hard capture requirement is one field: an event without
`data.session_id` is silently dropped. Its graph is built server-side; `parent_id` on every event is
what builds the delegation tree. The run dir's `sessions/<id>/events.jsonl` (C15.2) is a *different*
layout, a *different* record shape (three keys; CI's on-disk line has four, including `workspace`), and
carries **no `metadata.json` at all** — so it is invisible to CI's file-scan replay path, by construction.

One piece of plumbing already exists: `hooks-pipeline-observability` registers every `pipeline:*` event
name into the `observability.events` capability (`hooks-pipeline-observability/.../__init__.py:110-112`),
which is precisely the set the CI hook subscribes to. **When a run executes inside an Amplifier session,
its pipeline events are already being captured.** What is missing is not the channel — it is the
correlation fields: `pipeline:start` carries `graph_name / node_count / edge_count / goal / dot_source`
(`engine.py:310-319`) and no run id, so two runs in one session interleave with no discriminator.

## 2. Goals / non-goals

**Goals.** (G1) From a run — live or after the fact — reconstruct the full tree: run → node visits
→ worker sessions → LLM calls. (G2) One mechanism for all three workers; no per-worker special
cases. (G3) The parity kit's `telemetry_session_id` TARGET becomes a positive, RED-provable
assertion. (G4) Zero changes required in the CI server repo. (G5) C15.2's existing forensic path
keeps working byte-for-byte.

**Non-goals.** Not a new session store (§26 already rejected one). Not moving persistence upstream
into foundation's spawn path (§26's walk-upstream note stands; this is the interim seam). Not a
new graph vocabulary — no DOT attribute is added. Not a CI-server node type or lifter. Not
retiring the run directory: it stays the durable record, because `$HOME` is ephemeral in CI and
sandbox environments (§26's stated rationale).

## 3. The holistic model

**One spine, minted by the engine, stamped by the seam.**

```
run_id                     minted once, at engine construction
  └── visit_id             {run_id}/{node_id}/{execution_index} — one per node visit
        └── session_id     the telemetry identity for that visit
              └── LLM calls, tool calls — already emitted, now attributable
```

**M1 — The engine mints run and visit identity.** `run_id` at construction; `visit_id` at the
existing increment site (`engine.py:862-866`), where the visit boundary is already computed. Both
land on every `pipeline:*` payload and in every durable record (`manifest.json`, `status.json`,
`trace.jsonl`) — additive to payloads that already exist.

**M2 — The engine, not the worker, names the visit's telemetry session.** The inversion the whole
design rests on. Today the engine passively receives an id from whichever worker happens to have
one. Instead: the engine derives a deterministic `visit_session_id` from `(run_id, node_id,
execution_index)` and publishes it on a ContextVar **beside the one it already owns** —
`current_worker_sessions_dir` (`worker_observability.py:33-35`), set and reset by the codergen
handler around each backend call (`handlers/codergen.py:137-139`, `:156`). The persister then keys
on `data["session_id"]` when present and falls back to that value when absent
(`session_events.py:147-149` becomes a fallback, not an early return).

This does not violate the seam. `worker_protocol.py:6-9` forbids **session identity crossing the
worker seam** — a statelessness rule about the worker's own execution. The engine naming a
*telemetry* identity for a node visit is a different thing on a different channel, published to
observers rather than to the worker; `Worker.run` keeps its four arguments and its `(str, Outcome)`
return. Per worker:

| Worker | Effect |
|---|---|
| `llm-direct` | Its node-keyed `provider:*` events (`direct_worker.py:233-298`) acquire a session key. A `sessions/<visit_session_id>/events.jsonl` appears for the first time. No change to `DirectWorker`. |
| `coding-agent` | Its kernel `tool:pre`/`tool:post` and `provider:*` acquire the same key. Its private uuid (`agent_session.py:163`) becomes reconcilable — or deletable (item 7). |
| `amplifier-agent` | Unchanged and authoritative: it already stamps a real session id (`__init__.py:588-591`). The visit session id becomes that session's **parent**, not its replacement — two ids, correctly nested, instead of two ids naming one thing. |

**M3 — A node visit is modelled as a delegation.** CI builds its tree from `parent_id` on every
event, and lifts `sub_session_id` / `parent_session_id` off delegation-shaped events into queryable
properties. So the engine emits, at each visit, a delegation-shaped event naming the visit session
as child of the run's own session; CI's existing handlers then reconstruct
`run → visit → worker session → LLM call` **with no server-side change** (G4). Invented field names
would not work: an unrecognised key lands in the `data` JSON string, which Cypher cannot address.

**M4 — Durability, mechanical.** Alongside the C15.2 stream, the persister also writes CI's on-disk
contract — `<stage_dir>/sessions/<sid>/context-intelligence/{events.jsonl, metadata.json}` — in CI's
exact record shape, so `context-intelligence-upload <run_dir>` ingests a completed run whose live
push never happened (a `dot-runner` CLI run outside any Amplifier session). C15.2 is untouched.

## 4. The parity-kit telemetry TARGET, as it should read

Once identity is engine-minted, the TARGET stops asking a question `llm-direct` can never answer
("do you have a session id?") and asks the one every worker must answer:

> **`telemetry_session_id`** — *Given* the harness seeds a telemetry session identity for the
> turn, *When* the worker runs, *Then* **every** event the worker emits carries that identity in
> its payload. A worker that emits no events passes vacuously **only if** it declares
> `no_events` — silence is declared, not inferred.

Assertable at the generic seam by one addition: a fifth `TurnResult` field carrying the events the turn
emitted (`protocol.py:58-61`), and a seeded id in the probe config, replacing today's `{}`
(`suite.py:271`). Consequences: `llm-direct`'s declared absence retires — it becomes a positive pass;
`coding-agent`'s vacuous green becomes a real pass or a real failure, retiring the disclaimer at
`loop-agent/tests/test_worker_parity.py:56-72`; and the kit gains its first TARGET-tier broken-worker
fixture (today `broken_worker.py` RED-proofs M2/M3 only).

## 5. nlspec check

`docs/VISION.md`'s decision matrix, applied surface by surface. Per `AGENTS.md` step 1, the
canonical text was read first, holistically: §9.6 defines the engine's event vocabulary, §5.6 the
run-directory tree, and §4.11's manager loop *consumes* child telemetry (`ingest_child_telemetry`)
— so telemetry is a spec-acknowledged concern, and identity on the event stream is spec-named.

| Surface | Tier | Basis |
|---|---|---|
| **`run_id`** | **Conform-fix — easy yes** (`AGENTS.md` step 2) | canonical §9.6 specifies `PipelineStarted(name, **id**)`. This engine emits `pipeline:start` with no id (`engine.py:310-319`). The spec names it; we do not mint it. |
| **visit identity durably recorded** | near-conform | canonical §9.6's `StageStarted(name, **index**)`. `execution_index` already exists on the bus (`engine.py:874`); this only makes it durable. |
| **engine-minted worker-session telemetry; CI on-disk contract in the run dir** | **uncharted — the toll is owed** | the nlspec is silent on session-level telemetry. VISION: "the silence has to be argued rather than assumed, and what ships there stays additive and non-interfering." Additive: no DOT vocabulary, no behavior change to any conforming graph. `AGENTS.md` step 3 asked whether this can live outside the engine — **partly**: persistence already does (a hooks module), but the visit boundary cannot; only the engine knows it. |

**Existing entries touched.** `specs/EXTENSIONS.md` **§26** (worker-session observability) is
extended, not replaced — its forensic-navigation contract stays valid and gains a second
destination, and its walk-upstream note is unaffected; **§28** (provenance) gains `run_id` in
`manifest.json`; **§30** (attempt/cycle) gains `visit_id` beside `execution_index`; **§24**
(per-iteration records) gains both fields in `trace.jsonl`. **Contract clauses touched:** C15.2,
C15.3, C15.4 — all DRAFT and unstamped, so "nothing here binds a lane" yet
(`engine-surface.v1.md:3-6`): edits to a draft, not amendments to a frozen surface.

**Is a new clause implied?** Yes — C15 claims the run *directory* is the audit trail, a purely
local claim; making CI first-class adds a second, externally consumed surface. Proposed text,
**for the owner to add or reject — not added by this doc**:

> ### C18 — Run identity is engine-minted and externally reconstructible
> *From §26 (extended), §28, §30; conform-fix against canonical §9.6's `PipelineStarted(name, id)`.*
>
> 1. The engine mints a `run_id` at construction and a `visit_id` per node visit, derived from
>    `(run_id, node_id, execution_index)`. Both appear on every `pipeline:*` payload and in
>    `manifest.json`, `status.json`, and every `trace.jsonl` record.
> 2. The engine publishes a `visit_session_id` for each node visit on the observer seam it already
>    owns (`worker_observability`), alongside the sessions directory. The `Worker` protocol is
>    unchanged: no session identity crosses the worker seam.
> 3. An observer keys a worker event on the event's own `session_id` when present, and on the
>    engine-supplied `visit_session_id` when absent. No event is dropped for want of a key.
> 4. A worker that carries its own session identity keeps it; the visit session is that session's
>    parent, never its replacement.
> 5. Alongside the C15.2 stream, each worker session is written in the context-intelligence
>    on-disk contract, so a completed run is ingestible by file-scan replay with no server change.
>
> **Probe.** *Given* a two-node run whose first node uses `llm-direct` and second uses
> `amplifier-agent`, *Then* both nodes have a `sessions/<id>/events.jsonl`, every record in each
> carries the same `run_id`, the two `visit_id`s differ, and the `amplifier-agent` session's
> recorded parent is its own `visit_id`.

## 6. Alternatives considered

1. **Patch `llm-direct` — give it a session.** Rejected: it is what the maintainer directive declined,
   it pushes session identity back across a seam that forbids it (`worker_protocol.py:6-9`), and it
   leaves `coding-agent`'s vacuous green and the absent run id untouched. The gap is engine-level.
2. **Reconstruct the tree post-hoc from the run directory.** Rejected on precedent: §26 already
   rejected a fabricated ledger, because "a synthetic record cannot answer 'which tools did the worker
   call?'" It also cannot recover what was never emitted — `llm-direct`'s events are node-keyed, and
   no post-processor can invent the missing session boundary.
3. **Push a `PipelineLifter` / pipeline node type upstream into the CI server.** Rejected as the
   *first* move: different repo, blocks all value behind an external dependency, and M3 shows existing
   lifters suffice. Retained as the follow-on if delegation-shaping misrepresents the tree (Q4).
4. **Leave the engine alone; let the hooks module infer visits from the event stream.** Rejected: the
   module sees events, not visit boundaries, and would re-derive `execution_index` by counting —
   fragile under parallel branches and resume. The ContextVar seam exists because this is engine work.
5. **Reuse `_branch_id` as the correlation key.** Rejected: it is `id(clone)` (`engine.py:258`) —
   process-local, reusable after GC, not stable across a resume.

## 7. Gap analysis → follow-up build items

Each is independently takeable. Ordering constraint: **1 → 2 → 3** are a chain; 4–8 depend on 3.

**1. Mint `run_id` and record it durably.** *Given* any run, *When* it starts, *Then* a `run_id` is on
the `pipeline:start` payload and in `manifest.json`, and `context.get_string("internal.run_id")` returns
it rather than `"unknown"` (`fidelity.py:220`), *And* two runs in one process with no explicit
`logs_root` no longer share a directory (`__init__.py:443-447`). Cite canonical §9.6 in the PR.

**2. Record `visit_id` durably.** *Given* a node executed twice in a convergence loop, *When* the run
ends, *Then* both `status.json` records and both `trace.jsonl` records carry distinct `visit_id`s and
the same `run_id`; `execution_index` is unchanged for backward compatibility. Touches
`engine.py:1905-1928` and `engine.py:1949-1958`.

**3. Publish `visit_session_id` on the observer seam.** *Given* a node visit, *When* the codergen handler
enters the backend call, *Then* a `current_worker_session_id` ContextVar is set beside
`current_worker_sessions_dir` and reset in the same `finally` (`handlers/codergen.py:137-156`), *And*
`Worker.run`'s signature is unchanged, *And* a test asserts two parallel branches never see each other's
value.

**4. Close the persister's drop.** *Given* an event with no `session_id` emitted inside a node visit,
*When* the persister runs, *Then* it keys on the engine-supplied visit session id instead of returning
early (`session_events.py:147-149`), *And* an event emitted outside any node visit is still dropped.

**5. Prove `llm-direct` telemetry end to end.** *Given* a one-node graph with `worker="llm-direct"`, *When*
it runs, *Then* `<logs_root>/<node_id>/sessions/<id>/events.jsonl` exists and holds the `provider:request`
and `provider:response` records, *And* that node's `status.json` `session_id` is non-null and equals the
directory name, *And* `DirectWorker` is unmodified. RED-proof: the directory is absent before the change.

**6. Emit the delegation-shaped visit event.** *Given* a run inside an Amplifier session, *When* a node
dispatches to a worker, *Then* an event is emitted carrying `sub_session_id` (the visit session) and
`parent_session_id` (the run's session), *And* a CI graph query returns the run's node visits as children
of the run session. Verify against a live CI instance; record the query used.

**7. Reconcile `coding-agent`'s private uuid.** *Given* `agent_session.py:163`, *When* this item lands,
*Then* either that uuid is surfaced so it can be correlated (and `agent:session_start` added to
`PERSISTED_SESSION_EVENTS`, `session_events.py:81-90`), or it is deleted as dead weight — decision
recorded either way. Not both.

**8. Rewrite the `telemetry_session_id` TARGET (§4 above).** *Given* the kit, *When* this item lands, *Then*
`TurnResult` carries the turn's emitted events, the probe config seeds a session identity (replacing `{}`
at `suite.py:271`), a new broken-worker fixture emitting an unstamped event FAILS the TARGET (RED-proofed:
break → fails naming `telemetry_session_id` → restore), all three wired workers pass positively,
`llm-direct`'s declared absence is removed (`loop-pipeline/tests/test_worker_parity.py:55-57`), the
disclaimer at `loop-agent/tests/test_worker_parity.py:56-72` is deleted, *And* the class/test disagreement
(`direct_worker.py:101` says one absence, its test says three) resolves to one source.

## 8. Open questions for the owner

1. **Does the CI hook mount into dot-runner's spawned worker sessions today?** §26 establishes that
   `hooks-pipeline-observability` is; whether `hook-context-intelligence` rides the same path is
   unverified here. If it does, live push is primary and M4 is a fallback for CLI runs. If it does
   not, M4 is the *only* path and rises in priority.
2. **One `run_id` per run, or one per convergence iteration?** C15.1 already scopes records per
   iteration. The proposal is a single run id with `iteration` as a property; a per-iteration id
   would make each iteration a separate CI tree.
3. **Run directory, or `~/.amplifier/projects/`?** §26 argues for the run dir ($HOME is ephemeral in
   CI and sandboxes), so M4 writes CI's contract *inside* it. Writing both is possible, not proposed
   — it doubles the bytes for a marginal gain.
4. **Is "a node visit is a delegation" an acceptable modelling claim?** It buys G4 by reusing CI's
   delegation lifters. If the owner reads it as a misrepresentation, alternative 3 is the fallback,
   at the cost of an external dependency.
5. **New clause C18, or an extension of C15?** C15's title no longer covers the surface once a run
   is externally reconstructible, which argues for a separate clause — but the contract is DRAFT and
   unstamped, so either is cheap today.
