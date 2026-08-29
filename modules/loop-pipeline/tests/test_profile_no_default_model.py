"""Tests that _resolve_model() requires explicit model specification --
EXCEPT for the one case the LANE-D maintainer ruling carves out: a node
that sets `llm_provider` alone (no `llm_model`) now resolves a documented,
LIVE-resolved per-provider default family token instead of failing loud
(spec Sec8.5 rung 4 / Appendix A "Handler/system default" -- see
_PROVIDER_DEFAULT_MODEL_PATTERN and its citations in backend.py, and
specs/EXTENSIONS.md entry 41).

Original (pre-LANE-D) behavior, still true for the genuinely-bare case:
    _resolve_model(node)
    → if node.llm_model is None AND node.llm_provider is also None,
      raise ValueError -- forces every pipeline to declare model or
      provider explicitly.  (test_resolve_model_raises_without_explicit_model,
      below, still pins this.)

Historically-removed anti-pattern (pre-2026, NOT reinstated by LANE-D):
    _resolve_model(node)
    → if node.llm_model is None, return
      _DEFAULT_MODELS.get(provider, "claude-sonnet-4-20250514")
    → i.e., silently falling back to a LITERAL, hardcoded model id
      (which rots -- exactly why _DEFAULT_MODELS was deleted).

LANE-D behavior (this file, updated): llm_provider EXPLICITLY set + no
llm_model -> a per-provider default FAMILY TOKEN (e.g. "sonnet",
"gpt-5.*[0-9]", "gemini-3*pro*"), resolved LIVE via the same
unified_llm.resolve_latest_for machinery an author gets from writing
`llm_model=sonnet` themselves.  This is never a literal, rotting model id
-- the "cranky-old-sam: no silent defaults" principle is preserved in
spirit (no hardcoded id is baked in); what changed is that llm_provider is
now honored as the spec-level signal it is, so community .dot authors who
write `llm_provider=openai` alone are never surprised by a crash.

This is the direct-tool-loop (Path B) code path.  Path A (spawn) is
unaffected by this file (see test_provider_preflight.py /
test_direct_provider_backend_shim.py for that path's own coverage).
"""

import pytest

from amplifier_module_loop_pipeline.graph import Node

# ---------------------------------------------------------------------------
# Core: _resolve_model() must raise when no model is set
# ---------------------------------------------------------------------------


def test_resolve_model_raises_without_explicit_model():
    """_resolve_model() must raise ValueError when node.llm_model is not set.

    Before the fix: returns a hardcoded default from _DEFAULT_MODELS.
    After the fix: raises ValueError with a clear message.
    """
    from amplifier_module_loop_pipeline.backend import _resolve_model

    # Node with no explicit model
    node = Node(id="my-node", shape="box", prompt="Do something")

    with pytest.raises(ValueError) as exc_info:
        _resolve_model(node)

    error_message = str(exc_info.value)
    assert "model" in error_message.lower(), (
        f"ValueError message should mention 'model'. Got: {error_message!r}"
    )
    assert "my-node" in error_message or "llm_model" in error_message.lower(), (
        f"Error should identify what's missing (node id or attribute). "
        f"Got: {error_message!r}"
    )


def test_resolve_model_returns_default_token_for_anthropic_provider_without_model():
    """LANE-D: llm_provider=anthropic alone must resolve to the documented
    default FAMILY TOKEN ("sonnet"), not a literal/hardcoded model id, and
    NOT raise. This is the exact RED-proof scenario the maintainer ruling
    names: a community .dot author sets llm_provider alone.
    """
    from amplifier_module_loop_pipeline.backend import (
        _PROVIDER_DEFAULT_MODEL_PATTERN,
        _resolve_model,
    )

    node = Node(id="anthro-node", shape="box", prompt="Anthropic task")
    node.attrs["llm_provider"] = "anthropic"

    result = _resolve_model(node)

    assert result == _PROVIDER_DEFAULT_MODEL_PATTERN["anthropic"][0] == "sonnet"
    # Never the OLD hardcoded, rotting literal id this file used to guard against.
    assert result != "claude-sonnet-4-20250514"


