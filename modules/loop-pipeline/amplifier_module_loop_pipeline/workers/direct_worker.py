"""``DirectWorker`` -- the merged ``direct`` worker (P1 gap-table row 2).

Merges the former ``AmplifierBackend._run_with_tool_loop`` (``backend.py``,
the spawn-absent Path B fallback) and the standalone ``DirectProviderBackend``
class (formerly ``__init__.py:39-394``) into ONE registry-resolvable worker.
Both predecessors delegated the agentic tool loop to
``unified_llm.generate()``; this class is that shared body, now living in
one place instead of two.

Cites (nlspec-first gate, AGENTS.md -- spec text is never restated here):
  - specs/canonical/attractor-spec-canonical.md Sec1.4/Sec4.5 (backend
    delegation -- "what that backend does internally is entirely up to the
    implementor").
  - specs/EXTENSIONS.md Sec12/Sec13 (fidelity=full continuity realization,
    branch-local thread isolation), Sec23 (response_schema), Sec25
    (fail-closed goal-gate / is_explicit), Sec35 (report_outcome
    precedence), Sec40 (this program's own new ledger entry -- worker
    selection + registry semantics).
  - DESIGN-worker-registry-core-split.md Sec2.1 (seam contract), Sec4 P1
    (the merge), gap-table rows 2/6-12.

Asymmetries resolved by this merge (design doc gap-table rows 6-12; see
``modules/loop-pipeline/tests/test_direct_worker_merge.py`` for the
RED-proofs):
  - row 6 (``clone()``): both predecessors' tool-cloning logic unified here;
    ``DirectProviderBackend`` had none (silent ``hasattr`` guard skipped it).
  - row 7 (``close()``): same story for the cached ``unified_llm`` client.
  - row 8 (``response_schema``): preserved (EXT-23) -- unaffected by the
    merge; still refused on the spawn path (``backend.py``'s
    ``_run_with_spawn``, untouched).
  - row 9 (``user_instructions``): NOT claimed here -- both predecessors
    left this spawn-path-only (``backend.py``'s ``_run_with_spawn``); the
    direct worker still does not honor it. Declared absent to the
    worker-parity-kit harness (``tests/test_worker_parity.py``).
  - row 10 (``human.gate.text``): this worker never sees it directly -- it
    is injected by the adapter (``AmplifierBackend.run``) into the prompt
    BEFORE either path is reached. Because the adapter now ALWAYS
    constructs ``AmplifierBackend`` (never a bare standalone backend, see
    ``__init__.py``'s ``_build_backend``), gate text now reaches this
    worker on every invocation -- resolving the asymmetry by construction
    rather than by duplicating the injection here.
  - row 11 (``provider:{request,response,error}``): preserved verbatim.
  - row 12 (``llm_model``): mandatory here (``_resolve_model`` raises if
    absent) -- unchanged from both predecessors; optional on the spawn
    path. One documented rule per worker (this docstring + EXTENSIONS Sec40).
"""

from __future__ import annotations

import copy
import json
from typing import Any

from ..backend import (
    _MAX_TOOL_LOOP_ROUNDS,
    _build_unified_tools,
    _default_model_stable_only,
    _outcome_from_structured_output,
    _parse_outcome,
    _resolve_concrete_model,
    _resolve_model,
)
from ..context import PipelineContext
from ..graph import Node, resolve_bool_attr
from ..hook_bridge import _current_node_context, set_node_context
from ..outcome import Outcome, StageStatus
from ..pipeline_events import PROVIDER_ERROR, PROVIDER_REQUEST, PROVIDER_RESPONSE


def _clone_tool(tool: Any) -> Any:
    """Reset stateful-tool instance state for a branch clone.

    Identical detection rule to the one ``AmplifierBackend.clone()`` used
    pre-merge: explicit ``__dict__``/class inspection for ``last_outcome``,
    never a bare ``hasattr`` (which fabricates truthy hits on ``MagicMock``
    and similar proxies).
    """
    is_stateful = "last_outcome" in getattr(tool, "__dict__", {}) or any(
        "last_outcome" in vars(cls) for cls in type(tool).__mro__
    )
    if is_stateful:
        c = copy.copy(tool)
        c.last_outcome = None
        return c
    return tool


