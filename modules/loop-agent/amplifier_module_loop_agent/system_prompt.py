"""System prompt assembly (5-layer composition).

Spec coverage: PROMPT-001-007, LOOP-009, SYS-001, SYS-005-008.

Assembles the final system prompt from five layers:
  1. Provider-specific base instructions
  2. Environment context
  3. Tool descriptions
  4. Project-specific instructions (AGENTS.md, CLAUDE.md, etc.)
  5. User instruction override (highest priority)

Project doc discovery walks from git root to CWD, loading
provider-appropriate instruction files with a 32KB budget.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Budget for project docs (spec: 32KB)
_PROJECT_DOCS_BUDGET = 32 * 1024

# Provider-specific doc files
_PROVIDER_DOC_FILES: dict[str, list[str]] = {
    "anthropic": ["CLAUDE.md"],
    "openai": [".codex/instructions.md"],
    "gemini": ["GEMINI.md"],
}

# Always loaded regardless of provider
_UNIVERSAL_DOC_FILES = ["AGENTS.md"]


def build_system_prompt(
    base_prompt: str,
    environment: str,
    tool_descriptions: str,
    project_docs: str,
    user_override: str | None = None,
) -> str:
    """Assemble the final system prompt from 5 layers (spec SYS-001).

    Layers are concatenated in order, with later layers taking
    higher precedence:

      1. Provider-specific base instructions
      2. Environment context
      3. Tool descriptions
      4. Project-specific instructions
      5. User instruction override

    Args:
        base_prompt: Provider-specific base instructions.
        environment: Environment context block (from build_environment_context).
        tool_descriptions: Generated from the mounted tools' specs.
        project_docs: Discovered project documentation content.
        user_override: Optional user instruction override (appended last).

    Returns:
        The assembled system prompt string.
    """
    sections: list[str] = []

    # Layer 1: Base prompt (always present)
    sections.append(base_prompt)

    # Layer 2: Environment context
    sections.append(environment)

    # Layer 3: Tool descriptions
    if tool_descriptions:
        sections.append(f"## Tool Descriptions\n\n{tool_descriptions}")

    # Layer 4: Project docs
    if project_docs:
        sections.append(f"## Project Instructions\n\n{project_docs}")

    # Layer 5: User override (highest priority, last)
    if user_override is not None:
        sections.append(f"## User Instructions\n\n{user_override}")

    return "\n\n".join(sections)


def discover_project_docs(
    working_dir: str,
    provider_id: str | None = None,
) -> str:
    """Discover and load project instruction files.

    Walks from git root (or working_dir if not in a git repo) to
    the working directory. Loads AGENTS.md (always) plus provider-
    specific files. Root-level files first, subdirectory files
    appended (deeper = higher precedence).

    Spec: SYS-005 through SYS-008.

    Args:
        working_dir: Current working directory path.
        provider_id: Provider identifier ("openai", "anthropic", "gemini").

    Returns:
        Concatenated project doc content, truncated at 32KB if needed.
        Empty string if no docs found.
    """
    # Determine the root to walk from
    root = _find_git_root(working_dir)
    if root is None:
        root = working_dir

    # Build the list of recognized filenames
    recognized = list(_UNIVERSAL_DOC_FILES)
    if provider_id and provider_id in _PROVIDER_DOC_FILES:
        recognized.extend(_PROVIDER_DOC_FILES[provider_id])

    # Walk from root to working_dir, collecting docs
    dirs_to_check = _path_chain(root, working_dir)
    collected: list[str] = []

    for directory in dirs_to_check:
        for filename in recognized:
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath):
                try:
                    content = Path(filepath).read_text(
                        encoding="utf-8", errors="replace"
                    )
                    header = f"### {filename} ({os.path.relpath(filepath, root)})"
                    collected.append(f"{header}\n\n{content}")
                except OSError:
                    continue

    if not collected:
        return ""

    result = "\n\n".join(collected)

    # Enforce 32KB budget
    if len(result) > _PROJECT_DOCS_BUDGET:
        result = (
            result[:_PROJECT_DOCS_BUDGET]
            + "\n\n[Project instructions truncated at 32KB]"
        )

    return result


def _find_git_root(path: str) -> str | None:
    """Find the git repository root for the given path."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _path_chain(root: str, target: str) -> list[str]:
    """Return ordered list of directories from root to target (inclusive).

    If target is not under root, returns just [target].
    Root comes first (lowest precedence), target last (highest).
    """
    root_path = Path(root).resolve()
    target_path = Path(target).resolve()

    # If target is not under root, just return target
    try:
        target_path.relative_to(root_path)
    except ValueError:
        return [str(target_path)]

    # Build chain from root down to target
    chain = [str(root_path)]
    if root_path != target_path:
        relative = target_path.relative_to(root_path)
        current = root_path
        for part in relative.parts:
            current = current / part
            chain.append(str(current))

    return chain
