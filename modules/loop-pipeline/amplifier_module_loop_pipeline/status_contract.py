"""Status-file contract seam for SPAWNED workers (WAVE 4).

Spec basis (canonical, verbatim -- see contracts/external/attractor-spec-canonical.md):

    Sec 4.5 (line 709): "Status file: The handler writes ``status.json`` in
    the stage directory with the Outcome fields serialized as JSON. This
    file serves as an audit trail and enables the status-file contract:
    external tools or agents can write ``status.json`` to communicate
    outcomes back to the engine."

    Appendix C (lines 2053-2078): the ``status.json`` envelope shape
    (``outcome``, ``preferred_label``, ``suggested_next_ids``,
    ``context_updates``, ``notes``).

``read_status_override`` (``status_file.py``, EXTENSIONS.md Sec 41) is the
READ side of this contract: it re-reads a node's stage-directory
``status.json`` after the handler returns and honors it when it diverges
from what the handler's Python-level ``Outcome`` already carried. That read
side works for ANY writer -- a tool node's exit code, a shell script, or a
spawned agent with plain filesystem access. What was missing (WAVE 4, per
maintainer ruling 2026-08-29 retconning ``report_outcome``) was the WRITE
side for a *spawned child*: the child has no way to know the exact absolute
path unless the parent tells it.

This module owns exactly that seam, mirroring ``worker_observability.py``'s
``current_worker_sessions_dir`` pattern: a ``ContextVar`` (not a Protocol
parameter) so the ``CodergenBackend`` interface -- spec Sec 4.5's fixed
boundary, ``run(node, prompt, context) -> String | Outcome`` -- is never
touched, and so every existing conforming backend/test-double keeps working
unmodified. ``handlers/codergen.py`` sets this ContextVar (to the node's own
stage-directory ``status.json`` path, always absolute) for the duration of
each ``backend.run()`` call; ``backend.py``'s spawn path
(``_run_with_spawn``) reads it and, when set, appends the status-file
contract block to the instruction handed to the spawned child -- BEFORE the
child ever runs. This applies uniformly to every spawn-capable worker
(``loop-agent`` and the ``loop-amplifier-agent`` adapter): both receive
whatever instruction text ``backend.py`` builds, so injecting the contract
once, in one place, teaches both without either module needing its own
copy of the path-resolution logic.

The ``direct`` worker (in-process tool loop, ``workers/direct_worker.py``)
does not read this ContextVar: it already satisfies the spec's FIRST
channel today (it returns an ``Outcome`` in-process; see the WAVE 4 PR body
"Direct worker" note) and does not need a filesystem hand-off.
"""

from __future__ import annotations

from contextvars import ContextVar

#: Absolute path to the current node's stage-directory ``status.json``, set
#: by the codergen handler around each backend call. ``None`` (the default)
#: means "not inside a node execution that has a stage directory" -- the
#: spawn path must treat that as "nothing to inject" (never fabricate or
#: guess a relative path).
current_node_status_path: ContextVar[str | None] = ContextVar(
    "current_node_status_path", default=None
)


#: Rendered once and reused; the only variable part is the absolute path.
_STATUS_FILE_CONTRACT_TEMPLATE = """

---
## Status File Contract (spec Sec 4.5 / Appendix C)

When you have finished this task, write your outcome to this EXACT
absolute file path, exactly once:

    {status_path}

Use your normal file-write tool (it is not a special tool call). The file
must be a JSON object shaped like this:

    {{
      "outcome": "success | fail | partial_success | retry",
      "preferred_label": "optional -- which edge label to follow",
      "suggested_next_ids": ["optional", "explicit next node ids"],
      "context_updates": {{"optional": "key-value pairs to merge into pipeline context"}},
      "notes": "optional -- human-readable summary"
    }}

Only "outcome" is required; every other field is optional. This is the
authoritative way to report your outcome -- write the file once, when you
are truly finished, and let it reflect your real verdict.
"""


def build_status_file_contract(status_path: str) -> str:
    """Render the status-file contract block for a spawned child's instruction.

    ``status_path`` should already be absolute (``current_node_status_path``
    is always set with an absolute path -- see ``handlers/codergen.py``).
    """
    return _STATUS_FILE_CONTRACT_TEMPLATE.format(status_path=status_path)
