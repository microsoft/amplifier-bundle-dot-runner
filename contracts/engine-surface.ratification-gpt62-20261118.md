# Ratification — SDK-direct OpenAI fresh default

- **Date:** 2026-11-18 (UTC)
- **Decision-maker:** human self-identifying as the contract owner/maintainer
- **Exact owner response:** `I am the contract owner/maintainer and ratify both amendments`
- **Status:** exact C5.5 amendment ratified; review, merge, release and adoption remain separate

## Approved proposal

The owner accepted the DOT Runner C5.5 replacement and its compatibility,
migration and verification boundaries in
[`engine-surface.v2-gpt62-default-candidate.md`](engine-surface.v2-gpt62-default-candidate.md).
The approved proposal is identified by:

- **Reviewed candidate commit:** `53d61d9d0b092448a2073cef9f68554d1c93bcaf`
- **Proposal SHA-256:** `1250faf79e28fba990835f6584b655f1f096cddfd5aed1d078ef0e9c05e29c0e`

The proposal is preserved byte-for-byte as reviewed. Its pending/proposal-only
language records its status when prepared; this separate receipt records the
subsequent owner decision. The exact replacement in that proposal, not a
paraphrase here, supplies the amended rule.

## Decision provenance

The manager verified the answer to question
`5fcfadb3-6edb-433d-9c7c-e46dfce239d7`: question revision 1, answered record
revision 2, option `ratify-both`, at `2026-11-18T02:18:30.301235Z`.
The exact response above was submitted through the human UI. The manager
reported that verification to this worker in the attributed request
`gpt62-dot-owner-ratification-verified-v1`, with delivery input
`question:5fcfadb3-6edb-433d-9c7c-e46dfce239d7:answer`.
The worker's direct question read was caller-scoped and refused; attribution
here is to the manager's verified read, not an independent worker UI read.
The worker subsequently saved and inspected the manager's retained answer
receipt (SHA-256
`f5c74c617739e758e137b1001d0a3cbef4d7863f1bd278041e503183f82d4a25`).
Its full question-revision-1 prompt has UTF-8 SHA-256
`0d8f1eb2cb976b5f41190ad4124889014ad731fc77b4369da6e31f5c3f413b5b`
and explicitly names this proposal digest, the default order, possible Astra
cost increase and unpinned-resume rediscovery. This is inspection of the
retained parent-authenticated receipt, not a second human decision.

The combined question addressed separate Agent and DOT amendments. This receipt
records only the DOT decision; it does not adopt or attest the Agent amendment.
No personal identity beyond the owner's actual self-identification is inferred.

## Scope and compatibility disposition

Only C5.5's SDK-direct implicit OpenAI default changes. The order is listed
`gpt-6.2-sol`, `gpt-6.2-astra`, `gpt-6.1-sol`, `gpt-6-astra`, then the existing
stable GPT-5 selector. Selection is against the generation adapter's own list,
not a static availability assertion or a universal future-model ranking.
Empty/error/no-match listings fail before generation; generation failures do
not substitute another model.

The owner accepts that unchanged unpinned DOT can select a different model,
with different output, latency and cost. In particular, preferring listed
6.2 Astra over 6.1 Sol need not minimize cost. Explicit model pins remain the
reproducibility escape hatch. Worker/adapter-local caching is not a durable pin:
an unpinned resumed run can rediscover a different model, with no guarantee of
reasoning-item reuse across model changes and no history rewrite to force reuse.

Explicit IDs/globs, stylesheet precedence, other providers, spawned-worker
defaults, C5.1–C5.4/C5.6–C5.7, graph-default implementation status, outcomes,
history, checkpoints and attempt accounting remain unchanged as specified.

Canonical attractor §8.3–§8.5 protects explicit/stylesheet precedence and leaves
the handler/system default to the implementor. Unified-LLM §2.9 recommends
newer available models while retaining old and unknown explicit IDs; its
surrounding §2.7–§2.10 preserves native APIs, provider-option escape hatches
and caching obligations. Neither canonical spec chooses this exact order.
This is an owner-ratified implementor policy, not a new canonical divergence
or proof that frozen C5.5 already contains it.

## Preserved boundaries

This receipt does not edit or promote a FROZEN contract version, change ledger
dispositions, waive protected review or CI, merge, release, or adopt a live host.
Implementation and verification have their own exact-source evidence; passing
existence-indexed ledger probes neither caused nor substitutes for ratification.
The existing engineering repair at `0694e1f7fe472ffe16f86f6bffcaba21349c913d`
is preserved. Its CI success is distinct from checks on this documentation head.

Frozen `engine-surface.v1.md`, the prior completion-event identity proposal and
its 2026-09-16 ratification receipt, this GPT-6.2 proposal and `ledger/rows.yaml`
remain byte-identical. The amendment disposition is recorded in
[`EXTENSIONS.md` §42](../specs/EXTENSIONS.md#42-per-provider-default-model-for-llm_provider-alone-nodes-direct-path-spec-85-rung-4).