# DESIGN: full-run Context Intelligence for dot-runner

- **status:** PROPOSAL — design only; not runtime evidence of Context Intelligence (CI)
  ingestion.
- **date:** 2026-09-09
- **baselines:** dot-runner `99a28f009ab6dd05329bc5fdcdea8d839b5a68a7`; CI client
  `9c602a5378d51b327d0c8cdfb11db1627d63be88`; amplifier-agent
  `fa33a2099bf748c0e4553c0b74eafe1077fb9326`.
- **locked material:** `docs/VISION.md` and frozen contracts stay unchanged. This
  design maps work; it does not ratify an extension or authorize implementation.

## 1. Decision and user experience

An operator should reconstruct an observed graph execution as:

```text
capture → execution scopes → node visits → attempts
        → worker stream (only when actually observable)
        → provider LLM calls and executed tool calls
```

The result reports **complete**, **incomplete**, or **not captured**. Lost events,
local-write failure, slow-writer overflow, queue overflow, observer interruption, and
server failure never look complete. Observer absence or failure never changes graph
outcome, routing, checkpoint, `status.json`, fidelity, or worker statelessness.

**Minimal composition:** an opt-in observer uses existing `hooks=` on `drive_engine`,
`run_pipeline`, and `resume_pipeline`; existing `extra_overlays=` reaches spawned
sessions. It owns local capture and may forward a successful local record through the
existing CI transport. No engine CI import, new public `PipelineTelemetrySink`, server,
cloud transport, CLI/API, UI/deck, or pipeline framework is proposed.

The observer owns opaque capture and telemetry-session identities plus a sidecar join
map. The engine emits only native execution facts and structural scope. It does **not**
gain a run ID or node-visit ID, and neither host paths nor timestamps become identity.

### Non-goals

1. No raw-default retention. DOT, prompts, responses, free-text errors, context,
   tool arguments/results, paths, and full worker events stay out of both sinks.
2. No extension of the amplifier-agent adapter's private imports or hook reach-ins.
3. No assertion that an uninspected CI server has typed `Run` / `NodeVisit` entities.

## 2. Contract posture and completed event mapping

The pinned nlspec explicitly defines a typed pipeline event stream and permits an
observer/callback or async stream (§9.6). It also keeps traversal, deterministic
routing, stage artifacts, checkpoint/resume, and fidelity separate (§§3.2, 3.3, 4.5,
5.3, 5.4). The observer therefore consumes existing hooks and never influences those
mechanisms. https://github.com/microsoft/amplifier-bundle-dot-runner/blob/99a28f009ab6dd05329bc5fdcdea8d839b5a68a7/contracts/external/attractor-spec-canonical.md#L335-L458
https://github.com/microsoft/amplifier-bundle-dot-runner/blob/99a28f009ab6dd05329bc5fdcdea8d839b5a68a7/contracts/external/attractor-spec-canonical.md#L656-L718
https://github.com/microsoft/amplifier-bundle-dot-runner/blob/99a28f009ab6dd05329bc5fdcdea8d839b5a68a7/contracts/external/attractor-spec-canonical.md#L1096-L1175
https://github.com/microsoft/amplifier-bundle-dot-runner/blob/99a28f009ab6dd05329bc5fdcdea8d839b5a68a7/contracts/external/attractor-spec-canonical.md#L1609-L1648

The following is the required row-level disposition. **CONFORM** means only that an
existing implementation event is the named §9.6 lifecycle counterpart; it does not
license changed payloads or new emitters. **PROPOSED EXTENSION** needs a separately
ratified ledger/`specs/EXTENSIONS.md` disposition and executable assertion. Nlspec
silence is neither authority nor a conformance gap.

