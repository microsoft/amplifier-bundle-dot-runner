target: contracts/engine-surface.v1.md

# Candidate amendment — completion-event identity

**Status:** proposal only; not a contract-version adoption, ratification,
implementation authorization, or release authorization.

## Exact change

### Change 1 — C15, add C15.5 after C15.4

Current text:

```text
4. **Attempt and cycle observability** (§30): `attempt_count` on the outcome and `attempt` on `pipeline:node_complete`; a size-bounded `failed_step` capture; `cycle_index` on manager-loop and subgraph records; `pipeline:stage_retrying` carrying `reason="exception:<Type>"`; `_branch_id` scoping.
```

Replacement:

```text
4. **Attempt and cycle observability** (§30): `attempt_count` on the outcome and `attempt` on `pipeline:node_complete`; a size-bounded `failed_step` capture; `cycle_index` on manager-loop and subgraph records; `pipeline:stage_retrying` carrying `reason="exception:<Type>"`; `_branch_id` scoping.
5. **Completion-event identity:** On an outer-engine-produced `pipeline:node_complete`, generic `session_id`, when normal `HookRegistry` default fields provide it, identifies the emitting coordinator/host session. An `Outcome.session_id` that is a worker reference is instead emitted as `worker_session_id`. Engine-owned completion emitters never explicitly set generic `session_id`; no emitter fabricates either identity. A worker reference is emitted only where that path already receives one, and preserves the supplied value without new validation or coercion. No reference, or a value unusable for correlation, is unknown rather than a reason to infer a worker.
```

This is deliberately one insertion. C15.1–C15.4 retain their wording; the new
sentence defines only ownership on this event, not all hooks or session fields.

## Why an amendment is required

