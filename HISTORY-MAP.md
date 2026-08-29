# History map — extractions from `amplifier-bundle-attractor`

This repo has received two history-preserving extractions from
`microsoft/amplifier-bundle-attractor` via `git filter-repo`. Both are
recorded, commit-by-commit, in `HISTORY-MAP.tsv` (two columns: `old` =
original commit SHA in `amplifier-bundle-attractor`'s full history, `new` =
the corresponding commit SHA in this repo, or `0000...0000` if that original
commit touched none of the paths extracted in that event). This file is the
human-readable index; `HISTORY-MAP.tsv` is the mechanical one — use it to
resolve any pre-extraction commit citation (council transcripts,
`SPEC_CONFORMANCE.md` entries, code review comments) to its commit here.

## Extraction 1 — the engine (`DESIGN-repo-split.md`, shipped)

- **Source:** `microsoft/amplifier-bundle-attractor` (full history through
  `d634fc5`).
- **Paths:** `modules/loop-pipeline`, `modules/pipeline-runner`,
  `modules/unified-llm-client`, `modules/remote-source`,
  `modules/tool-report-outcome`, `specs/`, `SPEC_CONFORMANCE.md`.
- **Method:** `git filter-repo` subtree extraction (asymmetric strategy —
  the runner repo got history-preserving extraction; the attractor repo got
  an ordinary `git rm`, not a rewrite, per `DESIGN-repo-split.md` §3.6).
- **Result:** became this repo's initial history (root commit at the top of
  `HISTORY-MAP.tsv`'s `new` column corresponds to this extraction).

## Extraction 2 — the second extraction (`DESIGN-worker-registry-core-split.md`
Phase 2, `attractor-79z`)

- **Source:** `microsoft/amplifier-bundle-attractor` @ `4bdc47a710d218a985361e9edb919bc941bc3161`
  (`origin/main`, fetched into a scratch clone at `/var/tmp/p2-extraction/`
  — the working checkout at `~/dev/better-attractor/amplifier-bundle-attractor`
  was never touched).
- **Paths (seven modules; `tool-pipeline-run` HELD BACK — see below):**
  `modules/loop-agent`, `modules/hooks-pipeline-observability`,
  `modules/hooks-pipeline-progress`, `modules/hooks-tool-truncation`,
  `modules/tool-apply-patch`, `modules/tool-dashboard-query`,
  `modules/tool-pipeline-status`.
- **Held back:** `modules/tool-pipeline-run` is **not** part of this
  extraction. The maintainer's ratified gate decision (per
  `DESIGN-worker-registry-core-split.md` §6.3, the P2-gate open question) is
  that its namespace debt (`@attractor:` mention syntax and an
  `"attractor-pipeline-runner"` default baked into
  `…/tool_pipeline_run/__init__.py:42,62,429`) is unresolved, and the module
  stays in `amplifier-bundle-attractor` pending a separate, dedicated item.
- **SHA range (original attractor history):** oldest original commit
  `6c8bf5ae25b1f13406306c6e395acbcccada90d4` (2026-02-09) through
  `9b4fb5beda9b14df800e82df20832488ee5401ff` (2026-08-28); 73 original
  commits touch these seven module paths.
- **Method:** `git filter-repo --path modules/loop-agent --path
  modules/hooks-pipeline-observability --path modules/hooks-pipeline-progress
  --path modules/hooks-tool-truncation --path modules/tool-apply-patch --path
  modules/tool-dashboard-query --path modules/tool-pipeline-status` against
  the scratch clone, producing 73 commits (72 ordinary + 1 merge). The
  filtered history was then **grafted onto this repo's `main` tip** via
  `git rebase --root --onto <main tip>` (chosen over a squash because 73
  commits is well under the ~200-commit fragility threshold, and the
  rebase completed with zero conflicts) followed by a fast-forward merge —
  producing a fully linear branch (no merge commit lands in this repo's
  history), which is required because this repo's branch-protection
  ruleset mandates linear history and therefore forbids a merge commit.
- **The one merge commit, explicitly:** original commit `342cf9f7…` (`Merge
  pull request #69 from microsoft/spec-conformance-structured-output`) was a
  fast-forward-equivalent merge in the original history — its second parent
  (`11c1e2d…`, mapped to `4789aca8ac87faef255a304ba667d9f37fd96612` in this
  repo) already contained everything the merge added, with zero unique diff
  of its own. `git rebase` correctly elided it; it carries no independent
  row in `HISTORY-MAP.tsv` because it maps to no independent commit here —
  its content is fully present via its parent chain.
- **Content fidelity:** a sha256 manifest of all 94 tracked files across the
  seven modules was computed at the source commit and at the destination
  branch tip and found byte-identical (94/94 files, same hashes, same
  paths). No content was altered by the move itself.
- **Authored changes riding on top (separate commits, never mixed into the
  move):** CI matrix wiring for the seven modules
  (`.github/workflows/ci.yml`), a `README.md` module-inventory update, and
  this `HISTORY-MAP.md`/`HISTORY-MAP.tsv` extension. `loop-agent`'s
  dependencies (`amplifier-unified-llm-client`,
  `amplifier-worker-parity-kit`, `amplifier-module-loop-pipeline`) were
  verified to already resolve via `git+…/amplifier-bundle-dot-runner@main#…`
  URLs — i.e. they already pointed at this repo before the module itself
  moved — so no dependency-source edit was needed or made.
- **Distribution/import names unchanged:** `amplifier-module-loop-agent`
  stays `amplifier-module-loop-agent`, etc. — only the git URL that serves
  these seven modules moved.
