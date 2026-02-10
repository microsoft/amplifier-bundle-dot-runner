"""Tests for environment context builder.

Spec coverage: ENVCTX-001-002, GIT-001-002.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from amplifier_module_loop_agent.environment import build_environment_context


class TestBuildEnvironmentContext:
    """Tests for build_environment_context()."""

    def test_contains_working_directory(self, tmp_path: Path) -> None:
        ctx = build_environment_context(working_dir=str(tmp_path))
        assert str(tmp_path) in ctx

    def test_contains_platform(self, tmp_path: Path) -> None:
        ctx = build_environment_context(working_dir=str(tmp_path))
        assert "Platform:" in ctx

    def test_contains_date(self, tmp_path: Path) -> None:
        ctx = build_environment_context(working_dir=str(tmp_path))
        assert "Today's date:" in ctx

    def test_wrapped_in_environment_tags(self, tmp_path: Path) -> None:
        ctx = build_environment_context(working_dir=str(tmp_path))
        assert ctx.startswith("<environment>")
        assert ctx.strip().endswith("</environment>")

    def test_includes_provider_name(self, tmp_path: Path) -> None:
        ctx = build_environment_context(
            working_dir=str(tmp_path), provider_name="anthropic"
        )
        assert "anthropic" in ctx.lower() or "Provider:" in ctx

    def test_includes_model_when_given(self, tmp_path: Path) -> None:
        ctx = build_environment_context(
            working_dir=str(tmp_path), model="claude-sonnet-4-20250514"
        )
        assert "claude-sonnet-4-20250514" in ctx

    def test_non_git_dir_says_not_git(self, tmp_path: Path) -> None:
        ctx = build_environment_context(working_dir=str(tmp_path))
        assert "Is git repository: false" in ctx

    def test_git_dir_detected(self, tmp_path: Path) -> None:
        # Initialize a git repo in tmp_path
        subprocess.run(
            ["git", "init"], cwd=str(tmp_path), capture_output=True, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        # Make an initial commit so there's a branch
        (tmp_path / "init.txt").write_text("init")
        subprocess.run(
            ["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        ctx = build_environment_context(working_dir=str(tmp_path))
        assert "Is git repository: true" in ctx
        assert "Git branch:" in ctx

    def test_git_status_shows_counts(self, tmp_path: Path) -> None:
        # Initialize a git repo with some modified/untracked files
        subprocess.run(
            ["git", "init"], cwd=str(tmp_path), capture_output=True, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        (tmp_path / "tracked.txt").write_text("hello")
        subprocess.run(
            ["git", "add", "."], cwd=str(tmp_path), capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        # Now create modifications
        (tmp_path / "tracked.txt").write_text("modified")
        (tmp_path / "untracked.txt").write_text("new file")
        ctx = build_environment_context(working_dir=str(tmp_path))
        assert "modified" in ctx.lower() or "Modified:" in ctx
        assert "untracked" in ctx.lower() or "Untracked:" in ctx
