# loop-amplifier-agent

An adapter orchestrator module: lets a `.dot` pipeline node use
[microsoft/amplifier-agent](https://github.com/microsoft/amplifier-agent)'s
`Engine` (a full, self-contained coding-agent runtime -- its own bundle, its
own tool roster, its own provider mounting) as the node's worker, instead of
this ecosystem's native `loop-agent`.

**The engine stays agent-agnostic.** This module is a *second, opt-in* agent
worker option alongside `loop-agent`. `loop-agent` remains the default in
the attractor layer (`amplifier-bundle-attractor`). Nothing here changes
`modules/loop-pipeline`'s engine code, and this module is never pulled in by
the root `bundle.md` -- see `behaviors/dot-runner-amplifier-agent.yaml`,
which is an opt-in partial a consuming bundle includes explicitly.

## Why this exists

`modules/loop-pipeline` spawns a child agent per DOT node through a generic
`Orchestrator.execute(prompt, context, providers, tools, hooks, **kwargs) ->
str` contract (`amplifier_core.interfaces.Orchestrator`). Any module that
satisfies that contract can be the worker behind a node. `loop-agent` is one
implementation; this module is another, backed by a completely different
agent runtime.

## The mechanism

Proven empirically before this module was written -- see
`/var/tmp/aa-probe/probe_q1_q2.py` ("Q1: can a host mount a foreign tool
module into Engine's session? Q2: does the model actually call it?" -- both
answered yes against a real Engine and a real model turn):

1. `amplifier_agent_lib.engine.Engine` takes a caller-injected
   `turn_handler` coroutine at construction
   (`amplifier_agent_lib/engine.py`, the `TurnHandler` type alias and
   `Engine.__init__`). The vendored CLI's own handler factory,
   `amplifier_agent_lib._runtime.make_turn_handler`, builds a handler that
   calls `prepared.create_session()` and wires capabilities onto
   `session.coordinator`.
2. A custom turn handler can do the same `create_session()` call and
   additionally `await session.coordinator.mount("tools", tool,
   name=tool.name)` to add a tool amplifier-agent's own baked-in bundle
   never declared -- here, the REAL `tool-report-outcome` module's
   `ReportOutcomeTool`. This is the *same call shape*
   `amplifier_agent_http/_session_runner.py` uses (around line 134) to
   mount its own `HostToolProxy` tools onto a live per-turn coordinator, so
   it is a documented extension point, not a private hack.
3. After the turn, `tool.last_outcome` holds the agent's `report_outcome`
   call arguments (or `None` if it never called the tool). This module
   republishes that as `metadata.report_outcome` on an
   `ORCHESTRATOR_COMPLETE` event, in the exact envelope shape
   `amplifier_module_loop_agent.AgentOrchestrator._emit_completion` uses,
   which `amplifier_module_loop_pipeline.backend._outcome_from_spawn_result`
   already knows how to read.

A fresh `Engine` (and fresh amplifier-agent session) is booted per
`execute()` call -- **no caching** -- for per-node state isolation.

## Wiring an agent entry

A pipeline profile that wants a node backed by amplifier-agent declares an
inline `session.orchestrator` on that agent, exactly like a `loop-agent`
entry does today (see `behaviors/dot-runner-amplifier-agent.yaml` for the
full, commented example):

```yaml
agents:
  dot-runner-agent-amplifier-agent:
    session:
      orchestrator:
        module: loop-amplifier-agent
        source: git+https://github.com/microsoft/amplifier-bundle-dot-runner@main#subdirectory=modules/loop-amplifier-agent
        config:
          llm_provider: anthropic
          reasoning_effort: medium
          max_turns: 8
```

**Inline `session.orchestrator` is required, not optional polish.** The
spawn capability merges an agent's `session:` key onto the parent config,
and `loop-pipeline`'s recursion guard
(`amplifier_module_loop_pipeline.backend`) raises loudly if a node's
resolved agent config has `session.orchestrator.module` absent or equal to
`"loop-pipeline"` -- either would make the child inherit or re-enter
`loop-pipeline` and recurse infinitely.

## Config-key mapping (`orchestrator_config` -> amplifier-agent)

The dot-pipeline backend passes `orchestrator_config` keys blind -- it does
not know which orchestrator module is mounted, or which keys it understands.
This module maps the four keys `backend.py`'s spawn path forwards:

| key                 | injection point |
|---------------------|-----------------|
| `llm_provider`      | `amplifier_agent_cli.provider_sources.inject_provider(prepared, provider, ...)` -- the probe-proven seam. `prepared.mount_plan["providers"]` is cleared first: amplifier-agent's baked-in bundle declares 9 install-only provider *stubs*, and `inject_provider` no-ops ("don't clobber existing") unless they're cleared, which could otherwise trigger interactive OAuth for `openai-chatgpt` during session creation. |
| `reasoning_effort`  | forwarded to the same `inject_provider(...)` call as `effort_override`. |
| `max_turns`         | best-effort forward into `prepared.mount_plan["session"]["orchestrator"]["config"]["max_turns"]`. amplifier-agent's `Engine` has no "max turns" knob at the boot/turn-submit layer (one `execute()` call here is one `submit_turn`); this mirrors how the dot-pipeline backend itself blindly forwards `orchestrator_config` to whatever orchestrator is mounted, honored only if the mounted session orchestrator (`loop-streaming` by default) recognizes the key. |
| `user_instructions` | appended to the prompt text handed to `session.execute()` (Layer-5 override), next to the report_outcome nudge. |

When `llm_provider` is absent, this module defaults to `"anthropic"` --
amplifier-agent's own baked-in default (`bundle.md`'s `default_provider:
anthropic`).

