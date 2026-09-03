# Vision — dot-runner

What this repo is for, captured once so any change can be checked against it.
This page carries the **desired end state, written as though already true**. It
is never edited to report status: what is in flight lives in the tracker, what
is asserted lives in [`ledger/rows.yaml`](../ledger/rows.yaml), and what is
decided lives in [`specs/EXTENSIONS.md`](../specs/EXTENSIONS.md).

Where a governing contract exists, this page is a **thin pointer** to it, not a
restatement. The contract that governs this engine is not ours: it is the
vendored upstream nlspec at
[`contracts/external/attractor-spec-canonical.md`](../contracts/external/attractor-spec-canonical.md).

---

## The desired end state

- **A spec-pure engine.** The `strongdm/attractor` nlspec is the design of
  record. This engine runs a community `.dot` written to that spec, unmodified.
  Where it does not, the difference is a numbered, evidenced, asserted
  divergence — never a surprise.
- **One command.** `dot-runner` runs a `.dot`. Which worker executes a node and
  which model backs it arrive through composition, not through a second command
  name.
- **Workers, not bundles.** A node's executor is a worker behind one narrow
  interface. The engine knows nothing about any particular worker's packaging,
  and gains nothing from knowing.
- **Fail loud.** An engine that cannot do what a graph asked says so and stops.
  It does not substitute a different execution class, promote prose to success,
  or resolve quietly toward green.
- **Extensions get walked back when understanding improves.** An extension is a
  debt against the spec, not a feature to defend. When the spec turns out to
  have meant it all along, the extension is retconned, demoted, or removed
  behind a deprecation window — and the disposition is ledgered either way.

The postures these imply were ruled in the 2026-08-29 maintainer batch and are
carried here verbatim:

**Postures ruled 2026-08-29 (maintainer ruling batch).** Current-state pointers only; narrative,
tests and evidence live in the Changelog below and in `specs/EXTENSIONS.md` -- not duplicated here.

- **One CLI.** `dot-runner` is the only command; the standalone `attractor` entry point is deleted
  (`README.md`, "Getting started"). A bundle's opinion -- which worker runs a node, which model
  backs it -- arrives through composition (`--bundle`, config): mechanism, never a second command
  name.
