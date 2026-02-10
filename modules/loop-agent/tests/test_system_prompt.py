"""Tests for system prompt assembly (5-layer composition).

Spec coverage: PROMPT-001-007, LOOP-009, SYS-001, SYS-005-008.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from amplifier_module_loop_agent.system_prompt import (
    build_system_prompt,
    discover_project_docs,
)


# ---------------------------------------------------------------
# Tests for build_system_prompt (5-layer assembly)
# ---------------------------------------------------------------


class TestBuildSystemPrompt:
    """Tests for the 5-layer system prompt assembly."""

    def test_contains_all_five_layers(self) -> None:
        prompt = build_system_prompt(
            base_prompt="You are a coding agent.",
            environment="<environment>Linux</environment>",
            tool_descriptions="Available tools: read_file, write_file",
            project_docs="# AGENTS.md\nAlways write tests.",
            user_override="Focus on the auth module.",
        )
        assert "You are a coding agent" in prompt
        assert "<environment>" in prompt
        assert "read_file" in prompt
        assert "AGENTS.md" in prompt
        assert "Focus on the auth module" in prompt

    def test_layer_order_base_first(self) -> None:
        prompt = build_system_prompt(
            base_prompt="BASE_LAYER",
            environment="ENV_LAYER",
            tool_descriptions="TOOL_LAYER",
            project_docs="DOC_LAYER",
            user_override="USER_LAYER",
        )
        # Each layer should appear in order
        base_pos = prompt.index("BASE_LAYER")
        env_pos = prompt.index("ENV_LAYER")
        tool_pos = prompt.index("TOOL_LAYER")
        doc_pos = prompt.index("DOC_LAYER")
        user_pos = prompt.index("USER_LAYER")
        assert base_pos < env_pos < tool_pos < doc_pos < user_pos

    def test_user_override_is_last(self) -> None:
        prompt = build_system_prompt(
            base_prompt="base",
            environment="env",
            tool_descriptions="tools",
            project_docs="docs",
            user_override="OVERRIDE_LAST",
        )
        # User override is in the last section
        assert prompt.strip().endswith("OVERRIDE_LAST")

    def test_none_user_override_omitted(self) -> None:
        prompt = build_system_prompt(
            base_prompt="base",
            environment="env",
            tool_descriptions="tools",
            project_docs="docs",
            user_override=None,
        )
        assert "base" in prompt
        # No "User Instructions" section header when override is None
        assert "User Instructions" not in prompt

    def test_empty_project_docs_omitted(self) -> None:
        prompt = build_system_prompt(
            base_prompt="base",
            environment="env",
            tool_descriptions="tools",
            project_docs="",
            user_override=None,
        )
        assert "Project Instructions" not in prompt

    def test_empty_tool_descriptions_omitted(self) -> None:
        prompt = build_system_prompt(
            base_prompt="base",
            environment="env",
            tool_descriptions="",
            project_docs="",
            user_override=None,
        )
        assert "Tool Descriptions" not in prompt


# ---------------------------------------------------------------
# Tests for discover_project_docs
# ---------------------------------------------------------------


class TestDiscoverProjectDocs:
    """Tests for project doc discovery (SYS-005 through SYS-008)."""

    def test_discovers_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Project rules\nAlways test.")
        docs = discover_project_docs(str(tmp_path), provider_id="anthropic")
        assert "Project rules" in docs

    def test_agents_md_always_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("universal rules")
        for provider in ["openai", "anthropic", "gemini"]:
            docs = discover_project_docs(str(tmp_path), provider_id=provider)
            assert "universal rules" in docs

    def test_claude_md_loaded_for_anthropic(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("anthropic specific")
        docs = discover_project_docs(str(tmp_path), provider_id="anthropic")
        assert "anthropic specific" in docs

    def test_claude_md_not_loaded_for_openai(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("anthropic specific")
        docs = discover_project_docs(str(tmp_path), provider_id="openai")
        assert "anthropic specific" not in docs

    def test_gemini_md_loaded_for_gemini(self, tmp_path: Path) -> None:
        (tmp_path / "GEMINI.md").write_text("gemini specific")
        docs = discover_project_docs(str(tmp_path), provider_id="gemini")
        assert "gemini specific" in docs

    def test_gemini_md_not_loaded_for_anthropic(self, tmp_path: Path) -> None:
        (tmp_path / "GEMINI.md").write_text("gemini specific")
        docs = discover_project_docs(str(tmp_path), provider_id="anthropic")
        assert "gemini specific" not in docs

    def test_codex_instructions_loaded_for_openai(self, tmp_path: Path) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "instructions.md").write_text("openai specific")
        docs = discover_project_docs(str(tmp_path), provider_id="openai")
        assert "openai specific" in docs

    def test_codex_instructions_not_loaded_for_anthropic(self, tmp_path: Path) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "instructions.md").write_text("openai specific")
        docs = discover_project_docs(str(tmp_path), provider_id="anthropic")
        assert "openai specific" not in docs

    def test_subdirectory_docs_appended(self, tmp_path: Path) -> None:
        """Deeper docs are appended after root-level docs."""
        # Set up as a git repo so git root is tmp_path
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

        (tmp_path / "AGENTS.md").write_text("root rules")
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "AGENTS.md").write_text("subdir rules")
        docs = discover_project_docs(str(subdir), provider_id="openai")
        root_pos = docs.index("root rules")
        sub_pos = docs.index("subdir rules")
        assert root_pos < sub_pos  # Root first, subdir second

    def test_32kb_budget_truncation(self, tmp_path: Path) -> None:
        """Total project docs capped at 32KB."""
        # Write a 40KB AGENTS.md
        big_content = "x" * 40_000
        (tmp_path / "AGENTS.md").write_text(big_content)
        docs = discover_project_docs(str(tmp_path), provider_id="openai")
        assert len(docs) <= 32_768 + 200  # 32KB + truncation marker overhead
        assert "truncated" in docs.lower()

    def test_empty_when_no_docs_found(self, tmp_path: Path) -> None:
        docs = discover_project_docs(str(tmp_path), provider_id="openai")
        assert docs == ""

    def test_no_provider_loads_only_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("universal")
        (tmp_path / "CLAUDE.md").write_text("claude")
        (tmp_path / "GEMINI.md").write_text("gemini")
        docs = discover_project_docs(str(tmp_path), provider_id=None)
        assert "universal" in docs
        assert "claude" not in docs
        assert "gemini" not in docs
