---
bundle:
  name: dot-runner
  version: 0.1.0
  description: >
    The .dot pipeline engine as a composable Amplifier bundle: the loop-pipeline
    orchestrator. A mechanism, not a policy layer — compose it into an opinionated
    bundle that supplies a provider, context, tools, and a DOT graph. status.json
    (spec Sec 4.5 / Appendix C) is the taught, spec-native verdict channel
    (specs/EXTENSIONS.md Sec 41); no tool-call-shaped channel is layered on top.
---
You are running the .dot pipeline engine. Provide a DOT graph via the orchestrator config (dot_file or dot_source) and a provider before running a pipeline.
This engine is anchored to the vendored strongdm/attractor nlspec for all uses; see `docs/VISION.md`.
