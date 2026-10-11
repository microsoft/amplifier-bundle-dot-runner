---
id: ci-safe-escalated-human-gate
title: CI-safe escalated human-gate outcome
red_signal: AC-1: UNMET
criteria_digest: f9561b323c08266bcb4973d53fc19a16e041d09ff7d08cf00262e26d08711908
base_sha: fb35263d598187f05f33908c36f72287b95f4bfb
later_commit: e03f2844fa134e6d6a22d8d1722d205be1529a88
target_repo: /home/runner/work/amplifier-bundle-dot-runner/amplifier-bundle-dot-runner
verify: DEFINITION.verify.sh
---

# Goal

When an `escalate` human-gate node is reached without an `Interviewer` on the CI path, the human-gate handler writes `.ai/escalation.md` with the gate's question and the options' one-line meanings, and the run ends with the distinct `escalated` outcome rather than `abandon`. The process result for that outcome is distinct from a hard failure, and the escalation does not require a postmortem. An interactive run retains its existing choice-routing behavior.

# Why this matters

CI cannot honestly select a human option. An explicit escalation preserves the question and actionable choices for a maintainer instead of silently selecting a route or presenting the event as an ordinary hard failure. The extension record and ledger make this spec-silent behavior auditable without changing the shipped graph routes.

# Definition of done

- **AC-1:** `DEFINITION.verify.sh` invokes the public `HumanGateHandler.execute()` surface with no `Interviewer`, using two materially different runtime-created questions and option sets. It observes the `escalated` outcome, checks that `.ai/escalation.md` contains the supplied question and each option meaning on an artifact line, and checks that no postmortem artifact is required or produced. It then calls the public `run_pipeline` function directly, without replacing the runner, engine, handler, provider, or result, for a runtime-generated escalation graph and a materially different hard-failure graph; it observes the returned statuses, artifact, and postmortem absence independently of the CLI. Finally it invokes the actual public command entry point in child processes and checks its process-result mapping. The real runner and engine must propagate the `escalated` report, write an artifact containing the generated question and both generated meanings, produce no postmortem, and return a process result distinct from a hard-failure control run. Subject exceptions become this row's unmet result rather than verifier crashes.
- **AC-3 [guard]:** The verifier invokes the public interactive handler twice through `QueueInterviewer`, with materially different answers that must route to different target node IDs and return success. It also runs the existing `modules/loop-pipeline/tests/test_human.py` regression suite in the repository's locked project runtime, so the shipped human-gate behavior remains exercised.
- **AC-4 [guard]:** The verifier parses and lints `.github/capsule-pipeline/capsule.dot`, `feature-capsule.dot`, and `task-runner.dot` through the repository's public parser and linter, verifies every edge targeting `escalate` has a declared target, and checks those graphs plus `README.md` for the forbidden `trapdoor` wording. A clean lint result and the absence are observed, not inferred from file existence.
- **AC-5:** The verifier finds a dated extension entry naming both `escalated` and the human gate handler, explicitly classifying the behavior as an extension on a spec-silent surface and citing the owner ruling dated `2026-09-07`. It identifies every ledger row whose indexed tests cover `modules/loop-pipeline/tests/test_human.py`, requires every such row that existed at `base_sha` to differ from its pinned-base row, verifies each quoted contract passage against its contract file, and checks that each row has an executable indexed assertion. It then runs `ledger/checks/test_spec_conformance_matrix.py` in the locked project runtime.

# Non-goals

- A parked run that resumes in-process is out of scope; it is deferred to a follow-up feature once this lands.
- A GitHub-native `Interviewer` implementation is out of scope; it is deferred to the same follow-up.
- The interactive `Interviewer` path is not redesigned; AC-3 preserves its existing behavior.
- The three shipped graphs are not redesigned; AC-4 preserves their valid `-> escalate` edges and the absence of `trapdoor` wording.
- The implementation mechanism, internal status representation, artifact-writing helper, routing mechanism, exact numeric values of the distinct process results, and prose layout beyond the required question and one-line option meanings are delegated to the implementer. The verifier checks observable public behavior only.
- No new external dependency, network service, or postmortem workflow is part of this capability.
