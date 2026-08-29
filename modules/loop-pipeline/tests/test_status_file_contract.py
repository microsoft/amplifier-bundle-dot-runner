"""Status-file contract (canonical spec Sec 4.5 + Appendix C) -- read side.

Spec basis (specs/canonical/attractor-spec-canonical.md):

  Sec 4.5, line 709: "Status file: The handler writes ``status.json`` in
  the stage directory with the Outcome fields serialized as JSON. This
  file serves as an audit trail and enables the status-file contract:
  external tools or agents can write ``status.json`` to communicate
  outcomes back to the engine."

  Appendix C, lines 2053-2078: "Each non-terminal node writes a
  ``status.json`` file in its stage directory. This file drives routing
  decisions and provides an audit trail." Envelope: ``outcome``,
  ``preferred_label``, ``suggested_next_ids``, ``context_updates``,
  ``notes``.

Prior to EXTENSIONS.md Sec 41, the engine only ever WROTE status.json
(engine.py: _write_node_status, handlers/codergen.py: _write_status) --
nothing ever read one back, despite the spec's own words requiring a read
direction ("communicate outcomes BACK TO THE ENGINE"). These tests are
RED on the pre-Sec-41 engine (a divergent, node-written status.json was
silently ignored -- the handler's own computed Outcome always won) and
GREEN after amplifier_module_loop_pipeline.status_file.read_status_override
is wired into retry.py (all node types) and handlers/codergen.py (the
backend.run()-internal write path).

Coverage:
  SF-001  tool node (parallelogram): externally-written status.json
          contradicting the exit-code-derived outcome is honored.
  SF-002  codergen node: an external write during backend.run() (a raw
          string return) contradicting the handler's own default outcome
          is honored, not clobbered by the handler's own audit-trail write.
  SF-003  malformed status.json (invalid JSON) is a loud FAIL, never
          silently ignored -- tool node.
  SF-004  malformed status.json (missing required 'outcome' field) is a
          loud FAIL -- codergen node.
  SF-005  a status.json that merely MATCHES what the handler already
          returned is a no-op (CodergenHandler's own routine audit-trail
          write must not retroactively flip is_explicit).
  SF-006  goal_gate interaction: a goal_gate=true node whose backend
          returns plain prose (fail-closed RETRY under EXTENSIONS.md
          Sec 25) is satisfied when status.json explicitly asserts
          success -- status.json is an explicit verdict channel too.
  SF-007  goal_gate interaction (regression guard): a goal_gate=true node
          with plain prose and NO external status.json override still
          fails closed (Sec 25 is unaffected by this change).
  SF-008  unit-level coverage of read_status_override(): stale mtime, no
          file, wrong-typed fields.
"""

from __future__ import annotations

import json
import os
import shlex

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.status_file import read_status_override
from amplifier_module_loop_pipeline.validation import validate_or_raise


def _make_engine(
    dot_source: str, backend: object | None, logs_root: str
) -> PipelineEngine:
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
    )


# ---------------------------------------------------------------------------
# SF-001: tool node -- external status.json overrides the exit-code outcome
# ---------------------------------------------------------------------------

_TOOL_DOT = """
digraph ToolStatusOverride {{
    graph [goal="test"]
    start [shape=Mdiamond, label="Start"]
    check [shape=parallelogram, label="Check", tool_command="{cmd}"]
    ok    [shape=parallelogram, label="OK", tool_command="true"]
    bad   [shape=parallelogram, label="Bad", tool_command="true"]
    exit  [shape=Msquare, label="Exit"]

    start -> check
    check -> ok  [label="OK"]
    check -> bad [label="Bad"]
    ok -> exit
    bad -> exit
}}
"""


