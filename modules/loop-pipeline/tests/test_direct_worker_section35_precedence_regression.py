"""Genuine regression test for EXTENSIONS.md Sec35 precedence, against the
MERGED ``DirectWorker`` (adversarial-review Fix 3).

Honest framing (corrects the builder's original, inaccurate claim): the two
tests originally cited as "RED until fixed" for the old ``DirectProviderBackend``
Sec35-precedence bug --
``test_backend.py::test_tool_loop_report_outcome_call_wins_over_trailing_json_text``
and
``test_report_outcome_multiturn_convergence.py::test_report_outcome_wins_over_trailing_prose_across_three_turns``
-- are byte-identical to `github/main` and exercise ``AmplifierBackend``
constructed directly with a no-spawn-capability coordinator. On `main`,
constructing ``AmplifierBackend`` that way *already* falls into that class's
own internal ``_run_with_tool_loop`` ("Path B"), which checked
``_find_report_outcome_call`` FIRST -- the ALREADY-CORRECT order. Neither
test ever routed through the standalone ``DirectProviderBackend`` class (the
one `_build_backend` chose only when constructing a bare, no-coordinator
backend), so neither test was ever RED against the actual bug.

This test closes that gap for real. It exercises ``DirectWorker`` --
the class the merge actually produced -- directly, with a SINGLE
``unified_llm.generate()`` round whose one-and-only model response carries
BOTH a valid ``report_outcome`` tool call AND trailing JSON-shaped text
whose ``status`` CONTRADICTS it. Per EXTENSIONS.md Sec35 ("Precedence
Policy": "structured report_outcome status supersedes contradicting
trailing prose"), the tool call must win.

Why this would have FAILED against `main`'s standalone ``DirectProviderBackend``
(read, not re-run -- that class no longer exists in this tree to import;
see DESIGN-worker-registry-core-split.md P1 gap-table row 2 and this repo's
`git show f3a207d:modules/loop-pipeline/amplifier_module_loop_pipeline/__init__.py`):

    290        text = result.text
    292        if text:
    ...
    297            if bool(_fence_match) or stripped.startswith("{"):
    298                outcome = _parse_outcome(text, node=node)
    ...
    303                return outcome
    307        # Text is plain prose or empty -- check if report_outcome was called
    308        lo = _find_report_outcome_call(result)

`main`'s ``DirectProviderBackend.run()`` checked whether ``result.text`` was
JSON-shaped (line 297) and, if so, returned via ``_parse_outcome(text, ...)``
*unconditionally* at line 298-303 -- NEVER reaching the
``_find_report_outcome_call`` check at line 308. Feeding that exact
class the same single-response input this test constructs below (JSON-
shaped trailing text alongside a report_outcome tool call in one message)
would have taken the line-297 branch and returned the CONTRADICTING JSON
text's verdict, silently discarding the real tool-call verdict -- the
precise fail-unsafe bug EXTENSIONS.md Sec35 forbids. The merged
``DirectWorker._tool_loop_result`` (``workers/direct_worker.py``) checks
``_find_report_outcome_call`` FIRST, unconditionally, closing this by
construction.
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.workers import DirectWorker


class _SingleResponseClient:
    """Hermetic unified_llm client returning exactly ONE response for the
    entire ``generate()`` tool loop.

    ``finish_reason.reason="stop"`` (not ``"tool_calls"``) makes
    ``unified_llm.generate()`` break after this one round regardless of the
    ``tool_calls`` also present on the same message -- see
    ``unified_llm/generate.py``'s stop condition
    ``if not tool_calls or response.finish_reason.reason != "tool_calls": break``.
    This is what makes the scenario a genuine SINGLE TURN: one model output,
    carrying both a tool call and contradicting trailing text, not two
    separate rounds.
    """

    def __init__(self, response: Any) -> None:
        self._response = response
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> Any:
        self.requests.append(request)
        return self._response


def _make_mixed_response(*, tool_status: str, text_json_status: str) -> Any:
    """One model response carrying BOTH a ``report_outcome`` tool call (the
    real verdict, ``tool_status``) AND trailing JSON-shaped text asserting a
    CONTRADICTING ``text_json_status`` -- exactly the shape EXTENSIONS.md
    Sec35 governs.
    """
    import json

    import unified_llm

    content = [
        unified_llm.ContentPart(
            kind=unified_llm.ContentKind.TEXT,
            text=json.dumps({"status": text_json_status, "notes": "trailing json"}),
        ),
        unified_llm.ContentPart(
            kind=unified_llm.ContentKind.TOOL_CALL,
            tool_call=unified_llm.ToolCallData(
                id="tc-1",
                name="report_outcome",
                arguments={
                    "status": tool_status,
                    "failure_reason": "real verdict from tool call",
                },
            ),
        ),
    ]
    return unified_llm.Response(
        id="resp-mixed",
        model="test-model",
        provider="test",
        message=unified_llm.Message(role=unified_llm.Role.ASSISTANT, content=content),
        # NOT "tool_calls" -- see _SingleResponseClient docstring: this keeps
        # the scenario to exactly one round.
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=10, output_tokens=20, total_tokens=30),
    )


def _node(**attrs: Any) -> Node:
    defaults = {"llm_model": "test-model", "llm_provider": "test"}
    defaults.update(attrs)
    return Node(id="assess", shape="diamond", prompt="assess the work", attrs=defaults)


@pytest.mark.asyncio
async def test_report_outcome_wins_over_contradicting_trailing_json_in_same_turn():
    """EXTENSIONS.md Sec35 precedence, pinned against the merged `direct`
    worker: a SINGLE model turn carrying both a report_outcome tool call
    (status=fail) and contradicting JSON-shaped trailing text
    (status=success) must resolve to the TOOL CALL's verdict.

    See this module's docstring for why main's now-deleted
    ``DirectProviderBackend`` would have gotten this input WRONG (the
    contradicting trailing text would have won) -- this pins the merge's
    fix, closed by deletion, going forward.
    """
    response = _make_mixed_response(tool_status="fail", text_json_status="success")
    client = _SingleResponseClient(response)
    worker = DirectWorker(provider=object(), unified_client=client)
    node = _node()

    _text, outcome = await worker.run(
        node, "assess the work", PipelineContext(), replayed_history=[]
    )

    # The report_outcome tool call is authoritative -- must win over the
    # contradicting trailing JSON text.
    assert outcome.status == StageStatus.FAIL, (
        f"expected the report_outcome tool call's status=fail to win over "
        f"the contradicting trailing JSON text's status=success, got "
        f"{outcome.status!r} -- this is the exact Sec35 fail-unsafe "
        f"inversion the merge closed"
    )
    assert outcome.is_explicit is True
    assert outcome.failure_reason == "real verdict from tool call"
    # Sanity: exactly one round was needed (proves this is a single-turn
    # scenario, not a multi-round tool loop like the pre-existing tests).
    assert len(client.requests) == 1
