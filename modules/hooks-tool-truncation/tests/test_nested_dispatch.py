"""Real Core/live dispatch, QuickJS tool_exec and truncation-hook integration.

The only fixture tool writes an actual local receipt before returning a large
diagnostic payload. No model, network account or application state is used.
"""

import asyncio
import json

import pytest
import pytest_asyncio
from amplifier_core import AmplifierSession, HookResult, ToolResult
from amplifier_core.message_models import ToolCall
from amplifier_module_tool_exec import ProgrammaticTool

from amplifier_module_hooks_tool_truncation import mount


class SaveReceipt:
    name = "save_receipt"
    description = "Save a synthetic receipt in the fixture directory"
    def __init__(self, root):
        self.input_schema = {"type": "object", "properties": {}}
        self.root = root
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, arguments):
        identity = arguments["id"]
        self.calls.append(identity)
        with (self.root / identity).open("x") as stream:
            stream.write(identity)
        self.started.set()
        if arguments.get("wait"):
            await self.release.wait()
        return ToolResult(
            success=not arguments.get("fail", False),
            output={"receipt": {"id": identity}, "diagnostics": "x" * arguments.get("size", 100_000)},
            error={"code": "fixture_failure"} if arguments.get("fail") else None,
        )


@pytest_asyncio.fixture
async def environment(tmp_path):
    session = AmplifierSession({
        "session": {
            "orchestrator": {"module": "loop-live", "config": {
                "programmatic_dispatch": True, "min_delay_between_calls_ms": 0,
            }},
            "context": {"module": "context-simple"},
        },
        "providers": [],
    })
    await session.initialize()
    coordinator = session.coordinator
    saved = SaveReceipt(tmp_path)
    await coordinator.mount("tools", saved, name=saved.name)
    await coordinator.mount("tools", ProgrammaticTool(coordinator), name="tool_exec")
    cleanup = await mount(coordinator)
    observed = []

    async def observe(event, data):
        observed.append((event, data))
        return HookResult()

    coordinator.hooks.register("tool:post", observe, priority=100)
    coordinator.hooks.register("tool:dispatch_cancelled", observe, priority=100)
    try:
        yield coordinator, saved, observed
    finally:
        cleanup()
        await session.cleanup()


async def run(environment, code, name="tool_exec", arguments=None):
    coordinator, _, _ = environment
    loop = coordinator.get("orchestrator")
    with loop.approved_dispatch.run_scope(coordinator):
        _, _, content = await loop._execute_tool_only(
            ToolCall(id="parent", name=name, arguments=arguments or {"code": code}),
            coordinator.get("tools"), coordinator.hooks, "fixture", coordinator,
        )
    return json.loads(content)


@pytest.mark.asyncio
async def test_two_committed_results_remain_structured_and_selectable(environment):
    result = await run(environment, """
        await Promise.all(['one', 'two'].map(async id => {
            const r = await tools.save_receipt({id});
            text({success: r.success, id: r.output.receipt.id});
        }));
    """)
    assert result["success"] is True
    output = result["output"]
    assert sorted(json.loads(row)["id"] for row in output["output"]) == ["one", "two"]
    assert all(row["status"] == "completed" and row["success"] is True for row in output["calls"])
    _, saved, observed = environment
    assert sorted(saved.calls) == ["one", "two"]
    assert all((saved.root / name).read_text() == name for name in saved.calls)
    child_posts = [data for event, data in observed if event == "tool:post" and data["tool_name"] == "save_receipt"]
    assert len(child_posts) == 2
    assert all(isinstance(data["result"], dict) for data in child_posts)
    assert all(data["result"]["success"] is True for data in child_posts)
    assert all(len(data["result"]["output"]["diagnostics"]) == 100_000 for data in child_posts)
    assert len(json.dumps(output)) < 4_000  # No diagnostic payload reaches model output.


@pytest.mark.asyncio
async def test_large_failed_result_is_failed_not_unknown_or_success(environment):
    result = await run(environment, """
        const r = await tools.save_receipt({id:'failed', fail:true});
        text({status:r.status, success:r.success});
    """)
    assert result["success"] is False
    call = result["output"]["calls"][0]
    assert (call["status"], call["success"]) == ("failed", False)
    assert environment[1].calls == ["failed"]