@pytest.mark.asyncio
async def test_sf001_tool_node_status_json_override_wins(tmp_path):
    """SF-001: tool exits 0 (SUCCESS) but writes a contradicting status.json.

    Before Sec 41: the engine records SUCCESS (exit code only); the file is
    never read; edge selection follows the 'ok' branch.
    After Sec 41: the node-written status.json (outcome=fail,
    preferred_label naming the 'bad' edge) wins; the node is recorded FAIL,
    is_explicit=True, and routing follows the 'bad' branch.
    """
    logs_root = str(tmp_path / "logs")
    status_path = os.path.join(logs_root, "check", "status.json")
    payload = json.dumps(
        {
            "outcome": "fail",
            "preferred_label": "Bad",
            "notes": "external tool asserted failure",
        }
    )
    # Payload is written to a side file first and copied into place by the
    # tool_command -- embedding raw JSON (with double quotes) directly in a
    # DOT double-quoted attribute value would need DOT-level escaping, which
    # is orthogonal to what this test is proving.
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(payload)
    cmd = f"mkdir -p {shlex.quote(os.path.dirname(status_path))} && cp {shlex.quote(str(payload_file))} {shlex.quote(status_path)} && echo done"

    engine = _make_engine(_TOOL_DOT.format(cmd=cmd), backend=None, logs_root=logs_root)
    await engine.run()

    outcome = engine.node_outcomes["check"]
    assert outcome.status == StageStatus.FAIL, (
        f"status.json override must win over the exit-code outcome, got {outcome.status!r}"
    )
    assert outcome.is_explicit is True
    assert outcome.preferred_label == "Bad"
    assert "bad" in engine.completed_nodes, "routing must follow the overridden outcome"
    assert "ok" not in engine.completed_nodes, (
        "the exit-code-derived branch must not run"
    )


@pytest.mark.asyncio
async def test_sf001b_tool_node_no_status_json_unaffected(tmp_path):
    """Control: a tool node that writes NO status.json behaves exactly as before.

    Routing between the two equally-weighted unconditional 'ok'/'bad' edges
    is ambiguous by construction (pre-existing lexical-tiebreak behavior,
    unrelated to this change) when no preferred_label is set -- so this test
    only asserts on the node's own outcome, not which branch was taken.
    """
    logs_root = str(tmp_path / "logs")
    engine = _make_engine(
        _TOOL_DOT.format(cmd="echo hello"), backend=None, logs_root=logs_root
    )
    await engine.run()

    outcome = engine.node_outcomes["check"]
    assert outcome.status == StageStatus.SUCCESS
    assert outcome.is_explicit is True
    assert outcome.preferred_label is None


# ---------------------------------------------------------------------------
# SF-002: codergen node -- external write during backend.run() wins
# ---------------------------------------------------------------------------


class _ExternalWriterBackend:
    """Simulates an external tool/agent that writes status.json directly.

    This is the canonical Sec 4.5 scenario: "external tools or agents can
    write status.json to communicate outcomes back to the engine." The
    backend returns a plain STRING (not an Outcome) -- the ordinary
    CodergenBackend contract path -- but, as a side effect of its own
    execution, writes a contradicting status.json straight to the node's
    stage directory before returning.
    """

    def __init__(
        self, logs_root: str, payload: dict, target_node_id: str = "implement"
    ) -> None:
        self._logs_root = logs_root
        self._payload = payload
        self._target_node_id = target_node_id

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str:
        if node.id != self._target_node_id:
            # Downstream routing nodes (reached via the overridden verdict)
            # -- ordinary plain-prose completion, nothing to override here.
            return "downstream node, nothing special"
        stage_dir = os.path.join(self._logs_root, node.id)
        os.makedirs(stage_dir, exist_ok=True)
        with open(os.path.join(stage_dir, "status.json"), "w") as f:
            json.dump(self._payload, f)
        return "All done, looks great!"  # cheerful prose the handler would default to SUCCESS


_CODERGEN_DOT = """
digraph CodergenStatusOverride {
    graph [goal="test"]
    start     [shape=Mdiamond, label="Start"]
    implement [label="Implement", prompt="do the thing"]
    ok        [label="OK"]
    bad       [label="Bad"]
    exit      [shape=Msquare, label="Exit"]

    start -> implement
    implement -> ok  [label="OK"]
    implement -> bad [label="Bad"]
    ok -> exit
    bad -> exit
}
"""


