"""Live proof that seeded parent-turn history reaches the hosted model (support#497).

This is the amplifier-agent-backed, end-to-end analogue of the hermetic
``tests/test_v2_capabilities.py::
test_parent_history_replayed_into_hosted_session_context`` RED-proof: it runs a
REAL ``amplifier_agent_lib.engine.Engine`` turn and asserts the model actually
RECALLS a fact that exists ONLY in the parent-turn history the adapter replays
into the hosted session's context -- i.e. that fidelity="full" cross-node
continuity reaches the LLM, not merely the mount.

The ONLY doubled object is the input ``context`` carrier (a plain
``get_messages()`` data holder standing in for the parent session's already-
seeded ContextManager). Everything downstream is real: the adapter, the hosted
amplifier-agent Engine, its real ``context-simple`` mount, and a real,
credentialed provider turn.

NON-DETERMINISM NOTE: live-model output varies. The assertion matches the
secret case-insensitively in the reply text, and the prompt asks the model to
restate the code verbatim as its entire reply, to minimize false reds. A rare
miss (the model recalls the fact but paraphrases -- "the code you gave me" --
without restating the literal token) is a prompting artifact, NOT a mechanism
regression: re-run before treating it as one. The hermetic Gap-6 tests are the
deterministic guarantee; this test is the live corroboration that the seam
actually reaches the model.

WAVE 4 (ruling 5): this test no longer asks the model to call
``report_outcome`` -- this module doesn't mount that reach-in tool anymore,
so there would be nothing to call. The assertion reads the reply text alone.

Gated exactly like ``test_spawn_report_outcome_transport.py``:
  * ``importorskip("amplifier_agent_lib")`` -- skips when the peer lib is absent.
  * skip-if-no-provider-key -- skips in CI (no secrets) so it never blocks.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from amplifier_module_loop_amplifier_agent import AmplifierAgentOrchestrator

from ._fakes import CapturingHooks, FakeContextManager

pytest.importorskip(
    "amplifier_agent_lib",
    reason="amplifier-agent is a heavy, Python>=3.12-only peer dependency",
)

_HAS_PROVIDER_KEY = bool(
    os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
)

pytestmark = pytest.mark.skipif(
    not _HAS_PROVIDER_KEY,
    reason="no ANTHROPIC_API_KEY/OPENAI_API_KEY in the environment -- "
    "CI carries no secrets and must skip this live test honestly",
)


# A distinctive token the model cannot produce without seeing the seeded
# history -- so a passing assertion can only mean the history reached the model.
_SECRET = "QUOKKA-9317-ZEBRA"


@pytest.mark.asyncio
async def test_seeded_history_is_recalled_by_the_real_hosted_model():
    """The adapter replays prior-turn history into the hosted amplifier-agent
    session; the real model must recall a secret that appears ONLY in that
    history. Pre-fix (``context`` dropped) the hosted turn never saw the secret
    and cannot recall it; post-fix it can -- the live counterpart to the
    hermetic Gap-6 RED-proof.
    """
    history = [
        {
            "role": "user",
            "content": f"Remember this secret code exactly: {_SECRET}. Reply 'ok'.",
        },
        {"role": "assistant", "content": "ok"},
    ]
    context = FakeContextManager(history)
    hooks = CapturingHooks()

    orch = AmplifierAgentOrchestrator(coordinator=MagicMock(), config={})
    reply = await orch.execute(
        "Earlier in this conversation I gave you a secret code. Restate that "
        "exact code VERBATIM now as your entire reply, and nothing else.",
        context,
        {},
        {},
        hooks,
        coordinator=None,
    )

    # WAVE 4 (ruling 5): metadata never carries a fabricated report_outcome --
    # this module no longer mounts that reach-in tool.
    assert hooks.completion.get("metadata", {}) == {}

    assert _SECRET.upper() in reply.upper(), (
        f"the hosted model did not recall the seeded secret {_SECRET!r} -- "
        f"support#497: parent-turn history did not reach the model. "
        f"reply={reply!r}"
    )
