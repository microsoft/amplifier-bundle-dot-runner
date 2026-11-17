"""LANE D RED-proofs: `llm_provider`-alone default-model resolution.

Maintainer ruling: `llm_provider` is an nlspec-level node property (spec
Sec2.6/Appendix A, Sec8.5 resolution order) -- honor it SPEC-FIRST so a
community .dot author who writes `llm_provider=openai` (no `llm_model`) is
never surprised by a crash. Model CHOICE stays on stylesheets (the spec's
own hook, Sec8); this file covers the missing rung 4 ("Handler/system
default") that previously always failed loud on the direct path
(`_resolve_model`, backend.py) regardless of which provider was set.

RED-proof (verified against this branch's pre-fix commit): every
`test_provider_alone_*` test below raises ``ValueError`` on main (the
literal bug this program fixes) and passes after the fix. See
`test_profile_no_default_model.py` for the unit-level (`_resolve_model`)
equivalent of the same RED-proof; this file additionally proves the
PRECEDENCE ladder end-to-end through `DirectWorker.run()` (the real
`direct`-worker production seam a community .dot pipeline actually uses),
and that a malformed/unknown provider still fails loud.

Precedence proven, in order (spec Sec8.5 / Appendix A):
  1. explicit node llm_model            (test_explicit_node_model_wins_over_everything)
  2. model_stylesheet rule              (test_stylesheet_model_wins_over_provider_default)
  3. graph-level default                (not implemented by any layer today --
                                          out of scope, unchanged by this program)
  4. NEW per-provider default (this program, replacing fail-loud)
                                         (test_provider_alone_anthropic_resolves_default,
                                          test_provider_alone_openai_resolves_default,
                                          test_provider_alone_gemini_resolves_default)
  (malformed/unknown provider stays loud: test_unknown_provider_still_fails_loud)
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_loop_pipeline import backend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.stylesheet import apply_stylesheet, parse_stylesheet
from amplifier_module_loop_pipeline.workers import DirectWorker


class _RecordingClient:
    """Hermetic unified_llm client recording the concrete model actually used."""

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> Any:
        import unified_llm

        self.requests.append(request)
        return unified_llm.Response(
            id="r",
            model=request.model,
            provider="test",
            message=unified_llm.Message.assistant(self.reply),
            finish_reason=unified_llm.FinishReason(reason="stop"),
            usage=unified_llm.Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


@pytest.fixture(autouse=True)
def _clear_cache():
    backend._MODEL_RESOLUTION_CACHE.clear()
    yield
    backend._MODEL_RESOLUTION_CACHE.clear()


async def _run(node: Node, *, resolver_stub) -> tuple[str, Any]:
    """Run `node` through the real DirectWorker, monkeypatching only the
    LIVE resolver call (unified_llm.resolve_latest_for) -- everything else
    (precedence, caching, stable_only threading) is exercised for real."""
    from unittest import mock

    client = _RecordingClient()
    worker = DirectWorker(unified_client=client)
    context = PipelineContext()

    with mock.patch("unified_llm.resolve_latest_for", resolver_stub):
        _resolved_model, outcome = await worker.run(
            node, "do the task", context, replayed_history=[]
        )

    assert outcome.status == StageStatus.SUCCESS
    assert client.requests, "the direct worker never called the provider"
    return client.requests[-1].model, outcome


# ---------------------------------------------------------------------------
# RED-proof: llm_provider-alone works for each documented provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_alone_anthropic_resolves_default():
    """A node that sets ONLY llm_provider=anthropic (no llm_model) must
    resolve -- not raise -- via the documented default family token."""
    seen = {}

    async def _stub(provider, pattern, *, stable_only):
        seen["provider"], seen["pattern"], seen["stable_only"] = (
            provider,
            pattern,
            stable_only,
        )
        return "claude-sonnet-4-5"

    node = Node(
        id="n", shape="box", prompt="do work", attrs={"llm_provider": "anthropic"}
    )
    model, _outcome = await _run(node, resolver_stub=_stub)

    assert model == "claude-sonnet-4-5"
    assert seen["provider"] == "anthropic"
    assert seen["pattern"] == "*sonnet*"
    assert seen["stable_only"] is True


@pytest.mark.asyncio
async def test_provider_alone_openai_resolves_default():
    """A node that sets ONLY llm_provider=openai (no llm_model) must
    resolve -- not raise -- via the documented default family glob, and
    must NOT be silently handed the OLD hardcoded 'gpt-4o'."""
    seen = {}

    async def _stub(provider, pattern, *, stable_only):
        seen["provider"], seen["pattern"], seen["stable_only"] = (
            provider,
            pattern,
            stable_only,
        )
        return "gpt-5.2"

    node = Node(id="n", shape="box", prompt="do work", attrs={"llm_provider": "openai"})
    model, _outcome = await _run(node, resolver_stub=_stub)

    assert model == "gpt-5.2"
    assert seen["provider"] == "openai"
    assert seen["pattern"] == "gpt-5.*[0-9]"
    assert seen["stable_only"] is True


@pytest.mark.asyncio
async def test_provider_alone_gemini_resolves_default():
    """A node that sets ONLY llm_provider=gemini (no llm_model) must
    resolve -- not raise -- via the documented default family glob, WITH
    stable_only=False (the provider's own current flagship is itself
    preview-named -- see backend.py's _PROVIDER_DEFAULT_MODEL_PATTERN)."""
    seen = {}

    async def _stub(provider, pattern, *, stable_only):
        seen["provider"], seen["pattern"], seen["stable_only"] = (
            provider,
            pattern,
            stable_only,
        )
        return "gemini-3.1-pro-preview"

    node = Node(id="n", shape="box", prompt="do work", attrs={"llm_provider": "gemini"})
    model, _outcome = await _run(node, resolver_stub=_stub)

    assert model == "gemini-3.1-pro-preview"
    assert seen["provider"] == "gemini"
    assert seen["pattern"] == "gemini-3*pro*"
    assert seen["stable_only"] is False


# ---------------------------------------------------------------------------
# Precedence: each higher rung must win over the new rung-4 default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_node_model_wins_over_everything():
    """Rung 1 (explicit node llm_model) beats the rung-4 provider default --
    the resolver must never even be consulted for a concrete id."""

    async def _boom(*a, **k):
        raise AssertionError("resolver must NOT be called when llm_model is explicit")

    node = Node(
        id="n",
        shape="box",
        prompt="do work",
        attrs={"llm_provider": "openai", "llm_model": "gpt-4.1-mini"},
    )
    model, _outcome = await _run(node, resolver_stub=_boom)
    assert model == "gpt-4.1-mini"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_id", ["gpt-6.2-sol", "gpt-6.2-astra", "gpt-6.1-sol", "gpt-6-astra"]
)
async def test_explicit_new_and_old_pins_do_not_resolve(model_id):
    async def _boom(*a, **k):
        raise AssertionError("explicit pins must not list or resolve models")

    node = Node(id="n", attrs={"llm_provider": "openai", "llm_model": model_id})
    model, _ = await _run(node, resolver_stub=_boom)
    assert model == model_id


