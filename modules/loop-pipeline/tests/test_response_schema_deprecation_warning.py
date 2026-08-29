"""RED-proof: response_schema= emits a DeprecationWarning (EXTENSIONS.md Sec23).

Maintainer ruling (Lane F extensions-undo audit, dated 2026-08-29): Sec23
(`response_schema=`) is a BACK-OUT candidate -- zero shipped `.dot` graph
across amplifier-bundle-dot-runner, amplifier-bundle-attractor,
amplifier-resolver-dot-graph, or amplifier-resolve declares it. Behavior is
NOT removed in this change; only a loud, non-suppressible deprecation
warning is added, on the same precedent as `DirectProviderBackend`
(amplifier_module_loop_pipeline/__init__.py). This test proves the warning
actually fires -- and only for nodes that declare the attribute.
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.transforms import resolve_response_schemas


def _graph_with(node: Node) -> Graph:
    return Graph(
        name="test_graph", nodes={node.id: node}, edges=[], goal="", source_dir=""
    )


def test_response_schema_inline_json_emits_deprecation_warning():
    """A node declaring inline-JSON response_schema= triggers the warning."""
    node = Node(id="extract", response_schema='{"type": "object"}')
    graph = _graph_with(node)

    with pytest.warns(DeprecationWarning, match="response_schema.*deprecated"):
        resolve_response_schemas(graph)

    # Behavior is unchanged: the value still resolves to a dict.
    assert graph.nodes["extract"].response_schema == {"type": "object"}


def test_response_schema_warning_names_the_offending_node():
    """The warning message identifies which node declared response_schema=."""
    node = Node(id="my_special_node", response_schema='{"type": "object"}')
    graph = _graph_with(node)

    with pytest.warns(DeprecationWarning, match="my_special_node"):
        resolve_response_schemas(graph)


def test_no_response_schema_no_warning():
    """A node without response_schema= never triggers the warning (no change
    in behavior for the overwhelming majority of nodes -- the ones this
    census found actually shipping)."""
    node = Node(id="plain")
    graph = _graph_with(node)

    with warnings_none():
        resolve_response_schemas(graph)


class warnings_none:
    """Context manager asserting no warning of any kind was raised."""

    def __enter__(self):
        import warnings as _w

        self._cm = _w.catch_warnings(record=True)
        self._records = self._cm.__enter__()
        import warnings as _w2

        _w2.simplefilter("always")
        return self

    def __exit__(self, exc_type, exc, tb):
        self._cm.__exit__(exc_type, exc, tb)
        assert not self._records, f"expected no warnings, got: {self._records}"
        return False


def test_already_resolved_dict_response_schema_still_warns_once():
    """A programmatically-constructed node whose response_schema is already
    a dict (not a raw DOT string) still warns -- the deprecation applies to
    *use*, not to which resolution branch runs."""
    node = Node(id="pre_resolved", response_schema={"type": "object"})
    graph = _graph_with(node)

    with pytest.warns(DeprecationWarning, match="pre_resolved"):
        resolve_response_schemas(graph)

    # Still a dict, untouched.
    assert graph.nodes["pre_resolved"].response_schema == {"type": "object"}
