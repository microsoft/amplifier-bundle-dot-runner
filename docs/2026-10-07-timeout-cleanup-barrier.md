# Timeout cleanup barrier

Tracks support #534 and draft PR #117. October 7, 2026; not approved, merged or deployed.

## Existing behavior and defect

Node timeout requested cancellation, then used a second `asyncio.wait_for` to wait for cleanup. Its grace deadline sent another cancellation into the handler's teardown. If cancellation was suppressed, the supposedly bounded wait could also remain blocked. Allowing partial timeout advancement without confirmed cleanup can overlap subsequent work with the timed-out handler.

## Proposed safety barrier

Wait for handler cleanup with `asyncio.wait`, without cancelling teardown a second time. A private `_BoundedNodeResult` carries the handler outcome, timeout flag and cleanup-pending flag. Public failure text is not used as a control-flow discriminator. If cleanup remains pending, the proposed barrier terminates without verification, recovery or another attempt, including when `allow_partial` was selected. The public reason stays `timeout`; uncertainty is explained in notes. A binding pipeline fuse retains `max_pipeline_duration_exceeded` and its existing hard-stop semantics.

When cleanup finishes, existing timeout/failure/partial routing is unchanged. External cancellation also requests cancellation of the shielded handler instead of abandoning it silently. The incomplete handler is not recorded as a successfully completed node. Late task exceptions are retrieved without treating late completion as verification evidence.

## Spec and scope

The pinned canonical Attractor spec section 2.6 bounds node execution, section 3.7 defines failure routing, and section 3.8 describes sequential top-level execution. Existing extension 14 permits partial advancement after a node timeout. Neither citation authorizes the proposed hard-stop exception: refusing failure/retry/partial routing while cleanup is pending is an observable behavioral change, not merely a diagnostic fix. The proposal is explicit in `specs/EXTENSIONS.md` section 14, and ledger row ATX-M-117 is OPEN-PINNED, not silently CONFORMS or falsely DIVERGED-by-decision.

**Maintainer approval is a merge gate.** A maintainer must accept or reject the exception to section 3.7 and extension 14, decide the compatibility/upstream-action treatment, and record the decided ledger disposition in the same change before merge. Approval has not been obtained or invented. A typed private result avoids a new public failure vocabulary but does not itself settle the routing contract.

This is an engine safety barrier, not proof of terminating external threads, orphaned agent sessions or all descendant processes. Runtime cancellation/cleanup ownership still needs integration qualification with the existing execution owners. The engine cannot truthfully certify the candidate after an unconfirmed cleanup.

## Regression evidence

`test_timeout_cleanup_barrier.py` uses a real engine and a deterministic handler whose cancellation cleanup remains active. It verifies bounded failure, no Verify invocation despite partial/failure routes, one cancellation request and one terminal pipeline event. A cooperating cleanup permits the existing partial-timeout path only after cleanup finishes.

The existing fuse, cancellation and subprocess-reaping tests remain regression controls. Stronger agent/descendant termination evidence is required before treating the full historical incident as closed.

## Initial draft DTU evidence, October 7, 2026

The credential-free `goal-bugs-dtu-20261007` Incus DTU ran the actual engine with its unchanged five-second cleanup grace. A controlled handler launched a real subprocess writing a timestamp every 50 ms and deliberately held cancellation cleanup until released. The graph offered both partial and failure routes to Verify. This qualifies the engine barrier, not an actual Amplifier agent or assembled Resolve stack.

| Observation | Unchanged baseline | Patched |
|---|---|---|
| Unconfirmed cleanup before experiment release | Still waiting at 5.80 s; two cancellation requests | Failed at 5.21 s; one cancellation request |
| Unconfirmed cleanup final outcome | Advanced to Verify and success after release | `node_cancellation_unconfirmed`; Verify never invoked |
| Worker still alive before release | Yes, 115 writes observed | Yes, 103 writes observed |
| Cooperative cleanup control | Verify after process reaped; success | Verify after process reaped; success |
| Terminal pipeline events | Exactly one per completed experiment | Exactly one per completed experiment |

**The patched engine did not terminate the uncooperative worker.** Experiment cleanup subsequently killed/reaped it; that cleanup is not attributed to this fix. Preventing dependent work is the supported claim. Keep support #534 open for the actual agent/descendant lifecycle boundary.

Runner baseline: `ba0f9453a6343466eddab00204167f8b41685ad0`; Python 3.13.5. Engine baseline SHA-256: `9e53e07abdbfa581567c0ea974394ec643f4b14b6376325ec5f3287a5f4ae4ef`; patched: `8067c221388463f40626d7938ce0df355a40743486e05b87d5536d23d1283b55`. Profile, experiment, JSONL events, subprocess observations and results are retained locally under `worktrees/goal-bugs-20261007/dtu-evidence` in the Resolve workspace.

Initial draft full loop-pipeline suite: 2,068 passed, 12 failures, 197 skipped. All 12 failing identities also failed on unchanged source in the same isolated environment. Earlier counts varied; no unrelated repairs were claimed. Provider keys were unset. Draft review does not waive full-runtime qualification or turn this mitigation into complete incident resolution.

## Revised PR evidence: typed state and existing public reasons

The revised local DTU `goal-bugs-pr117-revision-20261007` repeated the same credential-free experiment against engine SHA-256 `12cb154427a107666fa6dbf5dc2b1b8c7d17ff1194ecbfee521c78a20701b71b`. Cleanup-pending execution failed after 5.21 s with public `failure_reason="timeout"`, one cancellation, no Verify and exactly one pipeline completion. The subprocess was still alive with 104 writes before experiment cleanup killed/reaped it; termination is still not attributed to the engine. The cooperative control reaped its process before Verify and succeeded. Profile, script, source snapshot, events and result are retained locally under `worktrees/goal-bugs-20261007/dtu-evidence/revised117` in the Resolve workspace.

Regression tests observe existing `timeout` in the returned outcome, node event and `status.json`; a binding fuse retains `max_pipeline_duration_exceeded`. Ordinary handler failures, including a literal diagnostic equal to the removed draft label, still follow failure edges. This demonstrates that reason text is not a hidden control channel. The interrupted node is not marked completed; one terminal event is preserved.

Verification: 76 focused cleanup/routing/fuse/event tests passed; ledger matrix/batch checks passed 289 tests with 40 skips. Full loop-pipeline suite: 2,073 passed, 11 failed, 197 skipped. Every failing identity also failed on unchanged source; there are no new failure identities. New tests lint clean and changed Python formats clean; inherited engine/event-test lint debt is unchanged. No full model-backed worker qualification or maintainer approval is claimed.
