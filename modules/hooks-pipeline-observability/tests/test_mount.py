"""Tests for hooks-pipeline-observability module mount."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from amplifier_module_hooks_pipeline_observability import mount


def test_mount_is_callable():
    """mount() should be importable and callable."""
    assert callable(mount)


@pytest.mark.asyncio(loop_scope="session")
async def test_mount_does_not_crash():
    """mount() should accept a coordinator mock without error."""
    coordinator = MagicMock()
    await mount(coordinator)
