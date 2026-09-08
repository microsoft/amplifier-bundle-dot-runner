"""Every shipped LLM node has a measured, enforced turn cap.

`max_agent_turns=` is a WALL, not a hint: when it trips, `loop-agent` stops
the session (`agent_session.py:560-563`) and the node then fails its
`must_write=` contract with no artifact written at all. A cap set below a
visit that has actually converged does not shorten that visit -- it deletes
it. Measured on this branch: `capsule.dot`'s `author` workload under the
review's originally-proposed `max_agent_turns=110` stopped at exactly
`1 + 55 + 54 = 110` turns (55 provider calls, 24.8 min) and produced no
`DEFINITION.verify.sh`.

So the numbers below are not style. This file pins the three shipped graphs
against the three ways this control becomes performative: silently omitting a
node, silently dropping its provider pin, or tightening a cap below a visit
that already converged.

THE UNIT (measured, not assumed). One provider call costs ~2 turns, because
`SessionHistory` counts a `UserTurn`, an `AssistantTurn` per provider call,
and a `ToolResultsTurn` per tool round (`turns.py`). Hence the floors below
are `2 x <largest converging provider-call count>`, and the shipped caps add
the review's 1.25x headroom on top of that.

EVIDENCE (read-only artifacts; cap = 2 * ceil(1.25 * calls)):

    orient    155 calls  Actions 34162618376, feature issue #78
    rival     451 calls  Actions 34162618376, feature issue #78
    author    575 calls  Actions 34162618376, feature issue #78
    mutate     37 calls  capsule-64-run3, session ad5827ef
    mutate_b   58 calls  capsule-64-run3, session 9c93909a
    void       78 calls  Actions 34161733454, capsule issue #85
    critique  125 calls  capsule-64-run3, session 9b7537d2
    postmortem 39 calls  capsule-64-run3, session 87a89f94

`capsule.dot`'s author cap remains 410 by the explicit owner instruction to
keep the prior, directly proven cap (164 calls -> 410), rather than importing
the feature-specific 575-call author workload. `diagnose` uses the observed
Anthropic postmortem maximum: both are bounded, tool-using failure-analysis
gates that must write a diagnostic artifact. Task-runner's `critique_b` uses
the independent-critic maximum; `feedback` uses the larger alternate-maker
maximum; and its `attempt` / `package` use the conservative author maximum.
Those task-runner roles have no durable provider-call event history of their
own. All pins use luna's instance default model and `high`, the effort #84
measured for the author; no role has evidence supporting a lower effort.

Stdlib unittest, no dependencies -- runs in CI under the
`capsule-pipeline-scripts` job's `unittest discover`.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: graph -> node -> (cap, largest measured calls used, evidence/assumption).
#: The numbers include ceil(1.25 * calls), then the measured two-turn unit.
SHIPPED_LLM_NODES = {
    "capsule.dot": {
        "orient": (388, 155, "34162618376 feature orient"),
        "rival": (1128, 451, "34162618376 feature rival"),
        "author": (410, 164, "kept owner-approved capsule cap; 34064448082"),
        "mutate": (94, 37, "capsule-64-run3"),
        "mutate_b": (146, 58, "capsule-64-run3"),
        "void": (196, 78, "34161733454 capsule void"),
        "critique": (314, 125, "capsule-64-run3"),
        "diagnose": (98, 39, "postmortem proxy; no diagnose event history"),
        "postmortem": (98, 39, "capsule-64-run3 Anthropic postmortem"),
    },
    "feature-capsule.dot": {
        "orient": (388, 155, "34162618376 feature orient"),
        "rival": (1128, 451, "34162618376 feature rival"),
        "author": (1438, 575, "34162618376 feature author"),
        "mutate": (94, 37, "capsule-64-run3 proxy"),
        "mutate_b": (146, 58, "capsule-64-run3 proxy"),
        "void": (196, 78, "34161733454 capsule void proxy"),
        "critique": (314, 125, "capsule-64-run3 Anthropic critique proxy"),
        "diagnose": (98, 39, "postmortem proxy; no diagnose event history"),
        "postmortem": (98, 39, "capsule-64-run3 Anthropic postmortem proxy"),
    },
    "task-runner.dot": {
        "orient": (388, 155, "feature orient proxy"),
        "attempt": (1438, 575, "feature author proxy"),
        "diagnose": (98, 39, "postmortem proxy"),
        "critique": (314, 125, "capsule critique proxy"),
        "critique_b": (314, 125, "independent critique proxy"),
        "feedback": (146, 58, "alternate-maker proxy"),
        "package": (1438, 575, "author proxy"),
        "postmortem": (98, 39, "capsule postmortem proxy"),
    },
}

#: Measured turns-per-provider-call for a tool-using node. Established
#: exactly, not estimated: a cap of 110 tripped at 55 provider calls.
TURNS_PER_CALL = 2


def _node_block(dot_text: str, node: str) -> str:
    """Return the local attribute block for a node.

    Deliberately crude and local -- this file may not import the engine (the
    CI job that runs it installs nothing). The attribute blocks in these
    graphs open with `<node> [` at the start of a line and the caps are
    written one-per-line, which is all this needs to be right about.
    """
    start = re.search(rf"^\s*{re.escape(node)}\s*\[", dot_text, re.MULTILINE)
    if start is None:
        raise AssertionError(f"node {node!r} not found in the graph")
    end = dot_text.find("\n\n", start.start())
    if end == -1:
        end = len(dot_text)
    return dot_text[start.start() : end]


def _node_attr(dot_text: str, node: str, attr: str) -> str | None:
    block = _node_block(dot_text, node)
    found = re.search(rf'{re.escape(attr)}\s*=\s*"([^"]*)"', block)
    return found.group(1) if found else None


class TestShippedGraphTurnCaps(unittest.TestCase):
    def _dot(self, graph: str) -> str:
        return (HERE / graph).read_text(encoding="utf-8")

    def test_every_box_prompt_node_is_censused(self) -> None:
        for graph, expected in SHIPPED_LLM_NODES.items():
            dot = self._dot(graph)
            actual = {
                node
                for node in re.findall(r"^    ([A-Za-z][A-Za-z0-9_]*)\s*\[", dot, re.MULTILINE)
                if 'shape=box' in _node_block(dot, node)
                and 'prompt=' in _node_block(dot, node)
            }
            with self.subTest(graph=graph):
                self.assertEqual(actual, set(expected))

    def test_every_censused_node_has_luna_high_and_a_cap(self) -> None:
        for graph, nodes in SHIPPED_LLM_NODES.items():
            dot = self._dot(graph)
            for node, (expected_cap, _, _) in nodes.items():
                with self.subTest(graph=graph, node=node):
                    self.assertEqual(_node_attr(dot, node, "llm_provider"), "luna")
                    self.assertIsNone(_node_attr(dot, node, "llm_model"))
                    self.assertEqual(_node_attr(dot, node, "reasoning_effort"), "high")
                    value = _node_attr(dot, node, "max_agent_turns")
                    self.assertIsNotNone(
                        value,
                        f"{graph}'s `{node}` node lost its max_agent_turns= cap.",
                    )
                    self.assertEqual(int(value), expected_cap)

    def test_caps_stay_above_a_measured_converging_visit(self) -> None:
        for graph, nodes in SHIPPED_LLM_NODES.items():
            dot = self._dot(graph)
            for node, (cap, calls, evidence) in nodes.items():
                with self.subTest(graph=graph, node=node):
                    value = _node_attr(dot, node, "max_agent_turns")
                    assert value is not None
                    floor = TURNS_PER_CALL * calls
                    self.assertGreaterEqual(
                        cap, floor,
                        f"{graph}'s `{node}` cap is {cap} turns, which is below "
                        f"{floor} -- the turn cost of a visit that ALREADY CONVERGED "
                        f"at {calls} provider calls ({evidence}). A cap under a "
                        "measured converging visit does not shorten it; the node "
                        "hits the wall and then fails its must_write contract with "
                        "nothing written. Raise the cap, or bring new evidence that "
                        "the node converges in fewer calls and update the table in "
                        "this file with it.",
                    )

    def test_caps_are_plain_positive_integers(self) -> None:
        for graph, nodes in SHIPPED_LLM_NODES.items():
            dot = self._dot(graph)
            for node in nodes:
                with self.subTest(graph=graph, node=node):
                    value = _node_attr(dot, node, "max_agent_turns")
                    assert value is not None
                    self.assertTrue(
                        value.isdigit() and int(value) > 0,
                        f"`{node}`'s max_agent_turns={value!r} is not a positive "
                        "integer; the engine int()-converts it (backend.py:402-404) "
                        "and a non-numeric value raises mid-run, not at parse time.",
                    )


if __name__ == "__main__":
    unittest.main()