class DirectWorker:
    """The ``direct`` worker: in-process LLM execution via ``unified_llm``.

    Stateless per node visit at the ``run()`` seam (Worker protocol) --
    ``self._tools``/``self._unified_client`` are immutable-ish shared
    resources, not per-visit state. Node-exchange transcript continuity
    (``fidelity=full``) is NOT owned here -- the adapter
    (``AmplifierBackend``) resolves and hands this worker its
    ``replayed_history`` already-computed; this worker only translates that
    into ``unified_llm`` messages.
    """

    #: TARGET-tier capabilities this worker does not honor (worker-parity-kit
    #: vocabulary; row 9 above). Declared, never silently dropped.
    DECLARED_ABSENCES: frozenset[str] = frozenset({"user_instructions"})

    def __init__(
        self,
        provider: Any = None,
        tools: dict[str, Any] | None = None,
        hooks: Any = None,
        unified_client: Any | None = None,
    ) -> None:
        self._provider = provider
        self._tools = tools or {}
        self._hooks = hooks
        self._unified_client = unified_client

    def clone(self) -> DirectWorker:
        """Branch-isolated clone (gap-table row 6).

        Shares immutable refs (``provider``, ``hooks``, ``unified_client``);
        gives each clone an independent tools dict with stateful tools reset
        -- identical policy to the pre-merge ``AmplifierBackend.clone()``.
        """
        new = DirectWorker.__new__(DirectWorker)
        new._provider = self._provider
        new._hooks = self._hooks
        new._unified_client = self._unified_client
        new._tools = {k: _clone_tool(v) for k, v in self._tools.items()}
        return new

    async def close(self) -> None:
        """Release the cached ``unified_llm`` client (gap-table row 7).

        Idempotent and tolerant of a client with no ``close()`` -- identical
        contract to the pre-merge ``AmplifierBackend.close()`` /
        ``DirectProviderBackend`` (which had neither).
        """
        client = self._unified_client
        if client is None:
            return
        close_fn = getattr(client, "close", None)
        if close_fn is not None:
            await close_fn()
        self._unified_client = None

    async def _emit(self, event_name: str, data: dict[str, Any]) -> Any:
        if self._hooks is not None:
            return await self._hooks.emit(event_name, data)
        return None

    def _get_or_create_unified_client(self) -> Any:
        if self._unified_client is not None:
            return self._unified_client
        import unified_llm

        self._unified_client = unified_llm.Client.from_env()
        return self._unified_client

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        replayed_history: list[dict[str, Any]],
    ) -> tuple[str, Outcome]:
        """Run one turn via ``unified_llm.generate()``.

        ``prompt`` is the already-expanded instruction (preamble and any
        human.gate.text already applied by the adapter -- see module
        docstring, row 10). ``replayed_history`` is the adapter-resolved
        node-exchange transcript for this thread (empty when not
        applicable); this worker prepends it verbatim as prior turns.
        """
        import unified_llm

        provider_name = node.llm_provider or node.attrs.get("llm_provider", "anthropic")
        model_token = _resolve_model(node)
        # Rung 4 (per-provider default model, spec Sec8.5 item 4) may need
        # stable_only=False when a provider's own current flagship is itself
        # preview-named (see _PROVIDER_DEFAULT_MODEL_PATTERN in backend.py).
        # Irrelevant -- and left at the pre-existing True -- when the node
        # declared an explicit llm_model (rungs 1-3).
        stable_only = (
            True if node.llm_model else _default_model_stable_only(provider_name)
        )
        model = await _resolve_concrete_model(
            provider_name, model_token, emit=self._emit, stable_only=stable_only
        )
        reasoning_effort = node.attrs.get("reasoning_effort")
        max_agent_turns_raw = node.attrs.get("max_agent_turns")
        max_agent_turns = (
            int(max_agent_turns_raw) if max_agent_turns_raw is not None else None
        )
        tools = _build_unified_tools(self._tools)
        client = self._get_or_create_unified_client()

        # EXT-23: response_schema -> structured output (row 8, preserved).
        response_format: Any = None
        if node.response_schema is not None:
            response_format = unified_llm.ResponseFormat(
                type="json_schema",
                json_schema=node.response_schema,
                strict=True,
            )

        generate_kwargs: dict[str, Any] = {
            "model": model,
            "tools": tools or None,
            "max_tool_rounds": max_agent_turns
            if max_agent_turns is not None
            else _MAX_TOOL_LOOP_ROUNDS,
            "reasoning_effort": reasoning_effort,
            "provider": provider_name,
            "client": client,
            "response_format": response_format,
        }

        if replayed_history:
            messages: list[Any] = [_message_from_dict(m) for m in replayed_history]
            messages.append(unified_llm.Message.user(prompt))
            generate_kwargs["messages"] = messages
        else:
            generate_kwargs["prompt"] = prompt

        token = set_node_context({"node_id": node.id})
        try:
            pre_result = await self._emit(
                PROVIDER_REQUEST,
                {
                    "provider": provider_name,
                    "model": model,
                    "node_id": node.id,
                    "tool_names": [t.name for t in tools] if tools else [],
                    "message_count": len(generate_kwargs.get("messages", [])) or 1,
                },
            )
            if (
                pre_result is not None
                and getattr(pre_result, "action", "continue") == "deny"
            ):
                reason = getattr(pre_result, "reason", None) or "Denied by hook"
                return "", Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=f"Denied by hook: {reason}",
                )

            result = await unified_llm.generate(**generate_kwargs)
        except unified_llm.SDKError as exc:
            await self._emit(
                PROVIDER_ERROR,
                {
                    "provider": provider_name,
                    "model": model,
                    "node_id": node.id,
                    "error_type": type(exc).__name__,
                    "error_class": type(exc).__mro__[1].__name__,
                    "retryable": getattr(exc, "retryable", False),
                    "message": str(exc),
                },
            )
            return "", Outcome(status=StageStatus.FAIL, failure_reason=str(exc))
        except Exception as exc:  # noqa: BLE001 -- mirrors predecessor's catch-all
            return "", Outcome(status=StageStatus.FAIL, failure_reason=str(exc))
        finally:
            _current_node_context.reset(token)

        await self._emit(
            PROVIDER_RESPONSE,
            {
                "provider": provider_name,
                "model": model,
                "node_id": node.id,
                "usage": {
                    "input_tokens": result.total_usage.input_tokens,
                    "output_tokens": result.total_usage.output_tokens,
                    "total_tokens": result.total_usage.total_tokens,
                    "reasoning_tokens": result.total_usage.reasoning_tokens,
                    "cache_read_tokens": result.total_usage.cache_read_tokens,
                    "cache_write_tokens": result.total_usage.cache_write_tokens,
                },
                "finish_reason": result.finish_reason.reason,
                "text_length": len(result.text) if result.text else 0,
                "step_count": len(result.steps),
                # Merge note (asymmetry, honestly resolved): only the former
                # DirectProviderBackend emitted this key; the former
                # AmplifierBackend._run_with_tool_loop did not. Preserved
                # here since it is a strict superset (richer observability,
                # never previously false) -- see
                # tests/test_provider_hooks.py::test_direct_backend_emits_provider_response.
                "cost_usd": result.total_usage.cost_usd,
            },
        )

        if node.response_schema is not None:
            return _structured_output_result(node, result)

        return _tool_loop_result(node, result)


