"""`thread_id` is branch-local: the discriminating pair for engine-surface C10.2.

`contracts/engine-surface.v1.md` C10.2 states the claim in as many words:

    `thread_id` is **branch-local**. The same explicit `thread_id` in two
    sibling branches shares no history -- the nlspec's section 3.8 isolation is
    given precedence over its section 5.4 thread reuse. Sequential
    same-`thread_id` reuse is unchanged.

and C10's Probe names both halves:

    *Given* two sibling parallel branches whose nodes both declare
    `thread_id="t"`, *Then* neither sees the other's messages; *and given* the
    same `thread_id` on two sequential nodes, *Then* the second sees the
    first's exchange.

Both halves are asserted here, against **the same four nodes** carrying **the
same explicit `thread_id`**, through the real engine -- the only difference
between the two graphs is the structural fact under test (sibling branches vs.
one sequential chain).  That is what makes this a discriminating pair rather
than two agreeable observations: the assertion target is identical (`b2`'s
replayed history) and the two topologies must produce *opposite* answers.

  * ``test_sibling_branches_with_the_same_thread_id_share_no_history``
        the isolation half -- `b2` sees ONLY `b1`, never `a1`/`a2`.
  * ``test_the_same_nodes_run_sequentially_do_share_history``
        the reuse-unchanged half -- the identical `b2`, now downstream of
        `a1 -> a2 -> b1`, sees all three exchanges.
  * ``test_both_sibling_branches_resolve_the_same_explicit_thread_key``
        the anti-false-green guard: the isolation above must come from
        branch-local transcripts, NOT from the two branches quietly resolving
        *different* thread keys.  The clause is about the SAME explicit
        `thread_id`; if the keys diverged, the pair would prove nothing.

What already existed, and why it was not enough (issue #46 / ledger row
ESF-010): ``test_backend_clone.py`` pins that ``clone()`` resets
``_thread_transcripts``, and
``test_isolation_boundary_preferred_label.py::test_parallel_branch_clone_starts_without_preferred_label``
pins that a branch clone starts without an inherited ``preferred_label``.  Both
are unit/clone-level facts about a NEARBY claim.  Neither runs two sibling
branches declaring the same explicit `thread_id` and observes what each branch's
worker actually receives, and neither pins the sequential control the clause
holds unchanged.

Hermetic by construction: the only "LLM" is ``SpawnRecorder``, an in-process
coordinator double that records each spawn's kwargs and returns a canned
output.  No network, no provider, no model call.
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise

#: The one explicit thread_id every node in both graphs declares.  Named once so
#: no test can accidentally exercise isolation-by-different-key.
THREAD_ID = "T"

#: The four node ids, in the order their prompts identify them.
NODE_IDS = ("a1", "a2", "b1", "b2")


class _MockSession:
    config: dict = {}


class SpawnRecorder:
    """Coordinator double: records every ``session.spawn`` call's kwargs.

    Mirrors ``test_backend_full_continuity.py::_SpawnCapture`` -- including the
    ``attractor-anthropic`` agent config whose non-pipeline
    ``session.orchestrator`` keeps the backend's identity-recursion guard from
    firing -- but keys its recorded calls by the node's prompt so that the
    concurrent, order-nondeterministic parallel branches can be read back
    deterministically.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.session = _MockSession()
        self.config: dict = {
            "agents": {
                "attractor-anthropic": {
                    "session": {"orchestrator": {"module": "loop-agent"}},
                },
            }
        }

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs):
        self.calls.append(dict(kwargs))
        # The engine appends a status-file contract to the spawn instruction;
        # the node's own prompt is its first line.  Echo only that, so the
        # transcript this run builds stays readable in a failure message.
        marker = str(kwargs.get("instruction", "")).splitlines()[0]
        return {"output": f"out-of-{marker}", "session_id": f"sess-{marker}"}

    # -- read-back helpers --------------------------------------------------

    def call_for(self, node_prompt: str) -> dict:
        """The single spawn whose instruction begins with ``node_prompt``."""
        matches = [
            c
            for c in self.calls
            if str(c.get("instruction", "")).splitlines()[:1] == [node_prompt]
        ]
        assert len(matches) == 1, (
            f"expected exactly one spawn for prompt {node_prompt!r}, got "
            f"{len(matches)}. All prompts seen: {self.prompts_seen()}"
        )
        return matches[0]

    def prompts_seen(self) -> list[str]:
        return [
            str(c.get("instruction", "")).splitlines()[0]
            if c.get("instruction")
            else ""
            for c in self.calls
        ]

    def history_for(self, node_prompt: str) -> list[dict]:
        """``parent_messages`` replayed into ``node_prompt``'s spawn ([] if none)."""
        return list(self.call_for(node_prompt).get("parent_messages") or [])

    def thread_key_for(self, node_prompt: str) -> str | None:
        oc = self.call_for(node_prompt).get("orchestrator_config") or {}
        return oc.get("thread_key")


