"""Direct Core rich tool results, separate from experimental media references.

Core owns validation and image-safe event presentation. No dictionaries/strings
are scanned for images, and no media capability, checkpoint or ref is invented.
"""
from __future__ import annotations

import json

from amplifier_core.models import ToolResult
from amplifier_core.message_models import ImageBlock, TextBlock

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
    """Expose actual text lines to hooks, never a JSON-escaped envelope.

    Core supplies the safe image projection. Failure output/error is additional
    information, not replaced by partial rich text. All of it goes through the
    same effective hook result before entering model history.
    """
    try:
        safe = result.safe_hook_presentation()
        text = "".join(block["text"] for block in safe.get("content", []))
        if not result.success:
            parts = ["[Tool failed]", text]
            for value in (safe.get("output"), safe.get("error")):
                if value is not None:
                    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                    if rendered and rendered not in parts:
                        parts.append(rendered)
            text = "\n".join(parts)
        return text
    except Exception:
        raise ValueError("Unable to present rich tool result safely") from None


def history_content(result: ToolResult):
    """Keep canonical text; refuse images without rebuilding pre-hook authority."""
    if any(isinstance(block, ImageBlock) for block in result.content):
        return safe_presentation(result) + "\n" + IMAGE_UNAVAILABLE
    return [TextBlock(text=safe_presentation(result))]


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
    if isinstance(replacement, str):
        # Consume EXACT effective text even if equal to our original projection.
        # Never restore pre-hook blocks or failure fields after filtering.
        if has_images:
            return ToolResult(success=False, output=replacement + "\n" + IMAGE_UNAVAILABLE)
        return ToolResult(
            success=original.success,
            output=replacement,
            error={"message": replacement} if not original.success else None,
        )
    return ToolResult(success=False, output="Unsupported rich post-hook replacement; no pixels sent.")
