# worker-parity-kit

An installable **pytest library** (not an Amplifier module -- no
`amplifier.modules` entry point) that pins the dot-pipeline worker seam
with EXECUTED tests, shared across every orchestrator worker that can back
a `.dot` pipeline node.

```
pip install "amplifier-worker-parity-kit @ git+https://github.com/microsoft/amplifier-bundle-dot-runner@main#subdirectory=modules/worker-parity-kit"
```

```python
# a worker's own tests/test_worker_parity.py
from worker_parity_kit.suite import *  # noqa: F401,F403

import pytest


@pytest.fixture
def worker_harness():
    return MyWorkerHarness()
```

## Why this exists

Three real incidents proved prose cannot hold the worker seam:

1. The `loop-amplifier-agent` adapter shipped with **five disclosed**
   capability gaps versus the vendored CLI handler it is modeled on (see
   that module's own README, "Capability gaps vs the vendored CLI handler
   (v2)") -- each judged and closed, each documented honestly.
2. An **undisclosed sixth gap** -- the adapter silently dropped the
   `context` parameter (`Orchestrator.execute()`'s parent-session history,
   seeded via `parent_messages` for `fidelity="full"` continuity) -- was
   found not by the disclosure process but by a teammate's independent
   read of the code. Prose disclosure caught five out of six; it missed the
   one that mattered enough to cause real damage.
3. The same **class** of bug then turned out to live in `loop-agent` too --
   the *default* worker, and support#497's actual production incident. Two
   workers, two completely different internal mechanisms (a self-contained
   hosted Engine vs. an in-process agent loop), the same failure mode: a
   worker can accept `context`, return a perfectly normal-looking reply and
   completion envelope, and still have silently thrown away every seeded
   message. Both are now fixed.

Five disclosed gaps were caught by discipline. The sixth was not. A normative
doctrine document does not stop a worker from silently regressing this
exact behavior six months from now -- only a test that actually runs, in
CI, on every change to either worker, does. That is what this kit is: not a
contract document, but the tests that would have caught all three
incidents, wired into both workers' CIs so this bug class cannot silently
reappear.

**Deliberately not built:** a normative "worker contract" doctrine document,
or new `specs/EXTENSIONS.md` ledger entries. Every MUST test below cites an
EXISTING authority (kernel protocol, attractor spec, EXTENSIONS ledger) in
its own docstring -- it never restates that authority as if this kit were
itself the source of truth. The rule of three: a normative contract document
is deferred until a THIRD worker exists. Until then, "worker parity kit" is
the honest name -- not "contract".

## What's in it

- **`worker_parity_kit.protocol`** -- the `WorkerHarness` Protocol (~5
  members) and `TurnResult`, the consumer seam. A worker implements one
  small harness class; the kit never reaches into worker internals.
- **`worker_parity_kit.doubles`** -- kernel-faithful shared doubles
  (`FakeContextManager` implementing all five `ContextManager` methods,
  `CapturingHooks` recording the `ORCHESTRATOR_COMPLETE` envelope) a new
  worker harness can use directly.
- **`worker_parity_kit.suite`** -- the parametrized test suite:
  - **3 MUSTs**, each citing an existing authority (never restated as new
    doctrine):
    - **M1 Mount shape** -- authority: the kernel's Orchestrator protocol,
      `amplifier_core.interfaces.Orchestrator.execute(prompt, context,
      providers, tools, hooks, **kwargs) -> str`.
    - **M2 Honor seeded `context`** -- authority: attractor spec sec5.4's
      fidelity table (`full`) + EXTENSIONS.md sec12 (`parent_messages`
      node-exchange-granularity continuity).
    - **M3 Never fabricate a verdict** -- authority: EXTENSIONS.md sec25
      (`is_explicit` gate) + sec35 (`report_outcome` transport). Verified
      via the REAL `amplifier_module_loop_pipeline.backend.
      _outcome_from_spawn_result` reader -- never a reimplementation.
  - **TARGET tier** (honored-OR-documented-absent, non-blocking): config
    keys, provider-preference precedence, approvals posture, telemetry
    session-id stamping, child spawn/delegate availability, tools
    passthrough. A worker's harness declares `declared_absences` for
    anything it openly does not honor; the matching test SKIPS, visibly,
    naming the capability. An undeclared capability that fails, FAILS.
- **`worker_parity_kit.broken_worker.BrokenWorker`** -- a deliberately
  non-conformant fixture harness (drops seeded context, fabricates an
  explicit-success verdict unconditionally) plus this kit's own
  `tests/test_broken_worker_meta.py`, which proves the M2/M3 tests actually
  go RED against it. A parity kit whose tests pass against a broken worker
  proves nothing; this is the kit's own non-vacuity proof.

## Wired workers

- **`loop-amplifier-agent`** (this repo, same PR) --
  `modules/loop-amplifier-agent/tests/test_worker_parity.py`.
- **`loop-agent`** (amplifier-bundle-attractor, the default worker) -- NOT
  wired by this change. That is a separate PR in a separate repo. This
  kit's `WorkerHarness` protocol was designed and read-only sanity-checked
  against `loop-agent`'s existing test machinery (its own fakes in
  `tests/test_context_history_hydration.py`, `tests/test_parity_matrix.py`,
  and `AgentOrchestrator._emit_completion`) to confirm feasibility before
  this kit shipped -- see the companion PR report for the friction notes.
