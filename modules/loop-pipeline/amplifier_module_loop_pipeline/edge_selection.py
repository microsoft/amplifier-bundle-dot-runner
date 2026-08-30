"""Five-step edge selection algorithm for pipeline routing.

After a node completes, the engine selects the next edge using a
deterministic priority order:

1. Condition-matching edges (evaluated against outcome + context)
2. Preferred label match (from outcome.preferred_label)
3. Suggested next IDs (from outcome.suggested_next_ids)
4. Highest weight among unconditional edges
5. Lexical tiebreak on target node ID

Spec coverage: ESEL-001–010, Section 3.3.
"""

from __future__ import annotations

import logging
import re

from .conditions import evaluate_condition
from .context import PipelineContext
from .graph import Edge, Graph
from .outcome import Outcome, StageStatus

logger = logging.getLogger(__name__)


def select_edge(
    node_id: str,
    outcome: Outcome,
    context: PipelineContext,
    graph: Graph,
) -> Edge | None:
    """Select the next edge from a node's outgoing edges.

    Returns None if no outgoing edges exist.
    """
    edges = graph.outgoing_edges(node_id)
    if not edges:
        return None

    # Step 1: Condition-matching edges
    condition_matched = [
        e
        for e in edges
        if e.condition and evaluate_condition(e.condition, outcome, context)
    ]
    if condition_matched:
        return _best_by_weight_then_lexical(condition_matched)

    # Step 2: Preferred label match
    # Spec §3.3 Step 2: only UNCONDITIONAL edges are eligible — a conditional
    # edge whose condition failed in Step 1 must NOT be selected here by label.
    if outcome.preferred_label:
        norm_pref = _normalize_label(outcome.preferred_label)
        for e in edges:
            if not e.condition and e.label and _normalize_label(e.label) == norm_pref:
                return e

    # Step 3: Suggested next IDs
    # Spec §3.3 Step 3: only UNCONDITIONAL edges are eligible (same rationale).
    #
    # Coercion policy (node IDs are strings by contract, spec DOT-001..017):
    # a suggested ID that arrives as a bare int/float (e.g. an LLM emitting
    # `"suggested_next_ids": [3]` instead of `["3"]` in JSON) is normalized to
    # its canonical string form before comparison -- `_coerce_suggested_id`
    # below. Genuinely malformed shapes (dict, list, bool, None, ...) are
    # REJECTED loudly (a warning naming the offending value) rather than
    # silently coerced into something plausible; they are simply skipped so
    # one bad entry in the list doesn't prevent the others from being tried.
    if outcome.suggested_next_ids:
        for raw_id in outcome.suggested_next_ids:
            suggested_id = _coerce_suggested_id(raw_id)
            if suggested_id is None:
                continue
            for e in edges:
                if not e.condition and e.to_node == suggested_id:
                    return e

    # Step 4 & 5: Weight with lexical tiebreak (unconditional edges only)
    #
    # EXTENSIONS.md Sec16 REMOVED (2026-08-30, maintainer ruling, branch
    # feat/extensions-rip-3): the runs_on=/continue_on_fail= fail-fast gate
    # that used to live here (blocking unconditional-edge traversal on a
    # FAIL outcome unless the target declared runs_on=always/failure) is
    # deleted -- canonical Sec3.3 step 4 behavior is restored verbatim: the
    # best unconditional edge is followed regardless of outcome status. A
    # pipeline author who wants fail-fast routing uses the spec-intended
    # pattern instead: an explicit `condition="outcome=fail"` edge (matched
    # in Step 1 above), plus canonical Sec3.7's own no-matching-edge
    # terminal failure when a FAIL node has no matching edge at all
    # (ATX-11, unaffected by this removal). See MIGRATION.md.
    unconditional = [e for e in edges if not e.condition]
    if unconditional:
        return _best_by_weight_then_lexical(unconditional)

    # Spec §3.3 final step: RETURN NONE (no unconditional edges exist and
    # no conditional edge matched). The engine halts this branch with a
    # FAIL outcome (or ATX-11's no_matching_edge terminal failure).
    return None


