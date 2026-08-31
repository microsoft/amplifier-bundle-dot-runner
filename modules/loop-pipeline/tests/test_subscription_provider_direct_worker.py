"""Hermetic RED-proofs for the subscription-provider seam on the ``llm-direct``
/ ``direct`` worker path (github-copilot / openai-chatgpt).

★ THE ARCHITECTURAL CONSTRAINT: ``llm-direct`` is the PURE unified-llm-spec
client (SDK-direct anthropic/openai/gemini, by maintainer ruling) -- it is
architecturally incapable of serving a subscription provider. A node
declaring ``llm_provider="github-copilot"``/``"openai-chatgpt"`` under
``llm-direct`` must fail loud with a message naming the fix (add
``--worker coding-agent``/``--worker amplifier-agent``, or change
``llm_provider``) -- never a generic "no adapter found" error several calls
deeper.

Also covers the per-provider ``llm_model`` fail-loud hint
(``_resolve_concrete_model``): both subscription providers proxy multiple
model families through one mounted adapter, so a family-token/glob
``llm_model`` (e.g. "sonnet") can never be live-resolved for them via
``unified_llm.resolve_latest_for`` (adapters only for the pure
anthropic/openai/gemini triad).
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.backend import (
    SUBSCRIPTION_ONLY_PROVIDERS,
    _resolve_concrete_model,
)
from amplifier_module_loop_pipeline.workers.direct_worker import DirectWorker


class _FakeNode:
    def __init__(self, node_id: str, llm_provider: str, llm_model: str | None = None):
        self.id = node_id
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.attrs = {"llm_provider": llm_provider}
        if llm_model is not None:
            self.attrs["llm_model"] = llm_model


@pytest.mark.parametrize("provider", sorted(SUBSCRIPTION_ONLY_PROVIDERS))
@pytest.mark.asyncio
async def test_llm_direct_fails_loud_for_subscription_provider(provider):
    worker = DirectWorker()
    node = _FakeNode("n1", provider, llm_model="some-model")

    with pytest.raises(ValueError) as excinfo:
        await worker.run(node, "prompt", context=None, replayed_history=[])

    message = str(excinfo.value)
    assert provider in message
    assert "coding-agent" in message
    assert "amplifier-agent" in message
    assert "anthropic, openai, gemini" in message


@pytest.mark.parametrize("provider", sorted(SUBSCRIPTION_ONLY_PROVIDERS))
@pytest.mark.asyncio
async def test_llm_direct_fails_loud_for_subscription_provider_no_model(provider):
    """Fails loud on the WORKER-CAPABILITY question before ever reaching the
    (also-unresolvable) model-token question -- fires regardless of whether
    llm_model was set at all."""
    worker = DirectWorker()
    node = _FakeNode("n1", provider, llm_model=None)

    with pytest.raises(ValueError, match="llm-direct"):
        await worker.run(node, "prompt", context=None, replayed_history=[])


def test_native_providers_are_not_subscription_only():
    """Sanity: the guard's own membership set must never include a native
    provider -- proves the subscription-only check cannot be overly broad."""
    assert "anthropic" not in SUBSCRIPTION_ONLY_PROVIDERS
    assert "openai" not in SUBSCRIPTION_ONLY_PROVIDERS
    assert "gemini" not in SUBSCRIPTION_ONLY_PROVIDERS


@pytest.mark.asyncio
async def test_llm_direct_native_provider_unaffected(monkeypatch):
    """The new guard must never fire for a native provider -- it should
    reach (and fail on, hermetically -- no real API key/network here) the
    real unified_llm call instead, never the subscription-provider message.
    Tolerant of EITHER outcome shape (a caught Outcome, or a raised
    ConfigurationError from a hermetic missing-API-key environment) --
    what matters is that neither ever mentions the subscription-only fix
    text for a native provider."""
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    worker = DirectWorker()
    node = _FakeNode("n1", "anthropic", llm_model="claude-sonnet-4-5")

    try:
        _, outcome = await worker.run(node, "prompt", context=None, replayed_history=[])
        message = outcome.failure_reason or ""
    except Exception as exc:  # noqa: BLE001 -- hermetic env may raise directly
        message = str(exc)

    assert "coding-agent" not in message
    assert "amplifier-agent" not in message


# ---------------------------------------------------------------------------
# _resolve_concrete_model: per-provider llm_model-required hint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", sorted(SUBSCRIPTION_ONLY_PROVIDERS))
@pytest.mark.asyncio
async def test_family_token_model_fails_loud_with_provider_hint(provider):
    """A family-token llm_model (e.g. "sonnet") cannot be live-resolved for
    a subscription provider -- unified_llm has no SDK adapter for it. The
    error must name the real fix (explicit concrete llm_model, or omit it
    to use the module's own default_model)."""
    with pytest.raises(ValueError) as excinfo:
        await _resolve_concrete_model(provider, "sonnet")

    message = str(excinfo.value)
    assert "llm_model" in message
    assert provider in message


@pytest.mark.asyncio
async def test_concrete_model_passthrough_unaffected_for_subscription_provider():
    """A CONCRETE model id (not a glob/family-token) is returned unchanged
    with no resolution attempt -- the new guard must not fire for it."""
    result = await _resolve_concrete_model("github-copilot", "claude-sonnet-4.6")
    assert result == "claude-sonnet-4.6"


@pytest.mark.asyncio
async def test_absent_model_passthrough_unaffected_for_subscription_provider():
    """None/empty llm_model is returned unchanged (the spawn path tolerates
    a missing model -- the mounted provider module supplies its own
    default_model) -- the new guard must not fire for it either."""
    result = await _resolve_concrete_model("openai-chatgpt", None)
    assert result is None
