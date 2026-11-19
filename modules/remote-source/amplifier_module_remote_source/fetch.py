"""httpx transport for one blob: fetch bytes, compute git blob SHA, ETag support.

Resource-agnostic (Layer A). Uses the git-host "contents" JSON API so the
server-declared blob sha is available for an integrity check.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import math
import os
import subprocess
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

from .errors import (
    RemoteFetchAuthError,
    RemoteFetchError,
    RemoteFetchNotFound,
)
from .limits import FetchLimits
from .uri import Origin

logger = logging.getLogger(__name__)

_GITHUB_API_DEFAULT = "https://api.github.com"
_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_MAX_ATTEMPTS = 3


@dataclass
class FetchResult:
    """Outcome of one conditional fetch.

    ``not_modified`` is True on an HTTP 304 (caller should reuse cached bytes),
    in which case ``content``/``blob_sha``/``server_sha`` are None.
    """

    content: bytes | None
    blob_sha: str | None  # locally computed git blob SHA
    server_sha: str | None  # sha the server declared (for integrity compare)
    etag: str | None
    not_modified: bool = False


def git_blob_sha(data: bytes) -> str:
    """Compute the git blob SHA-1: sha1("blob " + len + "\\0" + data)."""
    h = hashlib.sha1()
    h.update(b"blob " + str(len(data)).encode("ascii") + b"\0")
    h.update(data)
    return h.hexdigest()


def resolve_token() -> str | None:
    """Token order: $GITHUB_TOKEN -> $GH_TOKEN -> best-effort `gh auth token`
    -> None (anonymous, public repos)."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(var)
        if val:
            return val
    try:
        out = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        token = out.stdout.strip()
        if out.returncode == 0 and token:
            return token
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _api_base(base_url: str | None, origin: Origin) -> str:
    """Resolve the contents-API base for ``origin``.

    Explicit ``base_url`` wins for any host. Else ``$GITHUB_API_URL`` (also
    honored for any host -- it is an operator-level override, not
    github.com-specific). Else, if ``origin.host`` is ``github.com``, default
    to the public GitHub API. Any other host with no override fails loud
    instead of silently being routed to api.github.com.
    """
    if base_url:
        return base_url.rstrip("/")
    env = os.environ.get("GITHUB_API_URL")
    if env:
        return env.rstrip("/")
    if origin.host == "github.com":
        return _GITHUB_API_DEFAULT
    raise RemoteFetchError(
        f"Cannot route git+https://{origin.host}/{origin.owner}/{origin.repo}: "
        f"non-github.com hosts require an explicit API base URL "
        f"(set $GITHUB_API_URL or pass base_url=). Auto-routing for host "
        f"{origin.host!r} is not supported in this version."
    )


def _contents_url(base: str, origin: Origin) -> str:
    return f"{base}/repos/{origin.owner}/{origin.repo}/contents/{origin.path}"


