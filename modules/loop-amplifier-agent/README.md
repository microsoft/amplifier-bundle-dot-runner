# loop-amplifier-agent

Pipeline worker adapter using only the public Python binding:
`create_agent(AgentOptions)` → `create_session(SessionOptions(persistence="ephemeral"))`
→ `start_turn(TurnInput)` → one `events()` consumer → close session and agent.
Python >=3.12; Core 2.0.1. The root distribution always installs this adapter;
the thin engine bundle still does not include its opt-in behavior.

Canonical Attractor §§1.4 and 4.5 permit backend replacement: the graph,
String|Outcome boundary, and worker-written `status.json` channel are unchanged.
The parent reads the file; this adapter never writes a verdict or invents one.

## Selection and authority

Set `llm_provider` and a matching `llm_model`, or mount the matching provider
with a concrete `config.default_model`. Priority-promoted outer provider entries
carry Foundation's resolved node model. Instance addresses resolve to their actual
provider module family. An unknown model fails naming `llm_model/default_model`;
the binding's ambient model is never substituted for a different provider.
`reasoning_effort` comes from explicit node config, then the selected outer entry.
Unset effort remains unset (the public runtime applies its documented default).

Supported provider connection settings are translated into documented variables
in `AgentOptions.environment`, without mutating `os.environ`: `api_key/base_url`
for Anthropic, OpenAI, Gemini, Chat Completions and vLLM; `api_key/endpoint` for
Azure OpenAI; `api_key` for Ollama. Other connection variables, such as Azure
identity, `OLLAMA_HOST`, or Copilot credentials, can be supplied directly through
the per-agent `environment` map. Unsupported mounted provider settings fail by
name rather than disappear. No arbitrary provider request config is injected.

`approval_policy=accept` maps to public `allow` and remains the default.
`deny` maps to `deny`. `user_instructions` still appends to the prompt.
Working directory precedence: explicit `working_dir`, outer
`session.working_dir` capability, process cwd. Only the existing parent of the
task-scoped `current_node_status_path` is added to `additional_directories` when
outside cwd. No path is parsed from the prompt or widened to the whole log tree.
These write directories are not a sandbox for bash.

## Continuity and unsupported legacy controls

Each invocation creates a fresh ephemeral hosted session. Public
system/developer/user/assistant text history becomes fresh
`ConversationMessage`/`TextPart` records in order, passed as first-turn history.
Only context-simple's bookkeeping `metadata._seq` and `metadata.timestamp` are
discarded; semantic metadata, tool-call structures and non-text parts fail
clearly. Translation never edits the context or imports its dynamic bundle
prompt factory. `thread_key` belongs to outer transcript bookkeeping; it is never
a reusable hosted session ID.

- `max_turns` absent, `None`, or integer `0` means unrestricted. Positive caps
  (and other values) are refused before effects: use `worker=coding-agent`.
- Nonempty `workspace`, `host_config`, and injected `agent_configs` are refused.
  An ordinary outer pipeline agents roster is not injection and is accepted.
- Generic `delegate` remains one of the built-ins. Named caller roster injection
  is unsupported. Caller tools are not passed through and the built-in roster
  is not changed.

## Completion and cancellation

Terminal success returns concatenated text, including legitimate empty text.
Failure/rejected turns emit incomplete completion and raise the full public
`AgentError`, never partial success. Missing terminal means incomplete.
Cancellation before completion dispatch emits cancelled completion, requests
`turn.cancel()`, drains the same shielded consumer through terminal, closes
handles, and re-raises cancellation.
Cancellation drain (30s) and each close/cancel (10s) are bounded. A timed-out
cleanup reports uncertainty; a cleanup failure never replaces a primary error.

Both public handles are attempted in order: session, then agent, even when
session close fails. A successful terminal is not sufficient to return its
completed reply: an immediate close failure or close timeout emits incomplete
completion and raises the first cleanup error instead. With an existing primary
error, cleanup failures are attached as notes without replacing that error.
Worker side effects and a worker-written `status.json` may already exist; neither
an incomplete completion nor a raised error makes retry safe.

An unexpected exception escaping a persistence handler stops the event consumer,
emits incomplete completion and propagates unchanged. Unlike caller cancellation,
this path relies on public `session.close()`/`agent.close()` rather than an explicit
adapter `turn.cancel()`/consumer drain. The public lifecycle contract says close
is idempotent and requests cancellation/drains paired events for an active turn.
The focused tests model that contract with handle doubles and real public records;
they do not prove real-provider drain or absence of orphaned work. A close deadline
can expire before underlying cleanup settles; its effects remain unknown.

