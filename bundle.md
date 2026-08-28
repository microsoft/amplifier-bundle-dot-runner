---
bundle:
  name: dot-runner
  version: 0.1.0
  description: >
    The .dot pipeline engine as a composable Amplifier bundle: the loop-pipeline
    orchestrator plus the report_outcome tool. A mechanism, not a policy layer —
    compose it into an opinionated bundle that supplies a provider, context, tools,
    and a DOT graph.
includes:
  - bundle: dot-runner:behaviors/dot-runner
---
You are running the .dot pipeline engine. Provide a DOT graph via the orchestrator config (dot_file or dot_source) and a provider before running a pipeline.
This engine is anchored to the vendored strongdm/attractor nlspec for all uses; see `SPEC_CONFORMANCE.md`.
