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


# ---------------------------------------------------------------------------
# The generalized guard: EVERY call site, discovered structurally.
#
# The class-per-site tests above pin the three sites PR #42 swept, by name.
# That is the right shape for a regression pin and the wrong shape for a
# standing invariant: it can only ever catch a regression at a site someone
# already thought to enumerate. The sixth site this guard found on its first
# run (`remote_dot.extract_dot_file_refs`, the MATERIALIZE-time parse -- see
# entry 43's 2026-09-02 addendum) is the proof: it had been dropping `params=`
# since entry 43 shipped, through the #42 sweep, unnoticed, because nobody had
# listed it.
#
# So this walks the package with `ast` and asserts the invariant over whatever
# it finds, rather than over a list a human maintains. A NEW call site that
# threads params passes silently; a new one that does not fails by name.
# ---------------------------------------------------------------------------

import ast
from pathlib import Path

# Callees that take the graph-level `$name` mapping. `parse_dot` resolves it;
# `load_remote_or_local_graph` is the shared materialize/parse hook that must
# forward it.
_PARAMS_TAKING_CALLEES = {"parse_dot", "load_remote_or_local_graph"}

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "amplifier_module_loop_pipeline"
_MODULES_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PACKAGE_ROOT = (
    _MODULES_ROOT / "pipeline-runner" / "amplifier_module_pipeline_runner"
)


def _callee_name(node: ast.Call) -> str | None:
    """Return the called NAME for both `parse_dot(...)` and `mod.parse_dot(...)`."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _find_param_taking_call_sites(package_root: Path) -> list[dict[str, object]]:
    """Every call to a params-taking callee under ``package_root``.

    Definitions are excluded structurally (a ``def`` is not an ``ast.Call``),
    as are imports. Tests are excluded by scanning only the package directory.
    """
    sites: list[dict[str, object]] = []
    for path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee_name(node)
            if name not in _PARAMS_TAKING_CALLEES:
                continue
            keywords = {kw.arg for kw in node.keywords}
            sites.append(
                {
                    "file": str(path.relative_to(package_root.parent)),
                    "line": node.lineno,
                    "callee": name,
                    # `**kwargs` forwarding (kw.arg is None) counts: the mapping
                    # is being passed through, just not by literal keyword.
                    "threads_params": "params" in keywords or None in keywords,
                }
            )
    return sites


def _format(sites: list[dict[str, object]]) -> str:
    return "\n".join(
        f"  {'OK  ' if s['threads_params'] else 'MISS'} "
        f"{s['file']}:{s['line']} {s['callee']}(...)"
        for s in sites
    )


class TestEveryParamsTakingCallSiteThreadsParams:
    """The standing invariant, over sites discovered rather than enumerated."""

    def test_engine_package_has_call_sites_to_check(self) -> None:
        """Anti-vacuous-pass: a scanner that finds nothing must not read green.

        If a rename or a refactor moves these calls out from under the walk,
        this fails instead of quietly asserting over an empty list.
        """
        sites = _find_param_taking_call_sites(_PACKAGE_ROOT)
        assert len(sites) >= 5, (
            "expected at least the five known params-taking call sites in "
            f"amplifier_module_loop_pipeline/, found {len(sites)}:\n{_format(sites)}"
        )

    def test_every_engine_call_site_threads_params(self) -> None:
        sites = _find_param_taking_call_sites(_PACKAGE_ROOT)
        missing = [s for s in sites if not s["threads_params"]]
        assert not missing, (
            "these call sites drop the graph-level param mapping, so a "
            '`max_pipeline_duration="$name"` graph reached through them can '
            "never resolve, whatever the caller supplied (EXTENSIONS.md entry "
            "43 + its 2026-09-02 addendum):\n"
            f"{_format(missing)}\n\n"
            f"all discovered sites:\n{_format(sites)}"
        )

    def test_every_runner_call_site_threads_params(self) -> None:
        """The same invariant across the sibling pipeline-runner package.

        pipeline-runner drives the engine through the same two callees. It is
        a separate distribution, so it is scanned only when the sibling source
        is present in the checkout (it is, in CI and in a normal clone).
        """
        if not _RUNNER_PACKAGE_ROOT.is_dir():
            import pytest

            pytest.skip(f"sibling package not in this checkout: {_RUNNER_PACKAGE_ROOT}")

        sites = _find_param_taking_call_sites(_RUNNER_PACKAGE_ROOT)
        assert sites, (
            "expected pipeline-runner to call the engine's parse/load seam at "
            "least once -- finding zero means the scan, not the code, changed"
        )
        missing = [s for s in sites if not s["threads_params"]]
        assert not missing, (
            "these pipeline-runner call sites drop the graph-level param "
            f"mapping:\n{_format(missing)}\n\nall discovered sites:\n{_format(sites)}"
        )


class TestTheGuardItselfDetectsADroppedParam:
    """Regression proof for the checker logic, independent of the live tree.

    Without this, a scanner bug that silently matched nothing would let the
    guard above pass forever on a package that had regressed.
    """

    def _sites(self, tmp_path, source: str) -> list[dict[str, object]]:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "mod.py").write_text(source, encoding="utf-8")
        return _find_param_taking_call_sites(pkg)

    def test_flags_a_bare_call(self, tmp_path) -> None:
        sites = self._sites(tmp_path, "graph = parse_dot(dot_source)\n")
        assert len(sites) == 1
        assert sites[0]["threads_params"] is False

    def test_accepts_an_explicit_keyword(self, tmp_path) -> None:
        sites = self._sites(tmp_path, "graph = parse_dot(dot_source, params=params)\n")
        assert sites[0]["threads_params"] is True

    def test_accepts_kwargs_forwarding(self, tmp_path) -> None:
        sites = self._sites(tmp_path, "graph = parse_dot(dot_source, **kw)\n")
        assert sites[0]["threads_params"] is True

    def test_sees_an_attribute_call(self, tmp_path) -> None:
        """`mod.parse_dot(...)` is the same site wearing a different spelling."""
        sites = self._sites(tmp_path, "g = remote_dot.load_remote_or_local_graph(src)\n")
        assert len(sites) == 1
        assert sites[0]["callee"] == "load_remote_or_local_graph"
        assert sites[0]["threads_params"] is False

    def test_ignores_the_definition_and_the_import(self, tmp_path) -> None:
        """A `def` and a `from ... import` are not calls and must not be counted."""
        sites = self._sites(
            tmp_path,
            "from .dot_parser import parse_dot\n\n\n"
            "def parse_dot(source, params=None):\n    return source\n",
        )
        assert sites == []