Completion linearizes when the adapter enters `hooks.emit` with its frozen
envelope, not when delivery is scheduled or when all observers finish. Pending
caller cancellation is checked at that boundary, even before its exception
handler runs. Cancellation after dispatch still propagates, with a note naming
the committed status; it cannot retract observer-owned copies or issue a second
completion. Dispatch is not an acknowledgment that every observer received it.

Completion carries actual `metadata.worker_session_id` for the parent log join.
`turn_count=None`: one public turn does not expose internal model-call count.

## Public telemetry

The shipped `SessionEventPersister.make_handler()` writes curated
`amplifier-agent:<type>` events under
`<stage>/sessions/<actual-session-id>/events.jsonl`, using existing write-time
redaction. Curated types: `turn_started`, `tool_call`, `tool_result`,
`approval_request`, `approval_decision`, `progress`, `usage`, `terminal`.
Output/reasoning deltas and reasoning-final text are intentionally excluded.

Ordinary write failures are different from an escaping handler error:
`SessionEventPersister.make_handler()` catches ordinary `Exception` from its
write seam and logs at debug level. Event observers and terminal processing
continue, and a successful reply is allowed if cleanup succeeds. The failed
record is not evidence of a successful write; the retained stream can have gaps.
Separately, an ordinary event-observer exception is caught by the adapter and
logged at warning level after persistence. This does not depend on the
persister's write-failure catch and does not guarantee every observer saw the event.

Each record retains `contract_version=turn-events/1`, actual session/turn IDs,
sequence, type, optional `at`, and structurally serialized payload. Errors retain
code, category, message, remedy, retryable, correlation_id and details; Decimal
currency values serialize as strings. Actual reported selection is evidence;
requested identity is not used as fallback telemetry.

Usage snapshots replace preceding snapshots; terminal usage is the same
accounting domain, not another charge. The observability consumer stores these
per turn separately from legacy additive provider totals, retaining every actual
model and unknown counters/costs. No synthetic provider request/response events,
call counts, or call timing are emitted. The timing table shows `-` for public
LLM calls and correlates tool spans by session/turn/call ID. Nested delegation is
visible only to the extent exposed by public events/usage, not private hooks.

The runner installs child-to-parent accounting handlers through the public
`PreparedBundle.spawn(before_initialize=...)` seam, preserving explicit host
installers and bundle constraints. Only selection/usage/terminal envelopes are
forwarded, with actual session/turn identity; pipeline events and persistence
are not replayed. Status queries retain public snapshots and selections. With
public turns present, aggregate counters are unavailable (`null`), not zero;
legacy-only counters remain inspectable as `legacy_provider_metrics`. The status
bar shows actual per-model public usage and discloses unavailable call counts.

## Packaging and verification

The binding deliberately floats:

```text
amplifier-agent[github-copilot] @ git+https://github.com/microsoft/amplifier-agent@main#subdirectory=packages/python
```

Binding 0.22.0 pins engine v0.22.0; that engine pins Foundation
`21ad50fa40f7acff913cbf6615228ac359f7dedd`. Runner `main` Foundation conflicts in
an unoverridden consumer solve. The runner now aligns its published requirement
and source to that exact pin as a narrow consumer exception. There is no root
Foundation override hiding qualification failures. Other ecosystem consumers
still naming Foundation main may conflict and need independent qualification.

Upstream's `workspace=true` engine source can leak the binding-main revision
into a consumer uv lock despite the binding's published engine tag. Root and
adapter lock roots therefore carry a narrow engine-only override to the exact
published v0.22.0 tag. This corrects development locks, not runtime authority.
Fresh `uv --no-config pip install --no-sources` checks run independently of
that override. When binding main changes its engine requirement, requalify and
update this correction rather than silently holding an obsolete tag.

```bash
cd modules/loop-amplifier-agent
uv sync
env -u AMPLIFIER_AGENT_CONFIG -u ANTHROPIC_API_KEY -u OPENAI_API_KEY \
    -u GOOGLE_API_KEY -u GEMINI_API_KEY -u COPILOT_AGENT_TOKEN \
    -u COPILOT_GITHUB_TOKEN -u GH_TOKEN -u GITHUB_TOKEN uv run pytest -q
ruff check amplifier_module_loop_amplifier_agent tests
ruff format --check amplifier_module_loop_amplifier_agent tests
```

The mandatory public smoke uses credential-free local Ollama construction/close
without starting a turn or service; missing binding is never importorskip.
Hermetic handle doubles use real public records. Shared worker-parity MUST tests
remain unchanged. Legacy max-turns/per-call identity TARGETs are declared absent;
mandatory public selection/usage tests replace those observations.

Live tests require Anthropic credentials and have honest credential skips:
seeded random history recall plus a two-node full-fidelity graph/control run,
different models, actual selection records and status outside cwd read by the real
parent. The outer spawn carrier in that local test is doubled; it is not proof
of installed Foundation spawning. Manager DTU qualification remains required.