@pytest.mark.asyncio
async def test_sf002_codergen_node_status_json_override_wins(tmp_path):
    """SF-002: backend returns plain prose (-> defaulted SUCCESS) but has
    already written a contradicting status.json directly (outcome=fail).

    Before Sec 41: CodergenHandler's own Sec 4.5 audit-trail write
    (_write_status) unconditionally clobbers the external write with its
    own defaulted-SUCCESS record; the override is invisible.
    After Sec 41: the divergence is caught INSIDE the handler, before its
    own write -- the external verdict survives and wins.
    """
    logs_root = str(tmp_path / "logs")
    backend = _ExternalWriterBackend(
        logs_root,
        {
            "outcome": "fail",
            "preferred_label": "Bad",
            "notes": "external agent verdict",
        },
    )
    engine = _make_engine(_CODERGEN_DOT, backend=backend, logs_root=logs_root)
    await engine.run()

    outcome = engine.node_outcomes["implement"]
    assert outcome.status == StageStatus.FAIL, (
        f"external status.json write must override the handler default, got {outcome.status!r}"
    )
    assert outcome.is_explicit is True
    assert outcome.preferred_label == "Bad"
    assert "bad" in engine.completed_nodes
    assert "ok" not in engine.completed_nodes

    # The on-disk record must reflect the override, not the clobbered default.
    with open(os.path.join(logs_root, "implement", "status.json")) as f:
        on_disk = json.load(f)
    assert on_disk["outcome"] == "fail"


# ---------------------------------------------------------------------------
# SF-003 / SF-004: malformed status.json -> loud FAIL, never silent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sf003_tool_node_malformed_status_json_loud_fail(tmp_path):
    """SF-003: invalid JSON in status.json fails the node loudly."""
    logs_root = str(tmp_path / "logs")
    status_path = os.path.join(logs_root, "check", "status.json")
    payload_file = tmp_path / "bad_payload.txt"
    payload_file.write_text("not valid json {")
    cmd = f"mkdir -p {shlex.quote(os.path.dirname(status_path))} && cp {shlex.quote(str(payload_file))} {shlex.quote(status_path)} && echo done"

    engine = _make_engine(_TOOL_DOT.format(cmd=cmd), backend=None, logs_root=logs_root)
    await engine.run()

    outcome = engine.node_outcomes["check"]
    assert outcome.status == StageStatus.FAIL
    assert outcome.is_explicit is True
    assert "Malformed status.json" in (outcome.failure_reason or "")


@pytest.mark.asyncio
async def test_sf004_codergen_node_malformed_status_json_loud_fail(tmp_path):
    """SF-004: status.json missing the required 'outcome' field fails loudly."""
    logs_root = str(tmp_path / "logs")

    class _BadWriterBackend:
        async def run(
            self, node, prompt, context, incoming_edge=None, graph=None
        ) -> str:
            if node.id != "implement":
                return "downstream node, nothing special"
            stage_dir = os.path.join(logs_root, node.id)
            os.makedirs(stage_dir, exist_ok=True)
            with open(os.path.join(stage_dir, "status.json"), "w") as f:
                json.dump({"notes": "forgot the outcome field"}, f)
            return "prose response"

    engine = _make_engine(
        _CODERGEN_DOT, backend=_BadWriterBackend(), logs_root=logs_root
    )
    await engine.run()

    outcome = engine.node_outcomes["implement"]
    assert outcome.status == StageStatus.FAIL
    assert outcome.is_explicit is True
    assert "Malformed status.json" in (outcome.failure_reason or "")
    assert "outcome" in (outcome.failure_reason or "")


