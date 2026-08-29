# amplifier-bundle-dot-runner

The engine that runs `.dot` pipelines: a DOT-graph-driven multi-stage AI
workflow orchestrator, plus the CLI and provider client it depends on —
packaged as a proper, composable **Amplifier bundle**.

## 0.2.0 -- repair release (breaking changes)

This release repairs drift from the vendored spec that had crept into prior
releases (maintainer ruling, 2026-08-29/30):

- **`report_outcome` tool REMOVED.** No compat window, no deprecation
  period. `modules/tool-report-outcome` is deleted; `status.json` (spec
  Sec 4.5 / Appendix C -- see `specs/EXTENSIONS.md` Sec 35's dated
  `status: REMOVED` note and Sec 41) is the taught, spec-native verdict
  channel for every worker, spawned or direct.
- **`--bundle`/`DOT_RUNNER_BUNDLE` REMOVED from the CLI.** Bundles are
  under the hood now, never a user-facing concept. The whole user-facing
  worker-selection story is `--worker direct|loop-agent|amplifier-agent`
  (or a node's own `worker=` attribute, EXTENSIONS.md Sec40).

This repo implements the vendored **strongdm/attractor** nlspec faithfully
and is verified against it mechanically (see "Spec fidelity" below). It is
**not** an opinionated pipeline layer, a pattern library, or an authoring
guide — it is the mechanism. Policy (agents, providers, examples, authoring
docs) lives in the repos that consume this one.

## What's here

| Path | What it is |
|---|---|
| `bundle.md` | Root bundle (`dot-runner`) — includes the `dot-runner-core` behavior. |
| `modules/loop-pipeline` | **The engine.** DOT parser, validator, graph execution engine, handler dispatch. |
| `modules/pipeline-runner` | The `dot-runner` CLI (`run` / `resume` / `doctor` / `trace` / `lint`) plus the `drive_engine` / `run_pipeline` library surface. The `attractor` command has been removed entirely -- see "Getting started" below. |
| `modules/unified-llm-client` | Provider-agnostic LLM client — a faithful implementation of the Attractor Unified LLM Client spec. |
| `modules/remote-source` | Content-addressed `git+https://` fetcher (Layer A), used by `loop-pipeline[remote]` to materialize remote `.dot` graphs. |
| `modules/loop-agent` | The `coding-agent-loop` nlspec implementation — a general worker (registerable in the worker registry), not attractor-specific. |
| `modules/loop-amplifier-agent` | OPT-IN adapter orchestrator: hosts [microsoft/amplifier-agent](https://github.com/microsoft/amplifier-agent)'s `Engine` as a pipeline node's worker via `session.spawn`. Heavy, optional peer dependency (`amplifier_agent_lib`, Python >=3.12) -- installed via the root package's `[agent]` extra. See "Default worker" below. |
| `modules/hooks-pipeline-observability` | State aggregator, status bar, and event persistence hooks for pipeline runs. |
| `modules/hooks-pipeline-progress` | Progress display hook. |
| `modules/hooks-tool-truncation` | Tool-output truncation hook for context management. |
| `modules/tool-apply-patch` | v4a unified-diff patch-apply tool module. |
| `modules/tool-dashboard-query` | Dashboard HTTP query tool module. |
| `modules/tool-pipeline-status` | Pipeline execution state query tool module. |
| `specs/canonical/` | Byte-pinned vendored copies of the upstream `strongdm/attractor`, `coding-agent-loop`, and `unified-llm` nlspecs. |
| `specs/EXTENSIONS.md` | Append-only ledger of every place this implementation extends or deviates from the canonical specs. |
| `specs/conformance/attractor-matrix.yaml` | The conformance matrix: one row per normative statement cluster, each runner-verified against canonical spec bytes. |
| `SPEC_CONFORMANCE.md` | Compat doctrine + deviation ledger, human-readable. |

Python distribution and import names are unchanged from their original
home (`amplifier-module-loop-pipeline`, `import amplifier_module_loop_pipeline`,
etc.) — only the git URL that serves them moved. The one exception: the
legacy `attractor` console script has been removed entirely (no alias, no
shim) -- `dot-runner` is the only CLI this repo ships.

## This is a proper bundle

Beyond the pip-installable modules above, this repo is itself a mountable
Amplifier bundle (namespace `dot-runner`) — a *mechanism*, not a policy
layer. It ships no provider, no agents, no examples, and no runnable
standalone composition; compose it into an opinionated bundle that supplies
those.

```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-dot-runner@main
```

## Who consumes this

`amplifier-bundle-attractor` — the opinionated, spec-pure Attractor layer
(examples, agents, docs, the `attractor-core` behavior) — depends on this
repo for the engine. It is **not** part of this repo, and this repo has no
dependency back on it (the arrow is one-directional: `attractor -> runner`).
Other repos (a recipes layer, and others) are expected to mount the same
engine for different, non-convergence-loop orchestration styles.

That anchoring holds regardless of shape: a recipes-layer or other
non-attractor consumer is bound to the same strongdm/attractor nlspec as
this repo's own convergence-loop use, not a relaxed derivative of it. See
the Compatibility doctrine in `SPEC_CONFORMANCE.md` (rule 5, "Anchoring
survives scope") for how that bar is enforced.

If you're looking for `.dot` examples, authoring guides, agents, or the
attractor-specific vision/contracts docs, see
[`amplifier-bundle-attractor`](https://github.com/microsoft/amplifier-bundle-attractor).

## Spec fidelity

This is a **repackaging, not a rewrite**. Engine behavior is byte-identical
to its pre-extraction form where testable:

- `specs/canonical/attractor-spec-canonical.md` — the byte-pinned
  `strongdm/attractor` snapshot this engine implements.
- `specs/conformance/attractor-matrix.yaml` — 38 rows, each citing a
  verbatim spec quote and an in-process engine probe against it. All 38
  rows must pass, unmodified, in CI.
- `specs/EXTENSIONS.md` — every intentional extension beyond the canonical
  spec, numbered and cross-referenced from the matrix and from
  `SPEC_CONFORMANCE.md`.

## History

This repo was extracted from `microsoft/amplifier-bundle-attractor` via
`git filter-repo` (subtree extraction preserving commit history for the
moved paths). See `HISTORY-MAP.tsv` for the old-SHA -> new-SHA mapping —
useful for resolving any pre-extraction commit citation (council
transcripts, `SPEC_CONFORMANCE.md` entries, code review comments) to its
corresponding commit here.

## Getting started

**The `attractor` command is gone.** If you landed here from old docs or a
bookmark expecting `attractor run ...`: that console script has been removed
entirely (band-aid rip, no alias, no shim, no deprecation window). This
repo now ships exactly one CLI, `dot-runner`. Worker NAMES are the whole
user-facing concept for an opinionated (agent-hosted) experience -- see
`--worker` below; `--bundle` is not part of this CLI's surface.

### Pattern (a) — root one-liner (recommended)

```bash
uv tool install git+https://github.com/microsoft/amplifier-bundle-dot-runner
dot-runner --help
dot-runner lint path/to/pipeline.dot
```

This installs from the repo root (distribution `amplifier-dot-runner`) and
lands exactly one executable: `dot-runner`.

### Pattern (b) — pip-install a module directly (git + subdirectory, pinned)

```bash
uv tool install "git+https://github.com/microsoft/amplifier-bundle-dot-runner@main#subdirectory=modules/pipeline-runner"
dot-runner --help
```

Equivalent to pattern (a) -- useful when you want to pin to the
`pipeline-runner` module specifically (e.g. an existing subdirectory-pinned
install). Both forms install the SAME single console script, `dot-runner`.

### One CLI -- default worker: amplifier-agent (the bet), `direct` (the fallback)

`dot-runner` accepts `run` / `resume` / `doctor` / `trace` / `lint`. The
engine itself stays bare and mechanism-only (no built-in knowledge of any
pattern repo), but the **CLI** now has a maintainer-decided default-worker
policy: amplifier-agent is the team's bet for new dot-runner surfaces.

`run`/`resume` also accept `--worker <name>` (EXTENSIONS.md §40 — feeds the
worker registry's run-level `default_worker`; a node's own `worker=`
attribute still wins), one of `direct`, `loop-agent`, `amplifier-agent`.
**An explicit `--worker` always wins** over everything below. There is no
`--bundle` flag or `DOT_RUNNER_BUNDLE` env var on this CLI -- any bundle
machinery a named worker needs is internal, private implementation detail.

**The resolution ladder, when no explicit choice is made:**

1. **Probe.** `amplifier_module_pipeline_runner.default_worker` checks --
   cheaply, via `importlib.util.find_spec` (no import, no network) -- that
   BOTH the in-repo `loop-amplifier-agent` adapter (`modules/loop-amplifier-
   agent`) AND its heavy peer library (`amplifier_agent_lib`,
   [microsoft/amplifier-agent](https://github.com/microsoft/amplifier-agent))
   are installed.
2. **Available -> amplifier-agent.** The CLI synthesizes a minimal bundle
   internally (one agent entry backed by `loop-amplifier-agent`, `worker:
   spawn`, a `profiles` map routing every known LLM provider to that one
   agent) and wires it under the hood -- purely private implementation
   detail (`amplifier_module_pipeline_runner.default_worker`), never a
   user-facing `--bundle` concept. `--worker loop-agent`/`--worker
   amplifier-agent` use this exact same internal mechanism when chosen
   explicitly, parametrized by which adapter module to wire.
3. **Unavailable -> `direct`, loudly.** One stderr line names the upgrade
   path:

   ```
   dot-runner: no --worker given and amplifier-agent is not installed --
   falling back to worker=direct. Install the agent extra for the default
   amplifier-agent worker: see this repo's README, "The [agent] extra" section,
   for the two-step install that enables it today (a single `uv tool install
   "amplifier-dot-runner[agent]"` can hit a known uv dependency-resolution
   collision)
   ```

   Review finding, fixed here: the notice used to name the single-command
   `uv tool install "amplifier-dot-runner[agent]"` install directly -- but
   that is exactly the command the known `uv` collision below breaks. A
   notice that teaches a broken command is worse than no notice at all, so
   it now points at the two-step install that is proven to work instead.

   Every box node then runs through the worker registry's `direct` worker
   (unified-llm-client + a provider key) -- unchanged, bare-engine behavior.

```bash
# amplifier-agent installed (see "The [agent] extra" below): every box node
# is hosted by microsoft/amplifier-agent's Engine, no flags needed
dot-runner run path/to/pipeline.dot

# amplifier-agent NOT installed: falls back to `direct`, with the notice above
dot-runner run path/to/pipeline.dot

# explicit choice always wins
dot-runner run path/to/pipeline.dot --worker direct
dot-runner run path/to/pipeline.dot --worker loop-agent
dot-runner run path/to/pipeline.dot --worker amplifier-agent
```

An unknown `--worker` name is refused with a clean error naming every
registered worker — never a stack trace.

**Disclosure:** the hosted amplifier-agent worker does not (yet) receive a
pipeline node's own declared tools -- `loop-amplifier-agent` boots
amplifier-agent's own self-contained bundle (its own tool roster), and
tools-passthrough from a `.dot` node's config to that hosted agent has not
been built (an upstream, public-seam ask -- tracked, not silently dropped).
If a node's prompt assumes a custom pipeline-declared tool will be
available to the LLM, it will not reach amplifier-agent's Engine this way.

### The `[agent]` extra

Root install (`amplifier-dot-runner`) ships the thin engine only --
amplifier-agent (`amplifier_agent_lib` and its own heavy dependency tree:
a web-framework/ASGI-server stack, MCP client libs, etc., Python >=3.12) is
never a forced dependency.

**Known limitation, disclosed honestly, and why there is no single-command
install:** amplifier-agent is deliberately **not** declared as a formal
`[project.optional-dependencies]` extra in this repo's root `pyproject.toml`
(so `uv tool install "amplifier-dot-runner[agent]"` is not a thing you can
run -- there is no `[agent]` extra for `uv`/`pip` to resolve). This is a
review finding, not an oversight: a real, pre-existing cross-repo
declaration-style mismatch means `uv` reports *"Requirements contain
conflicting URLs for package `amplifier-foundation`"* the moment anything
asks it to co-resolve these two dependency graphs in one solve --
`modules/pipeline-runner` declares `amplifier-foundation` as a direct
`pkg @ git+https://...@main` URL (deliberately, to avoid a different,
already-encountered collision -- see that file's own comment on issue
#213), while `microsoft/amplifier-agent`'s own `pyproject.toml` declares
the same package as a plain named dependency redirected via its own
`[tool.uv.sources]` entry. Both resolve the identical repo/branch, but `uv`
treats the two declaration shapes as structurally different requirements
for the same package and refuses to unify them. Declaring `[agent]` as a
formal extra earlier in this repo's history did not just make the
single-command install fail -- it broke `uv lock`/`uv sync`/`uv run` for
the **whole root project**, for every consumer, because `uv`'s universal
lock resolution must still prove the "extra requested" split resolvable
even when nobody asked for the extra (this repo's own "Validate Bundle
Repo" CI check, which never installs `[agent]`, failed on exactly that).
Removing the formal extra fixes that for everyone; it changes nothing
about the runtime feature, since the default-worker probe
(`amplifier_agent_available()`) only checks what is actually installed
(`importlib.util.find_spec`), never how it got there. Fixing the
collision for real requires either upstream's declaration style to change
(a different repo) or a change to `modules/pipeline-runner`'s own,
deliberately-tuned dependency declaration whose interaction with its
existing CI-determinism pin (`[tool.uv] override-dependencies`, same file)
we could not safely re-verify in this pass -- so it was left untouched
rather than risk a silent regression there.

**Install path that works today:** install the base package, then add the
two peer components as a second, separate `uv pip install` into the same
environment (a separate resolve, so it never hits the one-solve collision
above):

```bash
uv tool install "amplifier-dot-runner"
uv pip install --python "$(uv tool dir)/amplifier-dot-runner/bin/python" \
  "amplifier-module-loop-amplifier-agent @ git+https://github.com/microsoft/amplifier-bundle-dot-runner@main#subdirectory=modules/loop-amplifier-agent" \
  "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@main"
```

Without doing this, `dot-runner` still works standalone (falls back to
`direct` with the one-line notice above) -- this two-step install only
changes what the DEFAULT worker resolves to, never whether the CLI runs at
all. Once both packages are present in the environment (however they got
there), the default-worker probe resolves `True` and every subsequent
`dot-runner run`/`resume` with no explicit `--worker` wires amplifier-agent
automatically -- verified end-to-end.

### Pattern (c) — mount as an Amplifier bundle

```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-dot-runner@main
```

This registers the `dot-runner` namespace (root `bundle.md`). Wire the
`loop-pipeline` module as your session's `session.orchestrator` (with a
DOT graph via `dot_file`/`dot_source`) and supply a provider — this bundle
deliberately supplies neither.

For local development, each module is its own uv-managed package:

```bash
cd modules/loop-pipeline && uv sync --extra remote && uv run pytest -q
cd modules/pipeline-runner && uv sync && uv run pytest -q
cd modules/unified-llm-client && uv sync && uv run pytest -q
cd modules/remote-source && uv sync && uv run pytest -q
cd modules/loop-agent && uv sync && uv run pytest -q
cd modules/hooks-pipeline-observability && uv sync && uv run pytest -q
cd modules/hooks-pipeline-progress && uv sync && uv run pytest -q
cd modules/hooks-tool-truncation && uv sync && uv run pytest -q
cd modules/tool-apply-patch && uv sync && uv run pytest -q
cd modules/tool-dashboard-query && uv sync && uv run pytest -q
cd modules/tool-pipeline-status && uv sync && uv run pytest -q
```

## Contributing

This project welcomes contributions and suggestions. Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit https://cla.opensource.microsoft.com.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.

## License

MIT — see `LICENSE`.