@pytest.mark.asyncio
@pytest.mark.parametrize("pin", ["gpt-6.2-sol", "gpt-6.2-astra", "gpt-6.1-sol", "gpt-5.2"])
async def test_direct_worker_responses_wire_tool_continuation(pin):
    """Real worker/generate/adapter/SDK; only HTTP is an offline fixture."""
    import copy
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx
    import openai
    import unified_llm
    from unified_llm.adapters.openai import OpenAIAdapter

    wire = []

    def handle(request):
        assert request.method == "POST"  # explicit IDs never list
        assert request.url.path == "/v1/responses"
        wire.append(json.loads(request.content))
        output = (
            [{
                "type": "function_call", "call_id": "call_1",
                "name": "lookup", "arguments": '{"key":"value"}',
            }]
            if len(wire) == 1
            else [{
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": '{"outcome":"success"}'}],
            }]
        )
        return httpx.Response(200, json={
            "id": f"resp_{len(wire)}", "model": pin, "status": "completed",
            "output": output,
            "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
        })

    adapter = OpenAIAdapter(api_key="offline-test-key")
    await adapter.close()
    adapter._client = openai.AsyncOpenAI(
        api_key="offline-test-key", base_url="https://offline.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    client = unified_llm.Client(providers={"openai": adapter})
    execute = AsyncMock(return_value=SimpleNamespace(output="tool-result"))
    tool = SimpleNamespace(
        name="lookup", description="Look up a key",
        parameters={"type": "object", "properties": {"key": {"type": "string"}}},
        execute=execute,
    )
    node = Node(id="n", attrs={
        "llm_provider": "openai", "llm_model": pin, "reasoning_effort": "medium",
    })
    before = copy.deepcopy(node)
    try:
        _, outcome = await DirectWorker(
            unified_client=client, tools={"lookup": tool}
        ).run(node, "work", PipelineContext(), [])
    finally:
        await client.close()
    assert outcome.status == StageStatus.SUCCESS
    assert node == before
    execute.assert_awaited_once_with({"key": "value"})
    assert len(wire) == 2
    for body in wire:
        assert body["model"] == pin
        assert body["tools"][0]["name"] == "lookup"
        assert body["reasoning"] == {"effort": "medium"}
        assert not {"temperature", "top_p", "top_logprobs"} & body.keys()
    assert wire[1]["input"][: len(wire[0]["input"])] == wire[0]["input"]
    assert wire[1]["input"][-2]["type"] == "function_call"
    assert wire[1]["input"][-2]["call_id"] == "call_1"
    assert wire[1]["input"][-1] == {
        "type": "function_call_output", "call_id": "call_1", "output": "tool-result",
    }


@pytest.mark.asyncio
async def test_stylesheet_model_wins_over_provider_default():
    """Rung 2 (model_stylesheet rule) resolves llm_model onto the node
    BEFORE backend/worker code ever runs -- so a stylesheet-set model also
    beats the rung-4 provider default, with no resolver call."""

    async def _boom(*a, **k):
        raise AssertionError(
            "resolver must NOT be called for a stylesheet-set concrete id"
        )

    node = Node(id="n", shape="box", prompt="do work", attrs={"llm_provider": "openai"})
    graph = Graph(name="g", nodes={"n": node}, edges=[])
    rules = parse_stylesheet("* { llm_model: gpt-4.1; }")
    apply_stylesheet(graph, rules)

    assert node.llm_model == "gpt-4.1"  # stylesheet transform applied rung 2
    model, _outcome = await _run(node, resolver_stub=_boom)
    assert model == "gpt-4.1"


# ---------------------------------------------------------------------------
# Malformed/unknown provider stays loud -- never a silent guess
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_provider_still_fails_loud():
    """A provider with no documented default must still raise ValueError
    naming the provider -- the new rung 4 never silently guesses."""

    async def _boom(*a, **k):
        raise AssertionError("resolver must NOT be called for an unresolvable provider")

    client = _RecordingClient()
    worker = DirectWorker(unified_client=client)
    context = PipelineContext()
    node = Node(
        id="n",
        shape="box",
        prompt="do work",
        attrs={"llm_provider": "not-a-real-provider"},
    )

    from unittest import mock

    with (
        mock.patch("unified_llm.resolve_latest_for", _boom),
        pytest.raises(ValueError, match="not-a-real-provider"),
    ):
        await worker.run(node, "do the task", context, replayed_history=[])
