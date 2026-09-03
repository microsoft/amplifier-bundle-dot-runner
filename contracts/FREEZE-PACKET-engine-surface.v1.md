# FREEZE PACKET — `contracts/engine-surface.v1.md`

**What this is.** The Freeze Bar evidence for `CONTRACT-engine-surface.v1`,
condition by condition and clause by clause, so the owner can decide from
artifacts rather than from a self-report.

**What this is not.** A stamp. `PROTOCOL.md` §5: *only the owner stamps FROZEN*,
and §7 resists self-ratified amendments "by lanes *or* by the orchestrator". The
contract stays **DRAFT** after this packet lands. Nothing here asks for less than
the whole bar; where a condition is unmet, this packet says so in as many words,
because pillar 5 holds that a missing artifact is a real result.

**Assessed:** 2026-09-02, against the tree at this branch. Guard suites re-run
green at assessment time: `ledger/checks` (214 passed, 24 skipped) and the doc
guards `test_extensions_ledger_integrity` · `test_doc_consistency` ·
`test_engine_semantics_doc_guard` · `test_explainer_doc_guard` ·
`test_examples_lint_clean` (12 passed, 6 skipped).

---

## The bar, at a glance

| # | Condition (`PROTOCOL.md` §5) | Verdict |
|---|---|---|
| 1 | The spec is written | **MET** — 17 Core clauses, each deriving from a named live `specs/EXTENSIONS.md` section, each carrying one executable Given/When/Then |
| 2 | A machine-checkable conformance kit exists, with ≥1 discriminating good/bad fixture pair | **NOT MET as a kit.** Discriminating tests exist for 15 of 17 clauses, but **none is bound to this contract**: every one was written against an EXTENSIONS section, and **no `ledger/rows.yaml` row derives from this contract**. The binding layer is a separate lane's work. C14 is partial; C17 has no test at all |
| 3 | At least one real implementation passes it | **MET.** One implementation — this engine — and its suites are green. Two named residuals below (C14.3's attachment path, C17's untested composition) are untested, not failing |
| 4 | A worked example exists end-to-end | **MET for 11 of 17 clauses**, via the three CI-executed capsule pipelines. **NOT MET for 6** (C1's node-attribute half, C2, C8, C10, C13, C17) — listed exactly, below |

**Overall: the bar is not met.** Condition 2 is the blocker, and it is structural
rather than a matter of writing more prose: this contract has no conformance
layer of its own yet.

---

## The worked-example corpus

Condition 4's evidence is one artifact family, and it is real: the three graphs
in `.github/capsule-pipeline/` — `capsule.dot`, `feature-capsule.dot`,
`task-runner.dot` — executed end-to-end by `.github/workflows/capsule-specify.yml`
and `capsule-implement.yml` as

```
dot-runner run … --worker coding-agent --param max_duration="$MAX_DURATION" …
```

with run artifacts uploaded as evidence. Between them they exercise `--worker`,
`--param` into `max_pipeline_duration="$max_duration"`, `must_write=`,
`goal_gate=`, `llm_provider="openai"` on a node, and the whole run directory.

**A named erosion.** Several EXTENSIONS entries cite exemplars under `examples/`
(`examples/pipelines/…`, `examples/patterns/task-runner.dot`,
`examples/objective/objective-runner.dot`). **That directory is not in the tree.**
`test_examples_lint_clean.py` skips accordingly — it is designed to, so this is
not a red test, but it does mean the corpus those entries point at is gone. Any
freeze that leans on those citations for condition 4 would be leaning on nothing.

---

## Per-clause evidence

Legend for condition 2 — **PAIR**: tests assert both the behavior *and* a
discriminating counter-case (absence, negative, or the same graph without the
attribute). **ONE-SIDED**: behavior asserted, no discriminating counter-case.
**NONE**: no test. Condition 4 — the worked example, or `—`.

| Clause | 2 — kit | Named tests | 4 — worked example |
|---|---|---|---|
| **C1** worker names / selection / default ladder | PAIR | `loop-pipeline/tests/test_worker_selection.py` (precedence ×3, `test_unknown_node_worker_attr_raises_loud_error`, `test_unknown_default_worker_raises_at_construction_time`, and the control `test_a_community_dot_with_no_worker_attribute_runs_via_direct_unchanged`); `test_worker_registry.py`; `test_worker_parity.py` + `worker-parity-kit`'s `broken_worker`; `pipeline-runner/tests/test_default_worker.py`, `test_library_seam_default_worker.py` | **Partial.** `--worker coding-agent` runs in both capsule workflows. **No shipped `.dot` uses the `worker=` node attribute** — that half has no end-to-end example |
| **C2** `status.json` verdict channel + spawn envelope | PAIR | `loop-pipeline/tests/test_status_file_contract.py` (RED-proofed; SF-006/SF-007 goal-gate interaction, `test_sf009_spawn_node_status_json_override_wins`, absent/malformed cases); `test_fail_closed_outcomes.py` (FC-008); `loop-amplifier-agent/tests/test_orchestrator.py::test_envelope_shape_never_fabricates_report_outcome`. Note `ledger/rows.yaml`'s `ATX-M-041` also asserts this behavior — but as a clause of the **external** nlspec (§4.5's status-file contract), not of this contract | **—** No shipped graph demonstrates a child writing a divergent `status.json` end-to-end |
| **C3** `must_write=` | PAIR | `loop-pipeline/tests/test_engine_must_write.py` — `test_case1_narration_no_write_fails` / `test_case3_planted_file_fails` / `test_case3b_equality_boundary_fails` / `test_empty_artifact_fails` against `test_case5_write_first_skeleton_passes` / `test_minimal_content_passes` / `test_case6_no_attribute_control`; retry-budget half in `test_retry.py` | **Yes** — `capsule.dot`'s `critique` / `critique_b` and `task-runner.dot`'s postmortem node declare `must_write=` |
| **C4** graph-level `$name` params | PAIR | `loop-pipeline/tests/test_graph_param_child_inheritance.py` — `test_absent_param_fails_loud` vs `test_supplied_param_resolves`, per-path message tests (`…_cli_mechanism`, `…_mounted_orchestrator_mechanism`, `…_composed_child_mechanism`), child-crossing (`test_child_pipeline_handler`, `test_manager_loop_handler`), and the AST all-call-sites guard `test_every_engine_call_site_threads_params`; `pipeline-runner/tests/test_params.py`, `test_lint_graph_param.py` | **Yes** — all three capsule graphs carry `max_pipeline_duration="$max_duration"`, supplied by `--param` in CI |
| **C5** subscription providers + rung-4 default model | PAIR | `pipeline-runner/tests/test_provider_detection.py` — the intent rule asserted both ways (`…_not_configured_from_gh_token_alone`, `…_generic_token_counts_with_explicit_ask`, `…_generic_token_ignored_when_ask_is_for_other_provider`, `…_high_intent_token_counts_even_without_explicit_ask`), plus `test_three_tables_derive_from_one_registry`; `loop-pipeline/tests/test_subscription_provider_direct_worker.py`, `test_llm_provider_alone_default_model.py`, `test_profile_no_default_model.py`, `test_sole_mounted_provider_default.py`; `loop-agent/tests/test_subscription_provider_prompt_profile.py` | **Partial.** `capsule.dot`'s `critique_b` declares `llm_provider="openai"` end-to-end. **Neither subscription provider appears in a shipped graph** |
| **C6** fuse at node granularity | PAIR | `loop-pipeline/tests/test_fuse_node_granularity.py` — `test_fuse_fires_during_node_execution_not_just_between_nodes` against `test_node_finishing_within_budget_completes_normally`, plus `…_node_own_timeout_still_governs_when_tighter_than_fuse` and `…_stubborn_cancellation_bounded_by_grace_window`; `test_node_timeout_units.py` | **Yes** — the capsule workflows set the fuse per invocation and their failure path reports it tripping |
| **C7** provider preflight | PAIR | `loop-pipeline/tests/test_provider_preflight.py`, `test_profile_resolver_parity.py`; `pipeline-runner/tests/test_provider_preflight_drive_engine.py` (both entry points) | **Yes** — every capsule run passes the preflight before its first node |
| **C8** refusal not degradation | PAIR | `loop-pipeline/tests/test_no_silent_fallback.py` — `test_unknown_shape_raises_value_error` / `…_lists_supported_shapes` / `…_names_the_bad_shape` against `test_known_shapes_still_dispatch_correctly`; `test_engine_semantics_doc_guard.py` (D-200 / D-201 / D-202a / D-202b); `test_engine.py`; `test_edge_selection_no_silent_fallthrough.py`; `test_spawn_suggested_next_ids_coercion.py`; ledger row `ATX-M-F01` in `ledger/checks` | **—** No shipped graph deliberately trips either refusal (correctly — they are refusals) |
| **C9** `shape=folder` / `dot_file=` | PAIR | `loop-pipeline/tests/test_child_dot_resolution.py`, `test_folder_node_failure_routing.py`; `pipeline-runner/tests/test_lint_folder_dot_file.py`; fixture pair `fixtures/parent_with_child.dot` + `fixtures/child_pipeline.dot` | **Partial** — the fixture pair is the only end-to-end sub-pipeline in the tree; no shipped pipeline uses `dot_file=` |
| **C10** session and thread scoping | ONE-SIDED | `loop-pipeline/tests/test_backend_clone.py`, `test_backend_full_continuity.py`, `test_fidelity.py`, `test_backend_fidelity.py`, `test_isolation_boundary_preferred_label.py` (which does carry both `…_clone_starts_without_preferred_label` and `…_child_converged_verdict_still_propagates_to_parent`) | **—** The sibling-branch `thread_id` case is asserted at unit level only |
| **C11** no `reasoning_effort` default | PAIR (absence) | `loop-pipeline/tests/test_doc_consistency.py` (D-243, pinned two-sided); `test_attribute_passthrough.py`; ledger row `ATX-M-F04` in `ledger/checks` | **Yes** — every capsule run omits the attribute and no default appears |
| **C12** fail-closed goal gate | PAIR | `loop-pipeline/tests/test_goal_gates.py`, `test_fail_closed_outcomes.py`, `test_goal_gate_retry_clears_failures.py`; fixture `fixtures/goal_gate.dot` | **Yes** — all three capsule graphs declare `goal_gate` |
| **C13** `outcome=` → `preferred_label` first | PAIR | `loop-pipeline/tests/test_conditions.py` — `test_preferred_label_equals` / `…_not_equals` / `…_none_resolves_to_empty` against `test_outcome_equals`; `test_edge_selection.py` | **—** Unit-level only; no fixture pair and no shipped graph turning on the distinction |
| **C14** additive graph vocabulary | **PARTIAL** | §21 `test_param_expansion.py`, `test_transforms.py`, `test_substitution_count_regression.py`; §20 `test_handlers.py`, `test_tool_cwd.py`, `test_tool_failure_capture.py`; §19 `test_human.py` (freeform + `attachments_inline` / `attachments_ref`); §14 `test_retry.py`, `test_attribute_passthrough.py`; §18 `test_parallel_policies.py` (`TestFailFastErrorPolicy`, `TestIgnoreErrorPolicy`) | **Partial** — `fixture_tool_reads_param.dot` and `fixture_human_gate.dot` are end-to-end for §21/§19; §14 and §18 have no shipped example |
| **C15** run directory as audit trail | PAIR | `loop-pipeline/tests/test_convergence_observability.py` (iteration dirs, `$iteration`, `trace.jsonl` shape), `test_worker_session_observability.py`, `test_run_directory.py`, `test_parallel_branch_observability.py`, `test_subgraph_runner.py`, `test_manager_loop.py`; `hooks-pipeline-observability/tests/test_session_events_redaction.py`; `pipeline-runner/tests/test_provenance.py`, `test_trace_subcommand.py` | **Yes** — capsule runs upload the run directory as evidence |
| **C16** validation narrowing + lint | PAIR | `loop-pipeline/tests/test_validation.py`, `test_dot_parser.py`, `test_retry.py`; lint half `test_topological_lint.py` (incl. `TestFolderDotFileAbsent`, `TestOutcomeLabelShadowingCalibration`), `test_inert_vocabulary_lint.py` (incl. `TestVocab001FalsePositives`) | **Partial** — `test_examples_lint_clean.py`, the corpus-sweep arm, **skips: `examples/` is absent** |
| **C17** bundle composition | **NONE** | No test asserts `attractor:attractor-expert` registration, the always-on `context:` key, or the ref-free same-repo source rule | **Partial** — the shipped bundle is itself the example, but nothing checks it |

