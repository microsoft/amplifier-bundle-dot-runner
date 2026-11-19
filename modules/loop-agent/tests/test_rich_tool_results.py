"""Actual loop boundary with canonical Core2 results and real hook dispatch.

Core1 compatibility is tested separately; rich cases explicitly require Core2.
No media capability is installed, and image refusal is not visual acceptance.
"""
import json
from types import SimpleNamespace

import pytest
from amplifier_core import HookRegistry
from amplifier_core.message_models import ChatResponse, ImageBlock, TextBlock, ToolCall
from amplifier_core.models import HookResult, ToolResult

from amplifier_module_loop_agent.agent_session import AgentSession
from amplifier_module_loop_agent.config import SessionConfig
from amplifier_module_loop_agent.messages import convert_history_to_messages
from amplifier_module_loop_agent.turns import AssistantTurn, ToolResultsTurn
from amplifier_module_loop_agent.unified_provider_adapter import UnifiedProviderAdapter
from unified_llm.types import (
    ContentKind, ContentPart, FinishReason, Message, Response, Role,
    StreamEvent, StreamEventType, ToolCallData, Usage,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    "content" not in ToolResult.model_fields, reason="Core1 has no canonical rich ToolResult")]
SECRET = "cHJpdmF0ZS1pbWFnZS1ieXRlcw=="


def rich(image=True):
    blocks = [TextBlock(text="before")]
    if image:
        blocks.append(ImageBlock(source={"type": "base64", "media_type": "image/png", "data": SECRET}))
    blocks.append(TextBlock(text="after"))
    return ToolResult(success=True, output="text receipt is not pixels", content=blocks)


class Provider:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return ChatResponse(content=[], tool_calls=[ToolCall(id="call", name="capture", arguments={})])
        return ChatResponse(content=[TextBlock(text="done")])

    async def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield {"tool_calls": [{"id": "call", "name": "capture", "arguments": {}}]}
        else:
            yield {"content": "done"}


class UnifiedClient:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        parts = ([ContentPart(kind=ContentKind.TOOL_CALL, tool_call=ToolCallData(
            id="call", name="capture", arguments={}))]
            if len(self.requests) == 1 else [ContentPart(kind=ContentKind.TEXT, text="done")])
        return Response(id="fake", model="fake", provider="fake",
                        message=Message(role=Role.ASSISTANT, content=parts),
                        finish_reason=FinishReason(reason="stop"),
                        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))

    async def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield StreamEvent(type=StreamEventType.TOOL_CALL_END,
                              tool_call=ToolCallData(id="call", name="capture", arguments={}))
        else:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, delta="done")


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("unified", [False, True])
@pytest.mark.parametrize("hook_case", ["none", "modify", "deny", "missing", "restore", "failure"])
async def test_actual_tool_round_refuses_images_and_events_are_safe(streaming, unified, hook_case):
    hooks = HookRegistry()
    events = []
    original = {}

    async def observer(event, data):
        events.append((event, data.copy()))
        if event == "tool:post":
            original.update(data)
        return HookResult(action="continue")

    hooks.register("tool:post", observer, priority=0)
    for event in ("agent:tool_call_end", "agent:tool_call_output_delta"):
        hooks.register(event, observer)

    async def alter(event, data):
        if hook_case == "deny":
            return HookResult(action="deny", reason="withheld")
        if hook_case == "missing":
            return HookResult(action="modify", data={})
        if hook_case == "failure":
            raise RuntimeError("fixture hook failed")
        return HookResult(action="modify", data={**data, "result": "redacted"})

    async def restore(event, data):
        return HookResult(action="modify", data=original.copy())

    if hook_case != "none":
        hooks.register("tool:post", alter, priority=10)
    if hook_case == "restore":
        hooks.register("tool:post", restore, priority=20)

    async def execute(args):
        return rich()

    tool = SimpleNamespace(name="capture", description="fixture", input_schema={"type": "object"}, execute=execute)
    recorder = UnifiedClient() if unified else Provider()
    provider = UnifiedProviderAdapter(provider_name="anthropic", model="fake", client=recorder) if unified else recorder
    if not streaming:
        # AgentSession detects async-generator stream methods automatically.
        provider.stream = None
    session = AgentSession(config=SessionConfig(system_prompt="fixture"), provider=provider,
                           tools={"capture": tool}, hooks=hooks)
    assert await session.process_input("inspect") == "done"
    content = recorder.requests[-1].messages[-1].content
    if unified:
        content = content[0].tool_result.content
    assert isinstance(content, str)
    assert "no pixels sent" in content
    assert SECRET not in json.dumps(events)
    assert SECRET not in content
    if hook_case in ("none", "restore", "failure"):
        assert "before" in content and "after" in content
    if hook_case == "modify":
        assert "redacted" in content and "before" not in content and "after" not in content