| Exact event name | §9.6 counterpart / present status | Required correlation fields | Disposition |
|---|---|---|---|
| `pipeline:start`, `pipeline:complete` | `PipelineStarted`, `PipelineCompleted` / `PipelineFailed` according to terminal status | Existing fields unchanged; **P:** `scope`, `restart_generation` | Existing emission is CONFORM; P is PROPOSED EXTENSION. |
| `pipeline:node_start` | `StageStarted` | **E:** `node_id`, `handler_type`, `attempt=1`, `execution_index`; **P:** `scope`, `restart_generation` | Existing emission is CONFORM; P is PROPOSED EXTENSION. |
| `pipeline:node_complete`, `pipeline:stage_failed` | `StageCompleted` / `StageFailed` | **E:** completion has `node_id`, `status`, `duration_ms`, `session_id`, `execution_index`, final `attempt`; stage-failed has `node_id`, `attempts`, `final_status`; **P:** `scope`, `restart_generation` | Existing emission is CONFORM; P is PROPOSED EXTENSION. |
| `pipeline:stage_retrying` | `StageRetrying` | **E:** `node_id`, `attempt`, `max_attempts`, `delay_ms`, optional `reason`; **P:** `execution_index`, `scope`, `restart_generation` | Existing emission is CONFORM; P is PROPOSED EXTENSION. |
| `pipeline:parallel_started`, `pipeline:parallel_branch_started`, `pipeline:parallel_branch_completed`, `pipeline:parallel_completed` | Four `Parallel*` events | Existing fields unchanged; **P:** `scope`, and branch/parent scope references; node events inside branch also require `execution_index` and `attempt` | Existing emissions are CONFORM; P is PROPOSED EXTENSION. |
| `pipeline:checkpoint` | `CheckpointSaved(node_id)` | Existing fields unchanged; **P:** `execution_index`, `scope`, `restart_generation` where known | Existing emission is CONFORM; P is PROPOSED EXTENSION. |
| `pipeline:resume`, `pipeline:resume_fidelity_degrade`, `pipeline:subgraph_start`, `pipeline:subgraph_complete` | Already implemented extra observability; not a named §9.6 event | **P:** structural scope and restart generation; ensure subgraph terminal also emits on exception | Existing extension/emission gap; any changed payload or terminal guarantee is a PROPOSED EXTENSION. |
| `pipeline:attempt_started` | None | `node_id`, `execution_index`, `attempt`, `scope`, `restart_generation` | **PROPOSED EXTENSION**. |
| `pipeline:attempt_terminal` | None | prior fields plus `terminal={completed,failed,cancelled}` and bounded outcome class | **PROPOSED EXTENSION**. |
| `pipeline:parallel_branch_cancelled` | None | `scope`, parent scope, bounded cancellation cause | **PROPOSED EXTENSION**. |
| `pipeline:restart` | None | prior scope, next scope, `restart_generation` | **PROPOSED EXTENSION**. |

Exact existing names are defined here:
https://github.com/microsoft/amplifier-bundle-dot-runner/blob/99a28f009ab6dd05329bc5fdcdea8d839b5a68a7/modules/loop-pipeline/amplifier_module_loop_pipeline/pipeline_events.py#L1-L131

The proposed attempt sequence is unambiguous: `pipeline:attempt_started` →
`pipeline:attempt_terminal` → (if another try is warranted) `pipeline:stage_retrying`
→ next `pipeline:attempt_started`. A retrying event never terminates an attempt.

The existing named Section 26 worker-session observability seam is separate: its
`current_worker_sessions_dir` ContextVar scopes native spawned-session persistence;
it is not a new engine ID or new ledger row.
https://github.com/microsoft/amplifier-bundle-dot-runner/blob/99a28f009ab6dd05329bc5fdcdea8d839b5a68a7/modules/loop-pipeline/amplifier_module_loop_pipeline/worker_observability.py#L1-L35
New rows concern only the **P** fields/names in the table.

## 3. Identity, scope, and coverage semantics

```text
type CaptureId = opaque observer-generated identifier
type TelemetrySessionId = opaque observer-generated visit-stream identifier
type NativeVisit = { scope, restart_generation: uint, node_id,
                     execution_index: uint, attempt: uint }
type ObservationRecord = { schema: "dot-runner-observation/1", capture_id,
  telemetry_session_id, native_visit?, source_event, sequence: uint, at,
  worker?, llm_call_id?, tool_call_id?, tool_name?, allowed_metadata }
```

A fresh capture gets a random `CaptureId`; two concurrent identical graphs get distinct
captures. Explicit resume reopens that sidecar capture—not a capture guessed from time
or path. `TelemetrySessionId` is observer state for every visit, including direct work;
it is never a fake amplifier-agent session. The sidecar join is:

```text
(CaptureId, NativeVisit) ↔ TelemetrySessionId ↔ optional native_worker_session_id
```

