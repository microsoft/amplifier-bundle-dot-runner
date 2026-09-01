"""Read-side pickup for a node-written ``status.json`` (Appendix C).

Spec basis (canonical, verbatim -- see specs/canonical/attractor-spec-canonical.md):

    Sec 4.5 (line 709): "Status file: The handler writes ``status.json`` in
    the stage directory with the Outcome fields serialized as JSON. This
    file serves as an audit trail and enables the status-file contract:
    external tools or agents can write ``status.json`` to communicate
    outcomes back to the engine."

    Appendix C (lines 2053-2078): "Each non-terminal node writes a
    ``status.json`` file in its stage directory. This file drives routing
    decisions and provides an audit trail." -- followed by the envelope
    shape (``outcome``, ``preferred_label``, ``suggested_next_ids``,
    ``context_updates``, ``notes``) and its field table.

Prior to this module the engine only ever WROTE ``status.json``
(``engine.py: _write_node_status``, ``handlers/codergen.py: _write_status``)
-- nothing ever read one back. The contract's own text ("communicate
outcomes BACK TO THE ENGINE") requires a read side; this module is it.

Precedence -- documented in EXTENSIONS.md Sec 25 (fail-closed ladder) and
Sec 41 (this entry), and positioned against Sec 35 (report_outcome spawn
transport):

  A node-written ``status.json`` is picked up as an override ONLY when its
  content DIVERGES from the ``Outcome`` the handler already returned through
  the Python interface (any of ``status``, ``preferred_label``,
  ``suggested_next_ids``, ``context_updates``, ``notes`` differs). This
  keeps the read-side pickup side-effect-free for the overwhelming common
  case: ``CodergenHandler`` already writes its OWN ``status.json`` mirroring
  its own returned ``Outcome`` as its Sec 4.5 audit-trail step, so
  re-reading an identical file must not retroactively flip ``is_explicit``
  -- doing so would silently reopen the Sec 25 fail-closed goal-gate hole
  for ordinary plain-prose codergen responses. Divergence is exactly the
  signal that something OTHER than the handler's own routine write touched
  the file: the "external tool or agent" scenario Sec 4.5 names.

  Sec 35 governs a DIFFERENT channel (the in-process ``report_outcome``
  tool call, transported through spawn metadata and consulted BEFORE the
  handler returns an ``Outcome`` at all). By the time this module runs,
  any ``report_outcome`` verdict is already folded into ``handler_outcome``
  -- so a node-written ``status.json`` that diverges from it is a STRICTLY
  LATER, OUT-OF-BAND correction, and wins. This module documents that
  ordering explicitly: status.json (filesystem, spec-native, Appendix C) is
  the outermost, last-mile channel; ``report_outcome`` (in-process tool
  call, Sec 35) sits inside it. Both are unambiguous verdict mechanisms per
  Sec 25's taxonomy -- a node/external process directly writing its own
  structured status file is exactly as explicit as a tool's exit code or a
  ``report_outcome`` call.

  A malformed override (invalid JSON, non-object JSON, missing/invalid
  ``outcome`` field, a fresh divergent external ``outcome=skipped``
  (engine-authored only), or a wrong-typed ``suggested_next_ids`` /
  ``context_updates`` / ``preferred_label`` / ``notes``) is NEVER silently
  ignored: it fails the node loudly (``is_explicit=True`` FAIL), regardless
  of what the handler itself returned. A stale SKIPPED audit file is ignored
  at the freshness gate, and a fresh SKIPPED file exactly matching an
  engine-returned SKIPPED outcome remains the routine audit no-op. See
  EXTENSIONS.md Sec 41.
"""

from __future__ import annotations

import json
import logging
import os

from .graph import Node
from .outcome import Outcome, StageStatus

logger = logging.getLogger(__name__)

# Parse every StageStatus value, including "skipped", before divergence
# classification. This is deliberate: the engine's OWN audit writers
# (engine.py: _write_node_status, handlers/codergen.py: _write_status) already
# serialize SKIPPED, and an identical fresh file must remain a no-op rather
# than being misclassified as a malformed external verdict. Freshness is
# checked even earlier, so a stale engine-authored SKIPPED audit file is also
# ignored before parsing.
#
# Worker/external writers have the narrower Appendix C verdict vocabulary
# taught by status_contract.py: success, fail, partial_success, retry. Once a
# fresh candidate is parsed and found to DIVERGE from handler_outcome, a
# candidate SKIPPED is therefore malformed and fails closed. The read order is
# load -> validate envelope -> build candidate -> matching engine-audit no-op ->
# reject divergent SKIPPED -> honor other divergent worker verdicts.
_RECOGNIZED_OUTCOME_VALUES: dict[str, StageStatus] = {s.value: s for s in StageStatus}
_WORKER_OUTCOME_VALUES = frozenset(
    {
        StageStatus.SUCCESS.value,
        StageStatus.FAIL.value,
        StageStatus.PARTIAL_SUCCESS.value,
        StageStatus.RETRY.value,
    }
)