## Fail-closed: never fabricate success

After the turn, this module reads `tool.last_outcome`. If it is `None`
(the agent never called `report_outcome`) or malformed (missing/unknown
`status`), the module does **not** leave the verdict empty and let the
parent's lifecycle-only fallback derive `SUCCESS` from a clean exit. It
synthesizes an explicit, non-passing verdict instead:

```python
{
    "status": "retry",
    "notes": "amplifier-agent turn ended without a valid report_outcome verdict ...",
}
```

This is *stricter* than `loop-agent`'s own default (plain prose without a
verdict is `SUCCESS`, non-explicit, per spec section 4.5) -- deliberately,
per this module's own contract: an amplifier-agent turn that produces prose
without ever calling `report_outcome` must read as "needs another look,"
never as silent success. See `tests/test_orchestrator.py`'s
`test_fail_closed_on_missing_verdict` /
`test_fail_closed_on_malformed_verdict_*`.

An empty final reply is not itself a failure: if the agent calls
`report_outcome` with `status="success"` and returns no closing prose, the
turn still succeeds (artifact over prose -- see
`test_empty_reply_with_verdict_still_succeeds`).

## Capability gaps vs the vendored CLI handler (v2)

v1 disclosed five deliberate scope cuts vs. the vendored
`amplifier_agent_lib._runtime.make_turn_handler` this module's custom
`TurnHandler` is modeled on (see "The mechanism" above). v2 closes all five,
each judged for what makes sense on an EMBEDDED node worker rather than an
interactive CLI (there is no human, no TTY, no `--config` file, no `--workspace`
flag):

- **`prepare_bundle_for_session` -- closed.** `_run_turn` now calls the REAL
  vendored `amplifier_agent_lib._runtime.prepare_bundle_for_session` (skills/
  modes `BUNDLE_DIR` injection, the host-config `merge_config` overlay, and
  the hook-context-intelligence workspace seed) instead of reimplementing it.
  **Judgment call:** the CLI's `--workspace` flag has no argv equivalent here,
  so `orchestrator_config.workspace` fills that role, resolved through the
  SAME `amplifier_agent_lib.persistence.resolve_workspace` (argv > env >
  cwd-derived slug) and always applied -- a child turn gets a real, isolated
  context-intelligence workspace bucket by default, not amplifier-agent's bare
  baked-in bundle. The CLI's `--config` file has no natural analogue for a
  pipeline node, so `host_config` defaults to `None` (no-op overlay) unless a
  pipeline author opts in via `orchestrator_config.host_config` (a
  host_config-shaped dict, forwarded verbatim). No remainder: closing this one
  call closes the whole gap for the embedded case. See `_run_turn`'s docstring
  and `tests/test_v2_capabilities.py`'s gap-1 tests.
- **`session.spawn` -- closed.** Registered on the child session's coordinator
  exactly like the vendored handler (same closure pattern: `agent_configs`
  defaults from the cold-path hydration, `parent_session` pinned to the
  current turn's session), so the child's own `delegate` tool can spawn
  grandchild sessions. See `test_session_spawn_registered_and_forwards_to_delegate`
  (RED-proof: against v1, `session.coordinator.capabilities.get("session.spawn")`
  was always `None`).
- **Approvals -- closed, safe-by-default.** `approval.request` now wires the
  REAL `WireApprovalProvider(approval_request_fn=ctx.approval.request)` seam,
  forwarding the actual decision instead of a hardcoded stub. **The decision
  itself is governed by a new `approval_policy: "accept" | "deny"` config key,
  defaulting to `"deny"`.** A headless pipeline node has no human to ask, so
  v1's auto-accept was the dangerous default; `"deny"` fails closed (every
  approval-gated action in the child turn is declined unless a pipeline
  author explicitly opts in), and `"accept"` logs a loud `WARNING` on every
  turn it's active so the dangerous choice is never silent. An unrecognized
  `approval_policy` value also fails closed to `"deny"` with a logged warning
  -- never fails open. See `test_approval_defaults_to_deny` (RED-proof: v1's
  stub always returned `{"action": "accept"}` regardless of config),
  `test_approval_policy_accept_forwards_accept`,
  `test_approval_policy_accept_logs_a_loud_warning`, and
  `test_approval_policy_invalid_value_fails_closed_to_deny`. One inherited
  claim, disclosed honestly: that a DECLINED approval degrades gracefully
  mid-turn (the agent proceeds past the denial rather than crashing) is
  upstream amplifier-agent's own tested non-interactive CLI behavior -- this
  module's tests prove the WIRING (the decision reaches the real
  `WireApprovalProvider`), not the mid-turn degradation itself.
