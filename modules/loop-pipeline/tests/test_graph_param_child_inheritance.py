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


class TestDiagnosticNamesTheMechanismPerPath:
    """The missing-param diagnostic must not speak CLI-only vocabulary.

    The original message said "Pass --param <name>=<value>" -- correct on the
    CLI, and actively misleading on every other path this parser is reached
    from, which is most of them once a graph is composed. A user who hit it
    while running a CHILD graph, or through the mounted orchestrator, was told
    to pass a flag that path has no way to accept.

    These assertions deliberately check the param NAME plus the presence of
    each MECHANISM, not the full sentence -- pinning the exact prose would
    make the message unrewordable without a test edit, which is how a
    diagnostic ends up frozen in the wrong vocabulary in the first place.
    """

    def _message(self) -> str:
        try:
            parse_dot(CHILD_DOT)
        except ValueError as exc:
            return str(exc)
        raise AssertionError("expected ValueError naming the missing param")

    def test_names_the_missing_param(self) -> None:
        assert "max_duration" in self._message()

    def test_names_the_cli_mechanism(self) -> None:
        assert "--param max_duration=" in self._message()

    def test_names_the_mounted_orchestrator_mechanism(self) -> None:
        msg = self._message()
        assert 'config["params"]' in msg, (
            "the mounted-orchestrator path supplies params via config['params'], "
            "not a CLI flag -- the diagnostic must say so"
        )

    def test_names_the_composed_child_mechanism(self) -> None:
        msg = self._message()
        assert "parent" in msg.lower(), (
            "a composed child inherits its parent's params -- the diagnostic "
            "must point at the PARENT's entry point, not at a flag the child "
            "has no way to receive"
        )
        assert "dot_file=" in msg or "folder" in msg, (
            "the diagnostic must name the composition shapes (shape=folder / "
            "dot_file=, manager-loop child) so the reader can tell which path "
            "they are on"
        )

    def test_stays_loud_about_declarative_only(self) -> None:
        msg = self._message()
        assert "declarative-only" in msg
        assert "no shell-style default" in msg

    def test_does_not_claim_the_cli_is_the_only_mechanism(self) -> None:
        """The pre-reword message ended with a bare CLI imperative."""
        assert "Pass --param" not in self._message()


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