@pytest.mark.asyncio
async def test_output_delivery_limit_keeps_completed_child_outcomes(environment):
    result = await run(environment, """
        const first = await tools.save_receipt({id:'first'});
        const second = await tools.save_receipt({id:'second'});
        text('x'.repeat(40000));
    """)
    assert result["success"] is False
    assert result["error"]["code"] == "output_limit"
    assert all(row["status"] == "completed" and row["success"] is True for row in result["output"]["calls"])
    assert environment[1].calls == ["first", "second"]


@pytest.mark.asyncio
async def test_nested_result_limit_remains_enforced_after_success(environment):
    result = await run(environment, "await tools.save_receipt({id:'large',size:300000});")
    assert result["success"] is False
    assert result["error"]["code"] == "result_limit"
    call = result["output"]["calls"][0]
    assert (call["status"], call["success"]) == ("completed", True)
    assert call["result_delivery"] == "omitted_limit"
    assert environment[1].calls == ["large"]


@pytest.mark.asyncio
async def test_denied_nested_call_does_not_write(environment):
    coordinator, saved, _ = environment

    async def deny(event, data):
        return HookResult(action="deny", reason="Fixture policy") if data["tool_name"] == "save_receipt" else HookResult()

    coordinator.hooks.register("tool:pre", deny)
    result = await run(environment, "await tools.save_receipt({id:'denied'});")
    assert result["success"] is False
    call = result["output"]["calls"][0]
    assert (call["status"], call["success"]) == ("denied", False)
    assert saved.calls == [] and not (saved.root / "denied").exists()


@pytest.mark.asyncio
async def test_other_post_hook_can_still_make_outcome_unknown(environment):
    coordinator, saved, _ = environment

    async def redact(event, data):
        if data["tool_name"] == "save_receipt":
            return HookResult(action="modify", data={**data, "result": {"output": "redacted"}})
        return HookResult()

    coordinator.hooks.register("tool:post", redact, priority=5)
    result = await run(environment, "const r=await tools.save_receipt({id:'redacted'}); text(r.status);")
    assert result["success"] is False
    call = result["output"]["calls"][0]
    assert (call["status"], call["success"]) == ("unknown", None)
    assert saved.calls == ["redacted"]


@pytest.mark.asyncio
async def test_immediate_cancellation_does_not_claim_rollback_or_success(environment):
    coordinator, saved, observed = environment
    task = asyncio.create_task(run(environment, "await tools.save_receipt({id:'cancelled',wait:true});"))
    await asyncio.wait_for(saved.started.wait(), 5)
    await coordinator.request_cancel(immediate=True)
    result = await asyncio.wait_for(task, 5)
    assert result["success"] is False
    call = result["output"]["calls"][0]
    assert (call["status"], call["success"]) == ("unknown", None)
    assert call["effects"] == "not_rolled_back"
    assert saved.calls == ["cancelled"]
    assert (saved.root / "cancelled").read_text() == "cancelled"
    cancelled = [data for event, data in observed if event == "tool:dispatch_cancelled"]
    assert len(cancelled) == 1 and cancelled[0]["outcome"] == "unknown"


@pytest.mark.asyncio
async def test_ordinary_model_output_is_still_truncated(environment):
    _, _, observed = environment
    # An ordinary call has no dispatch source and still takes both truncation passes.
    coordinator = environment[0]
    loop = coordinator.get("orchestrator")
    with loop.approved_dispatch.run_scope(coordinator):
        _, _, content = await loop._execute_tool_only(
            ToolCall(id="ordinary", name="save_receipt", arguments={"id": "ordinary"}),
            coordinator.get("tools"), coordinator.hooks, "fixture", coordinator,
        )
    assert "[WARNING: Tool output was truncated" in content
    assert len(content) < 31_000
    data = next(data for event, data in observed if event == "tool:post" and data["tool_call_id"] == "ordinary")
    assert data["full_output"]["success"] is True
    assert len(data["full_output"]["output"]["diagnostics"]) == 100_000
