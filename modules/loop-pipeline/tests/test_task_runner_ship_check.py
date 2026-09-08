"""Executable regression coverage for task-runner.dot's shipping gate."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.handlers.tool import ToolHandler
from amplifier_module_loop_pipeline.outcome import StageStatus

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIPPED_DOT = _REPO_ROOT / ".github" / "capsule-pipeline" / "task-runner.dot"


def _ship_check_node():
    """Read the command through the real parser, never a copied shell oracle."""
    dot_path = Path(os.environ.get("DOT_RUNNER_TASK_RUNNER_DOT", _SHIPPED_DOT))
    graph = parse_dot(
        dot_path.read_text(encoding="utf-8"), params={"max_duration": "19800s"}
    )
    return graph, graph.nodes["ship_check"]


def _init_repo(path: Path, *, commit: bool = True) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    if not commit:
        return
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-qm", "initial"],
        check=True,
        capture_output=True,
    )


def _fail_git_subcommand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subcommand: str
) -> None:
    """Put a narrow git fault in PATH while delegating every other invocation."""
    real_git = shutil.which("git")
    assert real_git is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "git"
    wrapper.write_text(
        f'#!/bin/sh\n[ "$1" = "{subcommand}" ] && exit 1\nexec "$REAL_GIT" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("REAL_GIT", real_git)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


async def _run_ship_check(repo: Path, logs_root: Path):
    graph, node = _ship_check_node()
    context = PipelineContext()
    context.set("context.target_dir", str(repo))
    outcome = await ToolHandler().execute(node, context, graph, str(logs_root))
    return outcome, context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("clean", "shipped"),
        ("untracked_ai", "shipped"),
        ("unexpected_file", "dirty"),
        ("tracked_ai", "dirty"),
        ("no_head", "dirty"),
        ("git_status_failure", "dirty"),
        ("ai_prefixed_neighbor", "dirty"),
        ("git_ls_files_failure", "dirty"),
    ],
)
async def test_ship_check_routes_only_a_clean_committed_tree_to_shipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected: str,
) -> None:
    """The parsed, real ToolHandler command preserves its zero-exit routing token."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, commit=scenario != "no_head")

    if scenario == "untracked_ai":
        (repo / ".ai").mkdir()
        (repo / ".ai" / "SHIPPED").write_text("run artifact\n", encoding="utf-8")
    elif scenario == "unexpected_file":
        (repo / "unexpected").write_text("not shipped\n", encoding="utf-8")
    elif scenario == "tracked_ai":
        (repo / ".ai").mkdir()
        (repo / ".ai" / "artifact").write_text("leak\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", ".ai/artifact"], check=True)
    elif scenario == "git_status_failure":
        _fail_git_subcommand(monkeypatch, tmp_path, "status")
    elif scenario == "ai_prefixed_neighbor":
        (repo / ".ai-other").write_text("not an artifact\n", encoding="utf-8")
    elif scenario == "git_ls_files_failure":
        _fail_git_subcommand(monkeypatch, tmp_path, "ls-files")

    outcome, context = await _run_ship_check(repo, tmp_path / "logs")

    assert outcome.status == StageStatus.SUCCESS
    assert context.get("tool.last_line") == expected