# ---------------------------------------------------------------------------
# SF-005: a matching status.json is a no-op (protects Sec 25 is_explicit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sf005_matching_status_json_is_noop_not_promoted_to_explicit(tmp_path):
    """SF-005: CodergenHandler's own routine write of status.json (mirroring
    its own defaulted, non-goal_gate SUCCESS) must not be treated as an
    override -- is_explicit must stay False, exactly as spec Sec 4.5
    (unconditional-SUCCESS wrap) already prescribes for non-goal_gate nodes.
    """
    logs_root = str(tmp_path / "logs")

    class _PlainProseBackend:
        async def run(
            self, node, prompt, context, incoming_edge=None, graph=None
        ) -> str:
            return "just some ordinary prose, nothing structured"

    engine = _make_engine(
        _CODERGEN_DOT, backend=_PlainProseBackend(), logs_root=logs_root
    )
    await engine.run()

    outcome = engine.node_outcomes["implement"]
    assert outcome.status == StageStatus.SUCCESS
    assert outcome.is_explicit is False, (
        "a non-divergent, handler-authored status.json must not flip is_explicit"
    )


# ---------------------------------------------------------------------------
# SF-006 / SF-007: goal_gate interaction (EXTENSIONS.md Sec 25)
# ---------------------------------------------------------------------------

_GOAL_GATE_DOT = """
digraph GoalGateStatusOverride {
    graph [goal="test"]
    start     [shape=Mdiamond, label="Start"]
    implement [label="Implement", prompt="do the thing", goal_gate=true]
    exit      [shape=Msquare, label="Exit"]

    start -> implement
    implement -> exit [condition="outcome=success"]
}
"""


@pytest.mark.asyncio
async def test_sf006_goal_gate_satisfied_via_status_json_override(tmp_path):
    """SF-006: goal_gate=true node, plain-prose backend response (which
    Sec 25 would otherwise fail-close to RETRY-then-FAIL), but the backend
    also wrote an explicit status.json (outcome=success). The node-written
    verdict satisfies the gate: status.json is exactly as explicit a
    verdict channel as report_outcome / JSON (Sec 25's taxonomy), extended
    by Sec 41 to include the status-file contract.
    """
    logs_root = str(tmp_path / "logs")

    class _ExplicitFileBackend:
        async def run(
            self, node, prompt, context, incoming_edge=None, graph=None
        ) -> str:
            stage_dir = os.path.join(logs_root, node.id)
            os.makedirs(stage_dir, exist_ok=True)
            with open(os.path.join(stage_dir, "status.json"), "w") as f:
                json.dump({"outcome": "success", "notes": "explicit file verdict"}, f)
            return "some narrative text with no embedded JSON verdict at all"

    engine = _make_engine(
        _GOAL_GATE_DOT, backend=_ExplicitFileBackend(), logs_root=logs_root
    )
    final = await engine.run()

    assert final.status == StageStatus.SUCCESS, (
        f"goal_gate must be satisfied by the node-written status.json verdict, got {final.status!r}"
    )
    gate_outcome = engine.node_outcomes["implement"]
    assert gate_outcome.is_explicit is True


@pytest.mark.asyncio
async def test_sf007_goal_gate_still_fails_closed_without_override(tmp_path):
    """SF-007 (regression guard): same goal_gate node, plain prose, but NO
    external status.json write at all -- Sec 25's fail-closed RETRY (then
    FAIL, since this fixture has no retry_target) must still apply. This
    change must not reopen the incident Sec 25 closed.
    """
    logs_root = str(tmp_path / "logs")

    class _PlainProseBackend:
        async def run(
            self, node, prompt, context, incoming_edge=None, graph=None
        ) -> str:
            return "NOT CONVERGED, just prose, no verdict"

    engine = _make_engine(
        _GOAL_GATE_DOT, backend=_PlainProseBackend(), logs_root=logs_root
    )
    final = await engine.run()

    assert final.status != StageStatus.SUCCESS, (
        "goal_gate must NOT be satisfied by a defaulted plain-prose response "
        "(EXTENSIONS.md Sec 25 fail-closed contract)"
    )


# ---------------------------------------------------------------------------
# SF-008: unit-level coverage of read_status_override()
# ---------------------------------------------------------------------------