---

## Exactly what is missing

**Clauses with no discriminating pair.**

1. **C17** — nothing tests bundle composition at all. A registration regression, or a same-repo source silently re-acquiring a git ref, would ship green. This is the one clause where the implementation is asserted **nowhere**.
2. **C14** — partial. Each of the five sub-items has behavior coverage, but the clause's own load-bearing claim — *"the same graph with the attribute removed behaves exactly as canonical"* — is asserted only for §21 and §18. §14 (`allow_partial` on timeout) and §19 (freeform/attachments) have no removed-attribute control.
3. **C10** — one-sided. `thread_id` branch-locality is asserted at clone/unit level; there is no test that runs two sibling branches declaring the same `thread_id` and proves neither sees the other's history, which is the clause's actual claim.

**Clauses with no worked end-to-end example.** C2, C8, C10, C13, C17; and half of C1 (the `worker=` node attribute), C5 (subscription providers), C9 (`dot_file=` outside a test fixture), C14 (§14, §18).

**The structural gap, which is the real blocker.** Every test above was written
against a `specs/EXTENSIONS.md` section, not against a clause of this contract.
There is no join: **no `ledger/rows.yaml` row derives from
`CONTRACT-engine-surface.v1`, and none may until it is stamped.** Until those
rows exist, a clause could silently drift and no check would name this contract
in its failure message. Seeding them is a separate lane by design; this packet
records the dependency rather than pretending the coverage above already
constitutes the kit.

