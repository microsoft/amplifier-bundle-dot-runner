# DESIGN: context intelligence first-class across dot-runner runs

- **status:** PROPOSAL — for owner review. Nothing here binds a lane until ratified.
- **date:** 2026-09-06 · **re-baselined:** 2026-09-07 against main `d832550` · **tracker:** `attractor-soo` · **repo:** `microsoft/amplifier-bundle-dot-runner`
- **scope:** design only. No code, no contract clause, no `specs/EXTENSIONS.md` entry lands with this doc.

> **RE-BASELINE, 2026-09-07 (main `d832550`).** Five PRs landed in this doc's own
> territory between the 2026-09-06 draft and today: **#69** (worker session ids in
> `status.json` + the persister finally mounted on the named-worker path), **#70**
> (loop-agent per-turn context bounding), **#73** (provider INSTANCE id resolution),
> **#74** (a node's declared provider/model/effort reaches the child), **#75**
> (the `amplifier-agent` worker's hosted session persists its own provider and tool
> events), **#76** (one five-key provider-identity vocabulary on every worker's
> provider events, plus a parity row that can actually fail).
>
> **What that changes for this design.** The premise below — "no dot-runner run is
> reconstructible today, **for any worker**" — is no longer true as written. Two of
> the three workers now write a real, per-node `sessions/<id>/events.jsonl`
> carrying LLM-call boundaries and tool calls, and `status.json` names the stream
> that holds them. What has **not** moved is the engine-level half this design is
> actually about: the engine still mints **no** run identity, **no** durable visit
> identity, and **no** telemetry identity of its own. `llm-direct` still produces no
> session stream at all. Of the eight build items in §7, **0 are delivered, 1 is
> partial (#8), 7 remain open** — every §7 line below carries its own verdict with
> current-main evidence. §1's tables are rewritten against main; §8's open question 1
> is now **answered from code**.

**Why this doc lives here, not in `amplifier-bundle-attractor`.** All four surfaces it touches are
owned by this repo and by nothing else: the node-visit boundary (only the engine knows when one
opens and closes — `engine.py:863-866`); the telemetry seam the engine already exposes to observers
(`worker_observability.py:33-35`); the parity kit that pins per-worker obligations
(`modules/worker-parity-kit/`); and `contracts/engine-surface.v1.md` C15. `amplifier-bundle-attractor`
consumes this engine and owns none of them. Per `docs/VISION.md`, context intelligence (CI) is
implementor-level observability **below** the nlspec's §4.5 backend boundary — the tier of C1's
worker names.

---

## 1. Problem, and today's reality

The maintainer directive (2026-08-29) asks that the `llm-direct` worker stop being a declared
telemetry absence. The finding of this design pass is that patching `llm-direct` would fix the
wrong layer. The 2026-09-06 draft stated that finding as *"no dot-runner run is reconstructible
today, for any worker"*.

**Re-baselined (2026-09-07, main `d832550`), that sentence is now too strong, and the correction
matters to the design's cost case.** #69 and #75 restored the *worker-session* half for the two
spawn workers: a `coding-agent` node and an `amplifier-agent` node each now write a real
`<stage_dir>/sessions/<session_id>/events.jsonl` with LLM-call boundaries and tool calls, and
`status.json` names the stream that actually holds them. What is unchanged is the **engine-level**
half, which is what §3 is about: the engine still mints no identity at all — not for the run, not
for the node visit, not for the worker session. What identity exists is still whatever a worker
happens to carry, recorded passively; and `llm-direct`, which carries none by construction, still
produces no stream at all.

### 1.1 Engine-level: three missing identities

*Evidence re-verified against main `d832550`, 2026-09-07. All three rows still hold; the third
row has moved, and its movement is recorded rather than overwritten.*

| Identity | State today | Evidence (main `d832550`) |
|---|---|---|
| **run id** | Never minted. **Unchanged.** Run identity is still the run *directory path*; the default path is still a fixed constant, so two runs in one process still overwrite each other's `manifest.json`. `internal.run_id` is still **read and never written** outside a test. | default logs_root `__init__.py:454-456`; read-only consumer `fidelity.py:220-221`; `manifest.json` fields `engine.py:1868-1880` carry `start_time` and `engine_commit`, not an id |
| **node-visit id** | `execution_index` exists — on the event bus only (`pipeline:node_start`, `pipeline:node_complete`) — and is **absent from `status.json`, from `trace.jsonl`, and from the checkpoint record**. **Unchanged.** | minted `engine.py:863-866`; on the bus `engine.py:874`, `:778-780`, `:1015`, `:1155`; *not* in the status payload `engine.py:1905-1928`, *not* in the trace record `engine.py:1949-1958`, and no occurrence anywhere in `checkpoint.py` |
| **worker-session id** | **Moved (#69, #75), and still engine-blind.** Still set on `Outcome` only on the spawn path — but the spawn path now (a) survives a worker-authored `status.json` instead of being nulled by it, and (b) lets a worker *correct* the id via a `worker_session_id` completion-metadata key, which is how an `amplifier-agent` node's `status.json` names the hosted session rather than the empty adapter one. The registry (`llm-direct`) path still never sets it and structurally still cannot: the seam forbids it. | set `backend.py:858-865`; resolution order `backend.py:1403-1437` (metadata key wins over the spawn result's own id); registry path `backend.py:913-931` returns `outcome` untouched; `Outcome.session_id` default `outcome.py:52`; proofs `loop-pipeline/tests/test_worker_session_observability.py:484`, `:563-626` |

**The engine still learns identity by being told, never by minting it.** Every id above that
exists is a *worker's* id, surfaced upward. That is exactly the inversion §3's M2 proposes to
reverse, and nothing that landed in #69–#76 touches it.

The seam rule is deliberate, and the design must not break it — `workers/worker_protocol.py:6-9`:

> A worker is stateless per node visit: `(prompt, context, replayed history)` in,
> `(output, outcome)` out. **No session identity ever crosses the seam.**

### 1.2 Per-worker: what each emits, what is missing

*Rewritten 2026-09-07 against main `d832550`. Every cite below is a current file:line.*

| Worker | Session id it holds | Does the engine see it? | Events it emits | Reaches `sessions/<id>/events.jsonl`? |
|---|---|---|---|---|
| `llm-direct` | **none, by design** — unchanged (`workers/worker_protocol.py:6-9`) | **no** — the registry path returns the worker's `Outcome` untouched (`backend.py:913-931`), so `outcome.session_id` stays `None` (`outcome.py:52`) and `status.json`'s `session_id` is `null` (`engine.py:1920`) | `provider:request` / `:response` / `:error`, keyed by **`node_id`**, and — **new, #76** — now carrying the same five-key identity vocabulary as the spawn workers (`provider` · `provider_module` · `provider_instance` · `model` · `reasoning_effort`): `direct_worker.py:69-98` (`_identity`), emitted `:266-273` and `:306-313`, node scoping `:263` | **no. Unchanged.** The persister still early-returns on a missing `session_id` (`session_events.py:167-169`), so no session directory is ever created for an `llm-direct` node. #76 made its events *self-describing*; it did not give them a session to be filed under |
| `coding-agent` | still a private `uuid4` (`agent_session.py:246`) | **the private uuid: no** — `execute()` still returns a bare `str`; `orchestrator:complete`'s `metadata` is deliberately `{}` (`loop-agent/.../__init__.py:652-683`, metadata at `:670`). **The kernel's id: yes, since #69** — the spawned session's id reaches `Outcome.session_id` via the spawn result (`backend.py:1434-1436`) and now survives a worker-authored `status.json` (proof: `test_worker_session_observability.py:484`) | `agent:*`, plus kernel `tool:pre`/`tool:post` and `provider:request`/`:response` — the latter now carrying the five identity keys via `_provider_identity` (`agent_session.py:307-345`, emitted `:665`). Only `agent:session_start` (`:526`) and `agent:awaiting_input` (`:730`) carry the private uuid | **yes, since #69** — for the kernel-stamped stream. `provider:*` and `tool:*` are in `PERSISTED_SESSION_EVENTS` (`session_events.py:81-110`) and the persister is now actually mounted on the named-worker path (`pipeline-runner/.../default_worker.py:355-365`). **Still no** for the private uuid: `agent:*` remains absent from that set, so the one event carrying it is still dropped |
| `amplifier-agent` | `dot-runner-thread-<slug>` or `engine-<uuid4>` (`loop-amplifier-agent/.../__init__.py:841-863`), stamped onto the hosted session as a default field (`:682-686`) | **yes, since #75** — no longer a local buried in `_run_turn`. It is resolved once, up front (`:329`), reported on every exit path (`:354`, `:373`), carried up as a `worker_session_id` completion-metadata key (`:185`, `:427-428`), and preferred by the engine over the spawn-boundary id (`backend.py:1429-1433`, ahead of the spawn result's own id at `:1434-1436`). **One id now names one node visit** | whatever the hosted kernel emits, attributed by the `set_default_fields` stamp — plus, since #75, an `llm:response` → `provider:response` bridge so the hosted orchestrator's usage block reaches the canonical event (`__init__.py:865-935`), with #76's identity keys stamped on it | **yes** — and for a different, stronger reason than in the 2026-09-06 draft. The hosted session is a **second** coordinator that bundle composition never reaches; #75 mounts the *shipped* persister directly onto it (`__init__.py:965-985`), so the node's `sessions/` dir holds the session that actually called the model, not the adapter's lifecycle brackets. Proofs: `loop-amplifier-agent/tests/test_child_session_telemetry.py:109`, `:130`, `:190` |

**Measured consequence of the above (read-only evidence, not committed):** the node-matrix axes
re-run `20260907T164521Z-axes/RESULTS.md` reports `served_by_source = events:provider-identity`
for **7 of 7 rows** — the served provider read out of the run's own events rather than inferred —
and both `amplifier-agent` rows now carry real call counts, token counts and cost
(`aa-anthropic-default`: 40 calls, $2.09), where the 04:38 matrix had two PASSing but wholly
unmeasured `amplifier-agent` rows.

**What this does *not* fix, and why §3 still stands.** `set_default_fields` — the mechanism both
working paths depend on — is still applied **by a worker, from an id the engine never mints**
(`loop-amplifier-agent/.../__init__.py:682-686`; the kernel's own stamp for the spawn path). The
engine still cannot answer "which node visit is this session?" because it never named the visit,
and it cannot give a session to the one worker that has none. §3's M1/M2 are untouched by
#69–#76; what those PRs removed is the *urgency* argument from the worst symptom, not the
structural gap.

### 1.3 The parity kit asserts nothing here

**The row this section is about is unchanged; the *argument* about the seam is not.**

`telemetry_session_id` is still a TARGET row (`suite.py:238-248`) with an **empty probe config**
(`suite.py:290`), so the check still reduces to: skip if declared absent (`suite.py:460-464`);
survive being asked; no warning naming it (`suite.py:482`, `_not_silently_dropped` at `:414`).
Of the three wired workers, `llm-direct` still skips by declared absence
(`loop-pipeline/tests/test_worker_parity.py:56-58`) and `coding-agent` still passes green — a
green its own test file still calls **"VACUOUSLY"**, adding that "the kit's TARGET bar doesn't
distinguish 'handled' from 'nothing here to mishandle'"
(`loop-agent/tests/test_worker_parity.py:56-92`, `DECLARED_ABSENCES = frozenset()` at `:93`).
**No worker has yet been positively proven to stamp a session identity.**

**Struck, 2026-09-07 (#76): "the kit cannot fix this at its current seam."** That claim is now
false, and its falsification is the strongest single piece of evidence for §4. `TurnResult` has
**five** fields — `provider_events` was added precisely to let a TARGET row observe a worker's
emitted event stream (`worker_parity_kit/protocol.py:56-86`, field at `:86`). #76 then used that
field to ship a *sibling* row, `telemetry_provider_identity` (`suite.py:246`), which:

- has a real probe config, not `{}` (`suite.py:291-294`);
- is verified **behaviorally**, not by smoke test (`_assert_provider_identity`, `suite.py:300-412`;
  dispatched at `:479-480`) — all five identity keys present on every `provider:response`, one
  identity across them, and the declared `reasoning_effort` echoed back;
- treats an unobservable stream (`provider_events is None`) as a **failure**, not a pass
  (`suite.py:335-341`);
- and is non-vacuity-proofed by the kit's first TARGET-tier broken-worker fixture
  (`broken_worker.py:160-171`; meta-proofs `tests/test_broken_worker_meta.py:118-146`).

So §4's proposed rewrite of `telemetry_session_id` no longer needs to invent a mechanism: the
mechanism shipped, was RED-proofed, and is in use one row away. What §4 still asks for is the
*same treatment for session identity* — which cannot be done until the engine mints one to seed
the probe with (§3 M2 → §7 items 3–4). That ordering is now the item's real blocker, not the seam.

**Secondary defect: resolved.** The class/test disagreement is gone — `DirectWorker`'s docstring
now points at the harness as the single source ("Declared absent to the worker-parity-kit
harness (`tests/test_worker_parity.py`)", `direct_worker.py:31-33`) instead of asserting its own
count.

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
name into the `observability.events` capability (`hooks-pipeline-observability/.../__init__.py:107-112`
— re-verified on main `d832550`), which is precisely the set the CI hook subscribes to. **When a run
executes inside an Amplifier session, its pipeline events are already being captured.** What is missing
is not the channel — it is the correlation fields: `pipeline:start` still carries
`graph_name / node_count / edge_count / goal / dot_source` (`engine.py:311-319`) and no run id, so two
runs in one session still interleave with no discriminator.

**Re-baseline note (2026-09-07).** Whether the CI hook reaches the *worker* sessions — as opposed to
the parent session running the engine — is §8's open question 1, and it is now **answered from code**
there. Short version: **no** for the spawn path, **no** for `llm-direct`, and **yes, incidentally**
for the `amplifier-agent` hosted session, which arrives from amplifier-agent's own bundle rather than
from anything dot-runner mounts.

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
existing increment site (`engine.py:863-866`), where the visit boundary is already computed. Both
land on every `pipeline:*` payload and in every durable record (`manifest.json`, `status.json`,
`trace.jsonl`) — additive to payloads that already exist.

**M2 — The engine, not the worker, names the visit's telemetry session.** The inversion the whole
design rests on. Today the engine passively receives an id from whichever worker happens to have
one. Instead: the engine derives a deterministic `visit_session_id` from `(run_id, node_id,
execution_index)` and publishes it on a ContextVar **beside the one it already owns** —
`current_worker_sessions_dir` (`worker_observability.py:33-35`), set and reset by the codergen
handler around each backend call (`handlers/codergen.py:137-139`, reset `:156-157`). The persister then keys
on `data["session_id"]` when present and falls back to that value when absent
(`session_events.py:167-169` becomes a fallback, not an early return).

This does not violate the seam. `worker_protocol.py:6-9` forbids **session identity crossing the
worker seam** — a statelessness rule about the worker's own execution. The engine naming a
*telemetry* identity for a node visit is a different thing on a different channel, published to
observers rather than to the worker; `Worker.run` keeps its four arguments and its `(str, Outcome)`
return. Per worker:

| Worker | Effect |
|---|---|
| `llm-direct` | Its node-keyed `provider:*` events (`direct_worker.py:266-313` on main `d832550`) acquire a session key. A `sessions/<visit_session_id>/events.jsonl` appears for the first time. No change to `DirectWorker`. |
| `coding-agent` | Its kernel `tool:pre`/`tool:post` and `provider:*` already have a key (the kernel's, since #69); the visit id becomes that stream's engine-side *parent*. Its private uuid (`agent_session.py:246`) becomes reconcilable — or deletable (item 7). |
| `amplifier-agent` | Unchanged and authoritative: it already stamps a real session id (`__init__.py:682-686`), and since #75 also persists that session's own events and reports the id upward (`:865-985`, `:427-428`). The visit session id becomes that session's **parent**, not its replacement — two ids, correctly nested, instead of two ids naming one thing. |

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

Assertable at the generic seam by one addition. **Half of that addition has since shipped (#76):** the
fifth `TurnResult` field carrying the events the turn emitted now exists (`protocol.py:86`), and a
behaviorally-verified TARGET row already uses it (§1.3). What remains is a seeded id in the probe
config, replacing today's `{}` (`suite.py:290`) — which needs an engine-minted identity to seed
(§7 items 3–4). Consequences: `llm-direct`'s declared absence retires — it becomes a positive pass;
`coding-agent`'s vacuous green becomes a real pass or a real failure, retiring the disclaimer at
`loop-agent/tests/test_worker_parity.py:56-92`; and the kit's TARGET-tier broken-worker fixture — which
#76 already added for the sibling identity row (`broken_worker.py:160-171`) — gains a session-identity
counterpart.

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
C15.3, C15.4.

> **RE-BASELINE, 2026-09-07 — the cheap window is closing.** As of main `d832550` the contract
> still reads **DRAFT** (`contracts/engine-surface.v1.md:3-8`), so the sentence above was true
> when written: these would be edits to a draft, not amendments to a frozen surface. **A sibling
> lane is stamping `engine-surface.v1` FROZEN today.** Once that lands, neither the C15 edits nor
> the C18 text below may be written into the contract directly. Both must be proposed as a
> sibling candidate file — `contracts/engine-surface.v2-candidate.md` — carrying the target line,
> the exact before/after, the evidence (a cost paid or a failure caught), and what does **not**
> change; the owner then answers *ratified* / *ratified with edits* / *declined* / *later*. This
> doc does not pre-empt that: it still adds nothing to the contract.
>
> Note also that C15.2's `session_id`-in-`status.json` requirement is no longer aspirational —
> #69 and #75 made it true for both spawn workers (§1.2) and it is `null` only on the
> `llm-direct` path. Whoever writes the freeze packet's C15 evidence should cite those, and
> should record the `llm-direct` null as a known, scoped exception rather than a gap.

**Is a new clause implied?** Yes — C15 claims the run *directory* is the audit trail, a purely
local claim; making CI first-class adds a second, externally consumed surface. Proposed text,
**for the owner to add or reject — not added by this doc**, and — post-freeze — to be carried in
via `contracts/engine-surface.v2-candidate.md` rather than written into `engine-surface.v1.md`:

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

**Status ledger, re-baselined 2026-09-07 against main `d832550`.** Ids are stable — nothing is
renumbered, nothing is removed. Verdicts are against each item's own *Given/When/Then*, not
against its intent, so an item whose purpose was partly served by adjacent work still reads OPEN
if its stated proof does not exist.

| # | Item | Verdict | Landed by | One-line evidence on main |
|---|---|---|---|---|
| 1 | Mint `run_id`, record durably | **OPEN** | — | no `run_id` anywhere; `internal.run_id` still read-only (`fidelity.py:220`), `manifest.json` still id-less (`engine.py:1868-1880`) |
| 2 | Record `visit_id` durably | **OPEN** | — | `execution_index` still bus-only; absent from `status.json` (`engine.py:1905-1928`), `trace.jsonl` (`:1949-1958`), `checkpoint.py` |
| 3 | Publish `visit_session_id` on the observer seam | **OPEN** | — | `worker_observability.py` still holds exactly one ContextVar (`:33-35`); codergen sets only it and the status path (`handlers/codergen.py:137-150`) |
| 4 | Close the persister's drop | **OPEN** | — | `session_events.py:167-169` still early-returns on a missing `session_id` |
| 5 | Prove `llm-direct` telemetry end to end | **OPEN** *(narrowed)* | — *(#76 adjacent)* | still no `sessions/` dir and still `session_id: null` for an `llm-direct` node; #76 made its events self-describing, not session-keyed |
| 6 | Emit the delegation-shaped visit event | **OPEN** | — | no `sub_session_id`/`parent_session_id` emitted at any node visit |
| 7 | Reconcile `coding-agent`'s private uuid | **OPEN** | — | uuid still private (`agent_session.py:246`), `agent:*` still absent from `PERSISTED_SESSION_EVENTS` (`session_events.py:81-110`), no decision recorded |
| 8 | Rewrite the `telemetry_session_id` TARGET | **PARTIAL** | **#76** | the mechanism shipped (`TurnResult.provider_events`, a behaviorally-verified TARGET row, a TARGET-tier broken-worker fixture) — but on the sibling `telemetry_provider_identity` row; `telemetry_session_id` itself is untouched (`suite.py:290`) |

**Totals: 0 delivered · 1 partial · 7 open.**

**1. Mint `run_id` and record it durably.** — **OPEN.** *Given* any run, *When* it starts, *Then* a `run_id` is on
the `pipeline:start` payload and in `manifest.json`, and `context.get_string("internal.run_id")` returns
it rather than `"unknown"` (`fidelity.py:220`), *And* two runs in one process with no explicit
`logs_root` no longer share a directory (`__init__.py:454-456` on main `d832550`; the draft cited
`:443-447`). Cite canonical §9.6 in the PR.

**2. Record `visit_id` durably.** — **OPEN.** *Given* a node executed twice in a convergence loop, *When* the run
ends, *Then* both `status.json` records and both `trace.jsonl` records carry distinct `visit_id`s and
the same `run_id`; `execution_index` is unchanged for backward compatibility. Touches
`engine.py:1905-1928` and `engine.py:1949-1958`.

**3. Publish `visit_session_id` on the observer seam.** — **OPEN.** *Given* a node visit, *When* the codergen handler
enters the backend call, *Then* a `current_worker_session_id` ContextVar is set beside
`current_worker_sessions_dir` and reset in the same `finally` (`handlers/codergen.py:137-157` on main `d832550` — note a second var, `current_node_status_path`, already shares that try/finally, so the pattern is established), *And*
`Worker.run`'s signature is unchanged, *And* a test asserts two parallel branches never see each other's
value.

**4. Close the persister's drop.** — **OPEN.** *Given* an event with no `session_id` emitted inside a node visit,
*When* the persister runs, *Then* it keys on the engine-supplied visit session id instead of returning
early (`session_events.py:167-169` on main `d832550`; the draft cited `:147-149`), *And* an event emitted outside any node visit is still dropped.

**5. Prove `llm-direct` telemetry end to end.** — **OPEN, narrowed by #76.** *Given* a one-node graph with `worker="llm-direct"`, *When*
it runs, *Then* `<logs_root>/<node_id>/sessions/<id>/events.jsonl` exists and holds the `provider:request`
and `provider:response` records, *And* that node's `status.json` `session_id` is non-null and equals the
directory name, *And* `DirectWorker` is unmodified. RED-proof: the directory is absent before the change.

> *Re-baseline note (2026-09-07).* Still fully OPEN as stated: no `sessions/` directory is created
> for an `llm-direct` node (`session_events.py:167-169`) and its `status.json` `session_id` is
> still `null` (`backend.py:913-931` → `outcome.py:52` → `engine.py:1920`). #76 narrowed the item's
> *intent* from a different direction: `llm-direct`'s `provider:*` events now carry the same
> five-key identity vocabulary as the spawn workers (`direct_worker.py:69-98`), so what is missing
> is no longer "who served this call" but only "which session was this". Note the `DirectWorker is
> unmodified` clause is now weaker as an acceptance criterion — the file has already changed for a
> different reason; the operative constraint is that this item must not push a session identity
> back across the worker seam.

**6. Emit the delegation-shaped visit event.** — **OPEN.** *Given* a run inside an Amplifier session, *When* a node
dispatches to a worker, *Then* an event is emitted carrying `sub_session_id` (the visit session) and
`parent_session_id` (the run's session), *And* a CI graph query returns the run's node visits as children
of the run session. Verify against a live CI instance; record the query used.

**7. Reconcile `coding-agent`'s private uuid.** — **OPEN.** *Given* `agent_session.py:246` (was `:163`), *When* this item lands,
*Then* either that uuid is surfaced so it can be correlated (and `agent:session_start` added to
`PERSISTED_SESSION_EVENTS`, `session_events.py:81-110`), or it is deleted as dead weight — decision
recorded either way. Not both.

**8. Rewrite the `telemetry_session_id` TARGET (§4 above).** — **PARTIAL (#76).** *Given* the kit, *When* this item lands, *Then*
`TurnResult` carries the turn's emitted events, the probe config seeds a session identity (replacing `{}`
at `suite.py:290`), a new broken-worker fixture emitting an unstamped event FAILS the TARGET (RED-proofed:
break → fails naming `telemetry_session_id` → restore), all three wired workers pass positively,
`llm-direct`'s declared absence is removed (`loop-pipeline/tests/test_worker_parity.py:56-58`), the
disclaimer at `loop-agent/tests/test_worker_parity.py:56-92` is deleted, *And* ~~the class/test disagreement
(`direct_worker.py:101` says one absence, its test says three) resolves to one source~~ (**DONE, #76**).

> *Re-baseline note (2026-09-07) — the only item with movement.* #76 delivered the **mechanism**
> this item specified. The sub-clauses that now read as delivered are struck below:
>
> - ~~`TurnResult` carries the turn's emitted events~~ — **DONE** (`protocol.py:86`,
>   rationale `:56-86`).
> - a new TARGET-tier broken-worker fixture that actually FAILS a telemetry row — **DONE as a
>   mechanism, for the sibling row only** (`broken_worker.py:160-171`; meta-proofs
>   `tests/test_broken_worker_meta.py:118-146`). A *session-identity* counterpart is still owed:
>   today's fixture emits identity-less provider events, not unstamped ones.
> - ~~the class/test disagreement resolves to one source~~ — **DONE** (`direct_worker.py:31-33`
>   now points at the harness; the class asserts no count of its own).
>
> Still OPEN: the probe config for `telemetry_session_id` is still `{}` (`suite.py:290`);
> `llm-direct`'s declared absence is still present (`loop-pipeline/tests/test_worker_parity.py:56-58`);
> the vacuous-green disclaimer is still present (`loop-agent/tests/test_worker_parity.py:56-92`).
> **The blocker is now upstream, not in the kit:** seeding a session identity into the probe
> requires an identity to seed, which is items 3–4. The kit is ready; the engine is not.

## 8. Open questions for the owner

1. **Does the CI hook mount into dot-runner's spawned worker sessions today?**
   **ANSWERED from code, 2026-09-07, main `d832550`: NO — dot-runner mounts it on no path it
   controls. `hook-context-intelligence` appears in this repo's mount plan zero times.**
   Path by path:

   - **The spawn / named-worker path (`coding-agent` and every `--worker` spawn): NO.** The
     synthesized worker bundle's hook set is an explicit two-entry dict —
     `_HOOK_MODULE_SOURCES = {"hooks-pipeline-observability", "hooks-tool-truncation"}`
     (`modules/pipeline-runner/.../default_worker.py:355-365`). There is no third entry, and no
     other code path adds one. The composition rule that *would* carry a third one is documented
     in the same file (`:316-323`): a hook reaches a worker session only by riding the **parent**
     bundle through `PreparedBundle.spawn`'s composition — and this synthesized bundle *is* the
     run's parent bundle for a CLI run. dot-runner's own root bundle declares no `hooks:` section
     at all (`bundle.md`), so nothing is inherited from there either. Consequence: whether the CI
     hook is present is entirely a property of the **host application's** bundle, never of
     dot-runner. A `dot-runner` CLI run has no host bundle, so the answer there is unconditionally
     no.
   - **`llm-direct`: NO, and not for want of mounting.** That worker runs in-process and opens no
     session at all (`workers/worker_protocol.py:6-9`), so there is nothing for a session-scoped
     hook to attach to.
   - **`amplifier-agent`: YES, incidentally — and it arrives from the *other* repo's bundle, not
     from ours.** The hosted session is built from amplifier-agent's own baked-in bundle via the
     vendored `prepare_bundle_for_session`, whose three transforms include the
     hook-context-intelligence workspace seed (`loop-amplifier-agent/.../__init__.py:549-552`,
     documented `:525-530`). The adapter then resolves a real workspace for it
     (`:544-548`) and re-asserts `workspace`/`project_slug` on the child coordinator config
     (`:674-677`). The hook's presence is directly attested by a defect note in the same file:
     "the context-intelligence LoggingHandler drops any event whose `session_id` default field is
     empty" (`:643-645`) — a constraint that only bites if that handler is live in that session.
     Note this session is a **second coordinator that bundle composition never reaches**
     (`:36-46`); it is not a "spawned worker session" in the sense this question asks about.

   **Consequence for the design, and it is a real one.** Live push is **not** primary. For two of
   three workers the CI hook is absent on every path dot-runner controls, and for the third it is
   present only because a peer repo's bundle happens to carry it — an arrangement no clause here
   asserts and any amplifier-agent release could change silently. **M4 is therefore the primary
   path, not the fallback, and rises in priority accordingly** (§3 M4; §7 has no item for it yet —
   one is owed). The cheaper alternative worth pricing first: add
   `hook-context-intelligence` to `_HOOK_MODULE_SOURCES` alongside the two hooks already there,
   which is one dict entry on a proven mechanism — but that buys live push only, still writes
   nothing a completed CLI run can replay, and still needs §7 items 1–4 before the events it
   pushes are correlatable. It is not a substitute for M4; it is a possible complement.
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
   **Re-baseline note (2026-09-07): "cheap today" has an expiry.** `engine-surface.v1` still reads
   DRAFT on main `d832550` (`contracts/engine-surface.v1.md:3-8`), but a sibling lane is stamping it
   FROZEN today. After that stamp, *both* options cost the same thing — a
   `contracts/engine-surface.v2-candidate.md` proposal carrying evidence, answered by the owner —
   so the choice becomes purely editorial. This doc's recommendation is unchanged: a separate C18,
   because C15's own title ("the run directory is the audit trail") is a local claim that a second,
   externally consumed surface would falsify.