def _node(node_id: str = "n") -> Node:
    return Node(id=node_id, attrs={})


def test_sf008a_no_file_returns_none(tmp_path):
    handler_outcome = Outcome(status=StageStatus.SUCCESS)
    result = read_status_override(_node(), str(tmp_path), 0.0, handler_outcome)
    assert result is None


def test_sf008b_stale_file_returns_none(tmp_path):
    node = _node("n")
    stage_dir = tmp_path / "n"
    stage_dir.mkdir()
    status_path = stage_dir / "status.json"
    status_path.write_text(json.dumps({"outcome": "fail"}))
    # node_start_wall AFTER the file's mtime -> stale, must be ignored
    future_floor = os.path.getmtime(status_path) + 3600
    handler_outcome = Outcome(status=StageStatus.SUCCESS)
    result = read_status_override(node, str(tmp_path), future_floor, handler_outcome)
    assert result is None


def test_sf008c_wrong_typed_suggested_next_ids_is_malformed(tmp_path):
    node = _node("n")
    stage_dir = tmp_path / "n"
    stage_dir.mkdir()
    status_path = stage_dir / "status.json"
    status_path.write_text(
        json.dumps({"outcome": "success", "suggested_next_ids": "not-a-list"})
    )
    handler_outcome = Outcome(status=StageStatus.FAIL)
    result = read_status_override(node, str(tmp_path), 0.0, handler_outcome)
    assert result is not None
    assert result.status == StageStatus.FAIL
    assert result.is_explicit is True
    assert "suggested_next_ids" in (result.failure_reason or "")


def test_sf008d_invalid_outcome_enum_value_is_malformed(tmp_path):
    node = _node("n")
    stage_dir = tmp_path / "n"
    stage_dir.mkdir()
    status_path = stage_dir / "status.json"
    status_path.write_text(json.dumps({"outcome": "bogus-not-a-real-status"}))
    handler_outcome = Outcome(status=StageStatus.SUCCESS)
    result = read_status_override(node, str(tmp_path), 0.0, handler_outcome)
    assert result is not None
    assert result.status == StageStatus.FAIL
    assert result.is_explicit is True


def test_sf008e_skipped_is_a_recognized_value_not_malformed(tmp_path):
    """ "skipped" is accepted on read (the engine's own writers legitimately
    serialize it), even though Appendix C's illustrative comment only
    enumerates success/retry/fail/partial_success. A handler-authored
    status.json reporting SKIPPED, matching what the handler itself
    returned, must be a no-op -- not a malformed-file FAIL.
    """
    node = _node("n")
    stage_dir = tmp_path / "n"
    stage_dir.mkdir()
    status_path = stage_dir / "status.json"
    status_path.write_text(json.dumps({"outcome": "skipped"}))
    handler_outcome = Outcome(status=StageStatus.SKIPPED)
    result = read_status_override(node, str(tmp_path), 0.0, handler_outcome)
    assert result is None, (
        "a matching SKIPPED status.json must be a no-op, not malformed"
    )


# ---------------------------------------------------------------------------
# SF-009: spawn path -- backend injects the absolute status.json path into
# the child's instruction (WAVE 4); a spawned child that writes a
# contradicting status.json wins, exactly like the direct/tool paths above.
# ---------------------------------------------------------------------------

import re
from types import SimpleNamespace

from amplifier_module_loop_pipeline.backend import AmplifierBackend


