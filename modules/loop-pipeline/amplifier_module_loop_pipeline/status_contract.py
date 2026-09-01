"""Status-file contract seam for SPAWNED workers (WAVE 4).

Spec basis (canonical, verbatim -- see specs/canonical/attractor-spec-canonical.md):

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

WAVE 4b -- host-authorization metadata (in addition to the instruction text).
A spawned child's *instruction* telling it to write a file is not the same
thing as that child's write-tool actually being allowed to write it: a host
application that sandboxes a spawned session's filesystem tools to its own
project workspace (rather than this engine's coordination directory) has no
way to grant access to *just* the one status.json path without knowing it.
``resolve_spawn_status_file_path`` computes that exact path and validates
that it is contained beneath the current engine-provided ``logs_root``
boundary before ``backend.py`` attaches it to ``session.spawn`` as
``status_file_path`` metadata. This module only computes and validates the
value; it does not grant, request, or configure any tool permission itself.
A host that acts on the metadata MUST independently validate it against the
host's own fixed, trusted coordination root before granting access: the
engine's current ``logs_root`` may itself be a nested/scoped stage path and
is not a substitute for the host's trust boundary. See this function's own
docstring for the engine-side validation and the host application's docs
for how (or whether) it acts on the metadata.
"""

from __future__ import annotations

import os
from contextvars import ContextVar

#: Absolute path to the current node's stage-directory ``status.json``, set
#: by the codergen handler around each backend call. ``None`` (the default)
#: means "not inside a node execution that has a stage directory" -- the
#: spawn path must treat that as "nothing to inject" (never fabricate or
#: guess a relative path).
current_node_status_path: ContextVar[str | None] = ContextVar(
    "current_node_status_path", default=None
)

#: Absolute path to the current node's ``logs_root`` (the engine's own
#: coordination-directory boundary the current node's stage directory is
#: nested beneath), set by the codergen handler alongside
#: ``current_node_status_path`` around each backend call. ``None`` means the
#: same as above: "not inside a node execution that has a stage directory."
#:
#: This exists ONLY to let ``resolve_spawn_status_file_path`` below prove
#: that ``current_node_status_path`` is actually contained beneath the
#: engine's own coordination boundary before that path is exported as
#: spawn-visible metadata -- a host application (e.g. a platform embedding
#: this engine) may use that metadata to narrowly authorize a spawned
#: child's write access to exactly one file. It changes nothing about the
#: instruction text injected by ``build_status_file_contract`` (that
#: continues to use ``current_node_status_path`` unconditionally, unchanged
#: from WAVE 4) and grants nothing on its own -- see the module docstring
#: and ``resolve_spawn_status_file_path``'s own docstring.
current_node_logs_root: ContextVar[str | None] = ContextVar(
    "current_node_logs_root", default=None
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


def resolve_spawn_status_file_path() -> str | None:
    """Return the current node's status.json path, validated for export as
    ``session.spawn`` metadata (WAVE 4b) -- or ``None`` when there is nothing
    safe to export.

    This is a VALIDATION-AND-EXPORT helper only. It does not authorize
    anything by itself: it hands a host application (e.g. a platform
    embedding this engine) a path it can choose to narrowly authorize a
    spawned child's write-tool for -- see the module docstring's "WAVE 4b"
    section. Whether, and how, the host acts on this metadata is entirely
    its own decision. A host that acts on it MUST independently validate the
    path against the host's own fixed, trusted coordination root before
    granting access; ``current_node_logs_root`` is an engine-provided,
    per-execution boundary and may itself be a nested/scoped stage path, so
    containment beneath it alone does not establish host trust. A host that
    ignores the metadata is otherwise unaffected, provided its
    ``session.spawn`` callback accepts either the optional
    ``status_file_path`` keyword or unknown ``**kwargs``; a strict callback
    must add the optional parameter. The instruction-text contract
    (``build_status_file_contract``, appended to the spawn instruction
    unconditionally whenever ``current_node_status_path`` is set) is
    unchanged and stands on its own.

    The returned value, when not ``None``, is guaranteed to be:

      - identical, byte-for-byte, to ``current_node_status_path.get()`` --
        this function never rewrites, re-derives, or "fixes" the path, it
        only accepts or refuses it as-is;
      - absolute and already lexically normalized (``os.path.normpath(p) ==
        p``) -- refuses a path that would need normalization, rather than
        silently normalizing an untrusted value and trusting the result;
      - named exactly ``status.json``;
      - contained STRICTLY BENEATH (not equal to) ``current_node_logs_root``
        -- the engine's own coordination-directory boundary for this node,
        set by ``handlers/codergen.py`` alongside
        ``current_node_status_path``.

    Any violation of the above -- including a programmatically constructed
    ``Node.id`` containing ``..`` segments or an absolute path, either of
    which can make ``os.path.join(logs_root, node.id, ...)`` resolve outside
    ``logs_root`` -- degrades to ``None``, exactly like "no current status
    path." The current DOT parser accepts only its restricted identifier
    grammar here; this check protects the unrestricted programmatic
    ``Graph``/``Node`` construction seam and future producers. This never
    raises: the metadata channel is a best-effort addition on top of a
    fully-functional instruction-text channel, so a validation failure here
    must never fail the node itself.
    """
    path = current_node_status_path.get()
    boundary = current_node_logs_root.get()
    if path is None or boundary is None:
        return None

    if not os.path.isabs(path) or not os.path.isabs(boundary):
        return None

    # Refuse anything that isn't ALREADY normalized -- normalizing an
    # untrusted value and then trusting the normalized form would silently
    # accept e.g. "<boundary>/../../etc/status.json" by collapsing it before
    # the boundary check ever ran. `current_node_status_path` is always set
    # via `os.path.abspath(...)` (handlers/codergen.py), which already
    # normalizes -- so a conforming caller always passes this check; only a
    # tampered or buggy caller would not.
    if os.path.normpath(path) != path or os.path.normpath(boundary) != boundary:
        return None

    if os.path.basename(path) != "status.json":
        return None

    try:
        common = os.path.commonpath([boundary, path])
    except ValueError:
        # E.g. different drives on Windows, or otherwise incomparable paths.
        return None
    if common != boundary or path == boundary:
        return None

    return path