def select_all_matching_edges(
    node_id: str,
    outcome: Outcome,
    context: PipelineContext,
    graph: Graph,
) -> list[Edge]:
    """Return ALL condition-matching edges from a node's outgoing edges.

    Unlike select_edge() which returns the single best edge (spec §3.3),
    this returns every edge whose condition evaluates to True.

    Note (T0-4): this function is NO LONGER called from the engine's main
    dispatch path. The multi-match fan-out dialect it supported has been
    retired in favor of spec-conformant single-edge selection via
    select_edge(). It is retained as a test/analysis utility: exercised by
    its own unit tests in test_edge_selection.py and used by
    test_dot_parser.py to assert that shipped graphs have at most one
    simultaneously-matching conditional edge.

    Returns an empty list if no edges have matching conditions.
    """
    edges = graph.outgoing_edges(node_id)
    if not edges:
        return []

    # All condition-matching edges
    condition_matched = [
        e
        for e in edges
        if e.condition and evaluate_condition(e.condition, outcome, context)
    ]
    return condition_matched


def _best_by_weight_then_lexical(edges: list[Edge]) -> Edge:
    """Sort by weight descending, then target node ID ascending."""
    return sorted(edges, key=lambda e: (-e.weight, e.to_node))[0]


def _coerce_suggested_id(value: object) -> str | None:
    """Normalize one ``suggested_next_ids`` entry to a node-ID string.

    Node IDs are strings by contract (spec DOT-001..017). A ``suggested_next_ids``
    entry that reaches this far may have travelled through raw JSON (from a
    ``report_outcome`` tool call, a pure-JSON verdict, or an embedded-verdict
    recovery -- see ``backend.py``) where an LLM emitted a bare number instead
    of a quoted string, e.g. ``[3]`` instead of ``["3"]``. Since ``"3" == 3``
    is always ``False`` in Python, comparing raw values would silently defeat
    this routing step for every such entry.

    Policy:
      - ``str`` -- returned as-is (the expected, contractual shape).
      - ``int`` (excluding ``bool``, which is a ``int`` subclass in Python but
        never a sane node-ID representation) -- coerced to its canonical
        string form, e.g. ``3 -> "3"``. This is the one recoverable mismatch:
        a plausible, unambiguous stand-in for a quoted ID.
      - Anything else (``bool``, ``float``, ``dict``, ``list``, ``None``, ...)
        is a genuinely malformed shape, not a type slip. It is REJECTED
        loudly -- logged as a warning naming the offending value and its
        type -- rather than silently coerced into something plausible (a
        float's string form is ambiguous, e.g. ``3.0`` vs node ``"3"`` vs
        node ``"3.0"``; nested/compound shapes have no sane string form at
        all). Returns ``None`` so the caller skips this entry and tries the
        next one in the list rather than aborting the whole routing decision.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        logger.warning(
            "suggested_next_ids entry %r is a bool, not a valid node-ID "
            "representation; skipping this entry.",
            value,
        )
        return None
    if isinstance(value, int):
        return str(value)
    logger.warning(
        "suggested_next_ids entry %r (type %s) is not a string or int and "
        "cannot be safely coerced to a node ID; skipping this entry.",
        value,
        type(value).__name__,
    )
    return None


# Accelerator key patterns: "[Y] Label", "Y) Label", "Y - Label"
_ACCELERATOR_RE = re.compile(r"^\[.\]\s*|^.\)\s*|^.\s*-\s*")


def _normalize_label(label: str) -> str:
    """Normalize a label for matching: lowercase, strip accelerators, trim."""
    s = label.strip().lower()
    s = _ACCELERATOR_RE.sub("", s)
    return s.strip()