def _node_line(node_id: str) -> str:
    """One graph node: full fidelity, the shared explicit thread_id, prompt=id."""
    return (
        f'    {node_id} [shape=box, prompt="{node_id}", llm_provider="anthropic", '
        f'fidelity="full", thread_id="{THREAD_ID}"]'
    )


def _nodes_block() -> str:
    return "\n".join(_node_line(n) for n in NODE_IDS)


#: SIBLING TOPOLOGY -- one `shape=component` fan-out, two branches of two nodes.
PARALLEL_DOT = f"""
digraph siblings {{
    start  [shape=Mdiamond]
    fork   [shape=component]
{_nodes_block()}
    gather [shape=tripleoctagon]
    exit   [shape=Msquare]

    start -> fork
    fork -> a1
    fork -> b1
    a1 -> a2
    b1 -> b2
    a2 -> gather
    b2 -> gather
    gather -> exit
}}
"""

#: SEQUENTIAL TOPOLOGY -- byte-for-byte the same four nodes, same thread_id, in
#: one chain.  The ONLY difference from PARALLEL_DOT is the wiring.
SEQUENTIAL_DOT = f"""
digraph sequential {{
    start [shape=Mdiamond]
{_nodes_block()}
    exit  [shape=Msquare]

    start -> a1
    a1 -> a2
    a2 -> b1
    b1 -> b2
    b2 -> exit
}}
"""


async def _run(dot: str, logs_root) -> SpawnRecorder:
    """Run a graph on a recording coordinator; return the recorder."""
    coordinator = SpawnRecorder()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    graph = parse_dot(dot)
    validate_or_raise(graph)
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=str(logs_root),
    )
    outcome = await engine.run()
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"the graph itself must run clean before its history means anything; "
        f"got {outcome.status} ({outcome.failure_reason})"
    )
    assert sorted(coordinator.prompts_seen()) == sorted(NODE_IDS), (
        f"expected exactly one spawn per node {sorted(NODE_IDS)}; "
        f"saw {sorted(coordinator.prompts_seen())}"
    )
    return coordinator


def _contents(history: list[dict]) -> list[str]:
    return [str(m.get("content", "")) for m in history]


