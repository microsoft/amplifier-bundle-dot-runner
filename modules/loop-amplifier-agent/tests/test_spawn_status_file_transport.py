"""Two real public workers under the real graph engine and status-file parent.

The outer spawn carrier is doubled, not Foundation qualification. Hosted turns,
selection, tools, graph traversal, history bookkeeping, and status reader are real.
Manager DTU must additionally qualify the installed Foundation spawn path.
"""

import json
import os
import secrets
from types import SimpleNamespace

import pytest
from amplifier_agent import AgentOptions  # noqa: F401 -- never skip a missing binding
from amplifier_module_loop_amplifier_agent import AmplifierAgentOrchestrator
from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import StageStatus

from ._fakes import CapturingHooks, FakeContextManager

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"), reason="live Anthropic credentials absent"
)


@pytest.mark.asyncio
async def test_two_node_graph_actual_selection_recall_and_external_status(tmp_path):
    fact = secrets.token_hex(12)
    work, logs = tmp_path / "work", tmp_path / "logs"
    work.mkdir()
    models = [
        os.getenv("AA_LIVE_MODEL_A", "claude-sonnet-5-5"),
        os.getenv("AA_LIVE_MODEL_B", "claude-haiku-4-5"),
    ]
    assert models[0] != models[1]
    assert not logs.resolve().is_relative_to(work.resolve())

    class Parent:
        def __init__(self):
            self.session = SimpleNamespace(config={})
            self.config = {
                "agents": {
                    "public": {
                        "session": {"orchestrator": {"module": "loop-amplifier-agent"}}
                    }
                }
            }

        def get_capability(self, name):
            return self.spawn if name == "session.spawn" else None

        async def spawn(self, **kwargs):
            # Mirrors outer preference promotion; ordinary agent_configs stays
            # outer bookkeeping and is NOT injected into the hosted runtime.
            preference = kwargs["provider_preferences"][0]
            child = SimpleNamespace(
                config={
                    "providers": [
                        {
                            "module": "provider-anthropic",
                            "config": {"default_model": preference.model},
                        }
                    ]
                },
                get_capability=lambda _: str(work),
            )
            config = dict(kwargs["orchestrator_config"])
            hooks = CapturingHooks()
            output = await AmplifierAgentOrchestrator(child, config).execute(
                kwargs["instruction"],
                FakeContextManager(kwargs.get("parent_messages")),
                {},
                {},
                hooks,
            )
            return {"output": output, "session_id": "outer-adapter", **hooks.completion}

    for name, thread in (("continuity", "same"), ("control", "different")):
        run_logs = logs / name
        nodes = {
            "start": Node("start", shape="Mdiamond"),
            "seed": Node(
                "seed",
                prompt=f"Remember code {fact}. Reply OK. Write success status.",
                attrs={
                    "llm_provider": "anthropic",
                    "llm_model": models[0],
                    "fidelity": "full",
                    "thread_id": "same",
                },
            ),
            "recall": Node(
                "recall",
                prompt="From conversation alone, return the remembered code on the first line, "
                "or UNKNOWN if absent. Do not read/search/list files or use bash. "
                "Use write_file to write status with outcome fail and preferred_label Zulu.",
                attrs={
                    "llm_provider": "anthropic",
                    "llm_model": models[1],
                    "fidelity": "full",
                    "thread_id": thread,
                },
            ),
            "alpha": Node("alpha", shape="diamond"),
            "zulu": Node("zulu", shape="diamond"),
            "done": Node("done", shape="Msquare"),
        }
        graph = Graph(
            name,
            nodes=nodes,
            edges=[
                Edge("start", "seed"),
                Edge("seed", "recall"),
                Edge("recall", "alpha", label="Alpha"),
                Edge("recall", "zulu", label="Zulu"),
                Edge("alpha", "done"),
                Edge("zulu", "done"),
            ],
        )
        backend = AmplifierBackend(Parent(), profiles={"anthropic": "public"})
        engine = PipelineEngine(
            graph,
            PipelineContext(),
            HandlerRegistry(HandlerContext(backend=backend)),
            str(run_logs),
        )
        await engine.run()
        assert (run_logs / "recall" / "response.md").is_file(), (
            f"Real recall worker did not produce a response: {engine.node_outcomes}"
        )
        reply = (run_logs / "recall" / "response.md").read_text()
        assert (fact in reply.splitlines()[0]) == (name == "continuity")
        assert fact not in (run_logs / "recall" / "prompt.md").read_text()
        assert engine.node_outcomes["recall"].status == StageStatus.FAIL
        assert engine.node_outcomes["recall"].is_explicit
        assert (
            "zulu" in engine.completed_nodes and "alpha" not in engine.completed_nodes
        )
        ids = []
        for node, expected_model in zip(("seed", "recall"), models):
            stage = run_logs / node
            status = json.loads((stage / "status.json").read_text())
            ids.append(status["session_id"])
            events = [
                json.loads(line)
                for line in (stage / "sessions" / ids[-1] / "events.jsonl")
                .read_text()
                .splitlines()
            ]
            started = next(
                e["data"]["payload"]
                for e in events
                if e["event"] == "amplifier-agent:turn_started"
            )
            assert started["primary_actual"]["model"].startswith(expected_model)
            if node == "recall":
                calls = [
                    e["data"]["payload"]["call"]
                    for e in events
                    if e["event"] == "amplifier-agent:tool_call"
                ]
                assert not any(
                    c["name"] in {"read_file", "glob", "grep", "bash"} for c in calls
                )
                writes = [c for c in calls if c["name"] in {"write_file", "edit_file"}]
                assert any(
                    str(stage / "status.json") in json.dumps(c["arguments"])
                    for c in writes
                )
        assert ids[0] != ids[1]
