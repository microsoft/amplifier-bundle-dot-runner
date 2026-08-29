---
bundle:
  name: dot-runner
  version: 0.1.0
  description: >
    The .dot pipeline engine as a composable Amplifier bundle: the loop-pipeline
    orchestrator. A mechanism, not a policy layer — compose it into an opinionated
    bundle that supplies a provider, context, tools, and a DOT graph. WAVE 5 repair
    (2026-08-30): the report_outcome tool and its dot-runner-core behavior partial
    are removed -- status.json (spec Sec 4.5 / Appendix C) is the taught, spec-native
    verdict channel (specs/EXTENSIONS.md Sec 35/Sec 41).
---
You are running the .dot pipeline engine. Provide a DOT graph via the orchestrator config (dot_file or dot_source) and a provider before running a pipeline.
This engine is anchored to the vendored strongdm/attractor nlspec for all uses; see `SPEC_CONFORMANCE.md`.
