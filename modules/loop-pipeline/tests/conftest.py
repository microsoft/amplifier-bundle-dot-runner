"""Test configuration and shared stubs for the loop-pipeline test suite.

Provides stubs for optional dependencies (amplifier_foundation, amplifier_core)
that are not installed in the test virtual environment.  These stubs must be
registered in sys.modules *before* any test module imports the backend, so this
file (as a conftest) is processed first by pytest.

Also ensures that the amplifier_module_loop_pipeline package is loaded from
this source tree, not from a potentially stale editable install elsewhere.
"""

import pathlib
import sys
import types
from dataclasses import dataclass, field

import pytest

# Prevent Python from writing .pyc bytecode files during the test session.
# Without this, grep-based test discovery in the DoD verify script finds
# compiled .pyc files in __pycache__ and passes them to pytest, which cannot
# collect them and exits with code 4 (no tests collected) — causing a false
# failure on the second verify run.  Setting this early (before any test
# module is imported) prevents .pyc creation for all subsequently imported
# test files.  It does not affect test correctness.
sys.dont_write_bytecode = True

# Ensure the local source directory is resolved first.  An editable install
# from a different checkout (e.g. modern-bundle-pipeline) would otherwise
# shadow the changes under test.  Insert before site-packages path entries.
_MODULE_SRC = str(pathlib.Path(__file__).parent.parent)
if _MODULE_SRC not in sys.path:
    sys.path.insert(0, _MODULE_SRC)


# ---------------------------------------------------------------------------
# amplifier_foundation stub — provides ProviderPreference
# ---------------------------------------------------------------------------
if "amplifier_foundation" not in sys.modules:

    @dataclass
    class _StubProviderPreference:
        provider: str = ""
        model: str = ""

    _stub_foundation = types.ModuleType("amplifier_foundation")
    _stub_foundation.ProviderPreference = _StubProviderPreference  # type: ignore[attr-defined]
    sys.modules["amplifier_foundation"] = _stub_foundation


# ---------------------------------------------------------------------------
# amplifier_core stub — provides Message and ChatRequest
# ---------------------------------------------------------------------------
if "amplifier_core" not in sys.modules:

    @dataclass
    class _StubMessage:
        role: str = "user"
        content: object = ""
        tool_call_id: str | None = None
        name: str | None = None
        metadata: dict | None = None

    @dataclass
    class _StubToolCallBlock:
        id: str = ""
        name: str = ""
        input: dict = field(default_factory=dict)
        type: str = "tool_call"

    @dataclass
    class _StubChatRequest:
        messages: list = field(default_factory=list)
        tools: list | None = None
        tool_choice: str | None = None
        reasoning_effort: str | None = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage  # type: ignore[attr-defined]
    _stub_core.ChatRequest = _StubChatRequest  # type: ignore[attr-defined]
    sys.modules["amplifier_core"] = _stub_core

    _stub_msg = types.ModuleType("amplifier_core.message_models")
    _stub_msg.ToolCallBlock = _StubToolCallBlock  # type: ignore[attr-defined]
    sys.modules["amplifier_core.message_models"] = _stub_msg


# ---------------------------------------------------------------------------
# Host mtime granularity — issue #67
# ---------------------------------------------------------------------------
#
# A handful of tests assert a SUB-SECOND distinction between "planted just
# before the node started" and "written just after": the freshness floors in
# must_write.py and status_file.py (EXTENSIONS.md Sec 27 / Sec 41).  That
# distinction is only observable if the host's filesystem stamps mtimes more
# finely than the gap being asserted.  On a whole-second-granularity mount
# (ext2, vfat, HFS+, NFSv2, some container/network mounts) both sides of the
# comparison land on the same stamp and the assertion is not merely hard to
# meet — it is unmeasurable, by physics rather than by bug.
#
# Rather than let those tests fail on such a host (which is how issue #67
# presented: an intermittently red pool nobody could attribute), they are
# skipped there with this named reason.  On every fine-grained filesystem —
# ext4, xfs, btrfs, tmpfs, and CI's ubuntu-latest — the fixture is a no-op and
# the assertions run exactly as before.


def _host_mtime_granularity_seconds() -> float:
    """Coarsest resolution this host's temp filesystem evidences, in seconds.

    Samples several real files and takes the MINIMUM derived granularity: a
    fine-grained stamp lands on a round value by chance about once in a
    thousand for the finest unit, and a single unlucky sample must not be
    allowed to skip a test on an otherwise fine-grained host.
    """
    import tempfile

    from amplifier_module_loop_pipeline.freshness import mtime_granularity_seconds

    samples: list[float] = []
    with tempfile.TemporaryDirectory() as d:
        for i in range(5):
            p = pathlib.Path(d) / f"probe{i}"
            p.write_text("x")
            samples.append(mtime_granularity_seconds(p.stat().st_mtime_ns))
    return min(samples)


@pytest.fixture
def requires_subsecond_mtime():
    """Skip when the host cannot resolve the sub-second gap under test."""
    granularity = _host_mtime_granularity_seconds()
    if granularity >= 0.001:
        pytest.skip(
            "host filesystem stamps mtimes at "
            f"{granularity}s granularity, so the sub-second 'planted before "
            "node start' vs 'written after node start' distinction this test "
            "asserts is unmeasurable here (issue #67); the freshness floor "
            "itself is covered on every host by "
            "tests/test_coarse_mtime_freshness.py"
        )