**One open question the contract deliberately refuses to answer.** §35's
`report_outcome` **ordering barrier**: WAVE 5 (2026-08-30) removed the tool
module and the `metadata.report_outcome` transport, but
`modules/loop-agent/amplifier_module_loop_agent/agent_session.py` still gates
batch execution on a tool named `report_outcome`, the EXTENSIONS body still
asserts the barrier as behavior, and
`modules/loop-pipeline/tests/fixtures/report_outcome_convergence.dot` plus
`modules/loop-amplifier-agent/tests/test_spawn_report_outcome_transport.py`
survive. WAVE 5's deletion list does not name any of them. Whether that barrier
is live behavior or unreachable residue is an **owner ruling**, not a lane's
call, so `engine-surface.v1` states no clause about it and reserves the name.

---

## Recommendation

Do not stamp. The honest sequence is:

1. Ratify or amend the clause text (owner) — this is the only step needing owner attention today.
2. Seed `ledger/rows.yaml` rows from these clauses, one per checkable clause, with real assertions (separate lane). That is what turns the tests above into *this contract's* kit and satisfies condition 2.
3. Close the three named coverage gaps — C17, C14's removed-attribute controls, C10's sibling-branch case.
4. Rule on the `report_outcome` ordering barrier, then either add a clause or delete the residue.
5. Then, and only then, the stamp.

---

## Changelog

- **2026-09-02 — initial packet.** Assessed all four Freeze Bar conditions across
  the 17 Core clauses of `contracts/engine-surface.v1.md`. Verdict: **bar not
  met**, condition 2 blocking. No stamp requested; the contract remains DRAFT.
