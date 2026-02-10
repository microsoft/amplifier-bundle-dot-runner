"""Parser for the v4a unified diff format used by OpenAI's codex-rs agent.

Grammar reference (from coding-agent-loop-spec Appendix A):

    patch       = "*** Begin Patch\n" operations "*** End Patch\n"
    operations  = (add_file | delete_file | update_file)*
    add_file    = "*** Add File: " path "\n" added_lines
    delete_file = "*** Delete File: " path "\n"
    update_file = "*** Update File: " path "\n" [move_line] hunks
    move_line   = "*** Move to: " new_path "\n"
    added_lines = ("+" line "\n")*
    hunks       = hunk+
    hunk        = "@@ " [context_hint] "\n" hunk_lines
    hunk_lines  = (context_line | delete_line | add_line)+
    context_line = " " line "\n"
    delete_line  = "-" line "\n"
    add_line     = "+" line "\n"
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Hunk:
    """A single hunk within an Update File operation."""

    context_hint: str = ""
    lines: list[tuple[str, str]] = field(default_factory=list)
    # Each line is (type, content) where type is "context", "delete", or "add"


@dataclass
class PatchOperation:
    """A single file operation within a patch."""

    operation: str  # "add_file", "delete_file", "update_file"
    path: str
    content: str = ""  # For add_file: the full file content
    hunks: list[Hunk] = field(default_factory=list)  # For update_file
    move_to: str | None = None  # For update_file with rename


def parse_v4a_patch(patch_text: str) -> list[PatchOperation]:
    """Parse a v4a format patch into a list of operations.

    Args:
        patch_text: The full patch text including Begin/End markers.

    Returns:
        List of PatchOperation objects.

    Raises:
        ValueError: If the patch format is invalid.
    """
    lines = patch_text.split("\n")

    # Find and validate envelope
    begin_idx = _find_line(lines, "*** Begin Patch")
    if begin_idx is None:
        raise ValueError("Missing '*** Begin Patch' header")

    end_idx = _find_line(lines, "*** End Patch", start=begin_idx + 1)
    if end_idx is None:
        raise ValueError("Missing '*** End Patch' footer")

    # Parse operations between Begin and End
    body_lines = lines[begin_idx + 1 : end_idx]
    return _parse_operations(body_lines)


def _find_line(lines: list[str], prefix: str, start: int = 0) -> int | None:
    """Find the index of the first line matching prefix."""
    for i in range(start, len(lines)):
        if lines[i].strip() == prefix:
            return i
    return None


def _parse_operations(lines: list[str]) -> list[PatchOperation]:
    """Parse all operations from the body lines between Begin/End markers."""
    operations: list[PatchOperation] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.startswith("*** Add File: "):
            path = line[len("*** Add File: ") :]
            i += 1
            content_lines: list[str] = []
            while i < len(lines) and _is_content_line(lines[i]):
                # Strip the leading '+' prefix
                content_lines.append(lines[i][1:])
                i += 1
            content = "\n".join(content_lines)
            if content_lines:
                content += "\n"
            operations.append(
                PatchOperation(operation="add_file", path=path, content=content)
            )

        elif line.startswith("*** Delete File: "):
            path = line[len("*** Delete File: ") :]
            operations.append(PatchOperation(operation="delete_file", path=path))
            i += 1

        elif line.startswith("*** Update File: "):
            path = line[len("*** Update File: ") :]
            i += 1

            # Check for optional Move to
            move_to = None
            if i < len(lines) and lines[i].startswith("*** Move to: "):
                move_to = lines[i][len("*** Move to: ") :]
                i += 1

            # Parse hunks
            hunks: list[Hunk] = []
            while i < len(lines) and (
                lines[i].startswith("@@ ") or _is_hunk_line(lines[i])
            ):
                if lines[i].startswith("@@ "):
                    context_hint = lines[i][3:].strip()
                    i += 1
                    hunk_lines: list[tuple[str, str]] = []
                    while i < len(lines) and _is_hunk_line(lines[i]):
                        hunk_lines.append(_parse_hunk_line(lines[i]))
                        i += 1
                    hunks.append(Hunk(context_hint=context_hint, lines=hunk_lines))
                else:
                    # Shouldn't happen with well-formed patches, but be safe
                    i += 1

            operations.append(
                PatchOperation(
                    operation="update_file",
                    path=path,
                    hunks=hunks,
                    move_to=move_to,
                )
            )

        elif line.strip() == "" or line.strip() == "*** End of File":
            # Skip blank lines and optional end-of-file markers
            i += 1

        else:
            # Skip unrecognized lines
            i += 1

    return operations


def _is_content_line(line: str) -> bool:
    """Check if line is an added content line (starts with '+')."""
    return line.startswith("+")


def _is_hunk_line(line: str) -> bool:
    """Check if line is a hunk body line (context, delete, or add)."""
    if not line:
        return False
    # Context lines start with space, delete with '-', add with '+'
    return line[0] in (" ", "-", "+")


def _parse_hunk_line(line: str) -> tuple[str, str]:
    """Parse a single hunk line into (type, content)."""
    if line.startswith(" "):
        return ("context", line[1:])
    elif line.startswith("-"):
        return ("delete", line[1:])
    elif line.startswith("+"):
        return ("add", line[1:])
    else:
        raise ValueError(f"Invalid hunk line: {line!r}")
