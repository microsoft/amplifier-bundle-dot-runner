"""Tests for process tracking and graceful shutdown (M-7).

The agent session should track running tool subprocesses and on
shutdown() send SIGTERM, wait a grace period, then SIGKILL survivors.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_module_loop_agent.agent_session import AgentSession
from amplifier_module_loop_agent.config import SessionConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(**overrides) -> AgentSession:
    """Create a minimal AgentSession for testing."""
    config = SessionConfig.from_dict(overrides.get("config", {}))
    provider = AsyncMock()
    hooks = MagicMock()
    hooks.emit = AsyncMock(return_value=MagicMock(action="continue"))
    return AgentSession(
        config=config,
        provider=provider,
        tools={},
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Process tracking
# ---------------------------------------------------------------------------


class TestProcessTracking:
    """AgentSession tracks running subprocess PIDs."""

    def test_session_has_tracked_processes_set(self):
        """Session exposes a _tracked_processes set."""
        session = _make_session()
        assert hasattr(session, "_tracked_processes")
        assert isinstance(session._tracked_processes, set)

    def test_register_process_adds_to_set(self):
        """register_process() adds a process to tracking."""
        session = _make_session()
        proc = MagicMock()
        proc.pid = 12345
        session.register_process(proc)
        assert proc in session._tracked_processes

    def test_unregister_process_removes_from_set(self):
        """unregister_process() removes a process from tracking."""
        session = _make_session()
        proc = MagicMock()
        proc.pid = 12345
        session.register_process(proc)
        session.unregister_process(proc)
        assert proc not in session._tracked_processes

    def test_unregister_missing_process_is_safe(self):
        """unregister_process() on unknown process is a no-op."""
        session = _make_session()
        proc = MagicMock()
        proc.pid = 99999
        # Should not raise
        session.unregister_process(proc)


# ---------------------------------------------------------------------------
# Shutdown with process termination
# ---------------------------------------------------------------------------


class TestShutdownProcessTermination:
    """shutdown() terminates tracked processes."""

    @pytest.mark.asyncio
    async def test_shutdown_terminates_tracked_processes(self):
        """shutdown() sends terminate() to each tracked process."""
        session = _make_session()
        # Force session into a state that allows shutdown
        session._session_started = True

        proc1 = MagicMock()
        proc1.pid = 100
        proc1.returncode = None  # Still running
        proc1.terminate = MagicMock()
        proc1.kill = MagicMock()
        proc1.wait = AsyncMock()

        proc2 = MagicMock()
        proc2.pid = 200
        proc2.returncode = None  # Still running
        proc2.terminate = MagicMock()
        proc2.kill = MagicMock()
        proc2.wait = AsyncMock()

        session.register_process(proc1)
        session.register_process(proc2)

        await session.shutdown(process_timeout=0.01)

        proc1.terminate.assert_called_once()
        proc2.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_skips_already_exited_processes(self):
        """shutdown() skips processes that already have a returncode."""
        session = _make_session()
        session._session_started = True

        proc = MagicMock()
        proc.pid = 300
        proc.returncode = 0  # Already exited
        proc.terminate = MagicMock()

        session.register_process(proc)
        await session.shutdown(process_timeout=0.01)

        proc.terminate.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_kills_after_timeout(self):
        """shutdown() sends kill() if process doesn't exit after terminate()."""
        session = _make_session()
        session._session_started = True

        proc = MagicMock()
        proc.pid = 400
        proc.returncode = None  # Still running
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        # wait() times out (simulated by raising TimeoutError)
        proc.wait = AsyncMock(side_effect=asyncio.TimeoutError)

        session.register_process(proc)
        await session.shutdown(process_timeout=0.01)

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_clears_tracked_set(self):
        """shutdown() clears the tracked processes set."""
        session = _make_session()
        session._session_started = True

        proc = MagicMock()
        proc.pid = 500
        proc.returncode = None
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        session.register_process(proc)
        await session.shutdown(process_timeout=0.01)

        assert len(session._tracked_processes) == 0

    @pytest.mark.asyncio
    async def test_shutdown_idempotent_with_processes(self):
        """Calling shutdown() twice is safe even with tracked processes."""
        session = _make_session()
        session._session_started = True

        proc = MagicMock()
        proc.pid = 600
        proc.returncode = None
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        session.register_process(proc)
        await session.shutdown(process_timeout=0.01)
        # Second call should be a no-op (CLOSED state)
        await session.shutdown(process_timeout=0.01)

        # terminate only called once (from first shutdown)
        proc.terminate.assert_called_once()
