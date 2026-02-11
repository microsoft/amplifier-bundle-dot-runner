"""Environment variable filtering for tool execution (M-6).

Strips sensitive environment variables (API keys, tokens, secrets,
passwords) before passing the environment to tool subprocesses,
preventing credential leakage into tool output sent to the LLM.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Suffixes that indicate sensitive values (matched case-insensitively)
_SENSITIVE_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_SECRET_KEY")


def _is_sensitive(name: str) -> bool:
    """Return True if *name* matches a sensitive env-var pattern."""
    upper = name.upper()
    return any(upper.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def sanitize_env(
    env: dict[str, str] | None = None,
    *,
    return_stripped: bool = False,
) -> dict[str, str] | tuple[dict[str, str], list[str]]:
    """Return a copy of *env* with sensitive variables removed.

    Args:
        env: Environment dict to filter.  Defaults to ``os.environ``.
        return_stripped: When True, return a ``(sanitized, stripped_names)``
            tuple so callers can log which variables were removed.

    Returns:
        Sanitized environment dict, or tuple of (dict, stripped_names).
    """
    source = env if env is not None else dict(os.environ)
    sanitized: dict[str, str] = {}
    stripped: list[str] = []

    for key, value in source.items():
        if _is_sensitive(key):
            stripped.append(key)
        else:
            sanitized[key] = value

    if stripped:
        logger.warning(
            "Stripped %d sensitive env var(s) from tool environment: %s",
            len(stripped),
            ", ".join(sorted(stripped)),
        )

    if return_stripped:
        return sanitized, stripped
    return sanitized
