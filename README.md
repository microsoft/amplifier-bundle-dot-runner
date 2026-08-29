# amplifier-bundle-dot-runner

The engine that runs `.dot` pipelines: a DOT-graph-driven multi-stage AI
workflow orchestrator, plus the CLI and provider client it depends on —
packaged as a proper, composable **Amplifier bundle**.

This repo implements the vendored **strongdm/attractor** nlspec faithfully
and is verified against it mechanically (see "Spec fidelity" below). It is
**not** an opinionated pipeline layer, a pattern library, or an authoring
guide — it is the mechanism. Policy (agents, providers, examples, authoring
docs) lives in the repos that consume this one.

## What's here

| Path | What it is |
|---|---|
| `bundle.md` | Root bundle (`dot-runner`) — includes the `dot-runner-core` behavior. |
| `behaviors/dot-runner.yaml` | Engine partial (`dot-runner-core`) — mounts the `report_outcome` tool. |
| `modules/loop-pipeline` | **The engine.** DOT parser, validator, graph execution engine, handler dispatch. |
| `modules/pipeline-runner` | The `attractor` CLI (`run` / `resume` / `doctor` / `trace` / `lint`) and the `drive_engine` / `run_pipeline` library surface. |
| `modules/unified-llm-client` | Provider-agnostic LLM client — a faithful implementation of the Attractor Unified LLM Client spec. |
| `modules/remote-source` | Content-addressed `git+https://` fetcher (Layer A), used by `loop-pipeline[remote]` to materialize remote `.dot` graphs. |
| `modules/tool-report-outcome` | The `report_outcome` tool module — lets a child agent set a structured pipeline verdict. |
| `modules/loop-agent` | The `coding-agent-loop` nlspec implementation — a general worker (registerable in the worker registry), not attractor-specific. |
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
the `attractor` console script, etc.) — only the git URL that serves them
moved.

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

### Pattern (a) — pip-install a module directly (git + subdirectory)

```bash
uv tool install "git+https://github.com/microsoft/amplifier-bundle-dot-runner@main#subdirectory=modules/pipeline-runner"
attractor --help
attractor lint path/to/pipeline.dot
```

### Pattern (b) — mount as an Amplifier bundle

```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-dot-runner@main
```

This registers the `dot-runner` namespace (root `bundle.md`) and its
`dot-runner-core` behavior (the `report_outcome` tool). Wire the
`loop-pipeline` module as your session's `session.orchestrator` (with a
DOT graph via `dot_file`/`dot_source`) and supply a provider — this bundle
deliberately supplies neither.

For local development, each module is its own uv-managed package:

```bash
cd modules/loop-pipeline && uv sync --extra remote && uv run pytest -q
cd modules/pipeline-runner && uv sync && uv run pytest -q
cd modules/unified-llm-client && uv sync && uv run pytest -q
cd modules/remote-source && uv sync && uv run pytest -q
cd modules/tool-report-outcome && uv sync && uv run pytest -q
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
