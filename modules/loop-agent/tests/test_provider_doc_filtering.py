"""Tests for provider-based project doc filtering (Fix 2.6).

Spec coverage: Section 6.2 — project instruction files should be filtered
by active provider. Anthropic loads CLAUDE.md and .claude/ files, OpenAI
loads CODEX.md / .codex/ files, Gemini loads GEMINI.md. ALL providers
load AGENTS.md (shared).

The _filter_project_docs() function provides a standalone filtering utility
that can be used to validate discovered doc paths against the active provider.
"""

from __future__ import annotations

from pathlib import Path

from amplifier_module_loop_agent.system_prompt import (
    _filter_project_docs,
    discover_project_docs,
)


# ---------------------------------------------------------------------------
# _filter_project_docs() unit tests
# ---------------------------------------------------------------------------


class TestFilterProjectDocs:
    """Tests for the _filter_project_docs standalone filter function."""

    def test_agents_md_always_included(self):
        """AGENTS.md passes filter for any provider."""
        paths = ["/repo/AGENTS.md"]
        for provider in ["anthropic", "openai", "gemini", None]:
            result = _filter_project_docs(paths, provider)
            assert "/repo/AGENTS.md" in result

    def test_claude_md_included_for_anthropic(self):
        paths = ["/repo/CLAUDE.md"]
        result = _filter_project_docs(paths, "anthropic")
        assert "/repo/CLAUDE.md" in result

    def test_claude_md_excluded_for_openai(self):
        paths = ["/repo/CLAUDE.md"]
        result = _filter_project_docs(paths, "openai")
        assert "/repo/CLAUDE.md" not in result

    def test_claude_md_excluded_for_gemini(self):
        paths = ["/repo/CLAUDE.md"]
        result = _filter_project_docs(paths, "gemini")
        assert "/repo/CLAUDE.md" not in result

    def test_claude_dir_files_included_for_anthropic(self):
        """Files under .claude/ directory are included for Anthropic."""
        paths = ["/repo/.claude/settings.json", "/repo/.claude/instructions.md"]
        result = _filter_project_docs(paths, "anthropic")
        assert len(result) == 2

    def test_claude_dir_files_excluded_for_openai(self):
        """Files under .claude/ directory are excluded for non-Anthropic."""
        paths = ["/repo/.claude/settings.json"]
        result = _filter_project_docs(paths, "openai")
        assert len(result) == 0

    def test_gemini_md_included_for_gemini(self):
        paths = ["/repo/GEMINI.md"]
        result = _filter_project_docs(paths, "gemini")
        assert "/repo/GEMINI.md" in result

    def test_gemini_md_excluded_for_anthropic(self):
        paths = ["/repo/GEMINI.md"]
        result = _filter_project_docs(paths, "anthropic")
        assert "/repo/GEMINI.md" not in result

    def test_codex_files_included_for_openai(self):
        """CODEX.md and .codex/ files included for OpenAI."""
        paths = ["/repo/CODEX.md", "/repo/.codex/instructions.md"]
        result = _filter_project_docs(paths, "openai")
        assert len(result) == 2

    def test_codex_files_excluded_for_anthropic(self):
        paths = ["/repo/CODEX.md", "/repo/.codex/instructions.md"]
        result = _filter_project_docs(paths, "anthropic")
        assert len(result) == 0

    def test_mixed_paths_filtered_correctly(self):
        """Only matching provider docs + AGENTS.md pass through."""
        paths = [
            "/repo/AGENTS.md",
            "/repo/CLAUDE.md",
            "/repo/GEMINI.md",
            "/repo/CODEX.md",
            "/repo/.claude/instructions.md",
        ]
        result = _filter_project_docs(paths, "anthropic")
        assert "/repo/AGENTS.md" in result
        assert "/repo/CLAUDE.md" in result
        assert "/repo/.claude/instructions.md" in result
        assert "/repo/GEMINI.md" not in result
        assert "/repo/CODEX.md" not in result

    def test_none_provider_only_agents_md(self):
        """When provider is None, only AGENTS.md passes."""
        paths = [
            "/repo/AGENTS.md",
            "/repo/CLAUDE.md",
            "/repo/GEMINI.md",
        ]
        result = _filter_project_docs(paths, None)
        assert result == ["/repo/AGENTS.md"]

    def test_empty_paths_returns_empty(self):
        result = _filter_project_docs([], "anthropic")
        assert result == []


# ---------------------------------------------------------------------------
# Integration: discover_project_docs with .claude/ directory
# ---------------------------------------------------------------------------


class TestDiscoverProjectDocsClaudeDir:
    """Verify .claude/ directory files are discovered for Anthropic."""

    def test_claude_dir_discovered_for_anthropic(self, tmp_path: Path) -> None:
        """Files in .claude/ are loaded for Anthropic provider."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "instructions.md").write_text("claude dir instructions")
        docs = discover_project_docs(str(tmp_path), provider_id="anthropic")
        assert "claude dir instructions" in docs

    def test_claude_dir_not_discovered_for_openai(self, tmp_path: Path) -> None:
        """Files in .claude/ are NOT loaded for OpenAI provider."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "instructions.md").write_text("claude dir instructions")
        docs = discover_project_docs(str(tmp_path), provider_id="openai")
        assert "claude dir instructions" not in docs

    def test_codex_md_discovered_for_openai(self, tmp_path: Path) -> None:
        """CODEX.md is loaded for OpenAI provider."""
        (tmp_path / "CODEX.md").write_text("openai codex rules")
        docs = discover_project_docs(str(tmp_path), provider_id="openai")
        assert "openai codex rules" in docs

    def test_codex_md_not_discovered_for_anthropic(self, tmp_path: Path) -> None:
        """CODEX.md is NOT loaded for Anthropic provider."""
        (tmp_path / "CODEX.md").write_text("openai codex rules")
        docs = discover_project_docs(str(tmp_path), provider_id="anthropic")
        assert "openai codex rules" not in docs
