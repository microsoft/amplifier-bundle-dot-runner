# DESIGN: a `.dot` visualizer for developers

Status: **DRAFT — for owner review.** Decides shape and placement only; no code.
Tracks work item `attractor-v6v`.

## 0. Premise correction, up front

The work item and lane brief both assume the repo already renders images somewhere
("the capsule pipeline's PNG rendering"). **It does not.** Measured on this worktree:

- `git ls-files '*.dot'` → 25 tracked graphs. `find . -name '*.png' -o -name '*.svg'`
  → exactly one file, `bundle.png`, and nothing in this repo generates it (it comes
  from the external `bundle-to-dot` skill; freshness is stamped inside `bundle.dot:13`
  as `source_hash=`).
- The only graphviz invocation anywhere is a **correctness oracle pointed at
  `/dev/null`**: `modules/loop-pipeline/tests/test_dot_render_compliance.py:79-89`
  runs `dot -Tsvg <f> -o /dev/null`, and `.github/workflows/ci.yml:186-215`
  (`dot-render-gate`) does the same over every tracked `.dot`.
- No pyproject in this repo (15 checked) declares graphviz, `pydot`, or the
  `graphviz` package. `_DISPATCH` at
  `modules/pipeline-runner/amplifier_module_pipeline_runner/cli.py:761-767` has five
  keys — `run`, `resume`, `doctor`, `trace`, `lint`. There is no `render`.

So: **image production is greenfield here, and "does it draw?" is already solved
without graphviz.** That asymmetry drives the whole recommendation below.

## 1. Audiences and what they actually lack

**A. Pipeline author, iterating locally** — wants edit→look→fix under a few seconds.
*Has:* `dot-runner lint <file.dot>` — the full rule set (structural `LINT-001..018`,
topological `TOPO-001..010`, command-content `CMD-001..002`, render-compliance
`RENDER-001..002`) at `cli.py:644-735` over `validation.lint()`; plus `dot -Tsvg` by hand.
*Lacks:* **the two are separate artifacts.** Lint prints
`ERROR: [rule][node_id] (from -> to) message` to a terminal (`cli.py:706-729`; no
`--json`, no `--format`); the picture is a separate file with none of that on it. The
author reads a finding about `node_id`, then hunts for that node in an image.

**B. Reviewer reading a capsule/fix PR** — wants to judge a graph change without
checking it out. *Has:* **nothing.** A `.dot` diff in a PR is a text diff;
`dot-render-gate` proves the file *draws* but never produces the drawing. *Lacks:* an
image, and more importantly a *correct* one — see §3, where the edges that matter most
are not in the file at all.

**C. User watching a live run** — wants "where is it now, what failed, how many
iterations in." *Has:* `dot-runner trace <run-dir>` (`cli.py:569-640`), a post-hoc text
table grouped by iteration; and a **phantom** — `modules/tool-dashboard-query` is a
complete HTTP client for `/api/pipelines*` on `:8050` with no server anywhere in this
repo. *Lacks:* anything visual and anything live. The data exists and is good (§2);
nothing consumes it as a picture.

## 2. What a live overlay would read (verified formats)

Run directory, `{logs_root}/` — spec §5.6 plus this engine's additions:

| Artifact | Written at | Shape |
|---|---|---|
| `graph.dot` | `engine.py:1884-1888` | `graph.dot_source` **post-materialization**; written unconditionally when non-empty |
| `pipeline.dot` | `runner.py:1140` | raw input text; **skipped for `git+https://` sources** |
| `trace.jsonl` | `engine.py:1948-1961` | append-only, one record per node completion |
| `<node_id>/status.json` | `engine.py:1893-1946` | flat path (back-compat) |
| `iteration_<N>/<node_id>/status.json` | same | iteration-scoped; survives `loop_restart` |
| `checkpoint.json` | `runner.py:1318`, `checkpoint.py` | schema v2 |
| `manifest.json`, `artifacts/` | `engine.py:1849` | metadata, file-backed artifacts |

