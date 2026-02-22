"""Apply Patch Tool Module for Amplifier.

Applies patches in the v4a unified diff format used by OpenAI's codex-rs agent.
Supports Add File, Delete File, Update File (with hunks), and Update+Rename operations.
"""

# Amplifier module metadata
__amplifier_module_type__ = "tool"

import logging
from pathlib import Path
from typing import Any

from .parser import Hunk, PatchOperation, parse_v4a_patch

__all__ = ["ApplyPatchTool", "mount"]

logger = logging.getLogger(__name__)


class ApplyPatchTool:
    """Apply code changes using the v4a patch format.

    Supports creating, deleting, modifying, and renaming files
    in a single patch operation.
    """

    name = "apply_patch"
    description = (
        "Apply code changes using the patch format. Supports creating, deleting, "
        "and modifying files in a single operation."
    )

    def __init__(self, config: dict[str, Any], coordinator: Any = None):
        """Initialize ApplyPatchTool with configuration.

        Args:
            config: Module configuration. Supports:
                - working_dir: Base directory for resolving file paths.
            coordinator: Optional module coordinator.
        """
        self.config = config
        self.coordinator = coordinator
        self.working_dir = config.get("working_dir")

    @property
    def input_schema(self) -> dict:
        """Return JSON schema for tool parameters."""
        return {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "string",
                    "description": "The patch content in v4a format",
                },
            },
            "required": ["patch"],
        }

    async def execute(self, input: dict[str, Any]) -> Any:
        """Execute the apply_patch tool.

        Args:
            input: {"patch": str} - The v4a format patch content.

        Returns:
            ToolResult with files_modified list and summary on success,
            or error details on failure.
        """
        # Import here to avoid hard dependency at module level
        from amplifier_core import ToolResult

        patch_text = input.get("patch")
        if not patch_text:
            error_msg = "patch parameter is required"
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )

        # Parse the patch
        try:
            operations = parse_v4a_patch(patch_text)
        except ValueError as e:
            error_msg = f"Patch parse error: {e}"
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )

        if not operations:
            error_msg = "Patch contains no operations"
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )

        # Apply each operation
        files_modified: list[str] = []
        summaries: list[str] = []

        for op in operations:
            try:
                result = self._apply_operation(op)
                files_modified.append(op.path)
                summaries.append(result)
            except Exception as e:
                error_msg = f"Error applying {op.operation} to {op.path}: {e}"
                return ToolResult(
                    success=False,
                    output=error_msg,
                    error={"message": error_msg},
                )

        summary = "; ".join(summaries)
        return ToolResult(
            success=True,
            output={"files_modified": files_modified, "summary": summary},
        )

    def _resolve_path(self, relative_path: str) -> Path:
        """Resolve a relative path against the working directory."""
        if self.working_dir:
            return Path(self.working_dir) / relative_path
        return Path(relative_path)

    def _apply_operation(self, op: PatchOperation) -> str:
        """Apply a single patch operation. Returns a summary string."""
        if op.operation == "add_file":
            return self._apply_add_file(op)
        elif op.operation == "delete_file":
            return self._apply_delete_file(op)
        elif op.operation == "update_file":
            return self._apply_update_file(op)
        else:
            raise ValueError(f"Unknown operation: {op.operation}")

    def _apply_add_file(self, op: PatchOperation) -> str:
        """Create a new file with the given content."""
        path = self._resolve_path(op.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(op.content)
        return f"Added {op.path}"

    def _apply_delete_file(self, op: PatchOperation) -> str:
        """Delete an existing file."""
        path = self._resolve_path(op.path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {op.path}")
        path.unlink()
        return f"Deleted {op.path}"

    def _apply_update_file(self, op: PatchOperation) -> str:
        """Apply hunk changes to an existing file, with optional rename."""
        path = self._resolve_path(op.path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {op.path}")

        content = path.read_text()
        file_lines = content.splitlines()

        # Apply hunks in order
        for hunk in op.hunks:
            file_lines = self._apply_hunk(file_lines, hunk, op.path)

        new_content = "\n".join(file_lines)
        if content.endswith("\n") or (file_lines and file_lines[-1] == ""):
            # Preserve trailing newline
            if not new_content.endswith("\n"):
                new_content += "\n"

        # Handle rename
        if op.move_to:
            new_path = self._resolve_path(op.move_to)
            new_path.parent.mkdir(parents=True, exist_ok=True)
            new_path.write_text(new_content)
            path.unlink()
            return f"Updated and moved {op.path} -> {op.move_to}"
        else:
            path.write_text(new_content)
            return f"Updated {op.path}"

    def _apply_hunk(
        self, file_lines: list[str], hunk: Hunk, filename: str
    ) -> list[str]:
        """Apply a single hunk to file lines. Returns updated lines."""
        # Build the sequence of context and delete lines we need to find
        match_lines: list[str] = []
        for line_type, line_content in hunk.lines:
            if line_type in ("context", "delete"):
                match_lines.append(line_content)

        if not match_lines:
            # Hunk has only additions — append at end
            additions = [lc for lt, lc in hunk.lines if lt == "add"]
            return file_lines + additions

        # Find the match position
        match_pos = self._find_match(file_lines, match_lines, hunk.context_hint)
        if match_pos is None:
            raise ValueError(
                f"Could not find hunk match in {filename} "
                f"(context hint: {hunk.context_hint!r})"
            )

        # Build replacement: walk through hunk lines and construct output
        result = file_lines[:match_pos]
        match_idx = match_pos

        for line_type, line_content in hunk.lines:
            if line_type == "context":
                result.append(file_lines[match_idx])
                match_idx += 1
            elif line_type == "delete":
                # Skip this line (don't add to result)
                match_idx += 1
            elif line_type == "add":
                result.append(line_content)

        # Append remaining lines after the matched region
        result.extend(file_lines[match_idx:])
        return result

    def _find_match(
        self,
        file_lines: list[str],
        match_lines: list[str],
        context_hint: str,
    ) -> int | None:
        """Find the position in file_lines where match_lines occur.

        Uses exact matching first, then tries whitespace-normalized matching.
        """
        match_len = len(match_lines)
        if match_len == 0:
            return 0

        # Exact match
        for i in range(len(file_lines) - match_len + 1):
            if file_lines[i : i + match_len] == match_lines:
                return i

        # Fuzzy match: strip trailing whitespace
        stripped_match = [line.rstrip() for line in match_lines]
        for i in range(len(file_lines) - match_len + 1):
            stripped_file = [line.rstrip() for line in file_lines[i : i + match_len]]
            if stripped_file == stripped_match:
                return i

        return None


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the apply_patch tool.

    Args:
        coordinator: Module coordinator for registering tools.
        config: Module configuration.
    """
    config = config or {}

    # Get session.working_dir capability if not explicitly configured
    if "working_dir" not in config:
        working_dir = coordinator.get_capability("session.working_dir")
        if working_dir:
            config["working_dir"] = working_dir

    tool = ApplyPatchTool(config, coordinator)
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("Mounted apply_patch tool")
