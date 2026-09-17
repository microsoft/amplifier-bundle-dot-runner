# Migration Guide

## Unreleased — completion-event identity correction (2026-09-16)

This unreleased correction implements ratified candidate C15.5
(`contracts/engine-surface.v2-candidate.md`; receipt:
`contracts/engine-surface.ratification-20260916.md`).

- **New `pipeline:node_complete` events:** `session_id`, when present, is the
  emitter/coordinator identity supplied by the normal HookRegistry default
  fields. `worker_session_id` is the optional worker reference supplied by
  `Outcome.session_id`.
- **`status.json` callers are unchanged.** Its existing `session_id` value
  remains the worker reference where that path supplies one.
- **Mixed history:** never treat `worker_session_id ?? session_id` as a worker
  lookup. A corrected no-worker completion can have only the emitter id.
  Classify records only from positive engine version and commit provenance;
  records without it remain unclassified. Do not rewrite old captures.
- **Release boundary:** the release note must name the first corrective version
  and commit. This is still unreleased, so neither is assigned here; the
  eventual first corrective commit is the boundary, not an event-schema or
  version-field change.

## 0.3.0 -- extensions-rip-3 (2026-08-30)

Three demoted extensions are DELETED (mechanism removed, not merely discouraged).
Each has a spec-intended replacement pattern using canonical vocabulary only.

### 1. `runs_on=` / `continue_on_fail=` (EXTENSIONS.md Sec16) -- REMOVED

Before:

```dot
build   [shape=box, prompt="..."]
cleanup [shape=box, prompt="Clean up.", runs_on=always]
build -> cleanup
```

After:

```dot
build   [shape=box, prompt="..."]
cleanup [shape=box, prompt="Clean up."]
build -> cleanup [condition="outcome=fail", label="fail-fast: explicit route"]
build -> deploy   [label="success: continue"]
```

Unconditional edges are now ALWAYS followed regardless of outcome status (canonical
Sec3.3 step 4 restored) -- a plain edge like `build -> cleanup` above already runs on
either outcome, so `runs_on=always` is simply deleted, not replaced, in that common
case. Use an explicit `condition="outcome=fail"` edge for true fail-fast routing.

### 2. `requires=` / `outputs=` (EXTENSIONS.md Sec17) -- REMOVED

Before:

```dot
extract [shape=box, prompt="...", outputs="extracted_record"]
summarize [shape=box, prompt="...", requires="extracted_record"]
extract -> summarize
```

After:

```dot
extract [shape=box, prompt="Extract the record to context key extracted_record."]

check_extracted [shape=tool, tool_command="test -f .ai/extracted_record.json && echo present || echo absent"]

extract -> check_extracted
check_extracted -> summarize  [condition="context.tool.last_line=present"]
check_extracted -> skip_note  [condition="context.tool.last_line=absent"]
```

### 3. `feedback_from=` (EXTENSIONS.md Sec29) -- REMOVED

Before:

```dot
critique [shape=box, prompt="Review the draft."]
generate [shape=box, prompt="Address feedback.", feedback_from="critique"]
critique -> generate [label="loop_restart", condition="outcome=fail"]
```

After (file-mediated feedback):

```dot
critique [shape=box, prompt="Review the draft at .ai/draft.md. Write your critique to .ai/feedback/critique.md, overwriting any prior content."]
generate [shape=box, prompt="Read .ai/feedback/critique.md if it exists and address it. Write the revised draft to .ai/draft.md."]
critique -> generate [label="loop_restart", condition="outcome=fail"]
```

### Lint

`dot-runner lint` reports **ATTR-LINT-001** (ERROR) for one release when a graph
still declares `runs_on=`, `continue_on_fail=`, `requires=`, `outputs=`, or
`feedback_from=` -- the message names the migration pattern above. These attrs are
otherwise silently ignored (the engine's standard unknown-attr behavior); the lint
rule exists so authors are told, not left to discover the no-op the hard way.
