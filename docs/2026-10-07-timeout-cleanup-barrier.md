# Timeout cleanup barrier

Tracks support #534. Local patch preparation, October 7, 2026; not merged or deployed.

## Existing behavior and defect

Node timeout requested cancellation, then used a second `asyncio.wait_for` to wait for cleanup. Its grace deadline sent another cancellation into the handler's teardown. If cancellation was suppressed, the supposedly bounded wait could also remain blocked. Allowing partial timeout advancement without confirmed cleanup can overlap subsequent work with the timed-out handler.

## Narrow correction

Wait for handler cleanup with `asyncio.wait`, without cancelling teardown a second time. If the task does not finish within the existing grace budget, terminate the pipeline with `node_cancellation_unconfirmed`, including when `allow_partial` was selected. Do not start verification, recovery nodes or another attempt while that cleanup is unconfirmed. Pipeline-fuse expiry retains its primary `max_pipeline_duration_exceeded` classification with the cleanup uncertainty in its notes.

When cleanup finishes, existing timeout/failure/partial routing is unchanged. External cancellation also requests cancellation of the shielded handler instead of abandoning it silently. The incomplete handler is not recorded as a successfully completed node. Late task exceptions are retrieved without treating late completion as verification evidence.

## Spec and scope

The pinned canonical Attractor spec section 2.6 bounds node execution, section 3.7 defines failure routing for returned stage outcomes, and section 3.8 requires sequential top-level execution. Existing extension 14 permits partial advancement after a node timeout. Unfinished cancellation is not a returned, settled stage outcome: this patch stops rather than route into further workspace effects. No new DOT attribute, handler shape, credential scope or contract promise is introduced.

This is an engine safety barrier, not proof of terminating external threads, orphaned agent sessions or all descendant processes. Runtime cancellation/cleanup ownership still needs integration qualification with the existing execution owners. The engine cannot truthfully certify the candidate after an unconfirmed cleanup.

## Regression evidence

`test_timeout_cleanup_barrier.py` uses a real engine and a deterministic handler whose cancellation cleanup remains active. It verifies bounded failure, no Verify invocation despite partial/failure routes, one cancellation request and one terminal pipeline event. A cooperating cleanup permits the existing partial-timeout path only after cleanup finishes.

The existing fuse, cancellation and subprocess-reaping tests remain regression controls. Stronger agent/descendant termination evidence is required before treating the full historical incident as closed.

## Local DTU evidence, October 7, 2026

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

Latest full loop-pipeline suite: 2,068 passed, 12 failures, 197 skipped. All 12 failing identities also failed on unchanged source in the same isolated environment; no new failing identities. An earlier patched run had 2,071 passing tests and nine failures; status-file freshness checks vary between runs, so neither count is presented as an unrelated fix. Provider keys were unset. Draft review does not waive full-runtime qualification or turn this mitigation into complete incident resolution.