- **`provider_preferences` -- closed.** There is no `provider_preferences`
  parameter on `Orchestrator.execute()` at all (the kernel's orchestrator call
  boundary, `amplifier_core._session_exec.run_orchestrator`, threads through
  only `prompt`/`context`/`providers`/`tools`/`hooks`/`coordinator`) -- the
  generic foundation spawn path resolves `provider_preferences` BEFORE session
  creation by mutating the CHILD session's OWN mount-plan `providers` list
  (`apply_provider_preferences_with_resolution` promotes the matching entry to
  `priority=0` and stamps its `config["default_model"]`). Since this
  orchestrator IS that child session's mounted orchestrator,
  `_resolve_parent_provider_preference` reads it back from
  `coordinator.config["providers"]`. **Fragile-signal caveat:** the read-back
  detects the promotion by the PRESENCE of `config["default_model"]` on the
  promoted provider entry -- that marker is foundation's current
  implementation detail, verified by source-reading (not by a live
  provider_preferences spawn). If a future foundation refactor changes how
  `apply_provider_preferences_with_resolution` records the preference, this
  detection must be revisited; the unit tests pin today's marker so the
  break will be loud, not silent. **Precedence (documented, tested):** an
  explicit `llm_provider` config always wins PROVIDER SELECTION --
  `loop-pipeline`'s own `backend.py` says so in its spawn_kwargs comment
  ("Provider SELECTION ... flows via orchestrator_config['llm_provider']"
  while `provider_preferences` exists purely to carry the model, which "has no
  other channel"). So: `llm_provider` set -> it selects the provider, and the
  parent's preferred model is honored only when it names that SAME provider
  (a mismatched model is dropped rather than forced onto the wrong provider).
  `llm_provider` absent -> the parent preference's provider+model wins
  outright. Neither present -> `DEFAULT_PROVIDER`, no model override (v1
  behavior, unchanged). See `_run_turn`'s docstring and the four
  `test_*provider_preferences*` / `test_explicit_llm_provider_wins_*` /
  `test_mismatched_parent_preference_model_is_dropped` /
  `test_no_parent_preference_and_no_llm_provider_falls_back_to_default` tests.
- **`hooks.set_default_fields` -- closed.** The per-turn handler now mints an
  ephemeral session id (mirroring `make_turn_handler`'s identical one-shot
  fallback: this adapter always submits with no incoming session id, so a
  fresh `ephemeral-<uuid>` id is minted every turn) and calls
  `session.coordinator.hooks.set_default_fields(session_id=..., turn_id=...)`,
  so every tool/llm/execution event this turn emits carries a non-empty
  `session_id` instead of being dropped by the context-intelligence
  `LoggingHandler`'s empty-session-id check. See
  `test_hooks_default_fields_stamped_with_session_and_turn_id` (RED-proof:
  against v1, `coordinator.hooks.set_default_fields` was never called at all).

## Python version note

`amplifier-agent` (the peer library this module hosts) declares
`requires-python = ">=3.12"`. This module cannot support a lower floor than
the library it wraps, so its own `pyproject.toml` also declares
`requires-python = ">=3.12"` -- one Python floor higher than this repo's
usual `>=3.11`. `.github/workflows/ci.yml`'s `unit-tests` matrix excludes
the `py3.11` cell for this one module accordingly (see that file's
`exclude:` entry and comment).

This module's own top-level code has no import-time dependency on
`amplifier_agent_lib` (the real import is lazy, inside `_load_dependencies`,
called only from `_run_turn`) specifically so its hermetic unit tests can
run without the real, heavy, Python-3.12-only library installed at all.

## Tests

* `tests/test_mount.py` -- protocol compliance (`mount()` registers the
  orchestrator).
* `tests/test_orchestrator.py` -- hermetic unit tests. Monkeypatches the one
  seam (`_load_dependencies`) with fakes faithful to the real amplifier-agent
  contract (`tests/_fakes.py`): envelope shape (cross-checked against the
  REAL `loop-pipeline` backend reader), config-key mapping, fail-closed
  behavior, empty-reply-with-verdict success, exception handling, and
  `Engine.shutdown()` always being called (including on exception).
* `tests/test_spawn_report_outcome_transport.py` -- a real,
  network-and-credential-requiring integration test mirroring
  `modules/pipeline-runner/tests/test_spawn_report_outcome_transport.py`
  with this module as the producer. `pytest.importorskip("amplifier_agent_lib")`
  skips when the peer library isn't installed; a `skipif` additionally skips
  when no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` is present, so CI (which
  carries no secrets) skips this test honestly rather than failing it.
