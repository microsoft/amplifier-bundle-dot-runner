"""Real, scoped engine/CLI regressions for vendored escalation edge guards.

These control projections retain each shipped graph's real ``escalate`` node
and choice edges but replace downstream targets with exits.  They therefore
exercise routing without a provider, LLM node, or post-gate pipeline behavior.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import sys
import tomllib
from uuid import uuid4

import pytest
import unified_llm
from amplifier_foundation import Bundle
from amplifier_foundation.bundle._prepared import PreparedBundle
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

from amplifier_module_pipeline_runner import cli, runner

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


@contextmanager
def _isolated_cli_dependencies(tmp_path):
    """Replace only context/CI loading; prepare and mount real local modules.

    Every scope owns a unique package *and submodule*, so changing homes never
    collides with context-simple's cached imports. Only our imports are removed.
    """
    module_name = f"context-escalation-test-{uuid4().hex}"
    package_name = f"amplifier_module_{module_name.replace('-', '_')}"
    source = tmp_path / "context-source"
    package = source / package_name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        '__amplifier_module_type__ = "context"\n'
        "from .store import Context\n\n"
        "async def mount(coordinator, config):\n"
        '    await coordinator.mount("context", Context())\n',
        encoding="utf-8",
    )
    (package / "store.py").write_text(
        """class Context:
    def __init__(self):
        self.messages = []

    async def add_message(self, message):
        self.messages.append(message)

    async def get_messages_for_request(self, token_budget, provider=None):
        return self.messages.copy()

    async def get_messages(self):
        return self.messages.copy()

    async def set_messages(self, messages):
        self.messages = messages.copy()

    async def clear(self):
        self.messages.clear()
