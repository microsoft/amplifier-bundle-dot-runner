# SPEC_CONFORMANCE.md — retired 2026-09-02

This file is a tombstone. It was split when the repo adopted the Converge layout
(vision-first, contract-driven development), because it had been doing three
jobs at once: stating the doctrine, holding the ledger, and recording history.

**Its two live homes:**

| What you are looking for | Where it lives now |
|---|---|
| The **Compatibility doctrine** — the five rules that decide every disposition | [`docs/VISION.md`](docs/VISION.md) |
| The **conformance ledger** — one row per contract clause, with its disposition and an executable assertion | [`ledger/rows.yaml`](ledger/rows.yaml) (checks: [`ledger/checks/`](ledger/checks/)) |

**The archived record:** [`docs/SPEC_CONFORMANCE_HISTORY.md`](docs/SPEC_CONFORMANCE_HISTORY.md)
retains this file's full prior text, frozen. It is still load-bearing: it holds
the dated decision record for every `ATX-*` / `ULM-*` / `CAL-*` id, the
`ULM-*` / `CAL-*` gap tables that have no successor home, and the DECIDE-item
context. `ledger/rows.yaml` rows cite it by id, and `ledger/checks/` verifies
those cites still resolve.

**Where a new record goes:**

- A new **divergence** from the vendored upstream nlspec → a `ledger/rows.yaml`
  row (`disposition: DIVERGED`) **plus** a `specs/EXTENSIONS.md` entry. Both, in
  the same PR.
- A new **doctrine ruling** → a dated changelog entry in `docs/VISION.md`.
- A new **gap** against a required clause → a `ledger/rows.yaml` row
  (`disposition: GAP`) with a tracker ref.

Nothing new belongs in this file, or in the archive.