A visualizer should prefer **`graph.dot`** — for remote pipelines it is the only one
that exists.

`trace.jsonl` record (`engine.py:1949-1957`):
`{iteration, node_id, status, preferred_label, is_explicit, duration_ms, ts}`.

`status.json` (`engine.py:1905-1927`) adds `outcome`, `suggested_next_ids`,
`context_updates`, `notes`, `failure_reason`, `session_id`, `failed_step`.

`checkpoint.json` v2 (`checkpoint.py:1-33`, `SCHEMA_VERSION = 2`) keeps the six §5.3
fields (`current_node`, `completed_nodes`, `context`, `timestamp`, `node_retries`,
`logs`) and adds `run_state` (`in_flight` | `completed`), `node_outcomes`,
`engine_state`, and `graph` (fingerprint + embedded DOT source).

**Every field a run-state overlay needs is already on disk** — no new engine artifact
or event required, which is what keeps this proposal inside AGENTS.md rule 3.

## 3. Attractor semantics vs plain graphviz

Plain graphviz draws the file. The file is not the pipeline. Three things are true of a
run that a stock `dot -Tsvg` renders **wrongly or not at all**:

**(a) Retry targets are invisible edges.** `retry_target` and `fallback_retry_target`
exist at node level (`graph.py:211-292`) *and* graph level, and spec §3.4 makes them
real control flow: on reaching `Msquare` with an unsatisfied goal gate, the engine
"jump[s] to the `retry_target` of the unsatisfied goal gate node… If that is not set,
try `fallback_retry_target`… then the graph-level" pair. **None of these jumps is an
edge in the DOT.** A rendered graph of a corrective pipeline shows a clean DAG where a
cycle actually exists. This is the single highest-value annotation.

**(b) `goal_gate=true` is styled as an ordinary box.** Spec §2.6 defines it as "this
node must reach `SUCCESS` or `PARTIAL_SUCCESS` before the pipeline can exit"; §3.4 is
the enforcement. It is one attribute among twelve promoted node fields and renders
identically to a node that gates nothing.

**(c) Budget walls are unrendered.** `max_retries` and node `timeout` (§2.6), plus
`max_pipeline_duration` (a documented divergence, `specs/EXTENSIONS.md:500-511`:
graph-level wall-clock in ms yielding `failure_reason="max_pipeline_duration_exceeded"`)
determine whether the drawn path is reachable at all.

### Worth encoding

1. **Retry / corrective cycles as synthetic dashed edges**, labelled with their origin
   (`node.retry_target`, `graph.fallback_retry_target`, …). Non-lossy: they are derived
   from attributes already in the file.
2. **Goal gates**, distinctly. Peripheries or fill, plus a legend entry.
3. **Run state**, from `trace.jsonl` + `status.json`: per-node status, current node from
   `checkpoint.json:current_node`, and *which iteration is being shown*. The
   iteration-scoped path exists precisely so the Nth pass is recoverable.
4. **Budget badges** on the label — `max_retries`, `timeout`, graph
   `max_pipeline_duration` — as text, not geometry.
5. **Lint findings pinned to their node/edge.** `Diagnostic` already carries `node_id`
   and `edge` (`validation.py:74-86`); `cli.py:706-729` proves both are populated.

### Not worth encoding