""",
        encoding="utf-8",
    )
    base = Bundle(
        name="escalation-test-base",
        session={"context": {"module": module_name, "source": str(source)}},
    )
    original_path = sys.path.copy()

    async def no_provider_call(*args, **kwargs):
        raise AssertionError("the no-LLM projection must not call a provider")

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("AMPLIFIER_HOME", str(tmp_path / "amplifier-home"))
        patch.setenv("ANTHROPIC_API_KEY", "unused-test-credential")
        patch.delenv("ATTRACTOR_INSTALL_DEPS", raising=False)
        patch.chdir(tmp_path)
        patch.setattr(runner, "_bare_base_bundle", lambda: base)
        patch.setattr(
            runner,
            "_context_intelligence_overlay",
            lambda: Bundle(name="escalation-test-no-ci"),
        )
        patch.setattr(
            runner,
            "_PREPARED_SOURCE_IDENTITIES",
            runner._PREPARED_SOURCE_IDENTITIES.copy(),
        )
        patch.setattr(unified_llm.Client, "complete", no_provider_call)
        try:
            yield module_name, package_name, source
        finally:
            for name in tuple(sys.modules):
                if name == package_name or name.startswith(f"{package_name}."):
                    del sys.modules[name]
            sys.path[:] = original_path


@pytest.fixture
def _cli_dependencies(tmp_path):
    with _isolated_cli_dependencies(tmp_path) as dependency:
        yield dependency


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


@pytest.mark.usefixtures("_cli_dependencies")
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


@pytest.mark.usefixtures("_cli_dependencies")
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


@pytest.mark.parametrize("home_present", (False, True), ids=("absent-home", "set-home"))
@pytest.mark.parametrize("raise_after_run", (False, True), ids=("normal", "exception"))
def test_adjacent_cli_dependency_scopes_restore_process_state(
    tmp_path, monkeypatch, home_present, raise_after_run
):
    """Two real prepare/mount/CLI/teardown cycles preserve pre-existing state."""
    if home_present:
        monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "original-home"))
    else:
        monkeypatch.delenv("AMPLIFIER_HOME", raising=False)
    home = os.environ.get("AMPLIFIER_HOME")
    cwd, path_object, path = Path.cwd(), sys.path, sys.path.copy()
    identities = runner._PREPARED_SOURCE_IDENTITIES
    identity_contents = identities.copy()
    seams = (
        runner._bare_base_bundle,
        runner._context_intelligence_overlay,
        unified_llm.Client.complete,
        Bundle.prepare,
        PreparedBundle.create_session,
        runner._BARE_BASE_BUNDLE,
    )
    modules = sys.modules.copy()
    module_bytes = {
        Path(module.__file__): hashlib.sha256(
            Path(module.__file__).read_bytes()
        ).digest()
        for name, module in modules.items()
        if name.startswith(("amplifier_", "unified_llm"))
        and getattr(module, "__file__", "").endswith(".py")
    }
    packages = []
    for index in range(2):
        cycle = tmp_path / f"cycle-{index}"
        cycle.mkdir()
        expected_exit = (
            pytest.raises(RuntimeError, match="exercise exceptional teardown")
            if raise_after_run
            else nullcontext()
        )
        with expected_exit:
            with _isolated_cli_dependencies(cycle) as (
                module_name,
                package_name,
                source,
            ):
                assert package_name not in modules
                assert package_name not in packages
                packages.append(package_name)
                assert runner._PREPARED_SOURCE_IDENTITIES is not identities
                assert runner._PREPARED_SOURCE_IDENTITIES == identity_contents
                assert os.environ["AMPLIFIER_HOME"] == str(cycle / "amplifier-home")
                assert Path.cwd() == cycle
                dot = _projection_dot(_source("capsule.dot"), guarded=False)

                async def mount_and_close():
                    prepared = await runner._build_prepared(
                        dot,
                        cycle / "mount-logs",
                        params=None,
                        profiles=None,
                        worker="llm-direct",
                    )
                    assert prepared.resolver.resolve(module_name).resolve() == source
                    assert prepared.mount_plan.get("hooks", []) == []
                    session = await prepared.create_session(session_cwd=cycle)
                    async with session:
                        context = session.coordinator.get("context")
                        assert (
                            type(context)
                            is sys.modules[f"{package_name}.store"].Context
                        )
                        await context.add_message({"role": "user", "content": "probe"})
                        assert await context.get_messages() == [
                            {"role": "user", "content": "probe"}
                        ]

                asyncio.run(mount_and_close())
                assert str(source) in runner._PREPARED_SOURCE_IDENTITIES
                assert identities == identity_contents
                assert str(source) in sys.path
                dot_path, logs = cycle / "cycle.dot", cycle / "cli-logs"
                dot_path.write_text(dot, encoding="utf-8")
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
                                str(cycle),
                                "--logs-root",
                                str(logs),
                                "--param",
                                "human.gate.selected=A",
                            ]
                        )
                    )
                    == 0
                )
                checkpoint = json.loads((logs / "checkpoint.json").read_text())
                assert checkpoint["completed_nodes"] == ["start", "escalate", "abandon"]
                if raise_after_run:
                    raise RuntimeError("exercise exceptional teardown")

        assert os.environ.get("AMPLIFIER_HOME") == home
        assert ("AMPLIFIER_HOME" in os.environ) is home_present
        assert Path.cwd() == cwd
        assert sys.path is path_object
        assert sys.path == path
        assert runner._PREPARED_SOURCE_IDENTITIES is identities
        assert identities == identity_contents
        restored_seams = (
            runner._bare_base_bundle,
            runner._context_intelligence_overlay,
            unified_llm.Client.complete,
            Bundle.prepare,
            PreparedBundle.create_session,
            runner._BARE_BASE_BUNDLE,
        )
        assert all(
            actual is original for actual, original in zip(restored_seams, seams)
        )
        assert not any(
            name == package_name or name.startswith(f"{package_name}.")
            for name in sys.modules
        )
        assert all(sys.modules.get(name) is module for name, module in modules.items())
        assert all(
            hashlib.sha256(file.read_bytes()).digest() == digest
            for file, digest in module_bytes.items()
        )


def test_remote_smoke_dependency_is_declared_in_runner_dev_group():
    """A same-repo source mapping alone does not declare a test dependency."""
    metadata = tomllib.loads(
        (REPO / "modules/pipeline-runner/pyproject.toml").read_text()
    )
    assert "amplifier-module-remote-source" in metadata["dependency-groups"]["dev"]
    assert metadata["tool"]["uv"]["sources"]["amplifier-module-remote-source"] == {
        "path": "../remote-source"
    }
