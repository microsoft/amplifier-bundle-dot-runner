"""Core Client class with provider routing (Spec §2.2, §3, §4.1-4.2)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from unified_llm.adapters import ProviderAdapter
from unified_llm.errors import ConfigurationError
from unified_llm.middleware import (
    Middleware,
    apply_middleware,
    apply_streaming_middleware,
)
from unified_llm.stream_validation import validate_stream
from unified_llm.types import Request, Response, StreamEvent

# Module-level default client (Spec §2.5)
_default_client: Client | None = None

#: Canonical provider name -> accepted env var name(s), in priority order.
#: SINGLE SOURCE OF TRUTH for "which provider(s) does the environment have an
#: API key for" -- shared by two consumers that must never drift apart:
#:   1. ``Client.from_env()`` below (constructs a live adapter per detected key).
#:   2. ``amplifier_module_pipeline_runner.default_worker`` (dot-runner), which
#:      mounts provider MODULES onto a synthesized ``--worker loop-agent`` /
#:      ``--worker amplifier-agent`` bundle -- see :func:`detect_configured_providers`.
#:      That caller never wants the adapter/SDK imports ``from_env()`` performs,
#:      only the env-var detection, so it is factored out as its own function
#:      rather than duplicated (issue #338: two independent copies of this env
#:      var list is exactly how a provider silently stopped being detected).
#: Gemini accepts GOOGLE_API_KEY as an alias -- same precedence ``from_env()``
#: has always used.
PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


def detect_configured_providers(env: Mapping[str, str] | None = None) -> list[str]:
    """Return canonical provider names with a configured API key present.

    Detection-only: never imports a provider SDK or constructs an adapter
    (unlike :meth:`Client.from_env`, which does both). Order matches
    :data:`PROVIDER_ENV_KEYS` (anthropic, openai, gemini) -- the same order
    ``from_env()`` registers adapters in, so index 0 of a non-empty result is
    the same provider ``from_env()`` would pick as its default.

    Args:
        env: Optional mapping to read keys from (defaults to ``os.environ``).
            Exposed for hermetic tests that need to pass a synthetic mapping
            without mutating the real process environment.

    Returns:
        Canonical provider names (e.g. ``["anthropic", "openai"]``) whose key
        is present. Empty list if none are configured.
    """
    import os

    source = env if env is not None else os.environ
    return [
        name
        for name, keys in PROVIDER_ENV_KEYS.items()
        if any(source.get(k) for k in keys)
    ]


class Client:
    """Provider-agnostic LLM client (Spec §3).

    Routes requests to registered provider adapters. Applies middleware.
    Does NOT retry — that's Layer 4's responsibility.
    """

    def __init__(
        self,
        providers: dict[str, ProviderAdapter],
        default_provider: str | None = None,
        middleware: list[Middleware] | None = None,
    ) -> None:
        self.providers = dict(providers)
        self.default_provider = default_provider
        self._middleware = middleware or []

    def _resolve_adapter(self, request: Request) -> ProviderAdapter:
        """Resolve which adapter handles this request."""
        provider_name = request.provider or self.default_provider
        if provider_name is None:
            raise ConfigurationError(
                "No provider specified and no default provider configured. "
                "Set provider on the request or configure a default_provider."
            )
        adapter = self.providers.get(provider_name)
        if adapter is None:
            raise ConfigurationError(
                f"Provider '{provider_name}' not found. "
                f"Available providers: {list(self.providers.keys())}"
            )
        return adapter

    async def complete(self, request: Request) -> Response:
        """Low-level blocking call. No retry. (Spec §4.1)."""
        from unified_llm._cost import compute_cost

        adapter = self._resolve_adapter(request)

        async def handler(req: Request) -> Response:
            return await adapter.complete(req)

        response = await apply_middleware(self._middleware, handler, request)

        # Inject cost_usd if not already set by the adapter
        if response.usage.cost_usd is None:
            computed = compute_cost(
                response.model,
                response.usage.input_tokens,
                response.usage.output_tokens,
                cache_read_tokens=response.usage.cache_read_tokens or 0,
                cache_write_tokens=response.usage.cache_write_tokens or 0,
            )
            if computed is not None:
                from unified_llm.types import Usage

                response.usage = Usage(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    total_tokens=response.usage.total_tokens,
                    reasoning_tokens=response.usage.reasoning_tokens,
                    cache_read_tokens=response.usage.cache_read_tokens,
                    cache_write_tokens=response.usage.cache_write_tokens,
                    raw=response.usage.raw,
                    cost_usd=computed,
                )

        return response

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        """Low-level streaming call. No retry. (Spec §4.2)."""
        adapter = self._resolve_adapter(request)

        async def handler(req: Request) -> AsyncIterator[StreamEvent]:
            if req.stream_validation_mode is None:
                async for event in validate_stream(adapter.stream(req)):
                    yield event
            else:
                async for event in validate_stream(
                    adapter.stream(req), mode=req.stream_validation_mode
                ):
                    yield event

        async for event in apply_streaming_middleware(
            self._middleware, handler, request
        ):
            yield event

    async def close(self) -> None:
        """Release resources on all adapters (Spec §2.4)."""
        for adapter in self.providers.values():
            if hasattr(adapter, "close"):
                await adapter.close()

    @classmethod
    def from_env(cls) -> Client:
        """Create a Client by detecting API keys from environment (Spec §2.2).

        Registers adapters for providers whose keys are present.
        First registered becomes default.

        Detection itself is delegated to :func:`detect_configured_providers`
        (this module) -- the single source of truth for the env-var list, also
        used by dot-runner's synthesized-bundle provider mounting. Only the
        per-provider adapter CONSTRUCTION (importing the provider SDK) stays
        here, since that is this method's own job and not shared detection.
        """
        providers: dict[str, ProviderAdapter] = {}
        default: str | None = None

        for name in detect_configured_providers():
            if name == "anthropic":
                from unified_llm.adapters.anthropic import AnthropicAdapter

                providers["anthropic"] = AnthropicAdapter()
            elif name == "openai":
                from unified_llm.adapters.openai import OpenAIAdapter

                providers["openai"] = OpenAIAdapter()
            elif name == "gemini":
                from unified_llm.adapters.gemini import GeminiAdapter

                providers["gemini"] = GeminiAdapter()
            else:  # pragma: no cover - defensive; PROVIDER_ENV_KEYS is closed
                continue
            if default is None:
                default = name

        if not providers:
            raise ConfigurationError(
                "No API keys found in environment. Set at least one of: "
                "ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY"
            )

        return cls(providers=providers, default_provider=default)


def set_default_client(client: Client) -> None:
    """Set the module-level default client (Spec §2.5)."""
    global _default_client
    _default_client = client


def get_default_client() -> Client:
    """Get or lazily initialize the default client."""
    global _default_client
    if _default_client is None:
        _default_client = Client.from_env()
    return _default_client
