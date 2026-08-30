# Capsule pipeline — the "specify" and "implement" stages (dot-runner port)

This directory ports the issue → attractor → PR system from
[`microsoft/amplifier-bundle-attractor`](https://github.com/microsoft/amplifier-bundle-attractor)
(source pin: `11cb3d7f0c51b30d1e21423db511ccffbec83506`, its `main` tip at
re-sync time, 2026-08-29) onto **this repo** — `amplifier-bundle-dot-runner`,
the engine itself.

That last fact is the whole reason this is a *port*, not a blind copy: in
the source repo, the pipeline drives an engine that lives in a *different*
repository (fetched at run time). Here, the pipeline drives the engine
*that ships in the same tree it is patching*. See "The engine difference"
below before touching any workflow.

- **specify**: given a GitHub issue labeled `ready:spec`, an attractor
  pipeline (`capsule.dot`) reads the issue, investigates the pinned
  repository, and produces a **work capsule** — a definition-of-done
  markdown (`DEFINITION.md`) paired with an executable gate script
  (`DEFINITION.verify.sh`) that is provably red-for-the-right-reason at the
  issue's base commit and provably non-vacuous, then judged behavior-bound
  by one LLM critic on a second model family. The pipeline never implements
  a fix and never merges anything; it opens a **capsule PR** for a human to
  review. See `.github/workflows/capsule-specify.yml`.
- **specify (feature)**: the same stage for a FEATURE request
  (`feature-capsule.dot`), driven by the `ready:feature-spec` label. RED-at-base
  stops discriminating when the capability is simply absent, so the anchor
  becomes maintainer-authored acceptance criteria delivered over the
  authenticated issue-comment channel and pinned by digest before any budget
  is spent. See `.github/workflows/feature-specify.yml`.
- **implement**: given a merged capsule PR (or a manual dispatch naming a
  capsule path), a hardened convergence-loop attractor (`task-runner.dot`)
  makes real code changes, verifies them against the gate, subjects green
  work to LLM critique, and packages the fix on a branch as a **fix PR**.
  See `.github/workflows/capsule-implement.yml`.

## Files

| Path | What it is | Source-pinned? |
|---|---|---|
| `capsule.dot` | The specify-stage attractor pipeline (`digraph CapsulePipeline`). | Yes |
| `feature-capsule.dot` | The FEATURE specify-stage pipeline (`digraph FeatureCapsulePipeline`). | Yes |
| `task-runner.dot` | The implement-stage pipeline (`digraph BacklogTaskRunner`). | Yes |
| `scrub_secrets.py` + `test_scrub_secrets.py` | Run-evidence secret scrubber + upload gate (`scrub` / `scan` / `gate`). | Yes |
| `capsule_pair_fence.sh` | Pair-integrity fence: sha256 every capsule_out file immediately after the run, re-verify immediately before the branch push. | Yes |
| `verify_shipped_gate.sh` | Executes the SHIPPED capsule gate (the exact bytes about to be pushed) in a pristine scratch worktree at the pinned base SHA. | Yes |
| `vendor/backlog/check-upstream-leaks.sh` + `vendor/backlog/fixtures/leak-scan/*` | Structural-leak tripwire for upstream-bound text (deny-list grep + self-test fixtures). | Yes |
| `vendor/runner/check-existing-tests.py` | Advisory tool the `critique` node may invoke — not a blocking gate. | Yes |

Every file above carries an outer **RE-VENDORED INTO amplifier-bundle-dot-runner**
provenance box at its top, naming the source path and commit. Below that box
each file is byte-identical to the source (including the source's own inner
vendor box, where it has one — most of these files were themselves vendored
into the attractor repo from a private working repo; that chain is preserved
verbatim, not collapsed). Do not hand-edit below the outer box; re-sync by
re-copying the source file and re-applying only the outer box.

## Deliberate deltas from the source (this port is NOT byte-identical to attractor's `.github/`)

1. **`attractor-pipeline-dual.yaml` is NOT ported.** In the source repo this
   file mounted a dual-provider (anthropic + openai) base bundle via
   `ATTRACTOR_PIPELINE_BUNDLE`, working around a bug where a spawned child's
   `llm_provider` preference couldn't promote a provider the parent session
   hadn't already mounted. As of the engine's 0.2.0 "worker-surface flip"
   (this repo — `default_worker.py` + `loop-pipeline/backend.py`'s per-spawn
   `orchestrator_config["llm_provider"]` injection, see "Bug B" in
   `modules/loop-pipeline/amplifier_module_loop_pipeline/backend.py`),
   `--worker coding-agent` honors a node's declared `llm_provider` directly at
   spawn time, and `--bundle`/`DOT_RUNNER_BUNDLE` were removed from the CLI
   entirely (`cli.py` module docstring). The **source repo's own current
   workflows already dropped the `ATTRACTOR_PIPELINE_BUNDLE` mount** for this
   exact reason (see `capsule-specify.yml`/`capsule-implement.yml`/
   `feature-specify.yml`'s "DUAL-FAMILY JUDGE, WORKER-SURFACE FLIP" comments
   at their re-synced pin) — porting the now-unused dual-provider bundle here
   would be porting dead configuration. It also still names
   `modules/tool-report-outcome`, a module this repo deleted outright in the
   0.2.0 repair release (`report_outcome` tool removed; `status.json` is the
   taught verdict channel) — porting it unmodified would ship a bundle that
   fails to resolve. Both `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are still
   required repo secrets (see workflow preflights) since the dual-family
   critique nodes are unchanged; they just no longer need a bundle file to
   reach the right family.
2. **The "detach the engine" step snapshots *locally*, not from a clone.**
   The source workflows `git clone --depth 1` this exact repo
   (`microsoft/amplifier-bundle-dot-runner@main`) into `$RUNNER_TEMP` because
   the engine lives elsewhere relative to the attractor checkout. Here the
   checkout *is* the engine, so the port tars the local working tree (minus
   `.git`/`.venv`/`__pycache__`) into `$RUNNER_TEMP/engine-src` instead of
   cloning anything — same structural goal (a maker rewriting the workspace
   can never reach the copy of the engine actually driving the run), but the
   snapshot is pinned to the run's own base SHA by construction (no floating
   `@main` fetch to go stale or drift from the commit under test).
3. **CLI install is the root form, not the `modules/pipeline-runner` form.**
   The source points `--project` at `modules/pipeline-runner` inside its
   fetched copy of this repo. Here `--project` points at the snapshot's
   **repo root** (`$RUNNER_TEMP/engine-src`), which resolves
   `amplifier-module-pipeline-runner` via the root `pyproject.toml`'s
   `[tool.uv.sources]` local-path entry — the same console script
   (`dot-runner`), reached through this repo's own documented root-install
   form (`amplifier-dot-runner`, see the top-level README's "Getting
   started"). No `bundles/` directory copy is needed either: that directory
   is attractor's opinionated layer and does not exist in this repo, and the
   current CLI's default base bundle path never reads a local `bundles/` dir
   (dead code path in `runner.py`'s comments, superseded by `default_worker.py`).
4. **`--worker coding-agent` is explicit on every run invocation** (source
   already does this too, but it is called out here because it is now the
   *entire* worker-selection mechanism — there is no bundle fallback path
   left to fall back to).
5. **README (this file) is not byte-ported.** The source README's provenance
   ledger narrates that repo's own multi-month incident history (run IDs,
   issue numbers, dated re-syncs against a private working repo) — none of
   that is this repo's history. This file instead documents the port itself:
   what moved, what changed mechanically, and why.

## Re-sync procedure

1. Shallow-clone `microsoft/amplifier-bundle-attractor` at the commit you
   want to sync to.
2. For each byte-pinned file above: diff the source file's body (everything
   below *its own* header box, if it has one) against this repo's copy
   (everything below the "RE-VENDORED INTO..." box). Re-copy on a real diff.
3. Re-apply the "RE-VENDORED INTO amplifier-bundle-dot-runner" box with the
   new source commit SHA and date.
4. Re-check the deltas above still hold (in particular: has the source
   re-introduced `ATTRACTOR_PIPELINE_BUNDLE`? has `tool-report-outcome` or
   `--bundle` come back?) — update this ledger if any assumption changed.
5. `uv run --project modules/pipeline-runner dot-runner lint <file>.dot` on
   every re-synced `.dot` file; `python3 -m py_compile` on every re-synced
   `.py`; `bash -n` on every re-synced `.sh`.
