# History map — extractions from `amplifier-bundle-attractor`

This repo has received three history-preserving extractions from
`microsoft/amplifier-bundle-attractor` via `git filter-repo`. All three are
recorded, commit-by-commit, in `HISTORY-MAP.tsv` (two columns: `old` =
original commit SHA in `amplifier-bundle-attractor`'s full history, `new` =
the corresponding commit SHA in this repo, or `0000...0000` if that original
commit touched none of the paths extracted in that event). This file is the
human-readable index; `HISTORY-MAP.tsv` is the mechanical one — use it to
resolve any pre-extraction commit citation (council transcripts,
`SPEC_CONFORMANCE.md` entries, code review comments) to its commit here.

## Known gap in the `new` column — rebase-merge rewrites SHAs

**Read this before trusting a `new` SHA that does not resolve.**

An extraction lands here through a pull request, and this repo's
branch-protection ruleset mandates linear history, so every PR merges with
`--rebase`. A rebase **re-writes every commit SHA on the branch**: the commit
whose SHA an extraction recorded while the work sat on its lane branch is not
the commit that ends up on `main`. Same content, same author, same message —
different SHA. A `new` value captured before the merge therefore names a
commit that is unreachable from `main` once the branch is deleted.

As of Extraction 3, **188 of the 512 distinct non-zero `new` SHAs in
`HISTORY-MAP.tsv` are unreachable from `main` for exactly this reason.** They
are all Extraction 2's; Extraction 1's survived because it became this repo's
initial history and was never rebased onto anything. Extraction 3's 10 rows
were corrected to their post-merge `main` SHAs and are all reachable.

Repairing the remaining 188 is mechanical and safe — each unreachable SHA has
exactly one same-subject, same-`git patch-id` counterpart on `main` — but it is
a bulk edit to a record file and is deliberately left as its own change rather
than folded into an unrelated extraction. **Until it lands: if a `new` SHA does
not resolve, look up its commit message and find that message on `main`.**

Future extractions: record `new` **after** the merge, from `main`, not from the
lane branch.

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

## Extraction 3 — `tool-pipeline-run`, the held-back module (`attractor-24e` stage 2)

This is the module Extraction 2 explicitly **held back** (see the "Held back"
bullet above). The gate condition named there — its namespace debt — was paid
off *in `amplifier-bundle-attractor` first*, as `attractor-24e` **stage 1**
(two commits, `2a2a1f88…` and `5ad4de98…`, both mapped in
`HISTORY-MAP.tsv` like every other commit in this extraction), so that the
de-attractorization is part of the module's own history and rides across the
move rather than appearing as a post-move edit here. This extraction is
**stage 2**: the move itself, carrying zero authored content change.

- **Source:** `microsoft/amplifier-bundle-attractor` @
  `1b7bac54acda0a699e56a6480ee00fbc5df3d993` (`main`).
- **Path (one module):** `modules/tool-pipeline-run`.
- **SHA range (original attractor history):** oldest original commit
  `367def86ce947ed0d2d3fb155b0598b249e391af` (2026-02-13) through
  `5ad4de986047d067b341e7ab337981cb016508a0` (2026-09-06); **10** original
  commits touch this module path, and all 10 are ordinary commits — unlike
  Extraction 2, **no merge commit** falls in this set, so there is no
  elided-merge caveat to record.
- **Method:** `git filter-repo --path modules/tool-pipeline-run` against a
  scratch clone, producing 10 commits, then grafted onto this repo's `main`
  tip (`1762fe95fe3a9e15052a38d994d0c7a268cd344f`) — same rebase-graft
  strategy as Extraction 2, and for the same reason: this repo's
  branch-protection ruleset mandates linear history, so the result is a
  fully linear chain (`8ba930b4…` parents directly onto the `main` tip) with
  no merge commit. The `new` column for this extraction records the
  **post-merge `main`** SHAs, not the pre-merge lane-branch ones — see "Known
  gap in the `new` column" above for why that distinction matters and which
  earlier rows still need it applied.
- **Content fidelity:** verified by a full recursive tree comparison of the
  moved directory against the source repo's copy at the source commit —

  ```
  $ diff -r modules/tool-pipeline-run \
      ~/dev/better-attractor/amplifier-bundle-attractor/modules/tool-pipeline-run
  $ echo $?
  0
  ```

  Zero differences across all 7 tracked files. No content was altered by the
  move itself.
- **History preservation:** verified by `git log --follow` on the module's
  main source file, which walks back through all 8 of the commits that touch
  it, oldest (`8ba930b4…`) through newest (`2c372edd…`) — i.e. the file's
  ancestry survived the move rather than starting at a single squashed
  import. (8 of the 10, because `4af0ada2…` and `f26944df…` touch other files
  in the module, not `__init__.py`.)
- **`uv.lock` — deliberately absent:** 13 sibling `modules/*/uv.lock` files
  are committed in this repo, so its absence here is worth stating
  explicitly rather than leaving as an apparent oversight. The source repo
  does **not** track a `uv.lock` for this module either (`git ls-files
  modules/tool-pipeline-run` lists 7 files, none of them a lock), so
  generating one would be an authored content change riding inside a move
  that must stay byte-identical. CI resolves the module's dependencies with
  `uv sync` at job time and does not require a committed lock. If a lock is
  wanted, it belongs in its own later commit.
- **Authored changes riding on top (separate commits, never mixed into the
  move):** CI matrix wiring for the module (`.github/workflows/ci.yml`,
  `c5d4389…`), a `README.md` module-inventory update (`2c80cca…`), and this
  `HISTORY-MAP.md`/`HISTORY-MAP.tsv` extension.
- **Distribution/import names unchanged:** `amplifier-module-tool-pipeline-run`
  stays `amplifier-module-tool-pipeline-run` — only the git URL that serves
  it moved.
- **The attractor-side copy is NOT deleted by this extraction.** Removing it
  there is a separate, third step, folded into the gated `28x` slim — the
  same asymmetric strategy used for Extraction 1 (`DESIGN-repo-split.md`
  §3.6): the receiving repo gets the history-preserving extraction, the
  source repo gets an ordinary later `git rm`, never a rewrite.
