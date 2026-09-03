# Working in this repo

`amplifier-bundle-dot-runner` implements the vendored **strongdm/attractor**
nlspec faithfully (see `README.md` "Spec fidelity" and `SPEC_CONFORMANCE.md`).
This file exists to make sure that stays true as the repo evolves.

## Before you design, file, or submit

Before any design proposal, issue report, or code change touching engine or
spec-adjacent behavior, ask yourself: **"Did you check what the
strongdm/attractor nlspec has to say about this first?"**

1. **Check the nlspec first.** The canonical, pinned copies live in
   `contracts/external/`. Cite the section (e.g. "attractor-spec-canonical.md
   §5.2") in your issue, PR, or design doc.
2. **Conform-fixes are easy yeses.** If the nlspec clearly defines the
   behavior and we implement it wrong or not at all, that's a "yes, fix it"
   (recent examples: support#497, support#498 — both spec-behavior holes,
   both sailed through review).
3. **Need it at all?** If the nlspec is silent, ask whether the need can be
   met *outside* the engine first: an extension/wrapper, pre/post-processing,
   or composing pipelines into something larger. Prefer those over touching
   the engine.
4. **True extensions/divergences face the hard bar.** `SPEC_CONFORMANCE.md`'s
   Compatibility doctrine (including rule 5, "Anchoring survives scope") plus
   a ledgered `specs/EXTENSIONS.md` entry are required — this is not the
   wild-west. "The spec didn't anticipate this shape" is a reason to file an
   entry, not to skip one.
5. **Evidence bar.** Cite the section(s) *and* state what the surrounding
   context says — proof the nlspec was read holistically, not a cherry-picked
   line wielded as leverage for an agenda. Spec silence is not support for a
   change: it doesn't clear step 1, it routes to steps 3/4 above (rule 5 /
   `specs/EXTENSIONS.md`), and the preferred first answer to "the spec
   doesn't do X" is a different pipeline design, not a new feature.

See `SPEC_CONFORMANCE.md` for the full doctrine and the deviation ledger, and
`specs/EXTENSIONS.md` for every documented extension/divergence.
