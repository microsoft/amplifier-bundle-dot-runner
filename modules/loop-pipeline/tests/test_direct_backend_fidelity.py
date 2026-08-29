"""Tests for fidelity-aware DirectProviderBackend (H-9).

Validates that DirectProviderBackend respects fidelity modes:
- full: reuses message history between calls with same thread key
- compact/truncate/summary: prepends a preamble to the prompt
"""

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

unified_llm = pytest.importorskip("unified_llm")

# ---------------------------------------------------------------------------
# Provide a minimal amplifier_core stub so the backend's lazy imports work
# in the test environment where amplifier_core may not be installed.
# ---------------------------------------------------------------------------
if "amplifier_core" not in sys.modules:

    @dataclass
    class _StubMessage:
        role: str = "user"
        content: Any = ""
        tool_call_id: str | None = None
        name: str | None = None
        metadata: dict | None = None

    @dataclass
    class _StubChatRequest:
        messages: list = field(default_factory=list)
        tools: list | None = None
        tool_choice: str | None = None
        reasoning_effort: str | None = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage  # type: ignore[attr-defined]
    _stub_core.ChatRequest = _StubChatRequest  # type: ignore[attr-defined]
    sys.modules["amplifier_core"] = _stub_core

    @dataclass
    class _StubToolCallBlock:
        id: str = ""
        name: str = ""
        input: dict = field(default_factory=dict)
        type: str = "tool_call"

    _stub_msg = types.ModuleType("amplifier_core.message_models")
    _stub_msg.ToolCallBlock = _StubToolCallBlock  # type: ignore[attr-defined]
    sys.modules["amplifier_core.message_models"] = _stub_msg

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.outcome import StageStatus

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class _MockUnifiedClient:
    """Mock unified_llm.Client for testing."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0
        self.call_count = 0
        self.requests = []

    async def complete(self, request):
        self.call_count += 1
        self.requests.append(request)
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return _make_text_response("fallback")


def _make_text_response(text):
    return unified_llm.Response(
        id=f"resp-{abs(hash(text)) % 10000}",
        model="test-model",
        provider="test",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


def _make_graph_with_fidelity(node_fidelity="compact"):
    """Build a minimal graph with nodes that have a fidelity attribute."""
    return Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "step1": Node(
                id="step1",
                shape="box",
                prompt="First step",
                attrs={
                    "fidelity": node_fidelity,
                    "llm_provider": "test",
                    "llm_model": "test-model",
                },
            ),
            "step2": Node(
                id="step2",
                shape="box",
                prompt="Second step",
                attrs={
                    "fidelity": node_fidelity,
                    "llm_provider": "test",
                    "llm_model": "test-model",
                },
            ),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="step1"),
            Edge(from_node="step1", to_node="step2"),
            Edge(from_node="step2", to_node="done"),
        ],
    )


@pytest.mark.asyncio
async def test_direct_backend_compact_fidelity_prepends_preamble():
    """With compact fidelity, the prompt should include a preamble after first node."""
    mock_client = _MockUnifiedClient(
        [
            _make_text_response(
                '{"status": "success", "notes": "Step completed successfully"}'
            ),
            _make_text_response('{"status": "success", "notes": "Step 2 done"}'),
        ]
    )
    backend = AmplifierBackend(
        provider=object(),  # truthy sentinel — no longer called
        unified_client=mock_client,
    )
    graph = _make_graph_with_fidelity("compact")
    context = PipelineContext()
    context.set("graph.goal", "test goal")

    edge_to_step1 = graph.edges[0]  # start -> step1

    # First call -- step1
    outcome1 = await backend.run(
        graph.nodes["step1"],
        "First step prompt",
        context,
        incoming_edge=edge_to_step1,
        graph=graph,
    )
    assert outcome1.status == StageStatus.SUCCESS

    edge_to_step2 = graph.edges[1]  # step1 -> step2

    # Second call -- step2 should have preamble with step1's outcome
    outcome2 = await backend.run(
        graph.nodes["step2"],
        "Second step prompt",
        context,
        incoming_edge=edge_to_step2,
        graph=graph,
    )
    assert outcome2.status == StageStatus.SUCCESS

    # Check that the second call's request included preamble content.
    # In compact/truncate modes, generate() is called with prompt= (not messages=),
    # so the Request has a single user message containing the preamble + prompt.
    second_request = mock_client.requests[1]
    user_message = second_request.messages[0].text
    # The preamble should mention the goal and completed stages
    assert "test goal" in user_message or "step1" in user_message


@pytest.mark.asyncio
async def test_direct_backend_truncate_fidelity_minimal_preamble():
    """With truncate fidelity, preamble should be minimal (just goal + run ID)."""
    mock_client = _MockUnifiedClient(
        [
            _make_text_response('{"status": "success", "notes": "Done"}'),
            _make_text_response('{"status": "success", "notes": "Done 2"}'),
        ]
    )
    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
    )
    graph = _make_graph_with_fidelity("truncate")
    context = PipelineContext()
    context.set("graph.goal", "my goal")

    # First node call to populate history
    await backend.run(
        graph.nodes["step1"],
        "Step 1",
        context,
        incoming_edge=graph.edges[0],
        graph=graph,
    )

    # Second node call should have truncate preamble
    await backend.run(
        graph.nodes["step2"],
        "Step 2",
        context,
        incoming_edge=graph.edges[1],
        graph=graph,
    )

    second_request = mock_client.requests[1]
    user_content = second_request.messages[0].text
    assert "my goal" in user_content


@pytest.mark.asyncio
async def test_direct_backend_full_fidelity_reuses_messages():
    """With full fidelity, message history should accumulate across calls."""
    mock_client = _MockUnifiedClient(
        [
            _make_text_response("Response text"),
            _make_text_response("Response 2"),
        ]
    )
    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
    )
    graph = _make_graph_with_fidelity("full")
    context = PipelineContext()

    # First call
    await backend.run(
        graph.nodes["step1"],
        "First prompt",
        context,
        incoming_edge=graph.edges[0],
        graph=graph,
    )

    # Second call should include the first call's messages
    await backend.run(
        graph.nodes["step2"],
        "Second prompt",
        context,
        incoming_edge=graph.edges[1],
        graph=graph,
    )

    second_request = mock_client.requests[1]
    # In full mode, messages from step1 should carry over
    # At minimum: user(first prompt), assistant(response), user(second prompt)
    assert len(second_request.messages) >= 3


@pytest.mark.asyncio
async def test_direct_backend_without_graph_falls_back_gracefully():
    """When graph/edge not provided, backend works like before (no fidelity)."""
    mock_client = _MockUnifiedClient(
        [_make_text_response('{"status": "success", "notes": "ok"}')]
    )
    backend = AmplifierBackend(
        provider=object(),
        unified_client=mock_client,
    )
    context = PipelineContext()

    node = Node(
        id="work",
        shape="box",
        prompt="do work",
        attrs={"llm_provider": "test", "llm_model": "test-model"},
    )
    outcome = await backend.run(node, "do work", context)
    assert outcome.status == StageStatus.SUCCESS