def read_status_override(
    node: Node,
    logs_root: str,
    node_start_wall: float,
    handler_outcome: Outcome,
) -> Outcome | None:
    """Read back a node-written ``status.json``, applying it if it overrides.

    Returns ``None`` when there is nothing to apply: no file, a stale
    (pre-existing) file whose mtime does not postdate ``node_start_wall``,
    or a well-formed file whose content matches ``handler_outcome`` exactly
    (``CodergenHandler``'s own routine audit-trail write -- see module
    docstring).

    Returns an explicit ``FAIL`` ``Outcome`` (``is_explicit=True``) when a
    fresh ``status.json`` exists but is malformed -- a spec Appendix C
    contract violation, never silently ignored. This includes a divergent
    external ``outcome=skipped``: SKIPPED is reserved for engine-authored
    audit state. A stale SKIPPED file is ignored by the freshness rule, and
    a fresh SKIPPED file exactly matching ``handler_outcome`` is the engine's
    routine audit no-op.

    Returns the file-derived ``Outcome`` (``is_explicit=True``) when a
    fresh, well-formed worker outcome (success/fail/partial_success/retry)
    diverges from ``handler_outcome`` -- the node/external tool's explicit
    verdict wins (see module docstring for the precedence rationale).
    """
    status_path = os.path.join(logs_root, node.id, "status.json")
    if not os.path.exists(status_path):
        return None

    try:
        mtime = os.path.getmtime(status_path)
    except OSError:
        return None

    # Freshness floor mirrors must_write.py's convention: strictly greater
    # than the node's execution-start wall clock, so a stale file left over
    # from an earlier iteration/attempt of this same node is never picked
    # up as if it had just been written.
    if mtime <= node_start_wall:
        return None

    try:
        with open(status_path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        return _malformed(node, f"could not read status.json: {exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _malformed(node, f"invalid JSON in status.json: {exc}")

    if not isinstance(data, dict):
        return _malformed(
            node, f"status.json must be a JSON object, got {type(data).__name__}"
        )

    if "outcome" not in data:
        return _malformed(node, "status.json missing required 'outcome' field")

    outcome_value = data["outcome"]
    status = (
        _RECOGNIZED_OUTCOME_VALUES.get(outcome_value)
        if isinstance(outcome_value, str)
        else None
    )
    if status is None:
        return _malformed(
            node,
            "status.json 'outcome' must be one of "
            f"{sorted(_WORKER_OUTCOME_VALUES)}, got {outcome_value!r}",
        )

    suggested_next_ids = data.get("suggested_next_ids")
    if suggested_next_ids is not None and not isinstance(suggested_next_ids, list):
        return _malformed(node, "status.json 'suggested_next_ids' must be a list")
    # Deliberately permissive on ITEM type: EXTENSIONS.md Sec 34 already
    # gives edge_selection.py's _coerce_suggested_id a documented, tested
    # coercion policy for bare int/float entries (an LLM/tool emitting
    # [999] instead of ["999"]). Re-validating item types here would
    # duplicate -- and could conflict with -- that decided policy; this
    # layer only confirms the envelope is a list at all.

    context_updates = data.get("context_updates")
    if context_updates is not None and not isinstance(context_updates, dict):
        return _malformed(node, "status.json 'context_updates' must be an object")

    preferred_label = data.get("preferred_label")
    if preferred_label is not None and not isinstance(preferred_label, str):
        return _malformed(node, "status.json 'preferred_label' must be a string")

    notes = data.get("notes")
    if notes is not None and not isinstance(notes, str):
        return _malformed(node, "status.json 'notes' must be a string")

    candidate = Outcome(
        status=status,
        preferred_label=preferred_label,
        suggested_next_ids=suggested_next_ids,
        context_updates=context_updates,
        notes=notes,
        is_explicit=True,
    )

    # Engine-owned audit compatibility comes first: the engine legitimately
    # writes SKIPPED into status.json, so a fresh file that exactly mirrors a
    # handler-returned SKIPPED outcome is the routine audit no-op. A stale file
    # was already ignored above at the freshness gate.
    if _matches(candidate, handler_outcome):
        return None

    # Appendix C and the spawned-worker instruction contract intentionally
    # limit worker-authored outcomes to success/fail/partial_success/retry.
    # SKIPPED means the engine decided the node did not execute; an external
    # writer cannot assert that state. Reaching this branch proves the fresh
    # file differs from the handler's own outcome/audit record, so treat a
    # divergent SKIPPED as malformed and fail closed rather than honoring it.
    if candidate.status == StageStatus.SKIPPED:
        return _malformed(
            node,
            "status.json 'outcome' value 'skipped' is engine-authored only; "
            f"worker-authored outcomes must be one of {sorted(_WORKER_OUTCOME_VALUES)}",
        )

    logger.info(
        "Node %s: status.json diverges from the handler-returned outcome "
        "(handler=%s, file=%s) -- honoring the node-written verdict per "
        "spec Sec 4.5 / Appendix C (status-file contract).",
        node.id,
        handler_outcome.status.value,
        candidate.status.value,
    )
    return candidate


def _matches(candidate: Outcome, existing: Outcome) -> bool:
    """True when the file-derived candidate carries nothing new vs. existing."""
    return (
        candidate.status == existing.status
        and candidate.preferred_label == existing.preferred_label
        and candidate.suggested_next_ids == existing.suggested_next_ids
        and candidate.context_updates == existing.context_updates
        and candidate.notes == existing.notes
    )


def _malformed(node: Node, reason: str) -> Outcome:
    """A malformed status.json is a loud FAIL -- never a silent no-op.

    ``is_explicit=True``: a broken contract is itself a definitive signal
    (mirrors the ``must_write=`` backstop's fail-closed treatment,
    EXTENSIONS.md Sec 27), not something to soften with a defaulted status.
    """
    return Outcome(
        status=StageStatus.FAIL,
        failure_reason=f"Malformed status.json for node '{node.id}': {reason}",
        notes="status.json contract violation (Appendix C) -- fail-closed",
        is_explicit=True,
    )
