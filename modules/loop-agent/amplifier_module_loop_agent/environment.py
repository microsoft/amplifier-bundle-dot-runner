"""Environment context builder for the coding agent loop.

Spec coverage: ENVCTX-001-002, GIT-001-002.

Builds a structured <environment> block with runtime information
that gets included in the system prompt. Includes working directory,
platform, git context, date, and model info.
"""

from __future__ import annotations

import platform
import subprocess
from datetime import date


def build_environment_context(
    working_dir: str,
    provider_name: str | None = None,
    model: str | None = None,
) -> str:
    """Build a structured environment context block for the system prompt.

    Args:
        working_dir: The current working directory path.
        provider_name: Provider identifier (e.g., "openai", "anthropic").
        model: Model name (e.g., "gpt-5.2-codex").

    Returns:
        A string wrapped in <environment> tags containing runtime info.
    """
    lines: list[str] = []
    lines.append(f"Working directory: {working_dir}")
    lines.append(f"Platform: {platform.system().lower()}")
    lines.append(f"Today's date: {date.today().isoformat()}")

    # Git context
    git_info = _build_git_context(working_dir)
    lines.append(f"Is git repository: {'true' if git_info['is_repo'] else 'false'}")
    if git_info["is_repo"]:
        if git_info["branch"]:
            lines.append(f"Git branch: {git_info['branch']}")
        if git_info["modified"] is not None:
            lines.append(f"Modified files: {git_info['modified']}")
        if git_info["untracked"] is not None:
            lines.append(f"Untracked files: {git_info['untracked']}")

    # Provider and model info
    if provider_name:
        lines.append(f"Provider: {provider_name}")
    if model:
        lines.append(f"Model: {model}")

    body = "\n".join(lines)
    return f"<environment>\n{body}\n</environment>"


def _build_git_context(working_dir: str) -> dict:
    """Snapshot git state for environment context.

    Returns a dict with is_repo, branch, modified count, untracked count.
    """
    result: dict = {
        "is_repo": False,
        "branch": None,
        "modified": None,
        "untracked": None,
    }

    # Check if this is a git repo
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return result
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return result

    result["is_repo"] = True

    # Get current branch
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            result["branch"] = proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Get status counts
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            modified = 0
            untracked = 0
            for line in proc.stdout.strip().splitlines():
                if not line:
                    continue
                if line.startswith("??"):
                    untracked += 1
                else:
                    modified += 1
            result["modified"] = modified
            result["untracked"] = untracked
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return result