async def test_canonical_text_only_content_survives_real_tool_round():
    async def execute(args):
        return rich(image=False)
    provider = Provider()
    provider.stream = None
    session = AgentSession(config=SessionConfig(system_prompt="fixture"), provider=provider,
        tools={"capture": SimpleNamespace(name="capture", description="fixture", input_schema={}, execute=execute)},
        hooks=HookRegistry())
    await session.process_input("read")
    assert provider.requests[-1].messages[-1].content == "beforeafter"


async def test_history_does_not_bypass_refusal_and_retention_still_elides():
    history = [AssistantTurn(content="", tool_calls=[{"id":"a", "name":"capture", "arguments":{}}]),
               ToolResultsTurn(results=[rich()]),
               AssistantTurn(content="", tool_calls=[{"id":"b", "name":"capture", "arguments":{}}]),
               ToolResultsTurn(results=[rich(image=False)])]
    direct = convert_history_to_messages(history)
    assert "no pixels sent" in direct[1].content
    assert "before" in direct[1].content and "after" in direct[1].content
    assert "".join(block.text for block in direct[-1].content) == "beforeafter"
    retained = convert_history_to_messages(history, tool_result_retention_turns=1)
    assert retained[1].content.startswith("[tool result elided:")
    assert retained[1].tool_call_id == "a"
    assert SECRET not in repr(retained)


@pytest.mark.parametrize("unified", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("failure", [False, True])
async def test_actual_lines_and_failure_reach_shipped_truncator(unified, streaming, failure):
    from amplifier_module_hooks_tool_truncation import mount
    text = "\n".join(f"line {i}" for i in range(300))
    hooks = HookRegistry()
    cleanup = await mount(SimpleNamespace(get=lambda key: hooks if key == "hooks" else None),
                          config={"char_limits": {"capture": 30000},
                                  "line_limits": {"capture": 200}, "modes": {}})
    async def execute(args):
        return ToolResult(success=not failure, output="FATAL_SENTINEL" if failure else text,
                          error={"message": "FATAL_SENTINEL"} if failure else None,
                          content=[TextBlock(text=text)])
    recorder = UnifiedClient() if unified else Provider()
    provider = UnifiedProviderAdapter(provider_name="anthropic", model="fake", client=recorder) if unified else recorder
    if not streaming:
        provider.stream = None
    session = AgentSession(config=SessionConfig(system_prompt="fixture"), provider=provider,
        tools={"capture": SimpleNamespace(name="capture", description="fixture", input_schema={}, execute=execute)},
        hooks=hooks)
    try:
        await session.process_input("inspect")
        content = recorder.requests[-1].messages[-1].content
        if unified:
            content = content[0].tool_result.content
        assert "lines omitted" in content
        assert "line 150\n" not in content
        if failure:
            assert "FATAL_SENTINEL" in content and "[Tool failed]" in content
    finally:
        cleanup()


async def test_failed_rich_error_is_effective_text_not_restored_after_redaction():
    hooks = HookRegistry()
    async def redact(event, data):
        assert "FATAL_SENTINEL" in data["result"]
        return HookResult(action="modify", data={**data, "result": "safe error explanation"})
    hooks.register("tool:post", redact)
    async def execute(args):
        return ToolResult(success=False, output="FATAL_SENTINEL", error={"message": "FATAL_SENTINEL"},
                          content=[TextBlock(text="partial text")])
    provider = Provider()
    provider.stream = None
    session = AgentSession(config=SessionConfig(system_prompt="fixture"), provider=provider,
        tools={"capture": SimpleNamespace(name="capture", description="fixture", input_schema={}, execute=execute)},
        hooks=hooks)
    await session.process_input("inspect")
    assert provider.requests[-1].messages[-1].content == "safe error explanation"
    assert not session._history[-2].results[0].success
