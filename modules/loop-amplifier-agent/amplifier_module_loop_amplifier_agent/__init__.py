"""Public agent binding adapter; the parent alone reads worker-written status.

Attractor canonical §§1.4/4.5 leave backend implementation replaceable while
preserving String|Outcome and the external status.json channel. No engine,
prepared bundle, CLI, coordinator, or module resolver reach-ins live here.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from amplifier_agent import (
    AgentError,
    AgentOptions,
    ConversationMessage,
    SessionOptions,
    TextPart,
    TurnInput,
    create_agent,
)
from amplifier_core.events import ORCHESTRATOR_COMPLETE

__amplifier_module_type__ = "orchestrator"
logger = logging.getLogger(__name__)
ORCHESTRATOR_NAME = "loop-amplifier-agent"
WORKER_SESSION_ID_METADATA_KEY = "worker_session_id"
DEFAULT_APPROVAL_POLICY = "accept"
VALID_APPROVAL_POLICIES = frozenset({"accept", "deny"})
PERSISTED_TYPES = frozenset(
    {
        "turn_started",
        "tool_call",
        "tool_result",
        "approval_request",
        "approval_decision",
        "progress",
        "usage",
        "terminal",
    }
)
# Documented provider connection variables, scoped through AgentOptions.environment.
_CONNECTIONS = {
    "anthropic": {"api_key": "ANTHROPIC_API_KEY", "base_url": "ANTHROPIC_BASE_URL"},
    "openai": {"api_key": "OPENAI_API_KEY", "base_url": "OPENAI_BASE_URL"},
    "gemini": {"api_key": "GOOGLE_API_KEY", "base_url": "GOOGLE_GEMINI_BASE_URL"},
    "azure-openai": {
        "api_key": "AZURE_OPENAI_API_KEY",
        "endpoint": "AZURE_OPENAI_ENDPOINT",
    },
    "ollama": {"api_key": "OLLAMA_API_KEY"},
    "chat-completions": {
        "api_key": "CHAT_COMPLETIONS_API_KEY",
        "base_url": "CHAT_COMPLETIONS_BASE_URL",
    },
    "vllm": {"api_key": "VLLM_API_KEY", "base_url": "VLLM_BASE_URL"},
    "github-copilot": {},
    "openai-chatgpt": {},
}
_SELECTION_KEYS = {"priority", "default_model", "model", "reasoning_effort"}
CLEANUP_TIMEOUT = 10.0
CANCEL_DRAIN_TIMEOUT = 30.0


def _error(code: str, message: str, remedy: str) -> AgentError:
    return AgentError(code, "turn", message, remedy)


def serialize(value: Any) -> Any:
    """Lossless structural projection of public records, including full errors."""
    if isinstance(value, AgentError):
        return {key: serialize(item) for key, item in vars(value).items()}
    if dataclasses.is_dataclass(value):
        return {key: serialize(item) for key, item in vars(value).items()}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    return value


async def _bounded(awaitable: Any, seconds: float, operation: str) -> Any:
    """A timeout never waits indefinitely for cancellation-resistant cleanup."""
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=seconds)
        if done:
            return task.result()
        raise _error(
            "adapter_cleanup_timeout",
            f"{operation} did not settle within {seconds:g}s; effects may remain.",
            "Inspect the retained public event stream before retrying.",
        )
    finally:
        if not task.done():
            task.cancel()
            # Do not wait forever for caller/provider code that refuses cancellation.
            task.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None
            )


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    await coordinator.mount(
        "orchestrator", AmplifierAgentOrchestrator(coordinator, config or {})
    )


class AmplifierAgentOrchestrator:
    def __init__(self, coordinator: Any, config: dict[str, Any]) -> None:
        self._coordinator = coordinator
        self._config = config

    def _validate_controls(self) -> None:
        cap = self._config.get("max_turns")
        if cap is not None and not (type(cap) is int and cap == 0):
            raise ValueError(
                "max_turns is unsupported by the public agent binding; "
                "use worker=coding-agent for a positive turn cap."
            )
        for name in ("workspace", "host_config", "agent_configs"):
            if self._config.get(name):
                raise ValueError(
                    f"{name} is unsupported by the public agent binding; use "
                    "working_dir/public AgentOptions or worker=coding-agent. "
                    "Generic delegate remains built in; named roster injection does not."
                )
        policy = self._config.get("approval_policy", DEFAULT_APPROVAL_POLICY)
        if policy not in VALID_APPROVAL_POLICIES:
            raise ValueError("approval_policy must be accept or deny")

    def _resolve_working_dir(self) -> Path:
        configured = self._config.get("working_dir")
        if isinstance(configured, str) and configured:
            return Path(configured).resolve()
        get = getattr(self._coordinator, "get_capability", None)
        value = get("session.working_dir") if callable(get) else None
        return Path(
            value if isinstance(value, str) and value else os.getcwd()
        ).resolve()

    def _selection(self, providers: dict[str, Any]) -> tuple[str, str, Any, dict]:
        outer = getattr(self._coordinator, "config", {})
        entries = outer.get("providers", []) if isinstance(outer, dict) else []
        entries = [entry for entry in entries if isinstance(entry, dict)]
        entries.sort(key=lambda entry: entry.get("config", {}).get("priority", 100))
        requested = self._config.get("llm_provider")
        selected = None
        for entry in entries:
            module = entry.get("module", "")
            family = module.removeprefix("provider-")
            if requested is None or requested in (
                entry.get("id"),
                entry.get("instance_id"),
                module,
                family,
            ):
                selected = entry
                break
        family = (
            selected.get("module", "").removeprefix("provider-")
            if selected
            else requested
        )
        if not family:
            family = next(iter(providers), None)
        if family not in _CONNECTIONS:
            raise ValueError(
                f"Unknown provider family {family!r}; set llm_provider or mount "
                "a supported default provider."
            )
        settings = dict(selected.get("config", {})) if selected else {}
        model = self._config.get("llm_model") or settings.get("default_model")
        model = model or settings.get("model")
        mounted = providers.get(
            (
                selected.get("instance_id")
                or selected.get("id")
                or selected.get("module")
            )
            if selected
            else family
        )
        mounted = mounted or providers.get(family)
        if not model and mounted is not None:
            info = mounted.get_info()
            defaults = (
                info.get("defaults", {})
                if isinstance(info, dict)
                else getattr(info, "defaults", {})
            )
            model = defaults.get("model") or defaults.get("default_model")
        if not isinstance(model, str) or not model:
            raise ValueError(
                f"Model unknown for provider {family}; set llm_model or configure "
                "default_model on the matching default provider."
            )
        effort = self._config.get("reasoning_effort", settings.get("reasoning_effort"))
        # OpenAI provisioning historically stores "none" as unspecified; it
        # is not an explicit node request for the public binding's named effort.
        if "reasoning_effort" not in self._config and family in {
            "openai",
            "openai-chatgpt",
        }:
            if isinstance(effort, str):
                effort = effort.strip().lower()
                if effort in {"", "none"}:
                    effort = None
        environment = dict(self._config.get("environment") or {})
        for key, value in settings.items():
            if key in _SELECTION_KEYS:
                continue
            variable = _CONNECTIONS[family].get(key)
            if variable is None or not isinstance(value, str):
                raise ValueError(
                    f"Unsupported {family} provider setting {key!r}; configure a "
                    "documented connection variable via per-agent environment, "
                    "or use worker=coding-agent."
                )
            environment[variable] = value
        return family, model, effort, environment

    @staticmethod
    async def _history_from_context(context: Any) -> list[ConversationMessage] | None:
        if context is None or not hasattr(context, "get_messages"):
            return None
        messages = await context.get_messages()
        if not isinstance(messages, list):
            raise ValueError(
                "Pipeline history must be a list of user/assistant messages"
            )
        history = []
        for index, message in enumerate(messages):
            if (
                not isinstance(message, dict)
                or message.get("role") not in ("user", "assistant")
                or set(message) - {"role", "content"}
            ):
                raise ValueError(
                    f"Unsupported pipeline history at index {index}: "
                    "only normal user/assistant text messages are supported; "
                    "tool-call history requires worker=coding-agent."
                )
            content = message.get("content")
            if isinstance(content, str):
                parts = [TextPart(content)]
            elif isinstance(content, list) and all(
                isinstance(part, dict)
                and set(part) <= {"type", "text"}
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
                for part in content
            ):
                parts = [TextPart(part["text"]) for part in content]
            else:
                raise ValueError(
                    f"Malformed/non-text pipeline history at index {index}"
                )
            history.append(ConversationMessage(message["role"], parts))
        return history or None

    def _additional_directories(self, cwd: Path) -> list[Path] | None:
        try:
            from amplifier_module_loop_pipeline.status_contract import (
                current_node_status_path,
            )
        except ImportError:
            return None
        value = current_node_status_path.get()
        if not value:
            return None
        parent = Path(value).resolve().parent
        if parent.is_relative_to(cwd):
            return None
        if not parent.is_dir():
            raise ValueError(f"Status parent directory does not exist: {parent}")
        return [parent]

    @staticmethod
    async def _publish(event: Any, hooks: Any, persister: Any) -> None:
        if event.type not in PERSISTED_TYPES:
            return
        name = f"amplifier-agent:{event.type}"
        data = serialize(event)
        if data.get("at") is None:
            data.pop("at", None)
        # Same write-time redaction and stage/session path as every other worker.
        if persister is not None:
            await persister.make_handler(name)(name, data)
        try:
            await hooks.emit(name, data)
        except Exception:
            logger.warning("Public event hook delivery failed", exc_info=True)

    async def execute(
        self,
        prompt: str,
        context: Any,
        providers: dict[str, Any],
        tools: dict[str, Any],
        hooks: Any,
        coordinator: Any = None,
    ) -> str:
        if coordinator is not None:
            self._coordinator = coordinator
        agent = session = turn = consumer = None
        session_id = None
        status = "incomplete"
        primary: BaseException | None = None
        try:
            self._validate_controls()
            history = await self._history_from_context(context)
            family, model, effort, environment = self._selection(providers)
            cwd = self._resolve_working_dir()
            policy = self._config.get("approval_policy", DEFAULT_APPROVAL_POLICY)
            agent = await create_agent(
                AgentOptions(
                    provider=family,
                    model=model,
                    reasoning_effort=effort,
                    environment=environment or None,
                    working_directory=cwd,
                    additional_directories=self._additional_directories(cwd),
                    approvals="allow" if policy == "accept" else "deny",
                )
            )
            session = await agent.create_session(
                SessionOptions(persistence="ephemeral")
            )
            session_id = session.info.session_id
            full_prompt = self._build_prompt(
                prompt, self._config.get("user_instructions")
            )
            turn = await session.start_turn(
                TurnInput(content=[TextPart(full_prompt)], history=history)
            )
            try:
                from amplifier_module_hooks_pipeline_observability.session_events import (
                    SessionEventPersister,
                )
            except ImportError:
                persister = None
            else:
                persister = SessionEventPersister()

            async def consume() -> Any:
                terminal = None
                async for event in turn.events():
                    await self._publish(event, hooks, persister)
                    if event.type == "terminal":
                        terminal = event.payload
                if terminal is None:
                    raise _error(
                        "adapter_missing_terminal",
                        "Public event stream ended without terminal; completion unknown.",
                        "Inspect worker evidence before retrying.",
                    )
                return terminal

            # Exactly one consumer. Shielding keeps caller cancellation from killing
            # the stream before paired resolutions/terminal can be persisted.
            consumer = asyncio.create_task(consume())
            result = await asyncio.shield(consumer)
            if result.state != "success":
                status = "cancelled" if result.state == "cancelled" else "incomplete"
                raise result.error or _error(
                    "adapter_terminal_failure",
                    f"Agent turn ended {result.state} without an error record.",
                    "Inspect the terminal event.",
                )
            reply = "".join(part.text for part in result.content or [])
            status = "success"
            return reply
        except asyncio.CancelledError as exc:
            primary = exc
            status = "cancelled"
            raise
        except BaseException as exc:
            primary = exc
            raise
        finally:
            cleanup_errors = []

            def cleanup_failure(operation: str, exc: BaseException) -> None:
                # An operation's cancellation is cleanup uncertainty, not
                # another caller cancellation allowed to abort finalization.
                if isinstance(exc, asyncio.CancelledError):
                    exc = _error(
                        "adapter_cleanup_cancelled",
                        f"{operation} itself was cancelled; cleanup is unverified.",
                        "Inspect retained worker evidence before retrying.",
                    )
                cleanup_errors.append(exc)
                logger.warning("%s failed", operation, exc_info=True)

            async def finalize() -> None:
                nonlocal status
                if status == "cancelled" and turn is not None:
                    try:
                        await _bounded(turn.cancel(), CLEANUP_TIMEOUT, "turn.cancel")
                    except (Exception, asyncio.CancelledError) as exc:
                        cleanup_failure("turn.cancel", exc)
                    # A failed cancellation acknowledgement is not permission
                    # to abandon the single stream's available terminal evidence.
                    if consumer is not None:
                        try:
                            await _bounded(
                                asyncio.shield(consumer),
                                CANCEL_DRAIN_TIMEOUT,
                                "cancelled event drain",
                            )
                        except (Exception, asyncio.CancelledError) as exc:
                            cleanup_failure("cancelled event drain", exc)
                for name, handle in (
                    ("session.close", session),
                    ("agent.close", agent),
                ):
                    if handle is not None:
                        try:
                            await _bounded(handle.close(), CLEANUP_TIMEOUT, name)
                        except (Exception, asyncio.CancelledError) as exc:
                            cleanup_failure(name, exc)
                if consumer is not None and not consumer.done():
                    consumer.cancel()
                if cleanup_errors and primary is None:
                    status = "incomplete"
                try:
                    await _bounded(
                        self._emit_completion(hooks, status, session_id),
                        CLEANUP_TIMEOUT,
                        "completion delivery",
                    )
                except (Exception, asyncio.CancelledError) as exc:
                    cleanup_failure("completion delivery", exc)

            # All finalization lives in one shielded task. Late or repeated
            # caller cancellation cannot skip agent.close or completion. Each
            # operation inside has its own finite budget, including delivery.
            finalizer = asyncio.create_task(finalize())
            while not finalizer.done():
                try:
                    await asyncio.shield(finalizer)
                except asyncio.CancelledError as exc:
                    status = "cancelled"
                    if primary is None:
                        primary = exc
            finalizer.result()
            if cleanup_errors:
                if primary is None:
                    raise cleanup_errors[0]
                for exc in cleanup_errors:
                    primary.add_note(f"Cleanup/drain failed: {exc}")
            if isinstance(primary, asyncio.CancelledError):
                raise primary

    @staticmethod
    async def _emit_completion(hooks: Any, status: str, session_id: str | None) -> None:
        await hooks.emit(
            ORCHESTRATOR_COMPLETE,
            {
                "orchestrator": ORCHESTRATOR_NAME,
                "status": status,
                "turn_count": None,  # public turn is not an internal model-call count
                "metadata": (
                    {WORKER_SESSION_ID_METADATA_KEY: session_id} if session_id else {}
                ),
            },
        )

    @staticmethod
    def _build_prompt(prompt: str, user_instructions: str | None) -> str:
        if user_instructions:
            return f"{prompt}\n\nAdditional instructions:\n{user_instructions}"
        return prompt
