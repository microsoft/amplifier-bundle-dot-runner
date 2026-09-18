target: contracts/engine-surface.v1.md
ratified by owner 2026-09-16

# Historical publication record — completion-event identity

**Status:** historical decision and publication-form migration requested 2026-09-18; ready for a later promotion, not applied. This record is not new policy, a new proposal, a new version, or a claim that the active frozen v1 is normative-complete.

## Exact previously approved change

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

The two blocks above are copied from the immutable C15.5 candidate; no wording or approved meaning has changed.

## Historical evidence

- The immutable source proposal is `contracts/engine-surface.v2-candidate.md`; its recorded Candidate SHA-256 is `1d2d90ae022e285208dc109cce8a8faafe02d1bc76187447b890a0f48f219b19`.
- The unchanged receipt `contracts/engine-surface.ratification-20260916.md` records the exact steward response `ratified` on 2026-09-16 and identifies that candidate hash. It is the evidence for the column-zero historical owner stamp above, not a fresh ratification.
- The public delivery context is [microsoft/amplifier-bundle-dot-runner PR #108](https://github.com/microsoft/amplifier-bundle-dot-runner/pull/108) and [PR #109](https://github.com/microsoft/amplifier-bundle-dot-runner/pull/109).

## What does not change

- C15.1–C15.4, all C15.5 semantics, checkpoint/status behavior, retries, attempt and cycle observability, fidelity, context threading, routing, and state-graph behavior remain exactly as approved.
- `Outcome.session_id`, `status.json.session_id`, worker event-file identity, existing captures, implementation evidence, approval evidence, and the original proposal and receipt remain read-only.
- No frozen contract is edited; no implementation, PR, merge, release, ledger row, `.v3` version, test, configuration, or new mechanism is created by this record.
- The original candidate's scope, migration, and proof conditions still govern. Its copied diff was already approved; no fresh decision or ratification is requested or required here.

## Later-promotion publication note

Only when a later authorized promotion actually occurs, record the full path
`contracts/CANDIDATE-completion-identity-publication.md` in the **Changelog of
the target contract**, `contracts/engine-surface.v1.md`, alongside the approved
C15.5 insertion. The guard checks that target's Changelog to prevent reuse.
Do not edit that target now.

## Purpose and boundary

This record only reconciles the separate 2026-09-16 receipt with the guard-expected inline owner stamp. It is not an authoritative overlay, does not alter existing evidence or approval, and does not promote or modify `contracts/engine-surface.v1.md`. A guard may recognize this complete candidate by its target line and full copied change without modifying that target.
