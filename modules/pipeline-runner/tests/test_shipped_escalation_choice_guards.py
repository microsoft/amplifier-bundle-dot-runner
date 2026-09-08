"""Real, scoped engine/CLI regressions for vendored escalation edge guards.

These control projections retain each shipped graph's real ``escalate`` node
and choice edges but replace downstream targets with exits.  They therefore
exercise routing without a provider, LLM node, or post-gate pipeline behavior.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import unified_llm

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerContext, HandlerRegistry
from amplifier_module_loop_pipeline.interviewer import Answer, QueueInterviewer
from amplifier_module_loop_pipeline.pipeline_events import (
    PIPELINE_NODE_COMPLETE,
    PIPELINE_NODE_START,
)
from amplifier_module_pipeline_runner import cli


REPO = Path(__file__).resolve().parents[3]
GRAPHS = REPO / ".github" / "capsule-pipeline"
CASES = {
    "capsule.dot": (
        ("A", "abandon", "[A] Abandon -- keep the postmortem"),
        ("C", "bump_budget", "[C] Continue -- raise the budget"),
        (
            "K",
            "package",
            "[K] Keep -- accept the capsule as-is, findings and postmortem attached",
        ),
    ),
    "feature-capsule.dot": (
        ("A", "abandon", "[A] Abandon -- keep the postmortem"),
        ("C", "bump_budget", "[C] Continue -- raise the budget"),
        (
            "K",
            "package",
            "[K] Keep -- accept the capsule as-is, findings, questions, and postmortem attached",
        ),
    ),
    "task-runner.dot": (
        ("A", "abandon", "[A] Abandon — keep the postmortem"),
        ("C", "bump_budget", "[C] Continue — raise the budget"),
    ),
}
PARAMS = {
    "issue_file": "/tmp/issue",
    "criteria_file": "/tmp/criteria",
    "target_dir": "/tmp",
    "base_sha": "test-base",
    "later_commit": "",
    "uplift_dir": "/tmp/uplift",
    "capsule_out": "/tmp/capsule-out",
    "max_iterations": "8",
    "gate_time_ceiling": "5",
    "max_duration": "30m",
    "task_file": "/tmp/task.md",
    "branch": "test",
}


class _Events:
    def __init__(self):
        self.items = []

    async def emit(self, name, data):
        self.items.append((name, data))

    def completed(self):
        return [
            data["node_id"]
            for name, data in self.items
            if name == PIPELINE_NODE_COMPLETE
        ]


class _NoLLMBackend:
    async def run(self, *args, **kwargs):  # pragma: no cover - forbidden path
        raise AssertionError("a scoped escalation projection must not execute an LLM")


class _CapturingQueue(QueueInterviewer):
    def __init__(self, answer):
        super().__init__([answer])
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        return super().ask(question)


def _source(name):
    return parse_dot((GRAPHS / name).read_text(encoding="utf-8"), params=PARAMS)


def _projection(source):
    choices = source.outgoing_edges("escalate")
    return Graph(
        name=f"{source.name}-escalation-projection",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "escalate": source.nodes["escalate"],
            **{
                edge.to_node: Node(
                    id=edge.to_node,
                    shape="parallelogram",
                    attrs={"tool_command": "true"},
                )
                for edge in choices
            },
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="escalate"),
            *choices,
            *(Edge(from_node=edge.to_node, to_node="done") for edge in choices),
        ],
    )


def _projection_dot(source, *, guarded=True):
    gate, choices = source.nodes["escalate"], source.outgoing_edges("escalate")
    lines = [
        "digraph ScopedEscalation {",
        "start [shape=Mdiamond]",
        f"escalate [shape=hexagon, label={json.dumps(gate.label)}, prompt={json.dumps(gate.prompt)}]",
    ]
    lines.extend(
        f'{edge.to_node} [shape=parallelogram, tool_command="true"]' for edge in choices
    )
    lines.append("done [shape=Msquare]")
    lines.append("start -> escalate")
    for edge in choices:
        attrs = [f"label={json.dumps(edge.label)}"]
        if guarded:
            attrs.insert(0, f"condition={json.dumps(edge.condition)}")
        lines.append(f"escalate -> {edge.to_node} [{', '.join(attrs)}]")
        lines.append(f"{edge.to_node} -> done")
    return "\n".join([*lines, "}"])


@pytest.mark.parametrize(
    ("graph_name", "key", "target", "label"),
    [
        (graph_name, *choice)
        for graph_name, choices in CASES.items()
        for choice in choices
    ],
)
def test_all_eight_shipped_choices_route_with_canonical_metadata(
    tmp_path, graph_name, key, target, label
):
    """Actual gate node/edges preserve original option labels, order, and targets."""
    source, expected = _source(graph_name), CASES[graph_name]
    assert [
        (edge.condition, edge.to_node, edge.label)
        for edge in source.outgoing_edges("escalate")
    ] == [
        (
            f"outcome=success && context.human.gate.selected={choice_key}",
            choice_target,
            choice_label,
        )
        for choice_key, choice_target, choice_label in expected
    ]
    hooks, interviewer = _Events(), _CapturingQueue(Answer(value=key))
    engine = PipelineEngine(
        graph=_projection(source),
        context=PipelineContext(),
        handler_registry=HandlerRegistry(
            HandlerContext(
                backend=_NoLLMBackend(), interviewer=interviewer, hooks=hooks
            )
        ),
        logs_root=str(tmp_path / "logs"),
        hooks=hooks,
    )
    outcome = asyncio.run(engine.run())
    assert outcome.status.value == "success", outcome.failure_reason or outcome.notes
    assert engine.context.get("human.gate.selected") == key
    assert engine.context.get("human.gate.label") == label
    assert [(item.key, item.label) for item in interviewer.questions[0].options] == [
        (choice_key, choice_label) for choice_key, _, choice_label in expected
    ]
    assert hooks.completed() == ["start", "escalate", target]
    assert [
        data["node_id"] for name, data in hooks.items if name == PIPELINE_NODE_START
    ] == ["start", "escalate", target]


@pytest.mark.parametrize("graph_name", CASES)
@pytest.mark.parametrize("payload_kind", ("valid", "generic"))
def test_real_cli_fail_policy_stops_at_each_guarded_escalation(
    monkeypatch, tmp_path, graph_name, payload_kind
):
    """Six real ``cmd_run -> run_pipeline`` controls; runner results are not mocked."""
    source = _source(graph_name)
    dot_path, logs_root, workdir = (
        tmp_path / graph_name,
        tmp_path / "logs",
        tmp_path / "work",
    )
    dot_path.write_text(_projection_dot(source), encoding="utf-8")
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unused-test-credential")
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "amplifier-home"))

    async def no_provider_call(*args, **kwargs):
        raise AssertionError("the no-LLM projection must not call a provider")

    monkeypatch.setattr(unified_llm.Client, "complete", no_provider_call)
    argv = [
        "run",
        str(dot_path),
        "--worker",
        "llm-direct",
        "--on-human-gate",
        "fail",
        "--cwd",
        str(workdir),
        "--logs-root",
        str(logs_root),
        "--param",
        "human.gate.selected=A",
    ]
    if payload_kind == "valid":
        argv.extend(
            [
                "--param",
                "escalation.question=Which documented outcome applies?",
                "--param",
                "escalation.evidence=AC-1 conflicts with observed behavior.",
                "--param",
                "escalation.next_action=Record the decision and re-run.",
            ]
        )
    assert cli.cmd_run(cli.build_parser().parse_args(argv)) == 1
    checkpoint = json.loads((logs_root / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["completed_nodes"] == ["start", "escalate"]
    assert checkpoint["context"]["human.gate.selected"] == "A"
    assert set(checkpoint["node_outcomes"]) == {"start", "escalate"}
    assert not (workdir / ".ai" / "postmortem").exists()
    assert not any(
        node.llm_provider or node.llm_model
        for node in _projection(source).nodes.values()
    )
    artifact = (
        workdir
        / ".ai"
        / (
            "escalation.md"
            if payload_kind == "valid"
            else "findings/invalid-escalation.md"
        )
    )
    assert artifact.is_file()


@pytest.mark.parametrize("graph_name", CASES)
def test_unguarded_source_main_baseline_routes_stale_a_to_first_abandon(
    monkeypatch, tmp_path, graph_name
):
    """RED control: source/main's former unguarded edges returned CLI 0 via [A]."""
    source = _source(graph_name)
    dot_path, logs_root, workdir = (
        tmp_path / graph_name,
        tmp_path / "logs",
        tmp_path / "work",
    )
    dot_path.write_text(_projection_dot(source, guarded=False), encoding="utf-8")
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unused-test-credential")
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "amplifier-home"))
    assert (
        cli.cmd_run(
            cli.build_parser().parse_args(
                [
                    "run",
                    str(dot_path),
                    "--worker",
                    "llm-direct",
                    "--on-human-gate",
                    "fail",
                    "--cwd",
                    str(workdir),
                    "--logs-root",
                    str(logs_root),
                    "--param",
                    "human.gate.selected=A",
                ]
            )
        )
        == 0
    )
    checkpoint = json.loads((logs_root / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["completed_nodes"] == ["start", "escalate", "abandon"]
