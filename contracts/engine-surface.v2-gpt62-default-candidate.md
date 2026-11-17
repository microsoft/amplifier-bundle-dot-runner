target: contracts/engine-surface.v1.md

# Candidate amendment — SDK-direct OpenAI fresh default

**Date:** 2026-11-17  
**Status:** proposal and candidate implementation for review only; **not ratified**.
This is not contract-version adoption, merge, release, or live-adoption authority.

The frozen v1 remains byte-identical at SHA-256
`524760cbb5fdffd874f0a961f5be7fd1709113266e6c05b4659de3d75a6104a7`.
The existing `engine-surface.v2-candidate.md` is a different, completion-event
identity proposal preserved by `engine-surface.ratification-20260916.md` at
SHA-256 `1d2d90ae022e285208dc109cce8a8faafe02d1bc76187447b890a0f48f219b19`.
This separately named sibling preserves those prior proposal/ratification bytes
and requests no changes to their C15 scope.

## Exact proposed C5.5 replacement

Target: frozen `engine-surface.v1.md`, C5 item 5 (line 107 at the above hash).
Current text, reproduced verbatim:

```text
5. A node with an explicit `llm_provider` and no `llm_model` resolves a **family token**, never a literal model id, live: `anthropic` → `sonnet` (stable only); `openai` → `gpt-5.*[0-9]` (stable only, anchored so `-mini` / `-codex` siblings cannot outrank the bare release); `gemini` → `gemini-3*pro*` (stable-only filtering disabled, because the flagship is itself preview-named). Rung order is otherwise unchanged: explicit `llm_model`, then `model_stylesheet`, then the unimplemented graph-level default, then this.
```

Proposed replacement:

```text
5. On the SDK-direct path, a node with an explicit `llm_provider` and no effective `llm_model` resolves a default against live availability: `anthropic` → `sonnet` (stable only); `openai` → the first listed model in the order `gpt-6.2-sol`, `gpt-6.2-astra`, `gpt-6.1-sol`, `gpt-6-astra`, then the existing stable `gpt-5.*[0-9]` selector if none of those exact IDs is listed; `gemini` → `gemini-3*pro*` (stable-only filtering disabled, because the flagship is itself preview-named). OpenAI availability is obtained from the generation adapter's own SDK client, not an advisory catalog, different account, or separately mounted provider. Cache the selected default only within the worker and its branch clones, scoped to the actual adapter; a fresh worker discovers its own availability. An empty/error listing or no eligible model fails loud before generation and never invents a fallback ID. A generation failure does not trigger model substitution. Explicit IDs and opt-in model patterns retain their existing behavior. Rung order is otherwise unchanged: explicit `llm_model`, then `model_stylesheet`, then the unimplemented graph-level default, then this. Spawned workers retain their separately owned defaults.
```

Only C5.5 changes. C5.1–C5.4 and C5.6–C5.7 remain unchanged, including
subscription-provider routing, malformed-provider failures and fully bare-node
handling. No frozen file or ledger hash is edited by this proposal.

## Why propose a change

The old `gpt-5.*[0-9]` default excludes both exact GPT-6.2 IDs even when the
same account serves them. The candidate's offline membership and Responses
wire tests expose that failure in the old implementation. This is a real
selection gap, not evidence of failed live generation or a latency benchmark.

Canonical attractor §8.3–§8.5 (`contracts/external/attractor-spec-canonical.md`)
allows any provider-native model string, gives explicit/stylesheet settings
priority and reserves the final default rung for the implementor. It names no
specific default. The surrounding stylesheet transform rules forbid replacing
an explicitly supplied model. This proposal preserves that precedence.

Unified-LLM canonical §2.9 (`contracts/external/unified-llm-spec-canonical.md`)
says to prefer latest available models while retaining older served models.
Its surrounding catalog design is advisory, not an allowlist; unknown explicit
strings must still reach the provider. Its illustrative GPT-5 list is not a
future-family parser. Consequently this proposal updates only the implicit
preference, not generic model sorting, explicit pins or author-written globs.
The canonical nlspec permits this choice, but that does **not** amend the
separately owned, frozen C5.5. Maintainer ratification is still required.

## Default choice and availability

