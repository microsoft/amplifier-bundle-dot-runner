"""Loop detection for the coding agent loop.

Spec coverage: Section 2.10 (Loop Detection).

Tracks tool call signatures (name + hash of sorted JSON arguments)
in a sliding window. Detects repeating patterns of length 1, 2, or 3.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque


class LoopDetector:
    """Detects repeating tool call patterns in the agentic loop.

    Maintains a sliding window of tool call signatures and checks
    for repeating patterns after each tool round.
    """

    def __init__(self, window_size: int = 10) -> None:
        self._window_size = window_size
        self._signatures: deque[str] = deque(maxlen=window_size)

    def record(self, tool_name: str, arguments: dict) -> None:
        """Record a tool call signature.

        The signature is: name + hash(json.dumps(arguments, sort_keys=True))
        """
        args_json = json.dumps(arguments, sort_keys=True)
        args_hash = hashlib.md5(args_json.encode()).hexdigest()[:8]
        self._signatures.append(f"{tool_name}:{args_hash}")

    def check(self) -> str | None:
        """Check for repeating patterns in the signature window.

        Returns a warning message string if a loop is detected,
        or None if no loop is found.
        """
        if len(self._signatures) < self._window_size:
            return None

        recent = list(self._signatures)[-self._window_size :]

        for pattern_len in [1, 2, 3]:
            if self._window_size % pattern_len != 0:
                continue
            pattern = recent[:pattern_len]
            all_match = True
            for i in range(pattern_len, self._window_size, pattern_len):
                chunk = recent[i : i + pattern_len]
                if chunk != pattern:
                    all_match = False
                    break
            if all_match:
                return (
                    f"Loop pattern detected: the same {pattern_len} tool "
                    f"call(s) have repeated {self._window_size // pattern_len} "
                    f"times. Try a different approach."
                )

        return None

    def reset(self) -> None:
        """Clear all recorded signatures."""
        self._signatures.clear()