class _SpawnStatusFileCoordinator:
    """Minimal coordinator exposing ``session.spawn``, capturing the exact
    instruction text the real ``AmplifierBackend._run_with_spawn`` builds.

    ``on_spawn`` receives the full ``spawn_kwargs`` dict for every spawn
    call, so it can parse the injected absolute status.json path out of
    ``spawn_kwargs["instruction"]`` -- the WAVE 4 load-bearing behavior: a
    spawned child (loop-agent OR loop-amplifier-agent) has no way to know
    that path except by being told, in its instruction, by the backend.
    """

    def __init__(self, on_spawn) -> None:
        self._on_spawn = on_spawn
        self.session = SimpleNamespace()
        # A non-loop-pipeline orchestrator module avoids backend.py's
        # recursion guard (ValueError: "child would inherit or re-enter
        # loop-pipeline") -- any real spawn worker name is fine here since
        # the actual orchestrator is never invoked (spawn_fn is faked).
        self.config: dict = {
            "agents": {
                "attractor-anthropic": {
                    "session": {"orchestrator": {"module": "loop-agent"}}
                }
            }
        }

    def get_capability(self, name: str):
        return self._spawn_fn if name == "session.spawn" else None

    async def _spawn_fn(self, **kwargs):
        return await self._on_spawn(kwargs)


_STATUS_PATH_RE = re.compile(r"^\s{4}(/\S+status\.json)\s*$", re.MULTILINE)


@pytest.mark.asyncio
async def test_sf009_spawn_node_status_json_override_wins(tmp_path):
    """SF-009 (WAVE 4): a SPAWNED child -- no in-process report_outcome call,
    no metadata channel at all -- writes a contradicting status.json to the
    EXACT absolute path the backend told it, extracted from its own
    instruction text. The engine honors the file, exactly like the tool
    (SF-001) and codergen-string (SF-002) paths.

    RED without WAVE 4's `status_contract.py` injection: pre-WAVE-4 main
    never puts an absolute status.json path into the spawn instruction at
    all, so `_STATUS_PATH_RE` would find nothing and this test would fail
    at the "path was injected" assertion below, before ever reaching the
    override-wins assertions -- proving the injection is what makes a
    spawned child's status-file contract possible in the first place.
    """
    logs_root = str(tmp_path / "logs")
    seen_instructions: list[str] = []

    async def on_spawn(kwargs):
        instruction = kwargs["instruction"]
        seen_instructions.append(instruction)
        match = _STATUS_PATH_RE.search(instruction)
        assert match is not None, (
            "spawn instruction never carried an absolute status.json path -- "
            f"status_contract.py injection is missing. instruction={instruction!r}"
        )
        status_path = match.group(1)
        assert os.path.isabs(status_path)
        assert status_path == os.path.abspath(
            os.path.join(logs_root, "implement", "status.json")
        )
        os.makedirs(os.path.dirname(status_path), exist_ok=True)
        with open(status_path, "w") as f:
            json.dump(
                {
                    "outcome": "fail",
                    "preferred_label": "Bad",
                    "notes": "spawned child asserted failure via status.json",
                },
                f,
            )
        # A real spawned child with no in-process verdict tool: plain
        # cheerful prose, empty metadata -- exactly what a hosted
        # loop-agent/loop-amplifier-agent child yields when its only
        # explicit channel is the status file.
        return {
            "output": "All done, looks great!",
            "status": "success",
            "session_id": "child-1",
            "metadata": {},
        }

    backend = AmplifierBackend(
        coordinator=_SpawnStatusFileCoordinator(on_spawn),
        profiles={"anthropic": "attractor-anthropic"},
    )
    engine = _make_engine(_CODERGEN_DOT, backend=backend, logs_root=logs_root)
    await engine.run()

    outcome = engine.node_outcomes["implement"]
    assert outcome.status == StageStatus.FAIL, (
        f"status.json override must win over the spawn's own prose/lifecycle "
        f"outcome, got {outcome.status!r}"
    )
    assert outcome.is_explicit is True
    assert outcome.preferred_label == "Bad"
    assert "bad" in engine.completed_nodes
    assert "ok" not in engine.completed_nodes

    # The instruction sent to the child must still carry the ORIGINAL prompt
    # text too -- the contract is appended, never a replacement.
    assert "do the thing" in seen_instructions[0]


# ---------------------------------------------------------------------------
# SF-010 / SF-011: compat window -- Sec 35's metadata.report_outcome keeps
# working for the spawn path; when BOTH channels are present, status.json
# (the strictly later, out-of-band, filesystem channel) wins (Sec 41).
# ---------------------------------------------------------------------------