The external nlspec requires stage observation but assigns none of these
identity fields. Its §9.6 lists `StageCompleted(name, index, duration)` and an
observer or stream, but names no session owner or worker-reference field.
See [canonical §9.6](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/bb836b36c075c05a1d8d3784e7491d507a3895da/contracts/external/attractor-spec-canonical.md#L1609-L1648).
The surrounding §4.5 worker-status, §5.3 checkpoint, and §5.4 context-fidelity
rules remain untouched. Silence is not support for a change.

The frozen contract already promises worker-session observability in C15.2 and
attempt observability on `pipeline:node_complete` in C15.4
([C15](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/bb836b36c075c05a1d8d3784e7491d507a3895da/contracts/engine-surface.v1.md#L202-L210)).
Neither clause says whether that event's `session_id` belongs to its emitter or
to the worker. This proposal preserves those promises while resolving the
unspoken collision. It is not a conform-fix disguised as housekeeping: public
event documentation and tests intentionally treated a worker id as
`session_id`, so the change has an owned consumer compatibility cost.

That posture follows the Compatibility doctrine: spec-silent territory must be
argued rather than assumed, while a divergence must have measured evidence and
be loud ([VISION](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/bb836b36c075c05a1d8d3784e7491d507a3895da/docs/VISION.md#L67-L85),
[rules 1–5](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/bb836b36c075c05a1d8d3784e7491d507a3895da/docs/VISION.md#L91-L120)).
The next documented release must therefore announce this consumer-breaking
field-meaning correction; it must not be presented as fully backward compatible.

## Evidence — failure caught

The normal outer completion event contains its node, final attempt, and
execution index, then explicitly overwrites `data.session_id` from
`Outcome.session_id` ([engine.py:1151–1173](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/bb836b36c075c05a1d8d3784e7491d507a3895da/modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py#L1151-L1173)).
The same conditional identity assignment appears in the timeout and ordinary
`run_subgraph()` completion paths
([1014–1019](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/bb836b36c075c05a1d8d3784e7491d507a3895da/modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py#L1014-L1019),
[1508–1522](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/bb836b36c075c05a1d8d3784e7491d507a3895da/modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py#L1508-L1522)).
The timeout path constructs a fresh outcome with no worker ID; it is a
no-worker control, not evidence of a captured timed-out worker reference.

This is demonstrated, not an unsupported-hook assumption. The real
`HookRegistry` test sets default `session_id="parent-session-1"` and then
asserts the worker completion as `session_id="worker-session-1"`
([test_pipeline_events.py:609–641](https://github.com/microsoft/amplifier-bundle-dot-runner/blob/bb836b36c075c05a1d8d3784e7491d507a3895da/modules/loop-pipeline/tests/test_pipeline_events.py#L609-L641)).
Thus the outer emitter replaces the host/coordinator routing identity with the
worker reference.

At the pinned Context Intelligence implementation, `LoggingHandler.__call__`
persists and dispatches by `data.session_id`, returning early when it is absent
([logging_handler.py:1460–1482](https://github.com/microsoft/amplifier-bundle-context-intelligence/blob/3e7d597f086e8c1462ddb132976f78a5bd0d6af7/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/logging_handler.py#L1460-L1482)).
Its initial metadata likewise treats that value as the captured session identity
and has no restoration of the original owner
([1779–1790](https://github.com/microsoft/amplifier-bundle-context-intelligence/blob/3e7d597f086e8c1462ddb132976f78a5bd0d6af7/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/logging_handler.py#L1779-L1790)).

A source-analyzed synthetic DTU proof observed twelve outer events and one
outer-emitted `pipeline:node_complete` for `write_status` (attempt 1, execution
index 1) filed under a worker-named native capture with no parent; the actual
hosted worker capture separately contained forty events, three model pairs, and
two tools, but not that outer completion. That is a misfiled outer-produced
completion, **not** evidence that the worker lifecycle should mirror it. A
worker reference without its capture is a dangling reference, not proof that a
capture was ingested or that a parent edge exists. Once overwritten, the
original outer identity cannot be reconstructed honestly from timestamps or
directories.

The CI document's `CannotOverride` wording is not frozen normative text and is
contradicted by the real registry test above. Correcting that documentation is
an implementation prerequisite in the owning CI repository, not part of this
one-file proposal; this amendment does not claim that one event becomes globally
non-overridable.

## Scope of the required implementation and proof

The static census is nine completion emits. It is a bounded implementation
scope, not a claim about unreviewed pipeline events:

| Site | Current identity treatment | Required treatment |
|---|---|---|
| `engine.py:770–782` skip | no explicit session id | retain no worker field |
| `engine.py:998–1019` timeout | conditional assignment, but fresh outcome has no worker ID | retain emitter binding; no worker field |
| `engine.py:1170–1173` main completion | outcome session id when supplied | `worker_session_id` when supplied |
| `engine.py:1474–1487` subgraph child-resolution | no explicit session id | retain no worker field |
| `engine.py:1493–1506` subgraph exception | no explicit session id | retain no worker field |
| `engine.py:1507–1522` subgraph completion | outcome session id when supplied | `worker_session_id` when supplied |
| `engine.py:2087–2097` terminal child-resolution | no explicit session id | retain no worker field |
| `engine.py:2212–2222` mid-node fuse | no explicit session id | retain no worker field |
| `handlers/parallel.py:190–201` branch completion | no explicit session id | retain no worker field |

All nine must preserve normal HookRegistry default host binding and never
explicitly write generic `session_id`. Hookless or mock registries with no
default identity must not synthesize an emitter identity; bare hooks receive a
worker field only if the existing path supplies one. `None` omits
`worker_session_id`; an empty or otherwise unusable supplied value gains no
invented meaning.

Update `modules/loop-pipeline/amplifier_module_loop_pipeline/pipeline_events.py`
with the correction: generic `session_id` is registry-supplied emitter identity
when available; optional `worker_session_id` is the supplied worker reference.
Do not describe either field as unconditionally present.

Before implementation merges, tests must cover every changed branch, including
worker-backed success, failure, and final retry attempt 2; timeout as a no-worker
control; and child-resolution, subgraph exception, fuse, skip, and parallel
controls. A supplied worker ID with no available capture must remain a
dangling reference, not a fabricated capture or parent edge. Hookless/mock
controls must retain the supplied worker reference without inventing an emitter
ID. The first case must be one attempt. A retry proves only the
final reference and must not assert an invented mapping for earlier attempts.
Two independent pipelines using the same node name and different worker ids
must prove correlation is not guessed from names, timestamps, or capture
directories. The invariant is exactly one outer completion under the emitter
and zero misfiled copies of that particular outer-produced completion under the
referenced worker capture; it does not prohibit legitimate nested pipeline
events in a worker capture.

The integration gate must exercise actual public hosted-worker/model/tool
activity plus the no-worker native CI-local control and configured isolated
server query visibility. A `202 Accepted` response alone is not proof of
capture. Disabled or unavailable capture and bad authentication must not alter
the engine outcome. Outcome values, `status.json.session_id`, per-worker event
file identity, attempt counts, execution indexes, graph state, retry behavior,
fidelity, checkpoints, and consumer-provided routing configuration require
identical-oracle checks. Worker-owned canonical LLM-call accounting remains
unchanged; provider-response counts do not replace it.

## Migration and compatibility boundary

At the next specifically documented release, the release note must identify the
first corrected engine version and commit once assigned; neither is invented or
approved by this draft. New readers treat event `session_id` solely as emitter
identity and `worker_session_id` solely as the worker reference. They must not
blindly use `worker_session_id ?? session_id`: a new no-worker event has only
an emitter id, not a worker.

Readers classify old versus corrected records only from positive producer
provenance (recorded engine version and revision), never from node names,
timestamps, or capture-directory layout. When that provenance is unavailable,
the format is unclassified: do not guess that a generic session ID is a worker.
Historical worker-bearing records that overwrote their owner cannot recover
that owner and remain unknown; unaffected legacy records are not invalidated.
Existing captures stay unmodified. There
is no dual emission and no permanent dual-purpose alias retaining the old
worker-in-`session_id` bug. A bounded scan of DotRunner, Attractor, the CI
bundle, and server found no in-repository production observer dependent on the
old worker meaning beyond tests; external consumers are unknown and are the
real compatibility cost.

Keeping the old key and making CI ignore this override globally would break
other producers; a new dispatch framework is unnecessary, and query-only repair
cannot recover the lost owner. This changes neither event type, UUID/span API,
parallel writer behavior, CI generic payload defaults/ignores, nor any
agent-private bridge. It requires a dated ledger disposition and semantic
regression row before shipment, consistent with EXTENSIONS §§26 and 30; it does
not edit append-only history or claim that row has passed.

## What does **not** change

- C15.2 worker-status files and C15.4 attempt semantics stay intact.
- `Outcome.session_id`, `status.json.session_id`, and worker event-file identity retain their meanings and values.
- Checkpoint/state-graph, routing, retry, fidelity, and context-threading do not change.
- The frozen v1 remains untouched until amendment promotion; no wholesale rewrite is proposed.

## Steward decision

**Decision (choose one):** ☐ ratified · ☐ ratified with edits · ☐ declined · ☐ later

**Steward notes / required edits:**

> _No implementation, PR, push, merge, ledger edit, or release follows without a later explicit decision._