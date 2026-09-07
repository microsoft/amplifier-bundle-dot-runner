"""Non-canonical provider-selection attributes refuse at startup.

A node that writes ``provider="openai"`` instead of the nlspec's
``llm_provider="openai"`` reads to every human as a deliberate model choice
and is read by nothing at all: the run takes the engine's default provider and
reports success.  That is issue #155's Mode B silent substitution reached by a
typo instead of by a missing profile, and it gets the same answer -- refuse at
the earliest static checkpoint, naming the node, the attribute, and the
canonical attribute to write instead (EXTENSIONS.md Section 36 fail-closed
doctrine).

Canonical set, fixed by the spec and not by this engine:
``attractor-spec-canonical.md`` Section 2.6 (:160-162), Appendix A
(:2018-2020) and Section 8.6's stylesheet grammar (:1460) all name exactly
``llm_model`` / ``llm_provider`` / ``reasoning_effort``.
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.preflight import (
    PROVIDER_SELECTION_ALIASES,
    ProviderPreflightError,
    check_provider_selection_attrs,
)


def _graph(*nodes: Node) -> Graph:
    return Graph(name="g", nodes={n.id: n for n in nodes}, edges=[])


def _box(node_id: str, **attrs: str) -> Node:
    return Node(id=node_id, shape="box", attrs=dict(attrs))


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_refuses_provider_instead_of_llm_provider():
    with pytest.raises(ProviderPreflightError) as exc:
        check_provider_selection_attrs(_graph(_box("critique", provider="openai")))

    message = str(exc.value)
    assert "critique" in message, "the refusal must name the offending node"
    assert 'provider="openai"' in message, "the refusal must name the attribute"
    assert "llm_provider" in message, "the refusal must name the canonical attribute"


def test_refuses_model_instead_of_llm_model():
    with pytest.raises(ProviderPreflightError) as exc:
        check_provider_selection_attrs(_graph(_box("author", model="gpt-5.6-luna")))

    message = str(exc.value)
    assert "author" in message
    assert 'model="gpt-5.6-luna"' in message
    assert "llm_model" in message


@pytest.mark.parametrize(
    ("alias", "canonical"), sorted(PROVIDER_SELECTION_ALIASES.items())
)
def test_every_alias_in_the_table_refuses_and_names_its_canonical(
    alias: str, canonical: str
):
    """No entry in the table may be dead -- each one must actually refuse."""
    with pytest.raises(ProviderPreflightError) as exc:
        check_provider_selection_attrs(_graph(_box("n", **{alias: "x"})))

    message = str(exc.value)
    assert f'{alias}="x"' in message
    assert canonical in message


def test_names_every_offending_node_in_one_error():
    """One misconfiguration costs one clear error, not a discovery loop."""
    with pytest.raises(ProviderPreflightError) as exc:
        check_provider_selection_attrs(
            _graph(
                _box("a", provider="openai"),
                _box("b", model="gpt-5.6-luna"),
                _box("c", llm_provider="anthropic"),  # canonical -- fine
            )
        )

    message = str(exc.value)
    assert "'a'" in message
    assert "'b'" in message
    assert "'c'" not in message, "a canonically-declared node must not be blamed"


# ---------------------------------------------------------------------------
# What must NOT refuse
# ---------------------------------------------------------------------------


def test_canonical_attributes_pass():
    check_provider_selection_attrs(
        _graph(
            _box(
                "ok",
                llm_provider="openai",
                llm_model="gpt-5.6-luna",
                reasoning_effort="medium",
            )
        )
    )


def test_unrelated_attributes_pass():
    """The nlspec's node-attribute surface is open by design.

    Only names that LOOK like provider selection are policed; refusing every
    unknown attribute would break conformant graphs (Section 2.6 passes
    unrecognized attributes through, and EXTENSIONS Section 40's ``worker=``
    is itself an additive attribute).
    """
    check_provider_selection_attrs(
        _graph(
            _box(
                "ok",
                worker="coding-agent",
                fidelity="full",
                max_agent_turns="30",
                must_write=".ai/x.md",
                prompt="do it",
                thread_id="t",
            )
        )
    )


def test_non_llm_node_types_are_not_policed():
    """An inert attribute on a tool node selects nothing and is out of scope.

    Same scope boundary check_provider_preflight already draws.
    """
    tool = Node(id="t", shape="box", type="tool", attrs={"provider": "openai"})
    check_provider_selection_attrs(_graph(tool))


def test_empty_graph_passes():
    check_provider_selection_attrs(_graph())
