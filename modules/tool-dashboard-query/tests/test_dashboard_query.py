"""Tests for dashboard_query tool."""

import json

import httpx
import pytest

from amplifier_module_tool_dashboard_query import DashboardQueryTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_response(data: dict | list, status_code: int = 200) -> httpx.Response:
    """Build an httpx.Response with JSON body."""
    return httpx.Response(
        status_code=status_code,
        json=data,
    )


def _make_tool(handler) -> DashboardQueryTool:
    """Create a DashboardQueryTool with a mock transport."""
    tool = DashboardQueryTool(config={"dashboard_url": "http://test"})
    tool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    return tool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_list_pipelines():
    """GET /api/pipelines returns a list of pipelines."""
    pipelines = [{"id": "p1", "status": "running"}, {"id": "p2", "status": "done"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines"
        assert request.method == "GET"
        return _json_response(pipelines)

    tool = _make_tool(handler)
    result = await tool.execute({"operation": "list_pipelines"})

    assert result.success
    assert result.output == pipelines


@pytest.mark.asyncio(loop_scope="session")
async def test_get_pipeline():
    """GET /api/pipelines/{id} returns pipeline detail."""
    pipeline = {"id": "p1", "status": "running", "nodes": []}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines/p1"
        assert request.method == "GET"
        return _json_response(pipeline)

    tool = _make_tool(handler)
    result = await tool.execute({"operation": "get_pipeline", "pipeline_id": "p1"})

    assert result.success
    assert result.output == pipeline


@pytest.mark.asyncio(loop_scope="session")
async def test_get_node():
    """GET /api/pipelines/{pid}/nodes/{nid} returns node detail."""
    node = {"id": "build", "status": "complete", "output": "ok"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines/p1/nodes/build"
        assert request.method == "GET"
        return _json_response(node)

    tool = _make_tool(handler)
    result = await tool.execute(
        {"operation": "get_node", "pipeline_id": "p1", "node_id": "build"}
    )

    assert result.success
    assert result.output == node


@pytest.mark.asyncio(loop_scope="session")
async def test_submit_pipeline():
    """POST /api/pipelines submits a new pipeline."""
    created = {"id": "p3", "status": "pending"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["dot_source"] == "digraph { a -> b }"
        assert body["goal"] == "test goal"
        return _json_response(created)

    tool = _make_tool(handler)
    result = await tool.execute(
        {
            "operation": "submit_pipeline",
            "dot_source": "digraph { a -> b }",
            "goal": "test goal",
        }
    )

    assert result.success
    assert result.output == created


@pytest.mark.asyncio(loop_scope="session")
async def test_submit_pipeline_without_goal():
    """POST /api/pipelines works without an optional goal."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "goal" not in body
        return _json_response({"id": "p4", "status": "pending"})

    tool = _make_tool(handler)
    result = await tool.execute(
        {"operation": "submit_pipeline", "dot_source": "digraph { x -> y }"}
    )

    assert result.success


@pytest.mark.asyncio(loop_scope="session")
async def test_cancel_pipeline():
    """POST /api/pipelines/{id}/cancel cancels a pipeline."""
    cancelled = {"id": "p1", "status": "cancelled"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines/p1/cancel"
        assert request.method == "POST"
        return _json_response(cancelled)

    tool = _make_tool(handler)
    result = await tool.execute({"operation": "cancel_pipeline", "pipeline_id": "p1"})

    assert result.success
    assert result.output == cancelled


@pytest.mark.asyncio(loop_scope="session")
async def test_get_questions():
    """GET /api/pipelines/{id}/questions returns pending questions."""
    questions = [{"id": "q1", "text": "Approve deploy?"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines/p1/questions"
        assert request.method == "GET"
        return _json_response(questions)

    tool = _make_tool(handler)
    result = await tool.execute({"operation": "get_questions", "pipeline_id": "p1"})

    assert result.success
    assert result.output == questions


@pytest.mark.asyncio(loop_scope="session")
async def test_answer_question():
    """POST /api/pipelines/{pid}/questions/{qid}/answer submits an answer."""
    answered = {"id": "q1", "status": "answered"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines/p1/questions/q1/answer"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["answer"] == "yes"
        return _json_response(answered)

    tool = _make_tool(handler)
    result = await tool.execute(
        {
            "operation": "answer_question",
            "pipeline_id": "p1",
            "question_id": "q1",
            "answer": "yes",
        }
    )

    assert result.success
    assert result.output == answered


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_operation():
    """Missing operation field returns error."""
    tool = DashboardQueryTool(config={})
    result = await tool.execute({})

    assert not result.success
    assert "operation" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_pipeline_id():
    """get_pipeline without pipeline_id returns error."""
    tool = DashboardQueryTool(config={})
    result = await tool.execute({"operation": "get_pipeline"})

    assert not result.success
    assert "pipeline_id" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_node_id():
    """get_node without node_id returns error."""
    tool = DashboardQueryTool(config={})
    result = await tool.execute({"operation": "get_node", "pipeline_id": "p1"})

    assert not result.success
    assert "node_id" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_dot_source():
    """submit_pipeline without dot_source returns error."""
    tool = DashboardQueryTool(config={})
    result = await tool.execute({"operation": "submit_pipeline"})

    assert not result.success
    assert "dot_source" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_answer_fields():
    """answer_question without question_id and answer returns error."""
    tool = DashboardQueryTool(config={})
    result = await tool.execute({"operation": "answer_question", "pipeline_id": "p1"})

    assert not result.success
    assert "question_id" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_tool_name_and_schema():
    """Tool has correct name, description, and input schema structure."""
    tool = DashboardQueryTool(config={})

    assert tool.name == "dashboard_query"
    assert "pipeline" in tool.description.lower()

    schema = tool.input_schema
    assert schema["type"] == "object"
    assert "operation" in schema["properties"]
    assert "operation" in schema["required"]
    assert "pipeline_id" in schema["properties"]
    assert "node_id" in schema["properties"]
    assert "dot_source" in schema["properties"]
    assert "goal" in schema["properties"]
    assert "question_id" in schema["properties"]
    assert "answer" in schema["properties"]


@pytest.mark.asyncio(loop_scope="session")
async def test_close_shuts_down_client():
    """close() closes the underlying httpx client and resets to None."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"ok": True})

    tool = _make_tool(handler)
    # Force client creation
    client = tool._get_client()
    assert client is not None

    await tool.close()
    assert tool._client is None


@pytest.mark.asyncio(loop_scope="session")
async def test_close_when_no_client_is_noop():
    """close() on a tool that never created a client is a safe no-op."""
    tool = DashboardQueryTool(config={})
    assert tool._client is None

    await tool.close()  # should not raise
    assert tool._client is None