def _message_from_dict(m: dict[str, Any]):
    """Translate a plain ``{"role": ..., "content": ...}`` dict (the
    adapter's node-exchange-granularity replay shape, EXTENSIONS.md Sec12)
    into a ``unified_llm.Message``."""
    import unified_llm

    role = m.get("role", "user")
    content = m.get("content", "")
    if role == "assistant":
        return unified_llm.Message.assistant(content)
    return unified_llm.Message.user(content)


def _structured_output_result(node: Node, result: Any) -> tuple[str, Outcome]:
    """EXT-23 structured-output branch -- identical logic to both
    predecessors (backend.py's ``_run_with_tool_loop`` /
    ``__init__.py``'s ``DirectProviderBackend.run``)."""
    raw_json = result.text or ""
    if not raw_json.strip() and result.tool_calls:
        _STRUCT_TOOL = "__structured_output__"
        for _tc in result.tool_calls:
            if _tc.name == _STRUCT_TOOL:
                _args = _tc.arguments
                raw_json = (
                    json.dumps(_args)
                    if isinstance(_args, dict)
                    else (str(_args) if _args else "")
                )
                break
    parsed_obj: Any = None
    if raw_json.strip():
        try:
            parsed_obj = json.loads(raw_json)
        except json.JSONDecodeError:
            pass
    ctx_updates: dict[str, Any] = {
        "last_stage": node.id,
        "last_response": raw_json[:200],
    }
    if parsed_obj is not None:
        ctx_updates[node.id] = parsed_obj
    outcome = _outcome_from_structured_output(
        raw_json=raw_json,
        parsed_obj=parsed_obj,
        ctx_updates=ctx_updates,
        result=result,
    )
    return raw_json, outcome


def _tool_loop_result(node: Node, result: Any) -> tuple[str, Outcome]:
    """Map a ``GenerateResult`` to ``(output_text, Outcome)``.

    WAVE 5 repair (2026-08-30, maintainer ruling): the former ``report_outcome``
    tool-call check that used to run here, AUTHORITATIVE and BEFORE
    ``result.text``, is removed -- the tool is gone repo-wide, no compat
    window (specs/EXTENSIONS.md Sec35 RETCON, dated status: REMOVED).  The
    spec's own channels are what remain, in priority order:

      1. result.text (JSON/fenced JSON/embedded/prose) -> ``_parse_outcome``
         handles all of these -- this is EXTENSIONS.md Sec25's fail-closed
         explicit-verdict ladder, unchanged and no longer preceded by a
         report_outcome check.
      2. No text at all -> SUCCESS for non-goal_gate, FAIL for goal_gate
         (EXTENSIONS.md Sec25).

    ``status.json`` (Sec41) is applied afterward at the handler layer and is
    unaffected by this function.
    """
    text = result.text or ""

    if text:
        outcome = _parse_outcome(text, node=node)
    else:
        is_goal_gate = resolve_bool_attr(node.attrs.get("goal_gate"), "goal_gate")
        if is_goal_gate:
            outcome = Outcome(
                status=StageStatus.FAIL,
                notes=f"No output from direct worker: {node.id}",
                failure_reason=(
                    "Empty direct-worker response -- goal_gate node requires "
                    "explicit verdict"
                ),
                is_explicit=False,
            )
        else:
            outcome = Outcome(
                status=StageStatus.SUCCESS,
                notes=f"Stage completed: {node.id}",
                is_explicit=False,
            )

    outcome.context_updates = {
        **(outcome.context_updates or {}),
        "last_stage": node.id,
        "last_response": text[:200] if text else "",
    }
    return text, outcome
