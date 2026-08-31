"""RED-proofs for the sole-mounted provider default (idea-transfer from
microsoft/amplifier-bundle-attractor#322, credited in this feature's commit).

Today's (pre-existing, verified) behavior: a node with NO ``llm_provider``
attribute at all falls back to a LITERAL ``"anthropic"`` in
``AmplifierBackend.run()`` -- unconditionally, regardless of what is
actually mounted. ``test_backend.py::test_backend_default_provider_is_anthropic``
already locks this in for the sole-anthropic-mounted case (unaffected by
this fix, since the sole mounted provider there already IS anthropic).

This fix: when exactly ONE provider is mounted (``profiles`` has exactly one
key), an implicit node defaults to IT -- not a hardcoded "anthropic" that
may not even be mounted (e.g. a subscription-only environment with just
github-copilot configured). Zero or multiple mounted preserves the literal
"anthropic" fallback byte-for-byte -- a deliberate, scope-bounded decision
(see this feature's report) that keeps every existing multi-profile caller
unaffected.
"""

from __future__ import annotations

from amplifier_module_loop_pipeline.backend import _implicit_default_provider


def test_sole_mounted_non_anthropic_provider_becomes_the_default():
    """THE fix: a subscription-only environment (just github-copilot
    mounted) must default an unattributed node to github-copilot, not to a
    hardcoded "anthropic" that isn't even mounted."""
    assert _implicit_default_provider({"github-copilot": "some-agent"}) == (
        "github-copilot"
    )


def test_sole_mounted_openai_chatgpt_becomes_the_default():
    assert _implicit_default_provider({"openai-chatgpt": "some-agent"}) == (
        "openai-chatgpt"
    )


def test_sole_mounted_anthropic_is_unaffected():
    """Byte-identical to today's behavior when the sole mounted provider
    already is anthropic (test_backend.py's existing proof)."""
    assert _implicit_default_provider({"anthropic": "attractor-anthropic"}) == (
        "anthropic"
    )


def test_zero_mounted_preserves_literal_anthropic_fallback():
    """Scope decision: keep existing behavior byte-for-byte when nothing is
    mounted at all (e.g. a bare stub coordinator/test harness)."""
    assert _implicit_default_provider({}) == "anthropic"


def test_multiple_mounted_preserves_literal_anthropic_fallback():
    """Scope decision (task: "keep existing behavior" for the ambiguous
    multi-mount case): today's literal "anthropic" fallback is left
    unchanged when more than one provider is mounted -- deliberately NOT
    replaced with fail-loud, to avoid regressing any existing multi-profile
    caller whose nodes omit llm_provider."""
    assert (
        _implicit_default_provider({"anthropic": "a", "openai": "b", "gemini": "c"})
        == "anthropic"
    )
    assert (
        _implicit_default_provider({"github-copilot": "a", "openai-chatgpt": "b"})
        == "anthropic"
    )