A visit reuses native per-node `execution_index`, one-based retry `attempt`, and
existing iteration projected as `restart_generation`; none enters `Outcome`, checkpoint,
context, thread/fidelity keys, or the worker protocol. Structural scope is emitted
provenance: parallel branch = parent visit + deterministic edge/branch ordinal; nested
child = parent visit + invocation ordinal. It is never a log directory. This is required
because parallel branch roots currently hard-code `execution_index=1`, suppress inner
node events, and omit cancellation terminals.
https://github.com/microsoft/amplifier-bundle-dot-runner/blob/99a28f009ab6dd05329bc5fdcdea8d839b5a68a7/modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/parallel.py#L123-L201
https://github.com/microsoft/amplifier-bundle-dot-runner/blob/99a28f009ab6dd05329bc5fdcdea8d839b5a68a7/modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/parallel.py#L253-L347

`complete` means all required **observable** records persisted and the final manifest
attests no loss. It never means that a worker's unavailable public stream was observed.
A whole-run tree with an `unobservable_public_api` descendant is **incomplete** for
full-tree coverage, even if its observable subset was perfectly captured. No observer is
`not_captured`.

## 4. Private local durability before CI forwarding

The observer owns a private sidecar writer; CI's best-effort logger is never the local
durability authority. Its only writer interface is:

```text
type AppendAck = Persisted(sequence: uint) | WriteFailed(sequence: uint, failure_class)
append(record: AllowlistedRecord) -> AppendAck
flush(deadline) -> Drained | TimedOut | WriteFailed
```

`Persisted` follows write, flush, and fsync of the append-only record. The hook offers
records to this bounded private writer without making engine outcome depend on I/O. The
writer records every accepted sequence; a full queue records loss. At finalization it
uses the same acknowledged append path to write/verify a manifest containing capture ID,
expected versus committed sequence counts, first/last sequence, and a SHA-256 chain
head. A missing or mismatched count/hash is `incomplete`; a manifest write/verification
failure is also `incomplete`, with allowlisted `failure_class=manifest_write` or
`manifest_verify`. If that record cannot be written, the reader treats the absent or
unverifiable final manifest as incomplete. Slow writer timeout, full queue, and observer
crash are loss causes, not inferred success.

Only a `Persisted` record is forwarded through the existing CI transport. A forwarding
failure is appended directly as an allowlisted local forwarding record—never re-entered
through the CI hook/dispatcher. CI currently catches disk failures, asynchronously queues
work, and drops on a full queue; its successful hook return is not a remote receipt.
https://github.com/microsoft/amplifier-bundle-context-intelligence/blob/9c602a5378d51b327d0c8cdfb11db1627d63be88/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/logging_handler.py#L397-L426
https://github.com/microsoft/amplifier-bundle-context-intelligence/blob/9c602a5378d51b327d0c8cdfb11db1627d63be88/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/logging_handler.py#L1286-L1325

Remote state is only `disabled`, `queued`, or `forward_failed`. `ingested` is forbidden
until a pinned server defines and live-tests an acknowledgement. Local sidecar failure
never changes the graph result.

## 5. Enforced metadata boundary and worker coverage

One serializer runs **before both** sidecar append and CI forwarding. It accepts only:

- opaque IDs; bounded identifiers for `node_id`, scope, worker kind, provider/model,
  source-event label, and call IDs;
- status/terminal/failure-class enums, RFC3339 timestamp, sequence/counter/duration, and
  non-content usage counts;
- fixed schema/version fields and boolean coverage flags.

It rejects every unknown key, `raw`, DOT, prompt/response, context, free-text error,
tool arguments/results, path, arbitrary scalar map, or overlong/invalid identifier.
`source_event` is retained for unknown-event reconstruction only when it passes the
bounded event-label grammar; a malicious label is rejected and makes capture incomplete.
Secret canaries and malicious unknown scalar/source-event tests must prove that neither
sink receives a forbidden value.

`workspace` and required `working_dir` are explicit operator CI-transport configuration,
validated outside the observation payload and never derived from event data. The
`working_dir` transport header is a separate privacy boundary, not record metadata.
CI's current envelope has top-level workspace/working directory and arbitrary data, so
this projection is mandatory.
https://github.com/microsoft/amplifier-bundle-context-intelligence/blob/9c602a5378d51b327d0c8cdfb11db1627d63be88/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/upload.py#L15-L52

