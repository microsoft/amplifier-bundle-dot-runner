# CONTRACT: engine surface beyond the nlspec, v1

> **DRAFT.** Not FROZEN. Only the owner stamps FROZEN, by editing the `status:`
> line below and adding a dated Changelog entry. The Freeze Bar evidence is in
> the sibling `FREEZE-PACKET-engine-surface.v1.md`; this contract does not
> self-stamp and nothing here governs a lane until it is stamped.

- **id:** `CONTRACT-engine-surface.v1`
- **version:** 1.0.0
- **status:** **DRAFT**
- **date:** 2026-09-02
- **owner:** maintainer
- **repo:** `microsoft/amplifier-bundle-dot-runner`

**Scope.** This is the contract **we own**: the behavior of this engine
*beyond* the external nlspec at `contracts/external/attractor-spec-canonical.md`.
The nlspec is governed elsewhere and asserted in `ledger/rows.yaml`; **this file
governs the surface the nlspec does not**. Its Core clauses are derived, one per
live extension surface, from `specs/EXTENSIONS.md` — the decision-record store
that has carried these surfaces "pending promotion to an owned contract"
(EXTENSIONS.md header). This is that contract.

**Out of scope.** Anything the nlspec already governs (its clauses are
`ledger/rows.yaml`'s rows, not this file's); the five proposed engine changes in
`contracts/recipe-substrate.v1.md` (a separate, paused DRAFT — not duplicated
here); anything `specs/EXTENSIONS.md` marks REMOVED or ABSORBED UPSTREAM.

---

## Clause census (the derivation, stated so it can be checked)

Every one of `specs/EXTENSIONS.md`'s 44 sections was read and classified. A Core
clause exists for every LIVE surface; nothing else may appear as a clause.

| EXTENSIONS § | Classification | Where it lands |
|---|---|---|
| §1–§7 | ABSORBED UPSTREAM @ `fb57a55` | nowhere — the nlspec is the normative text |
| §8, §9, §11, §12, §13 | LIVE | C13 |
| §10 | LIVE | C12 |
| §14 | LIVE | C17.4 |
| §15 | LIVE | C9 |
| §16 | REMOVED (2026-08-30, `feat/extensions-rip-3`) | Reserved |
| §17 | REMOVED (2026-08-30, `feat/extensions-rip-3`) | Reserved |
| §18 | PARTIAL — `k_of_n`/`quorum` REMOVED (2026-08-31), `error_policy` LIVE | C17.5 + Reserved |
| §19 | LIVE | C17.3 |
| §20 | LIVE | C17.2 |
| §21 | LIVE | C17.1 |
| §22 | LIVE (divergence) | C16 |
| §23 | REMOVED (2026-08-31, `feat/extensions-walkback-2`) | Reserved |
| §24, §26, §28, §30 | LIVE | C18 |
| §25 | LIVE (divergence) | C15 |
| §27 | LIVE | C5 |
| §29 | REMOVED (2026-08-30, `feat/extensions-rip-3`) | Reserved |
| §31 | LIVE (narrowing) | C19 |
| §32 | LIVE | C20 |
| §33 | LIVE (divergence) | C11 |
| §34 | LIVE (bug fix) | C21 |
| T0-4 note | conformance restoration — nlspec behavior, not ours | Reserved |
| §35 | PARTIAL — `report_outcome` REMOVED (2026-08-30, WAVE 5); spawn lifecycle envelope + injected worker contract LIVE | C4, C3.5 + Reserved |
| §36 | LIVE | C10 |
| §37 | LIVE | C22 |
| §38 | LIVE (divergence) | C11 |
| §39 | LIVE (divergence) | C14 |
| §40 | LIVE | C1, C2 |
| §41 | LIVE (conformance restoration of a spec-native channel) | C3 |
| §42 | LIVE | C8 |
| §43 | LIVE | C6 |
| §44 | LIVE | C7 |

---

## Core

*(clauses land in the next commit)*

## Backlogged

*(next commit)*

## Reserved

*(next commit)*

## Changelog

- **1.0.0 — 2026-09-02 — DRAFT.** Initial draft: the owned contract for engine
  behavior beyond the external nlspec, derived clause-by-clause from
  `specs/EXTENSIONS.md`'s live surfaces.
