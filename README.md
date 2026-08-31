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
  worker-selection story is `--worker llm-direct|coding-agent|amplifier-agent`
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
| `modules/loop-agent` | The `coding-agent-loop` nlspec implementation — a general worker (registerable in the worker registry as `coding-agent`), not attractor-specific. The module directory keeps its historical name; the user-facing worker name is `coding-agent` (renamed from `loop-agent`, WAVE 7). |
| `modules/loop-amplifier-agent` | Adapter orchestrator: hosts [microsoft/amplifier-agent](https://github.com/microsoft/amplifier-agent)'s `Engine` as a pipeline node's worker via `session.spawn`. ALWAYS INSTALLED (WAVE 6): a real, unconditional dependency of the root package, along with its heavy peer library (`amplifier_agent_lib`, Python >=3.12). See "Default worker" below. |
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
| `.github/capsule-pipeline/` | Issue -> attractor -> PR pipeline (ported from `amplifier-bundle-attractor`): label an issue `ready:spec`/`ready:feature-spec` and an autonomous pipeline proposes a work capsule, then (on merge) a fix PR. See `.github/capsule-pipeline/README.md` and [docs/ISSUE_PIPELINE.md](docs/ISSUE_PIPELINE.md). |

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

## Breaking changes (0.3.0)

Three demoted extensions are DELETED as of 0.3.0 -- mechanism removed, not merely
discouraged. Each has a spec-intended replacement using canonical vocabulary only;
see `MIGRATION.md` for before/after `.dot` snippets:

- `runs_on=` / `continue_on_fail=` (EXTENSIONS.md Sec16) -- use an explicit
  `condition="outcome=fail"` edge.
- `requires=` / `outputs=` (EXTENSIONS.md Sec17) -- use `condition=context.<key>`
  and/or a `shape=tool` file-existence probe.
- `feedback_from=` (EXTENSIONS.md Sec29) -- use file-mediated feedback (critique
  writes `.ai/feedback/*.md`; the generator's own prompt reads it back).

`attractor lint` reports **ATTR-LINT-001** (ERROR) for one release when a graph
still declares any of these five attributes, naming the migration pattern.

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

### One CLI -- default worker: amplifier-agent (unconditional), `direct` (broken-env fallback only)

`dot-runner` accepts `run` / `resume` / `doctor` / `trace` / `lint`. The
engine itself stays bare and mechanism-only (no built-in knowledge of any
pattern repo), but the **CLI** now has a maintainer-decided default-worker
policy: amplifier-agent is embedded in the root install and is the
unconditional default worker for new dot-runner surfaces (WAVE 6,
feat/agent-always-installed: "I thought we were embedding amplifier-agent
engine/lib, so it's always installed? ... YES, that's the intent.").

`run`/`resume` also accept `--worker <name>` (EXTENSIONS.md §40 — feeds the
worker registry's run-level `default_worker`; a node's own `worker=`
attribute still wins), one of `llm-direct` (bare loop, unified-llm-spec),
`coding-agent` (implements the coding-agent-loop spec), `amplifier-agent`.
`direct`/`loop-agent` are RETIRED names -- no alias; using one fails loud
naming its replacement.
**An explicit `--worker` always wins** over everything below. There is no
`--bundle` flag or `DOT_RUNNER_BUNDLE` env var on this CLI -- any bundle
machinery a named worker needs is internal, private implementation detail.

**The resolution ladder, when no explicit choice is made:**

```
explicit --worker / node worker= > amplifier-agent, PERIOD.
```

There is no third rung and no "is it installed" question: `uv tool install
git+https://github.com/microsoft/amplifier-bundle-dot-runner` (root, no
`#subdirectory`) always pulls in the in-repo `loop-amplifier-agent` adapter
AND its heavy peer library (`amplifier_agent_lib`,
[microsoft/amplifier-agent](https://github.com/microsoft/amplifier-agent))
-- see root `pyproject.toml`'s `dependencies` and the "Dependency conflict
fix" section below for how the historical `uv` collision that used to make
this optional is resolved.

`amplifier_module_pipeline_runner.default_worker` still runs a cheap
`importlib.util.find_spec` check (no import, no network) before wiring the
synthesized bundle -- but it is now a RUNTIME IMPORT GUARD, not an
availability probe with an "install this to unlock the feature" story. In a
healthy always-installed environment it is always `True` and never fires.
If it is ever `False`, that is an ABNORMAL, broken-environment state (a
stale cache, a partial install, a hand-edited venv) -- `dot-runner`
degrades to `direct` but prints ONE loud stderr line diagnosing the broken
install and naming the reinstall command:

```
dot-runner: amplifier-agent ships as an unconditional dependency of this install
ships as an unconditional dependency of this install and could not be imported
-- this environment is broken (stale cache, partial install, or a hand-edited
venv); reinstall: `uv tool install --reinstall
git+https://github.com/microsoft/amplifier-bundle-dot-runner` (or, from the
repo tree, `cd modules/pipeline-runner && uv sync --reinstall`)
```

Every box node then runs through the worker registry's `direct` worker
(unified-llm-client + a provider key) -- unchanged, bare-engine behavior --
same as before, just a different diagnostic story for why.

```bash
# amplifier-agent is always installed at the root: every box node is hosted
# by microsoft/amplifier-agent's Engine, no flags needed
dot-runner run path/to/pipeline.dot

# explicit choice always wins
dot-runner run path/to/pipeline.dot --worker llm-direct
dot-runner run path/to/pipeline.dot --worker coding-agent
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

### Library seam: `run_pipeline()` / `resume_pipeline()` get the SAME default-worker story (fix/library-seam-default-worker)

`modules/pipeline-runner` is also a reusable library -- e.g.
`from amplifier_module_pipeline_runner import run_pipeline`
(microsoft/amplifier-app-wiki-weaver consumes it exactly this way). Calling
it bare (no `bundle=`, no `worker=`) now applies the **exact same**
default-worker ladder the CLI applies: amplifier-agent, unconditionally,
via the same internal synthesis (`default_worker.py`) -- fail loud, never a
silent degrade, if that install is broken.

**Incident this closes (2026-08-30, proven live in a DTU):** before this
fix, a bare `run_pipeline()` call hardcoded `worker="llm-direct"` whenever
the caller made no explicit choice. A spawn-path DOT graph (one whose box
nodes expect a real tool-calling agent) then ran silently on the
TEXT-ONLY `llm-direct` worker: the model emitted tool calls as prose,
nothing executed, and a step burned its whole retry budget (137
iterations / 16 minutes of paid LLM calls) before failing to converge. The
CLI never had this bug -- `cli.py` always resolves the default worker
before calling `run_pipeline`. Now both seams share the identical ladder.

```python
from amplifier_module_pipeline_runner import run_pipeline

# Bare call: resolves to amplifier-agent (spawn + real tool loop),
# exactly like `dot-runner run pipeline.dot` with no --worker flag.
# Fails loud (raises `default_worker.WorkerResolutionError`) if that
# install is broken -- never silently falls back to a text-only worker.
result = await run_pipeline(dot_source)

# Explicit choice always wins -- same three names as `--worker`, and the
# bare, text-only loop remains fully available, deliberately, by asking
# for it:
result = await run_pipeline(dot_source, worker="llm-direct")
result = await run_pipeline(dot_source, worker="coding-agent")
result = await run_pipeline(dot_source, worker="amplifier-agent")
```

`worker=` mirrors `--worker` exactly: an unrecognized name fails loud
downstream (the engine's own worker registry, naming every registered
worker); a retired name (`direct`/`loop-agent`) fails loud immediately with
a migration hint. `worker=` and `bundle=` (the pre-existing mechanism for
loading an explicit, opinionated bundle -- e.g. attractor's own) are
**mutually exclusive** -- passing both raises `ValueError` immediately. An
explicit `bundle=` caller's behavior is completely unchanged by this fix.

The ONE difference from the CLI: a fail-loud case on this library seam
raises `default_worker.WorkerResolutionError` (a normal, catchable
exception) rather than calling `sys.exit(1)` -- a library caller's host
process must never be killed out from under it.

### Subscription providers: `github-copilot` / `openai-chatgpt` (spawn workers only)

`llm_provider="github-copilot"` and `llm_provider="openai-chatgpt"` on a box
node work with the `coding-agent`/`amplifier-agent` spawn workers -- run
pipelines on subscriptions you already have, no Anthropic/OpenAI/Gemini API
key needed (idea-transfer from external PR
`microsoft/amplifier-bundle-attractor#322`; EXTENSIONS.md §44).

```bash
# github-copilot: set a GitHub token that carries intent (any one of these)
export COPILOT_AGENT_TOKEN=...      # or COPILOT_GITHUB_TOKEN
dot-runner run pipeline.dot --worker coding-agent

# openai-chatgpt: authenticate once via the provider module's own device-code
# flow, which writes ~/.amplifier/openai-chatgpt-oauth.json
amplifier provider login openai-chatgpt
dot-runner run pipeline.dot --worker amplifier-agent
```

**Detection.** `github-copilot` is configured iff `COPILOT_AGENT_TOKEN` or
`COPILOT_GITHUB_TOKEN` is set (these carry intent by their very name), OR the
generic `GH_TOKEN`/`GITHUB_TOKEN` is set **and** some node in the pipeline
explicitly declares `llm_provider="github-copilot"` (the INTENT RULE --
GitHub Actions injects `GITHUB_TOKEN` into every job, so its bare presence
must never silently auto-mount copilot into an ordinary CI lane).
`openai-chatgpt` is configured iff its OAuth token cache exists and is
non-empty at `~/.amplifier/openai-chatgpt-oauth.json` -- no equivalent
ambiguity, since a human had to deliberately run the login flow to create it.

**`llm-direct` cannot serve either** -- it is the pure unified-llm-spec
client (SDK-direct anthropic/openai/gemini only, by maintainer ruling). A
node declaring one of these two providers under `--worker llm-direct` fails
loud, naming the fix: add `--worker coding-agent`/`--worker amplifier-agent`,
or change `llm_provider`.

**Model selection.** Both providers proxy multiple model families through
one mounted adapter, so `llm_model` family tokens/globs (e.g. `sonnet`)
cannot be live-resolved for them the way they can for anthropic/openai/gemini
-- set an explicit concrete `llm_model` (e.g. `llm_model="claude-sonnet-4.6"`
for github-copilot, `llm_model="gpt-5.5"` for openai-chatgpt), or omit it
entirely and let the mounted provider module apply its own configured
default (`github-copilot` defaults to `claude-opus-4.5`; `openai-chatgpt`
resolves `"latest"` dynamically).

See [`amplifier-module-provider-github-copilot`](https://github.com/microsoft/amplifier-module-provider-github-copilot)
and [`amplifier-module-provider-openai-chatgpt`](https://github.com/microsoft/amplifier-module-provider-openai-chatgpt)
for full auth setup instructions per provider.

### Dependency conflict fix: amplifier-foundation SHAPE mismatch (resolved)

Root install (`amplifier-dot-runner`) now declares
`amplifier-module-loop-amplifier-agent` as a real, unconditional
`[project.dependencies]` entry (WAVE 6) -- `amplifier-agent`
(`amplifier_agent_lib` and its own heavy dependency tree: a web-framework/
ASGI-server stack, MCP client libs, etc., Python >=3.12) is pulled in every
time, no extra install step, no probe.

**The historical collision, and how it's fixed:** this used to be
optional-with-probe for exactly one reason: a real, disclosed cross-repo
dependency-declaration mismatch made `uv` report *"Requirements contain
conflicting URLs for package `amplifier-foundation`"* the moment anything
asked it to co-resolve `modules/pipeline-runner`'s dependency graph with
`microsoft/amplifier-agent`'s in one solve -- `modules/pipeline-runner`
declared `amplifier-foundation` as a direct `pkg @ git+https://...@main`
URL, while `amplifier-agent`'s own `pyproject.toml` declares the same
package as a plain named dependency redirected via its own
`[tool.uv.sources]` entry (`{ git = ..., branch = "main" }`). Both resolve
the identical repo/branch, but `uv` identifies a git requirement by its
ref-KIND representation, not just the commit it resolves to, and treats a
`@main`-suffixed direct URL and a `branch = "main"` sources-redirect as
different requirements for the same package -- confirmed empirically (`uv
tool install`/`uv pip install` still raised the conflict with both printed
as identical-looking `@main` text).

Two fixes landed together, because they close different halves of the gap:

1. **Root `[tool.uv] override-dependencies`** (root `pyproject.toml`) pins
   ONE canonical `amplifier-foundation` requirement line for the whole
   root resolution -- the exact mechanism (and scope guarantee: applies
   only when this project is resolved as root, never when it is someone
   else's dependency) `modules/pipeline-runner`'s own CI-determinism pin
   already uses. This makes `uv lock`/`uv sync` green when the root
   project itself is the resolution root.
2. **`modules/pipeline-runner`'s own `amplifier-foundation` dependency now
   matches amplifier-agent's declaration SHAPE** (named requirement +
   `[tool.uv.sources]` `branch = "main"` redirect, not a direct URL) --
   because `override-dependencies` does NOT reach `uv tool install
   git+<url>` (verified: it is resolution-root-scoped only, and a git/tool
   install never treats the fetched project as that root), but a git
   dependency's own `[tool.uv.sources]` table IS read transitively by `uv`
   even when it is someone else's dependency. Matching the shape is what
   makes `uv tool install git+https://github.com/microsoft/amplifier-bundle-dot-runner`
   -- the primary distribution path -- actually work. The dependency still
   floats on the `main` branch (unchanged ref, unchanged CI-determinism
   override, unchanged issue #213 floating-requirement intent); only the
   declaration shape changed.

Neither fix touches `microsoft/amplifier-agent` (a separate repo), and
neither regresses `modules/pipeline-runner`'s own CI-determinism pin (still
fires only when pipeline-runner itself is the resolution root) or its
standalone installability (`#subdirectory=modules/pipeline-runner` still
works, still floats on `@main`, still co-installable with the wider
ecosystem's own `@main`-declaring consumers when resolved on its own).

**Proof:** `uv lock`/`uv sync` green at the repo root (61 packages,
including amplifier-agent + its full dependency tree, with
`amplifier-foundation` resolving to a single git source) and standalone in
`modules/pipeline-runner` and `modules/loop-amplifier-agent`; a scratch-env
`uv tool install git+file://<this repo>` (and an equivalent local-path
install) both succeed end-to-end and land a working `dot-runner`.

### The library-seam / module-dist coupling: which install a bare `run_pipeline()` needs (fix/library-seam-default-worker)

The library-seam fix above (previous section) makes a bare `run_pipeline()`
resolve to `amplifier-agent`, unconditionally -- the SAME ladder the CLI
applies. That guarantee is only as good as the *installed distribution*
actually shipping `amplifier-agent` + the `loop-amplifier-agent` adapter.
Two distributions exist (pattern (a) vs (b) above), and **only the root one
does**:

* `amplifier-dot-runner` (root, no `#subdirectory` -- pattern (a)): declares
  `amplifier-module-loop-amplifier-agent` as an unconditional
  `[project.dependencies]` entry (WAVE 6) -- see the "Dependency conflict
  fix" section above. `amplifier-agent` is always present.
* `amplifier-module-pipeline-runner` (the module dist,
  `#subdirectory=modules/pipeline-runner` -- pattern (b)'s underlying
  package name, and what a `pip`/`uv add` **library** consumer like
  microsoft/amplifier-app-wiki-weaver names in its own `dependencies`) does
  **NOT** depend on `amplifier-agent` or the adapter at all -- see this
  module's own `pyproject.toml`. A bare `run_pipeline()` call against
  *this* install alone finds no adapter installed and raises
  `default_worker.WorkerResolutionError` on every call: fail-loud is
  honored (never a silent `llm-direct` degrade), but the library is
  unusable zero-config out of the box.

**Resolution (decided here, evidence below):** do **NOT** add
`amplifier-module-loop-amplifier-agent` as an unconditional dependency of
the `modules/pipeline-runner` module dist itself. A library consumer that
wants the zero-config, same-as-CLI default-worker experience should depend
on the root **`amplifier-dot-runner`** distribution instead (it already
depends on, and re-exports, `amplifier_module_pipeline_runner` -- see
`amplifier_dot_runner/__init__.py` -- so `from
amplifier_module_pipeline_runner import run_pipeline` still works
unchanged). A library consumer that wants to stay on the lean module dist
keeps that option too, by always passing `worker=`/`bundle=` explicitly
(the ladder is a no-op the moment any explicit choice is made).

**Why not add the adapter to the module dist directly (the other
candidate):** measured, not assumed -- `amplifier-module-loop-amplifier-agent`
declares `requires-python = ">=3.12"` (it wraps `amplifier-agent`, which
requires the same floor). `modules/pipeline-runner/pyproject.toml`
deliberately keeps a `>=3.11` floor (WAVE 6 explicitly preserved this "for
modules/pipeline-runner ... unaffected"). Adding the adapter as an
unconditional dependency there forces `requires-python >=3.12` onto
**every** module-dist consumer -- proven with a scratch `uv lock` against a
module `pyproject.toml` carrying that added dependency:

```
No solution found when resolving dependencies for split ...:
  Because the requested Python version (>=3.11) does not satisfy
  Python>=3.12 and amplifier-module-loop-amplifier-agent==0.1.0
  depends on Python>=3.12, ... your project's requirements are
  unsatisfiable.
```

That is a strictly worse blast radius than the root dist's own choice: the
root dist bumped ITS OWN floor to `>=3.12` (it exists *only* to ship the
batteries-included console-script experience), while the module dist is
also legitimately consumed by callers who explicitly pick `worker=` /
`bundle=` and never touch `amplifier-agent` at all -- forcing them onto
`>=3.12` and the full agent dependency tree for a feature they don't use
would be the wrong trade. Scoping the floor-bump + heavier install to the
root dist (opt-in, by depending on it) rather than the module dist
(unconditional, no opt-out) is the more surgical fix. (Note: this floor
bump is NOT unique to whichever option was picked here -- ANY consumer that
wants amplifier-agent as its default, module dist or root dist, ends up
needing Python >=3.12 one way or another. The question was only ever which
distribution's consumers should be forced to pay it.)

microsoft/amplifier-app-wiki-weaver's own fix (branch
`fix/engine-020-compat`) implements the consumer side of this: its engine
dependency now points at the root `amplifier-dot-runner` distribution (not
the module subdirectory), and its own `requires-python` floor moved to
`>=3.12` to match -- see that repo's PR for the install proof.

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
