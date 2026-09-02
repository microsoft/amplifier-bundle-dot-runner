"""Graph-level ``$name`` params must resolve at EVERY parse site, not just the CLI.

EXTENSIONS.md entry 43 added ``parse_dot(source, params=...)`` so a graph-level
duration attribute can hold a bare ``"$name"`` token, resolved at PARSE time and
failing loud when the param is absent. The entry's implementation list covers
``remote_dot.py`` and pipeline-runner's ``runner.py`` -- the direct-run path.

Three other parse sites were left passing no params at all:

* ``handlers/pipeline.py``  -- a ``shape=folder`` / ``dot_file=`` CHILD graph
* ``handlers/manager_loop.py`` -- a manager-loop child dotfile
* ``__init__.py`` -- the mounted ``PipelineOrchestrator``, which additionally
  read ``config["params"]`` only AFTER its parse

The asymmetry mattered because the parent's params already reach a child at
EXECUTION time: ``PipelineHandler.execute`` clones the whole parent context,
which carries ``graph.params_values``, so node-level ``$param`` expansion
(entry 21) has always worked inside children. Only the parse-time mechanism
stopped at the boundary -- making a child graph the one place a ``"$name"``
graph attribute could never resolve, whatever the caller supplied.
"""

from __future__ import annotations

import inspect

from amplifier_module_loop_pipeline import handlers as handlers_pkg
from amplifier_module_loop_pipeline.dot_parser import parse_dot

CHILD_DOT = """digraph Child {
  graph [max_pipeline_duration="$max_duration", params="max_duration"];
  Start [shape=Mdiamond];
  T [shape=parallelogram, tool_command="printf ok"];
  Exit [shape=Msquare];
  Start -> T;
  T -> Exit;
}"""


class TestParseContract:
    def test_absent_param_fails_loud(self) -> None:
        try:
            parse_dot(CHILD_DOT)
        except ValueError as exc:
            assert "max_duration" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected ValueError naming the missing param")

    def test_supplied_param_resolves(self) -> None:
        assert (
            parse_dot(CHILD_DOT, params={"max_duration": "19800s"}).max_pipeline_duration
            == 19800000
        )


class TestEveryParseSiteThreadsParams:
    """Static coverage across the parse sites entry 43 left behind.

    A behavioural test per site would need a full child-engine run each; the
    static form pins the exact regression (dropping ``params=``) at the exact
    lines that carried it, in the same partial-coverage class this repo's own
    AGENTS.md names.
    """

    def _parse_calls(self, module) -> list[str]:
        import re

        source = inspect.getsource(module)
        return [
            call
            for call in re.findall(r"parse_dot\(\s*([^)]*)\)", source, re.S)
            if "dot_source" in call
        ]

    def test_child_pipeline_handler(self) -> None:
        from amplifier_module_loop_pipeline.handlers import pipeline as mod

        calls = self._parse_calls(mod)
        assert calls, "expected a parse_dot(dot_source ...) call"
        for call in calls:
            assert "params=" in call, f"child parse must thread params: {call!r}"

    def test_manager_loop_handler(self) -> None:
        from amplifier_module_loop_pipeline.handlers import manager_loop as mod

        calls = self._parse_calls(mod)
        assert calls, "expected a parse_dot(dot_source ...) call"
        for call in calls:
            assert "params=" in call, f"manager child parse must thread params: {call!r}"

    def test_mounted_orchestrator_reads_params_before_parse(self) -> None:
        import amplifier_module_loop_pipeline as mod

        source = inspect.getsource(mod)
        parse_at = source.index("load_remote_or_local_graph(")
        # the params read must appear before the parse, not after it
        read_at = source.index('self.config.get("params")')
        assert read_at < parse_at, (
            "the mounted orchestrator must read config['params'] BEFORE parsing, "
            "or a graph-level $name attribute can never resolve on this path"
        )
        assert "params=params" in source[parse_at : parse_at + 200], (
            "the mounted orchestrator must pass params= into the graph load"
        )


def test_handlers_package_importable() -> None:
    assert handlers_pkg is not None