`coding-agent` native session/provider/tool boundaries may be joined when emitted.
`llm-direct` is a one-shot `unified_llm.generate()` worker that advertises tool names and
emits provider boundaries; it is not required to become an agent loop. If it actually
executes tool calls, a future SDK-facing probe must emit correlated call/result evidence;
otherwise it records no tool call. https://github.com/microsoft/amplifier-bundle-dot-runner/blob/99a28f009ab6dd05329bc5fdcdea8d839b5a68a7/modules/loop-pipeline/amplifier_module_loop_pipeline/workers/direct_worker.py#L189-L332

The amplifier-agent v1 contract promises `create_agent`, `start_turn`, and ordered
`turn.events()`, but the pinned implementation has not shipped a usable public binding:
its package exports only `__version__`, while the legacy `Engine` needs a custom
`TurnHandler`. This design is blocked on the owners shipping that public binding; Unit 3
must not work around it privately. If/when shipped, outer turn/session/`call_id` evidence
can join a visit, but child/grandchild lifecycle remains separately backlogged pending
two implementations. https://github.com/microsoft/amplifier-agent/blob/fa33a2099bf748c0e4553c0b74eafe1077fb9326/contracts/agent-interface.v1.md#L23-L69
https://github.com/microsoft/amplifier-agent/blob/fa33a2099bf748c0e4553c0b74eafe1077fb9326/contracts/turn-events.v1.md#L70-L100

## 6. Proposed build units — pending approval, not work claims

### Unit 1 — mapped §9.6 emission gaps and separately ratified additions

- **Files:** dot-runner `modules/loop-pipeline/amplifier_module_loop_pipeline/{engine.py,retry.py,pipeline_events.py,worker_observability.py,handlers/parallel.py,handlers/pipeline.py}`, tests, and only the new ledger/extension rows the table requires. Frozen contracts and vision excluded.
- **Given** retry, restart, nested, parallel, cancellation graph, **when** observed,
  **then** existing CONFORM lifecycle emissions cover their listed §9.6 gaps unchanged;
  P fields/new attempt-cancellation-restart names ship only after their exact table rows
  are ratified. No engine ID, traversal, checkpoint, routing, or status change occurs.

### Unit 2 — acknowledged local capture and raw CI proof

- **Files:** dot-runner observer/observability module and tests; CI composition only via
  existing `modules/hook-context-intelligence/.../{upload.py,handlers/logging_handler.py}`.
  No CI server is built by default.
- **Given** append failure, manifest failure, slow-writer timeout, local/CI queue overflow,
  secret canary, and an unknown valid source-event label, **when** captured/forwarded,
  **then** ACK/count/hash oracles report incomplete correctly, forbidden data
  reaches neither sink, and graph outcome is unchanged. CI reconstruction must run against
  an actual server pinned by exact SHA at implementation time, version-gate its
  `context-intelligence` / `1.0.0` JSONL metadata envelope, reconstruct the unknown raw
  label, and make no typed-entity claim. The current schema itself requires this version
  gate and says JSONL lacks semantic grouping.
  https://github.com/microsoft/amplifier-bundle-context-intelligence/blob/9c602a5378d51b327d0c8cdfb11db1627d63be88/context/jsonl-event-schema.md#L24-L111

### Unit 3 — conditional three-worker parity

- **Files:** dot-runner `workers/direct_worker.py`, `modules/loop-agent/`,
  `modules/loop-amplifier-agent/`, parity/observation tests; any public-agent binding in
  amplifier-agent public bindings/tests, never frozen v1 contracts or private reach-ins.
- **Given** every presently observable worker runs the Unit 1 graph, **when** provider
  work, actual tools, retry, nesting, cancellation, resume, and telemetry loss occur,
  **then** visits join truthful evidence and direct calls appear only if executed. The
  amplifier-agent cell is blocked—not simulated—until its owners ship the public binding;
  later descendants remain `unobservable_public_api` until their separate contract bar.

## 7. Decision required and readiness

**Implementation steward decision:** approve the table's existing **CONFORM/emission-gap**
portion for implementation planning, and separately ratify—or reject—the explicitly
listed **PROPOSED EXTENSION** field/name subset. This is not a request for the steward to
perform ordinary classification; the mapping is above. Ratification is required because
it changes observability surface beyond named nlspec detail.

Ready for independent owner re-review only. No emitter, private writer, CI-server proof,
or worker-parity evidence has been implemented or executed.
