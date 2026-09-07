"""The shipped graphs' turn caps must stay above the measured evidence.

`max_agent_turns=` is a WALL, not a hint: when it trips, `loop-agent` stops
the session (`agent_session.py:560-563`) and the node then fails its
`must_write=` contract with no artifact written at all. A cap set below a
visit that has actually converged does not shorten that visit -- it deletes
it. Measured on this branch: `capsule.dot`'s `author` workload under the
review's originally-proposed `max_agent_turns=110` stopped at exactly
`1 + 55 + 54 = 110` turns (55 provider calls, 24.8 min) and produced no
`DEFINITION.verify.sh`.

So the numbers in `capsule.dot` are not style. This file pins them against
the two things that make them wrong: silent removal, and a well-meaning
"tighten the budget" edit that drops one below what a converging visit has
already been measured to need.

THE UNIT (measured, not assumed). One provider call costs ~2 turns, because
`SessionHistory` counts a `UserTurn`, an `AssistantTurn` per provider call,
and a `ToolResultsTurn` per tool round (`turns.py`). Hence the floors below
are `2 x <largest converging provider-call count>`, and the shipped caps add
the review's 1.25x headroom on top of that.

EVIDENCE (read-only run artifacts, all `status: success` visits):

    author    164 provider calls  Actions 34064448082, session c2e6940c
    critique  125 provider calls  capsule-64-run3,     session 9b7537d2

Stdlib unittest, no dependencies -- runs in CI under the
`capsule-pipeline-scripts` job's `unittest discover`.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: node -> (largest CONVERGING provider-call count measured, evidence)
MEASURED_CONVERGING_CALLS = {
    "author": (164, "Actions 34064448082, session c2e6940c, status success"),
    "critique": (125, "capsule-64-run3, session 9b7537d2, status success"),
}

#: Measured turns-per-provider-call for a tool-using node. Established
#: exactly, not estimated: a cap of 110 tripped at 55 provider calls.
TURNS_PER_CALL = 2


def _node_attr(dot_text: str, node: str, attr: str) -> str | None:
    """Return `attr`'s value inside `node`'s attribute block, or None.

    Deliberately crude and local -- this file may not import the engine (the
    CI job that runs it installs nothing). The attribute blocks in these
    graphs open with `<node> [` at the start of a line and the caps are
    written one-per-line, which is all this needs to be right about.
    """
    start = re.search(rf"^\s*{re.escape(node)}\s*\[", dot_text, re.MULTILINE)
    if start is None:
        raise AssertionError(f"node {node!r} not found in the graph")
    block = dot_text[start.start() : start.start() + 4000]
    found = re.search(rf'{re.escape(attr)}\s*=\s*"([^"]*)"', block)
    return found.group(1) if found else None


class TestCapsuleTurnCaps(unittest.TestCase):
    def setUp(self) -> None:
        self.dot = (HERE / "capsule.dot").read_text(encoding="utf-8")

    def test_bounded_nodes_declare_a_cap(self) -> None:
        for node in MEASURED_CONVERGING_CALLS:
            with self.subTest(node=node):
                value = _node_attr(self.dot, node, "max_agent_turns")
                self.assertIsNotNone(
                    value,
                    f"capsule.dot's `{node}` node lost its max_agent_turns= cap. "
                    "It is the graph's only bound on that node: without it the "
                    "visit runs until the pipeline fuse, which is what made one "
                    "author visit cost 59.9 minutes of a 330-minute budget.",
                )

    def test_caps_stay_above_a_measured_converging_visit(self) -> None:
        for node, (calls, evidence) in MEASURED_CONVERGING_CALLS.items():
            with self.subTest(node=node):
                value = _node_attr(self.dot, node, "max_agent_turns")
                assert value is not None  # covered by the test above
                cap = int(value)
                floor = TURNS_PER_CALL * calls
                self.assertGreaterEqual(
                    cap, floor,
                    f"capsule.dot's `{node}` cap is {cap} turns, which is below "
                    f"{floor} -- the turn cost of a visit that ALREADY CONVERGED "
                    f"at {calls} provider calls ({evidence}). A cap under a "
                    "measured converging visit does not shorten it; the node "
                    "hits the wall and then fails its must_write contract with "
                    "nothing written. Raise the cap, or bring new evidence that "
                    "the node converges in fewer calls and update the table in "
                    "this file with it.",
                )

    def test_caps_are_plain_positive_integers(self) -> None:
        for node in MEASURED_CONVERGING_CALLS:
            with self.subTest(node=node):
                value = _node_attr(self.dot, node, "max_agent_turns")
                assert value is not None
                self.assertTrue(
                    value.isdigit() and int(value) > 0,
                    f"`{node}`'s max_agent_turns={value!r} is not a positive "
                    "integer; the engine int()-converts it (backend.py:402-404) "
                    "and a non-numeric value raises mid-run, not at parse time.",
                )


if __name__ == "__main__":
    unittest.main()