def _nonnegative_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _rate_limit_delay(
    resp: httpx.Response, fallback: float
) -> tuple[float, float | None]:
    """Honor provider cooldowns without retaining untrusted headers or bodies."""
    reset = _nonnegative_number(resp.headers.get("x-ratelimit-reset"))
    retry_after = resp.headers.get("Retry-After")
    if retry_after is not None:
        seconds = _nonnegative_number(retry_after)
        if seconds is None:
            try:
                date = parsedate_to_datetime(retry_after)
                if date.tzinfo is not None:
                    seconds = max(0.0, date.timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
        if seconds is not None:
            return seconds, reset
    if resp.headers.get("x-ratelimit-remaining") == "0" and reset is not None:
        return max(0.0, reset - time.time()), reset
    return fallback, reset


def _is_rate_limited(resp: httpx.Response) -> bool:
    if resp.status_code == 429:
        return True
    if resp.status_code != 403:
        return False
    if (
        "Retry-After" in resp.headers
        or resp.headers.get("x-ratelimit-remaining") == "0"
    ):
        return True
    try:
        payload = resp.json()
    except ValueError:
        return False
    message = payload.get("message") if isinstance(payload, dict) else None
    return isinstance(message, str) and "secondary rate limit" in message.lower()


async def fetch_blob(
    origin: Origin,
    *,
    token: str | None = None,
    base_url: str | None = None,
    etag: str | None = None,
    limits: FetchLimits | None = None,
) -> FetchResult:
    """Fetch one file's bytes via the contents JSON API, with bounded retry on
    transient failures. Sends ``If-None-Match`` when ``etag`` is given.

    Fails fast on 404 (RemoteFetchNotFound) and non-rate-limit 401/403
    (RemoteFetchAuthError).
    """
    limits = limits or FetchLimits()
    base = _api_base(base_url, origin)
    url = _contents_url(base, origin)
    params = {"ref": origin.ref}
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if etag:
        headers["If-None-Match"] = etag

    backoff = 1.0
    rate_backoff = 60.0
    last_exc: Exception | None = None
    async with httpx.AsyncClient() as client:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=limits.per_request_timeout,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt + 1 < _MAX_ATTEMPTS:
                    await asyncio.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 304:
                return FetchResult(None, None, None, etag, not_modified=True)
            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except ValueError as exc:
                    raise RemoteFetchError(
                        f"Non-JSON response body fetching {origin.owner}/{origin.repo}"
                        f"@{origin.ref}#{origin.path}: {exc}"
                    ) from exc
                if isinstance(payload, list):
                    raise RemoteFetchNotFound(
                        f"path resolves to a directory, not a file: "
                        f"{origin.owner}/{origin.repo}@{origin.ref}#{origin.path}"
                    )
                content_b64 = payload.get("content")
                encoding = payload.get("encoding")
                if content_b64 is None or encoding != "base64":
                    raise RemoteFetchError(
                        f"file exceeds the Contents API 1MB inline limit (or "
                        f"has no inline content) for "
                        f"{origin.owner}/{origin.repo}@{origin.ref}#{origin.path} "
                        f"(encoding={encoding!r})"
                    )
                content = base64.b64decode(content_b64)
                return FetchResult(
                    content=content,
                    blob_sha=git_blob_sha(content),
                    server_sha=payload.get("sha"),
                    etag=resp.headers.get("ETag"),
                )
            if resp.status_code == 404:
                raise RemoteFetchNotFound(
                    f"404 fetching {origin.owner}/{origin.repo}@{origin.ref}"
                    f"#{origin.path} (check the ref and the file path)"
                )
            if _is_rate_limited(resp):
                delay, reset = _rate_limit_delay(resp, rate_backoff)
                kind = (
                    "primary rate limit"
                    if resp.headers.get("x-ratelimit-remaining") == "0"
                    else "rate limit"
                )
                detail = f"; reset_epoch={reset}" if reset is not None else ""
                last_exc = RemoteFetchError(
                    f"HTTP {resp.status_code} {kind} on {origin.owner}/{origin.repo}"
                    f"; retry_after_seconds={delay}{detail}"
                )
                if attempt + 1 < _MAX_ATTEMPTS:
                    await asyncio.sleep(delay)
                rate_backoff *= 2
                continue
            if resp.status_code in (401, 403):
                raise RemoteFetchAuthError(
                    f"{resp.status_code} fetching {origin.owner}/{origin.repo}"
                    f"@{origin.ref} — check the token scope for this repo"
                )
            if resp.status_code in _RETRYABLE_STATUS:
                last_exc = RemoteFetchError(f"{resp.status_code} from {url}")
                if attempt + 1 < _MAX_ATTEMPTS:
                    await asyncio.sleep(backoff)
                backoff *= 2
                continue
            raise RemoteFetchError(f"Unexpected {resp.status_code} fetching {url}")

    raise RemoteFetchError(
        f"Retry exhausted after {_MAX_ATTEMPTS} attempts for "
        f"{origin.owner}/{origin.repo}@{origin.ref}#{origin.path}: {last_exc}"
    )
