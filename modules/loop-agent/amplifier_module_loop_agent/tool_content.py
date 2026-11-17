"""Direct Core rich tool results, separate from experimental media references.

Core owns validation and image-safe event presentation. No dictionaries/strings
are scanned for images, and no media capability, checkpoint or ref is invented.
"""
from __future__ import annotations

import json

from amplifier_core.models import ToolResult
from amplifier_core.message_models import ImageBlock

IMAGE_UNAVAILABLE = (
    "[Image unavailable: this tool-post path cannot attest image preservation; no pixels sent.]"
)


def rich_result_copy(result: ToolResult) -> ToolResult | None:
    """Snapshot canonical Core content; old Core/ordinary results stay unchanged."""
    content = getattr(result, "content", None)
    if not content:
        return None
    # Core >=2 supplies both the canonical field and public safe presentation.
    # Do not duck-type an arbitrary result's content into a delivery authority.
    if not isinstance(result, ToolResult) or not callable(getattr(result, "safe_hook_presentation", None)):
        raise ValueError("Rich tool results require compatible Core support")
    try:
        return ToolResult.model_validate(result.model_dump()).model_copy(deep=True)
    except Exception:
        # Pydantic/decoder errors can contain the original image or private URL.
        raise ValueError("Invalid canonical rich tool result") from None


def safe_presentation(result: ToolResult) -> str:
    """Text hook/event carrier without image bytes or private source locations."""
    try:
        return json.dumps(result.safe_hook_presentation(), ensure_ascii=False)
    except Exception:
        raise ValueError("Unable to present rich tool result safely") from None


def history_content(result: ToolResult):
    """Keep canonical text; refuse images without rebuilding pre-hook authority."""
    if any(isinstance(block, ImageBlock) for block in result.content):
        return safe_presentation(result) + "\n" + IMAGE_UNAVAILABLE
    return result.content


def effective_rich_result(original: ToolResult, presentation: str, post_result) -> ToolResult:
    """Keep useful text without reconstructing hidden images after hooks.

    Missing/changed hook data never restores images from the pre-hook result.
    The safe string stays the existing tool:post result ABI. Hooks cannot inject
    image blocks through this text channel. This is not Foundation's stronger
    cooperative media settlement/checkpoint protocol.
    """
    data = getattr(post_result, "data", None)
    if getattr(post_result, "action", None) != "continue":
        return ToolResult(success=False, output="Rich tool result withheld by post-hook action; no pixels sent.")
    if not isinstance(data, dict) or "result" not in data:
        return ToolResult(success=False, output="Rich tool result has no effective post-hook result; no pixels sent.")
    replacement = data["result"]
    has_images = any(isinstance(block, ImageBlock) for block in original.content)
    if isinstance(replacement, str) and replacement == presentation:
        if has_images:
            # Final equality cannot prove no intervention: Core may collapse
            # modify/restore or swallow a hook failure. Safe markers are not
            # permission to recover the retained original image.
            return ToolResult(success=False, output=replacement + "\n" + IMAGE_UNAVAILABLE)
        return original
    if isinstance(replacement, str):
        # Preserve the hook's useful replacement, but not the original image.
        return ToolResult(success=original.success, output=replacement + "\n[Original rich content replaced by post-hook; no pixels sent.]")
    return ToolResult(success=False, output="Unsupported rich post-hook replacement; no pixels sent.")