# ---------------------------------------------------------------------------
# Half 1 -- sibling branches sharing an explicit thread_id share NO history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sibling_branches_with_the_same_thread_id_share_no_history(tmp_path):
    """C10.2, isolation half: two sibling branches, one explicit `thread_id`.

    Branch A runs ``a1 -> a2``; branch B runs ``b1 -> b2``; all four declare
    ``thread_id="T"`` and ``fidelity=full``.  Each branch's SECOND node must be
    replayed its OWN branch's first exchange and nothing else -- the sibling
    branch's messages must be absent, not merely trailing.
    """
    coordinator = await _run(PARALLEL_DOT, tmp_path)

    # Each branch's first node opens its branch's transcript: no prior history.
    for first in ("a1", "b1"):
        assert coordinator.history_for(first) == [], (
            f"'{first}' is the first node on its branch's thread and must be "
            f"replayed nothing; got {coordinator.history_for(first)!r}"
        )

    # Each branch's second node sees EXACTLY its own branch's one exchange.
    for first, second in (("a1", "a2"), ("b1", "b2")):
        history = coordinator.history_for(second)
        assert history == [
            {"role": "user", "content": first},
            {"role": "assistant", "content": f"out-of-{first}"},
        ], (
            f"'{second}' must be replayed exactly its own branch's single prior "
            f"exchange ('{first}'). Got {history!r}"
        )

    # The claim stated as the clause states it: neither sibling sees the other.
    a2_history = _contents(coordinator.history_for("a2"))
    b2_history = _contents(coordinator.history_for("b2"))
    for foreign in ("b1", "out-of-b1", "b2", "out-of-b2"):
        assert foreign not in a2_history, (
            f"branch A's 'a2' was replayed branch B's {foreign!r}. Sibling "
            f"branches sharing thread_id={THREAD_ID!r} must share no history "
            f"(C10.2). Full history: {a2_history!r}"
        )
    for foreign in ("a1", "out-of-a1", "a2", "out-of-a2"):
        assert foreign not in b2_history, (
            f"branch B's 'b2' was replayed branch A's {foreign!r}. Sibling "
            f"branches sharing thread_id={THREAD_ID!r} must share no history "
            f"(C10.2). Full history: {b2_history!r}"
        )


@pytest.mark.asyncio
async def test_both_sibling_branches_resolve_the_same_explicit_thread_key(tmp_path):
    """The pair's anti-false-green guard.

    C10.2 is about the SAME explicit `thread_id` in two sibling branches.  If
    the two branches quietly resolved *different* thread keys, the isolation
    proved above would be a statement about key derivation, not about
    branch-local transcripts -- and the clause would still be unasserted.

    So pin the premise: every node in both branches resolves the identical
    explicit key.
    """
    coordinator = await _run(PARALLEL_DOT, tmp_path)

    resolved = {n: coordinator.thread_key_for(n) for n in NODE_IDS}
    assert set(resolved.values()) == {THREAD_ID}, (
        "every node in BOTH sibling branches must resolve the one explicit "
        f"thread_id {THREAD_ID!r}; isolation proved across differing keys would "
        f"not be C10.2's claim at all. Resolved: {resolved!r}"
    )


# ---------------------------------------------------------------------------
# Half 2 -- the control: the SAME nodes, run sequentially, DO share history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_same_nodes_run_sequentially_do_share_history(tmp_path):
    """C10.2, reuse-unchanged half -- and the discriminating counter-case.

    The identical four nodes with the identical explicit ``thread_id="T"``, wired
    as one sequential chain ``a1 -> a2 -> b1 -> b2`` instead of two sibling
    branches.  Now ``b2`` -- the very node that saw only two messages above --
    must see all three prior exchanges.

    This is what makes the isolation above load-bearing: a backend that simply
    never replayed history would pass the sibling test and fail this one.
    """
    coordinator = await _run(SEQUENTIAL_DOT, tmp_path)

    assert coordinator.history_for("a1") == [], (
        "'a1' opens the thread and must be replayed nothing; got "
        f"{coordinator.history_for('a1')!r}"
    )

    # Growth is monotonic, one user/assistant pair per prior node exchange.
    expected_priors = {"a2": ["a1"], "b1": ["a1", "a2"], "b2": ["a1", "a2", "b1"]}
    for node_id, priors in expected_priors.items():
        expected: list[dict] = []
        for prior in priors:
            expected.append({"role": "user", "content": prior})
            expected.append({"role": "assistant", "content": f"out-of-{prior}"})
        assert coordinator.history_for(node_id) == expected, (
            f"sequential same-thread_id reuse is unchanged (C10.2): '{node_id}' "
            f"must be replayed the exchanges of {priors}. Got "
            f"{coordinator.history_for(node_id)!r}"
        )

    # The pair, stated as one number: the same 'b2' on the same thread_id sees
    # 6 messages sequentially and 2 as a sibling branch.
    assert len(coordinator.history_for("b2")) == 6, (
        "'b2' must carry three prior exchanges (6 messages) when reached "
        "sequentially; the sibling-branch test pins the same node at 2. If both "
        "numbers ever agree, the pair has stopped discriminating."
    )
