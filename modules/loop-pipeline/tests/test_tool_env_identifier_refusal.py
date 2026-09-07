"""A ``tool_env`` name that is not a POSIX identifier is REFUSED, never run.

Issue #64 (support#506/#507).  ``tool_env="human.gate.text"`` uppercases to
``HUMAN.GATE.TEXT``, which is not a POSIX environment-variable name.  The tool
handler runs its command through ``/bin/sh`` via
``asyncio.create_subprocess_shell``; ``dash`` DROPS environment entries whose
names are not identifiers before exec'ing the child, so the value never reached
the command -- and the node still reported SUCCESS.  Silent data loss with a
green run.  Measured on the incident host (``/bin/sh -> dash``)::

    $ env "HUMAN.GATE.TEXT=v" /bin/dash -c 'printenv "HUMAN.GATE.TEXT" || echo DROPPED'
    DROPPED
    $ env "HUMAN.GATE.TEXT=v" /bin/bash -c 'printenv "HUMAN.GATE.TEXT" || echo DROPPED'
    v

The shell asymmetry is why it stayed invisible: the same graph "works" under
bash and loses the value under dash.

A contributor's PR (#41, reverted in #68) fixed it by ALSO exporting a
sanitized twin name (dots -> underscores) under a new EXTENSIONS section 20.1.
The owner declined to widen section 20 that way: sanitizing invents a second
name the graph never wrote, and dual emission makes the graph's declared
vocabulary and the subprocess's actual vocabulary disagree.  Owner ruling
(2026-09-07): **fail loud**.  A name that cannot become an environment
variable is refused at lint and at preflight, naming the node, the attribute,
the offending name and the fix.  The consumer-side fix -- renaming the context
keys to valid identifiers -- is resolver-dot-graph#142.

These tests pin BOTH directions:

- the #64 repro graph is refused (lint ERROR, ``validate_or_raise``, preflight),
- and a VALID name still round-trips end to end (``result`` -> ``RESULT``),
  so the rule refuses the invalid NAME and not ``tool_env`` itself.
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.handlers.tool import ToolHandler
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.preflight import (
    ToolEnvPreflightError,
    check_tool_env_names,
)
from amplifier_module_loop_pipeline.validation import (
    ValidationError,
    lint,
    validate,
    validate_or_raise,
)

_RULE = "tool_env_posix_identifier"

# The issue #64 repro, verbatim in shape: a gate's freeform answer is handed to
# a tool node under a DOTTED context key.  Today the file is written EMPTY and
# the node reports SUCCESS.
_REPRO_DOT = """\
digraph repro {
    graph [goal="issue #64 repro -- dotted tool_env name"]
    start [shape=Mdiamond];
    capture [shape=parallelogram,
             tool_env="human.gate.text",
             tool_command="printenv 'HUMAN.GATE.TEXT' > captured.txt; exit 0"];
    done [shape=Msquare];
    start -> capture -> done;
}
"""

# The control: identical shape, a VALID identifier name.
_VALID_DOT = """\
digraph valid {
    graph [goal="control -- a valid tool_env name still works"]
    start [shape=Mdiamond];
    capture [shape=parallelogram,
             tool_env="result",
             tool_command="printenv RESULT > captured.txt; exit 0"];
    done [shape=Msquare];
    start -> capture -> done;
}
"""


def _errors(diags, rule: str = _RULE):
    return [d for d in diags if d.rule == rule and d.severity == "ERROR"]


class TestRefusedAtLint:
    """`dot-runner lint` catches it -- ERROR severity, so rc=1."""

    def test_repro_graph_is_an_error_naming_node_attr_name_and_fix(self):
        graph = parse_dot(_REPRO_DOT)

        errors = _errors(lint(graph))

        assert len(errors) == 1, (
            f"expected exactly one {_RULE} ERROR, got {[(d.rule, d.severity) for d in lint(graph)]}"
        )
        diag = errors[0]
        assert diag.node_id == "capture"  # the node
        assert "capture" in diag.message
        assert "tool_env" in diag.message  # the attribute
        assert "human.gate.text" in diag.message  # the offending name
        assert diag.fix, "a refusal without a fix is not actionable"
        assert "rename" in diag.fix.lower()  # the fix
        assert "20" in diag.fix  # ...pointing at EXTENSIONS section 20

    def test_validate_also_carries_it_so_a_run_cannot_start(self):
        """The rule lives in ``validate()``, not only in lint-only rules --
        so every validating run path refuses too, not just the linter."""
        graph = parse_dot(_REPRO_DOT)

        assert _errors(validate(graph)), "validate() must carry the refusal"
        with pytest.raises(ValidationError) as exc_info:
            validate_or_raise(graph)
        assert "human.gate.text" in str(exc_info.value)

    def test_every_offending_name_is_named_not_just_the_first(self):
        """A misconfiguration costs one clear error, not a discovery loop."""
        graph = parse_dot(
            "digraph multi {\n"
            "    start [shape=Mdiamond];\n"
            '    t [shape=parallelogram, tool_command="true",\n'
            '       tool_env="delivery.pr_url,ok_name,delivery.result"];\n'
            "    done [shape=Msquare];\n"
            "    start -> t -> done;\n"
            "}\n"
        )

        messages = " ".join(d.message for d in _errors(lint(graph)))

        assert "delivery.pr_url" in messages
        assert "delivery.result" in messages
        assert "ok_name" not in messages, "a valid sibling name must not be flagged"

    @pytest.mark.parametrize(
        "name",
        [
            "human.gate.text",  # the incident shape
            "1leading_digit",
            "has-a-dash",
            "has space",
            "café",  # non-ASCII: uppercases, still not an identifier
        ],
    )
    def test_non_identifier_names_are_refused(self, name: str):
        graph = Graph(
            name="g",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "t": Node(
                    id="t",
                    shape="parallelogram",
                    attrs={"tool_command": "true", "tool_env": name},
                ),
            },
            edges=[],
        )

        assert _errors(validate(graph)), f"{name!r} must be refused"

    @pytest.mark.parametrize(
        "attr",
        [
            "result",
            "RESULT",
            "_leading_underscore",
            "with_digits_9",
            " padded , also_ok ",  # whitespace is trimmed, as the handler trims it
            "a,,b",  # empty segments are skipped, as the handler skips them
        ],
    )
    def test_valid_names_are_not_refused(self, attr: str):
        graph = Graph(
            name="g",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "t": Node(
                    id="t",
                    shape="parallelogram",
                    attrs={"tool_command": "true", "tool_env": attr},
                ),
            },
            edges=[],
        )

        assert not _errors(validate(graph)), f"{attr!r} must NOT be refused"


class TestRefusedAtPreflight:
    """The second surface: a run refuses to start even with validation off."""

    def test_preflight_refuses_naming_node_attr_name_and_fix(self):
        graph = parse_dot(_REPRO_DOT)

        with pytest.raises(ToolEnvPreflightError) as exc_info:
            check_tool_env_names(graph)

        msg = str(exc_info.value)
        assert "capture" in msg  # the node
        assert "tool_env" in msg  # the attribute
        assert "human.gate.text" in msg  # the offending name
        assert "rename" in msg.lower()  # the fix
        assert "20" in msg  # ...pointing at EXTENSIONS section 20

    def test_preflight_is_silent_on_a_valid_graph(self):
        check_tool_env_names(parse_dot(_VALID_DOT))  # must not raise

    def test_preflight_tolerates_a_graph_stand_in_without_nodes(self):
        """Same posture as the provider preflight: no visible nodes, nothing
        to check -- a bare stub graph must not crash the startup path."""

        class _Stub:
            pass

        check_tool_env_names(_Stub())  # type: ignore[arg-type]


class TestValidNameStillRoundTrips:
    """The discriminating control: the rule refuses a NAME, not the feature."""

    @pytest.mark.asyncio
    async def test_result_reaches_the_subprocess_as_RESULT(self, tmp_path):
        ctx = PipelineContext()
        ctx.set("result", "the-answer")
        node = Node(
            id="capture",
            shape="parallelogram",
            attrs={"tool_command": "printenv RESULT", "tool_env": "result"},
        )

        outcome = await ToolHandler().execute(
            node,
            ctx,
            Graph(name="g", nodes={}, edges=[]),
            str(tmp_path),
        )

        assert outcome.status == StageStatus.SUCCESS, outcome.failure_reason
        assert "the-answer" in ctx.get("tool.output", "")

    def test_the_control_graph_is_clean_at_lint_and_preflight(self):
        graph = parse_dot(_VALID_DOT)

        assert not _errors(lint(graph))
        check_tool_env_names(graph)
