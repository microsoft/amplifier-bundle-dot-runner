"""`dot-runner lint --param`: a `$name` graph attribute must not un-lintable a graph.

EXTENSIONS.md entry 43 made a graph-level duration attribute able to hold a
bare ``"$name"`` token, resolved at PARSE time and failing loud when absent.
``run`` and ``resume`` both grew ``--param`` for it. ``lint`` did not -- and
``cmd_lint`` parses with the very same ``parse_dot``.

The consequence was concrete rather than theoretical: the three pipelines
entry 43 shipped as its own first consumers
(``.github/capsule-pipeline/{capsule,feature-capsule,task-runner}.dot``, each
carrying ``max_pipeline_duration="$max_duration"``) could not be linted by
this repo's own linter at all. It exited 1 with "failed to parse", naming a
flag the subcommand did not accept. The engine's test corpus sweeps had
already worked around it by supplying a placeholder param directly to
``parse_dot`` (see entry 43's implementation list) -- the CLI had no such
escape hatch.

Lint never executes anything, so the value is irrelevant to the outcome: any
placeholder that parses is enough. What matters is that the graph becomes
parseable, and that supplying nothing still fails loud rather than silently
linting a graph with an unresolved fuse.
"""

from __future__ import annotations

from pathlib import Path

from amplifier_module_pipeline_runner import cli

_PARAM_DOT = """\
digraph parameterized {
    graph [max_pipeline_duration="$max_duration", params="max_duration"];
    start [shape=Mdiamond];
    work  [shape=parallelogram, tool_command="printf ok"];
    done  [shape=Msquare];
    start -> work;
    work -> done;
}
"""


def _write(tmp_path: Path) -> str:
    dot = tmp_path / "parameterized.dot"
    dot.write_text(_PARAM_DOT, encoding="utf-8")
    return str(dot)


class TestLintAcceptsGraphLevelParams:
    def test_with_param_the_graph_lints(self, tmp_path, capsys):
        rc = cli.main(["lint", _write(tmp_path), "--param", "max_duration=19800s"])
        out = capsys.readouterr().out

        assert rc == 0, "a parseable, clean graph must lint successfully"
        assert "failed to parse" not in out

    def test_a_lint_only_placeholder_value_is_enough(self, tmp_path, capsys):
        """Lint never runs the pipeline, so the value need not be realistic."""
        rc = cli.main(["lint", _write(tmp_path), "--param", "max_duration=1s"])
        assert rc == 0
        assert "failed to parse" not in capsys.readouterr().out

    def test_without_the_param_it_still_fails_loud(self, tmp_path, capsys):
        """The fix supplies a mapping; it must not weaken the fail-loud contract."""
        rc = cli.main(["lint", _write(tmp_path)])
        err = capsys.readouterr().err

        assert rc == 1, "an unresolvable $name must never lint as if it were fine"
        assert "max_duration" in err, "the diagnostic must name the missing param"

    def test_malformed_param_is_a_cli_error_not_a_traceback(self, tmp_path, capsys):
        rc = cli.main(["lint", _write(tmp_path), "--param", "nonsense"])
        assert rc == 1
        assert "lint:" in capsys.readouterr().err


class TestShippedParameterizedPipelinesAreLintable:
    """The graphs entry 43 shipped must be lintable by this repo's own linter."""

    _REPO_ROOT = Path(__file__).resolve().parents[3]

    def test_capsule_pipelines_lint_with_a_placeholder_param(self, capsys):
        pipeline_dir = self._REPO_ROOT / ".github" / "capsule-pipeline"
        if not pipeline_dir.is_dir():  # module checked out standalone
            import pytest

            pytest.skip("repo-root .github/capsule-pipeline/ not present")

        parameterized = [
            p
            for p in sorted(pipeline_dir.glob("*.dot"))
            if 'max_pipeline_duration="$' in p.read_text(encoding="utf-8")
        ]
        assert parameterized, (
            "expected at least one shipped pipeline carrying a $-token fuse -- "
            "if none remain, this guard has lost its subject"
        )

        for path in parameterized:
            rc = cli.main(["lint", str(path), "--param", "max_duration=19800s"])
            out = capsys.readouterr().out
            assert "failed to parse" not in out, (
                f"{path.name} must be parseable for lint once its param is supplied"
            )
            assert rc in (0, 1), f"unexpected exit code {rc} for {path.name}"