- **Layout.** Graphviz is the layout engine. The spec chose DOT partly for this
  (§1.2: "DOT files can be rendered to SVG/PNG with standard Graphviz tooling, giving
  pipeline authors immediate visual feedback").
- **Prompt bodies on nodes.** They are paragraphs. A detail panel, not a graph label —
  and one already exists (§4, `NodeDetailPanel.tsx`).
- **Pretty-printed condition expressions.** The §10 language lives in `conditions.py`;
  a second renderer of it drifts. Show the edge's literal `condition`/`label`.
- **Resolved `model_stylesheet` assignments.** §8 resolution depends on runtime provider
  config; a stale model badge is worse than none.
- **A second graph model.** Consume `Graph`/`Node`/`Edge` (`graph.py`), not a copy.

## 4. Reuse map — what already exists

**In this repo (must reuse, not re-derive):**
- `parse_dot(source, params) -> Graph` — `dot_parser.py:79`. The only DOT parser.
- `validation.lint(graph) -> list[Diagnostic]` — the structured seam the CLI already
  uses. A visualizer calls the library, never scrapes `dot-runner lint`'s text.
- `SHAPE_TO_HANDLER` — `validation.py:57-68`: `Mdiamond`→start, `Msquare`→exit,
  `box`→codergen, `diamond`→conditional, `hexagon`→wait.human, `component`→parallel,
  `tripleoctagon`→parallel.fan_in, `parallelogram`→tool, `house`→stack.manager_loop,
  `folder`→pipeline. **The shape is the semantics** (§2.8) — the legend is a lookup,
  not a new vocabulary.
- `RENDER-001` / `RENDER-002` (`validation.py:27-33`, `:3263`, `:3342`) — already answer
  "will graphviz accept this?" with the engine's own tokenizer, **WARNING severity, no
  graphviz dependency**. Do not add a second render check.

**`amplifier-bundle-dot-graph`** (`~/.amplifier/cache/amplifier-bundle-dot-graph-43d42df775a679a7/`,
bundle 0.3.1) — the `dot_graph` tool, operations `validate | render | setup | analyze |
prescan | assemble`:
- `render` shells out to the graphviz CLI (`render.py:110-120`), formats
  `svg png pdf json ps eps`, engines `dot neato fdp sfdp twopi circo`, 30 s cap, and
  returns a structured `{success: False, error, install_hint}` when graphviz is absent
  rather than raising. **Use this for images. Do not write a subprocess wrapper.**
- `analyze` is networkx-backed: `stats reachability unreachable cycles paths
  critical_path subgraph_extract diff`. **Do not reimplement cycle detection.**
- Its `unreachable`/`cycles` operations already return an **`annotated_dot`** — the
  source with findings recolored inline. That is prior art for exactly the
  annotate-then-render shape recommended below.
- It ships **no viewer**: zero `.html`/`.js`/`.ts` files in the whole bundle.

**`amplifier-resolver-dot-graph-viewport`** (`~/dev/amplifier-resolver-dot-graph-viewport`)
— **the browser visualizer already exists.** React 19 + `@viz-js/viz@^3.25.0`
(Graphviz-WASM, client-side), pan/zoom/smart-fit, follow-mode that auto-centres the
running node and disengages on user input, click-to-inspect, `NodeDetailPanel`. Two
files are directly transferable:
- `ui/src/utils/sanitizeDot.ts` — strips pipeline-DSL attributes (`prompt`,
  `llm_provider`, `tool_command`, …) that are legal to this parser and **syntax errors
  to stock graphviz**. Mandatory for rendering attractor `.dot` at all.
- `ui/src/utils/svgProcessing.ts` — pure functions: fit, theme, status colouring.

Its live-status contract is small and is the reuse seam:
`DotGraphState = { nodes: Record<id, {status, label}>, active_node, pipeline }`, fed by
`fetchData('graph.dot')`. **Anything that emits that shape drives the existing viewer
unmodified.**

Note: `amplifier-resolver-dot-graph` (no `-viewport`) is a pipeline *resolver*, not a
viewer — easy to conflate by name.

## 5. Shape decision

| Shape | Serves | Cost | Verdict |
|---|---|---|---|
| **1. Library** (annotate `Graph` + run state → DOT) | nobody directly | small; pure function | **Yes — necessary, insufficient alone** |
| **2. CLI image generation** (`dot-runner render` → PNG) | A, B | adds a **runtime** graphviz dep to a repo that today has none | **No, in that form** |
| **3. Browser drop-in service** | C, A | a whole app + a server + a WASM-binding choice | **No — one already exists** |
| **4. Combination** | A, B, C | bounded, if it declines 2 and 3 as stated | **Recommended** |

### Recommendation: **annotate in the engine, render outside it.**

A three-layer combination, with layers 2 and 3 deliberately *not built here*:

1. **`annotate` library, in this repo.** A pure function:
   `annotate(graph, diagnostics=None, run_state=None) -> str` — DOT in, DOT out. It
   materializes retry edges, marks goal gates, badges budgets, pins lint findings, and
   applies run status. It changes no engine behaviour, adds no artifact, emits no event.
2. **`dot-runner render <file.dot> [--run-dir DIR]` → annotated DOT on stdout.**
   Not an image. Zero new dependencies. The pipeline is
   `dot-runner render p.dot | dot -Tsvg -o p.svg`, or hand the DOT to `dot_graph render`
   for png/pdf. Audience A and B are served without this repo ever owning a renderer.
3. **`--state` → `DotGraphState` JSON** from `trace.jsonl` + `checkpoint.json`. Audience
   C is served by driving the **existing** viewport, not a second one.

Why this and not a `render` subcommand that emits PNG: image production is a solved,
externally-owned job (`dot_graph render`, or `dot` itself). What is *not* solved
anywhere, and cannot be solved anywhere else without copying this repo's vocabulary, is
knowing that `retry_target="fix"` is an edge. Put that here; buy the pixels retail.

## 6. Placement

**Here — `amplifier-bundle-dot-runner` — for layers 1 and 2. Nothing new anywhere else.**

The item anticipated "the shared engine or its own consumer, NOT the opinionated
attractor repo." This repo is the shared engine (`README.md:73-80`: "`amplifier-bundle-attractor`
… depends on this repo for the engine… the arrow is one-directional: `attractor -> runner`").

Justification:
- The annotator's inputs are `Graph`, `SHAPE_TO_HANDLER`, `Diagnostic`, and the run-dir
  contract. **All four are owned here.** In a consumer they become a second, drifting
  copy of the engine's vocabulary — the exact failure the split exists to prevent.
- Every consumer benefits. `README.md:73-80` names a prospective recipes layer
  (`contracts/recipe-substrate.v1.md`, **DRAFT/PAUSED**, "governs nothing yet"); a
  visualizer in `attractor` would not reach it.
- It stays additive. Under `docs/VISION.md`'s three-tier arc — aligned / drift /
  uncharted, where uncharted is "a toll, not a wall… what ships there stays additive and
  non-interfering" — visualization is uncharted (the nlspec is silent). This design pays
  the toll by touching no engine behaviour: a new pure module, a sixth `_DISPATCH` key,
  and no runtime dependency. Under AGENTS.md rule 3, "met outside the engine" is
  satisfied literally — the annotator reads the same files any third party could.
- **A `specs/EXTENSIONS.md` entry is not required** as scoped, because nothing here
  changes engine behaviour. It becomes required the moment layer 1 wants a new run-dir
  artifact or a new event. That line should not be crossed without a fresh decision.

Second-order: `docs/designs/` does not currently exist in this repo, though ~20 live
references point into it (`validation.py:33`, `backend.py:15`, `ledger/rows.yaml:32`,
`specs/EXTENSIONS.md:318`, …) — casualties of the split. **This file is the first
occupant of a directory the repo already cites constantly.**

## 7. Build plan

**B1 — `annotate` module (layer 1).** New `modules/loop-pipeline/amplifier_module_loop_pipeline/annotate.py`.
*Acceptance:* Given a `.dot` with `goal_gate=true` and `retry_target=`, When
`annotate(parse_dot(src))` is called, Then the output contains a dashed synthetic edge
from the terminal node to the retry target and the gate node carries a distinguishing
attribute; And the output passes `dot -Tsvg` in the existing `dot-render-gate`; And
`annotate(g) == annotate(g)` for the same input (deterministic ordering).

**B2 — lint-finding overlay.** Accept `list[Diagnostic]`; pin each to its `node_id`/`edge`.
*Acceptance:* Given a graph with ≥1 ERROR and ≥1 WARNING, When annotated with
`lint(graph)`, Then both appear on their respective elements with severity
distinguished; And a `Diagnostic` with neither `node_id` nor `edge` lands in a
graph-level label rather than being dropped silently.

**B3 — run-state overlay.** Read `trace.jsonl`, `<node_id>/status.json`,
`iteration_<N>/`, `checkpoint.json`.
*Acceptance:* Given a completed run directory, When annotated with `--run-dir`, Then
each executed node shows its `status` and the node named by `checkpoint.json:current_node`
is marked current; And a run dir with **no** `trace.jsonl` annotates cleanly with no
status rather than erroring (matching `cmd_trace`'s existing tolerance,
`cli.py:590-597`); And `iteration_<N>` selection is explicit, not "last writer wins".

**B4 — `dot-runner render` subcommand (layer 2).** Sixth `_DISPATCH` key; DOT to stdout.
*Acceptance:* Given any tracked fixture, When `dot-runner render <f> | dot -Tsvg -o /dev/null`
runs, Then exit 0; And `pip show graphviz` remains empty — no new runtime dependency in
any pyproject; And `--run-dir` on a missing directory fails with a message naming the
path, never a traceback.

**B5 — `--state` emitter (layer 3 seam).** `DotGraphState` JSON.
*Acceptance:* Given a run directory, When `dot-runner render --state <run-dir>` runs,
Then stdout parses as `{nodes: {id: {status, label}}, active_node, pipeline}`; And every
`status` value is one of the viewport's six (`pending|running|completed|failed|awaiting_input|skipped`),
with the engine's `StageStatus` values (`success|partial_success|retry|fail|skipped`)
mapped explicitly and the mapping table asserted in a test.

**B6 — CI + docs.** Extend `dot-render-gate` to also render every fixture *after*
annotation; add a README section; register the extension decision if B1–B5 ever grow an
engine-side artifact.
*Acceptance:* Given the CI job, When any fixture's annotated output stops rendering,
Then the job fails naming the file (mirroring the existing `::error file=` emission,
`ci.yml:216`); And the sweep fails vacuously-empty, as the current gate already does
(`ci.yml:226-229`).

## 8. Open questions for the owner

1. **Is the `:8050` dashboard real?** `modules/tool-dashboard-query` is a complete client
   for a server that exists nowhere in this repo. Is it live in `attractor`, dead code,
   or the de-facto target B5 should be aiming at?
2. **Does a `render` subcommand belong in a mechanism repo at all?** B4 is the one item
   that puts a developer-experience surface on the engine CLI. The alternative — library
   only, CLI in a consumer — is cleaner doctrinally and worse ergonomically.
3. **Which WASM binding is canonical?** The viewport uses `@viz-js/viz`; `amplifier-app-resolve`'s
   `FileViewer` uses `@hpcc-js/wasm-graphviz`; the dot-graph bundle's own
   `docs/research/DOT-ECOSYSTEM-RESEARCH.md:715-726` recommends `@hpcc-js`. Nobody has
   reconciled these. B5 does not force the choice, but the next person will.
4. **Should annotated DOT be committed?** A checked-in `*.annotated.dot` makes audience
   B's review diff structural rather than textual, at the cost of a staleness contract
   (the `bundle.dot:13` `source_hash=` pattern is the local precedent).
5. **Was there an earlier visualizer effort?** `~/dev/dot-graph-visualizer/` is an April
   2026 multi-repo lane workspace. Its history was not mined for this design; if it
   contains a prior decision, it should override §5 rather than be re-litigated.