[GPT-6.2 Sol](https://developers.openai.com/api/docs/models/gpt-6.2-sol) is
described as the go-to everyday/agent model; the
[migration guide](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6.2-astra)
describes Sol as complex work at lower cost and Astra as the most demanding
option. Prefer Sol when both are available, rather than relying on arbitrary
lexical ordering. Astra remains the next general-purpose choice. The known
predecessors are exactly `gpt-6.1-sol` and `gpt-6-astra`; there is no invented
`gpt-6.1-astra` alias or speculative future GPT family parser.

Every preferred ID must occur in the generation adapter's own served list.
Specialized siblings such as law/cyber and unknown future IDs cannot win this
implicit preference, although an explicit author choice still passes through.
The old stable GPT-5 fallback retains its existing selector semantics, including
its existing date/suffix handling; this is not a new guarantee about every
future suffix. Listing establishes listed availability only, not successful
Responses generation, capacity, service-tier eligibility or pricing.

The existing SDK adapter consumes `models.list().data`. At the inspected
OpenAI SDK 3.6/3.7 versions this endpoint uses `AsyncPage`, whose implementation
explicitly says the API has no pagination and returns no next-page information.
This candidate does not invent an undocumented cursor or claim independently
complete account inventory. A future SDK/endpoint pagination change requires
an affected lister review; preference is over the list actually returned.

No canonical mounted-provider default selector exists on this SDK adapter.
Reusing a different Amplifier provider's default string would neither establish
this client's availability nor preserve its account/endpoint namespace. Reuse
the existing `select_latest` only for the old fallback and leave the SDK catalog,
prices, generic resolver and other provider defaults untouched.

## Migration and reproducibility boundary

If accepted and shipped, an unpinned SDK-direct OpenAI node can choose a newer
model. That is an intentional default-selection change with possible output,
latency and cost differences, not fully identical behavior for omitted models.
The eventual release note must identify the first adopting commit/version;
none is invented or approved here. Users requiring an unchanged model should
set an explicit `llm_model`, including through the existing model stylesheet.

Already explicit old or new IDs remain unchanged and do not require listing.
An explicit old default glob continues to opt into its original resolution.
No saved configuration, graph node, history, checkpoint, worker outcome or
attempt-accounting record is rewritten. Selection is cached for worker/adapter
stability only; there is no new durable model pin across process restart or
checkpoint resume. An unpinned resumed run may therefore rediscover a different
model, just as a fresh worker does. Do not claim reasoning-item reuse across
model changes or rewrite the prefix/history to force it.

## Candidate implementation and required review

The separate exact-support checkpoint is
`a72d50c2ca946228004e55983e55db4e85cec2e5`. The older preserved combined
candidate `287422fcd19685f17296dbd84d85209dc9827bad` was not accepted or fully
qualified after its final correction; it is not qualification for this proposal.
The reviewed candidate must be an identified descendant of the exact-support
checkpoint with its own affected checks.

Focused assertions in `test_llm_provider_alone_default_model.py` must prove:

- The generation adapter lists once for a stable shared worker/adapter, including
  concurrent branch clones, and every preference transition is membership-only.
- Fresh workers and distinct lazy-created clone adapters cannot share another
  account's selection. Listing failures/no candidates never generate or cache.
- Explicit 6.2 and older IDs, explicit/stylesheet precedence, opt-in globs and
  non-OpenAI defaults retain their behavior.
- Real worker → tool loop → OpenAI SDK with only HTTP mocked sends the selected
  ID through Responses, preserving tools, tool-call/result IDs, prior input
  prefix and graph-node attributes. A generation error cannot silently retry
  with an older model.

Run affected model/default, worker observability, checkpoint/resume, parity
and ledger checks. Preserve original failures and declared skips. Existing
ledger tests include existence-indexed probes: a passing ledger does **not**
ratify this behavior change or prove the literal frozen C5.5 already covers it.
Manager-owned same-existing-DTU qualification and independent review remain
separate, with exact built input/provenance and no second environment.

## Maintainer decision

**Decision:** pending; no ratification recorded by this proposal.

Review must name this proposal's exact source/digest and independently dispose
of the C5.5 compatibility/default-choice change before integration. Approval to
prepare proposals is not approval to promote the frozen contract, change its
ledger dispositions, merge, release or adopt a running host.