- **Spec-channels-first; extensions are retconned, not defended.** Artifact files, tool exit codes,
  and a node-written `status.json` (canonical spec §4.5 / Appendix C; read-side conformance
  restoration at §41, auto-injected into every spawned worker's instruction) are the taught,
  implemented outcome mechanism, with a pure-JSON verdict as its sharpest reading (§25).
  `report_outcome` is RETCONNED to a legacy compatibility window -- functional, no longer taught as
  primary (§35's dated RETCON note); AP-4 names the anti-pattern of teaching it as primary. The same
  posture governs extensions generally: a spec-intended design alternative is documented FIRST, and
  the disposition is ledgered whether it leaves the extension in place (§16/§17/§29: DEMOTE, not
  BACK-OUT) or removes it behind a deprecation window (§23: BACK-OUT).
- **`llm_provider` is spec-first.** A node that declares `llm_provider` alone resolves to a live
  per-provider default model out of the box (§42) -- this repo's own ecosystem conventions get a
  vote only after the spec's own meaning is honored.

---

## Our relationship to the nlspec

The governing rule for every change here is the **decision matrix** (maintainer ruling, 2026-08-15),
quoted byte-identical from the sibling repo where it was ratified
(`amplifier-bundle-attractor`, `docs/VISION.md`):

Every change here is weighed against the `strongdm/attractor` nlspec -- not code alone, but
behavior, philosophy, decision-making, design-thinking, process and documentation alike. Movement
that brings this project **more aligned** with the spec is the easy path: supported by default,
carrying the presumption of yes. Movement that would **drift** us away from the spec is made
genuinely hard and is readily pushed back on -- permitted only on measured evidence, and only as a
loud, ledgered divergence. Movement into territory the spec **does not address** meets real
resistance, though less of it: the silence has to be argued rather than assumed, and what ships
there stays additive and non-interfering. That gradient is the steering rule of this project.

Three postures, not two -- and resisted is not forbidden. The uncharted tier is a toll, not a wall:
every extension this repo carries passed through it, paying that toll on the way.

Its concrete four-rule form is the **Compatibility doctrine** below (maintainer ruling,
2026-08-14) — carried here verbatim from the retired `SPEC_CONFORMANCE.md`, which is where it
was ratified and where it decided every disposition until this page took over that role.

Its mechanical enforcement is [`ledger/rows.yaml`](../ledger/rows.yaml) and its checks, which
assert decided **divergences** as well as conformances — because drift is any movement not
recorded in the ledger, in either direction.

---

## Compatibility doctrine

Maintainer ruling, 2026-08-14. The five rules that decide every disposition in this file:

1. **Honor the nlspec design where possible.** The upstream natural-language spec is the design of
   record; "we'd have done it differently" is not a reason to diverge.
2. **100% support for community `.dot` files built against the nlspec.** A graph written to the
   canonical spec must run on this engine unmodified. This is the hard constraint **on
   extensions** — an extension that breaks a conforming graph is a bug, not an extension. It is
   not a claim that *nothing* can require an edit: the decided **divergences** under rule 4 are
   the enumerated exceptions, and each one names the graph shape it refuses and the one-line
   remedy. Today the divergences that can require touching a conforming graph are `EXTENSIONS.md`
   §16 (fail-fast routing — a graph relying on canonical "continue past FAIL on the best
   unconditional edge" must add `runs_on=always` or `continue_on_fail` to the intended successor)
   and §38 / ATX-13 (unknown-shape hard-fail — a decorative out-of-table shape must carry an
   explicit `type=`). Rule 2 bounds what an *extension* may do; it does not repeal rule 4.
3. **Extensions must be additive and non-interfering.** New attributes, shapes, and semantics may
   only add reachable behavior; they may not change what a spec-conformant graph does.
4. **Divergences only for safety, backed by measured evidence, and always LOUD.** A divergence must
   name the safety property it buys, cite the evidence (an incident, a measurement, a live run) that
   the spec-literal behavior actually failed, and fail loudly rather than silently — a divergence
   that resolves quietly toward "success" is the failure mode this doctrine exists to prevent. Every
   one is ledgered here and in `specs/EXTENSIONS.md`.
5. **Anchoring survives scope.** The strongdm/attractor nlspec is this runner's design of record for
   every use, not only attractor-shaped ones. Consumers are expected to drive this engine in
   recipe-shaped and other non-convergence-loop styles; that broadens what pipelines this engine
   runs, and narrows nothing about what it must conform to. A new use case is never a reason to
   relax rules 1-4: an extension motivated by a non-attractor consumer meets the same
   additive/non-interfering bar as any other, and a divergence meets the same
   safety-plus-measured-evidence bar, ledgered identically in `specs/EXTENSIONS.md` and asserted in
   the conformance matrix. "The spec didn't anticipate this shape" is a reason to file an extension
   entry, not to skip one.

> **Editorial note (2026-09-02, not part of the ruling).** The two blocks above are carried
> **verbatim** from `SPEC_CONFORMANCE.md`, so their self-references still read as that file's:
> "every disposition in this file" and "ledgered here" now mean **`ledger/rows.yaml`**, which
> inherited that role; "the Changelog below" means
> [`docs/SPEC_CONFORMANCE_HISTORY.md`](SPEC_CONFORMANCE_HISTORY.md), which retains it; and "the
> conformance matrix" is `ledger/rows.yaml` under its new name. `ATX-13` in rule 2 is a row id in
> that same history file. The wording is left untouched rather than silently modernised — an
> owner-ratified ruling is quoted, not paraphrased.

---

## What this repo deliberately resists

- **YAML and recipe vocabulary inside the engine.** The graph is the workflow.
  A second orchestration vocabulary living beside DOT — steps, stages,
  templating — is a competing engine wearing this one's name, and every
  `.dot` written against the nlspec is what pays for it. Consumers may drive
  this engine in recipe-shaped styles; that broadens what pipelines run here
  and narrows nothing about what the engine must conform to.
- **Self-report gates.** A node that decides its own success by describing it
  is not a gate. An outcome arrives through a channel the engine can read
  without believing prose: an exit code, an artifact, a `status.json`, an
  explicit verdict. "It said it was done" is the failure mode, not the
  mechanism.
- **Silent degradation.** Falling back to a default handler for an unknown
  shape, continuing past a FAIL on the best unconditional edge, promoting a
  no-status node to SUCCESS, warning where it should refuse. Every one of these
  is a run that looks green and did not happen. The engine refuses instead, and
  the refusal names the node, the cause, and the remedy.
- **Ledger entries with no teeth.** A divergence recorded in prose and asserted
  nowhere is a promise CI cannot keep. A decided divergence is a ledger row with
  an executable assertion, or it is not decided.

---

## Maintaining this page

This page states desired state only. It is **never edited to report progress**;
a change here is a change of *intent*, and every one lands as a dated changelog
entry below, ratified by the owner. If an edit would make this page describe
what is true today rather than what this repo is steering toward, it belongs in
the tracker, the ledger, or `specs/EXTENSIONS.md` instead.

---

## Changelog

### 2026-09-02 — VISION established; the Converge layout adopted

Ratified by the owner (converge-adoption ruling, 2026-09-01), executed under the
Converge protocol's `docs/PROTOCOL.md`. This page is composed of already-ratified
text rather than new prose:

| Section | Source |
|---|---|
| Compatibility doctrine (the five rules) | `SPEC_CONFORMANCE.md`'s header section, verbatim (maintainer ruling 2026-08-14) |
| Postures (One CLI · spec-channels-first · `llm_provider` spec-first) | `SPEC_CONFORMANCE.md`'s header section, verbatim (maintainer ruling batch 2026-08-29) |
| The decision matrix | `amplifier-bundle-attractor`'s `docs/VISION.md`, byte-identical (maintainer ruling 2026-08-15) |
| Desired end state · What this repo deliberately resists · Maintaining | New, and deliberately short — the only new prose on this page |

Landed with it: `specs/canonical/` → `contracts/external/` (the external contract
this repo does not own); `specs/conformance/attractor-matrix.yaml` →
`ledger/rows.yaml` in the Converge ledger format, with its checks moved to
`ledger/checks/`; `SPEC_CONFORMANCE.md` retired to a tombstone over
`docs/SPEC_CONFORMANCE_HISTORY.md`; `contracts/recipe-substrate.v1.md` added as
DRAFT (paused — it does not meet the Freeze Bar).