def test_resolve_model_returns_default_token_for_openai_provider_without_model():
    """LANE-D: llm_provider=openai alone must resolve to the documented
    default family glob, not raise, and not a literal hardcoded 'gpt-4o'.
    """
    from amplifier_module_loop_pipeline.backend import (
        _PROVIDER_DEFAULT_MODEL_PATTERN,
        _resolve_model,
    )

    node = Node(id="oai-node", shape="box", prompt="OpenAI task")
    node.attrs["llm_provider"] = "openai"

    result = _resolve_model(node)

    assert result == _PROVIDER_DEFAULT_MODEL_PATTERN["openai"][0] == "gpt-5.*[0-9]"
    assert result != "gpt-4o"


def test_resolve_model_returns_default_token_for_gemini_provider_without_model():
    """LANE-D: llm_provider=gemini alone must resolve to the documented
    default family glob, not raise, and not a literal hardcoded
    'gemini-2.0-flash'.
    """
    from amplifier_module_loop_pipeline.backend import (
        _PROVIDER_DEFAULT_MODEL_PATTERN,
        _resolve_model,
    )

    node = Node(id="gem-node", shape="box", prompt="Gemini task")
    node.attrs["llm_provider"] = "gemini"

    result = _resolve_model(node)

    assert result == _PROVIDER_DEFAULT_MODEL_PATTERN["gemini"][0] == "gemini-3*pro*"
    assert result != "gemini-2.0-flash"


def test_resolve_model_raises_for_unknown_provider_without_model():
    """Malformed/unknown provider stays loud: _resolve_model() must still
    raise ValueError when llm_provider is set to a value with no documented
    default -- no silent guess for a provider we don't recognize.
    """
    from amplifier_module_loop_pipeline.backend import _resolve_model

    node = Node(id="bogus-node", shape="box", prompt="Unknown provider task")
    node.attrs["llm_provider"] = "not-a-real-provider"

    with pytest.raises(ValueError) as exc_info:
        _resolve_model(node)

    assert "not-a-real-provider" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Positive: explicit model still works
# ---------------------------------------------------------------------------


def test_resolve_model_returns_explicit_model():
    """_resolve_model() must return the explicitly set llm_model."""
    from amplifier_module_loop_pipeline.backend import _resolve_model

    node = Node(
        id="explicit-node",
        shape="box",
        prompt="With explicit model",
        attrs={"llm_model": "claude-3-7-sonnet-20250219"},
    )

    result = _resolve_model(node)

    assert result == "claude-3-7-sonnet-20250219", (
        f"_resolve_model() should return the explicit llm_model, got {result!r}"
    )


def test_resolve_model_returns_explicit_model_via_llm_model_attr():
    """_resolve_model() must use node.llm_model when set."""
    from amplifier_module_loop_pipeline.backend import _resolve_model

    # node.llm_model is a direct attribute (not via node.attrs)
    node = Node(id="direct-attr-node", shape="box", prompt="Direct attr test")
    # llm_model is a dataclass field on Node
    node.llm_model = "gpt-4.1-mini"

    result = _resolve_model(node)

    assert result == "gpt-4.1-mini", (
        f"_resolve_model() should return node.llm_model='gpt-4.1-mini', got {result!r}"
    )


# ---------------------------------------------------------------------------
# Structural check: _DEFAULT_MODELS is removed (or at minimum, not consulted)
# ---------------------------------------------------------------------------


def test_default_models_dict_not_used_as_fallback():
    """The _DEFAULT_MODELS dict (if it still exists) must NOT be consulted as a fallback.

    This test verifies the behavior, not the existence of the dict.  Even if
    _DEFAULT_MODELS remains for other reasons, _resolve_model() must not use
    it when llm_model is unset — it must raise instead.
    """
    import amplifier_module_loop_pipeline.backend as backend_module
    from amplifier_module_loop_pipeline.backend import _resolve_model

    # If the dict exists, its values must not be returned by _resolve_model
    default_models = getattr(backend_module, "_DEFAULT_MODELS", {})

    node = Node(id="probe-node", shape="box", prompt="Probe")

    # Regardless of whether _DEFAULT_MODELS exists, _resolve_model must raise
    with pytest.raises(ValueError):
        result = _resolve_model(node)
        # If we somehow reach here, fail the test explicitly
        if default_models:
            assert result not in default_models.values(), (
                f"_resolve_model() returned a value from _DEFAULT_MODELS: {result!r}. "
                f"It must raise instead of returning a default."
            )
