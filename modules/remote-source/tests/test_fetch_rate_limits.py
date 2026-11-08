import asyncio
import base64
import time
from email.utils import formatdate

import httpx
import pytest
import respx

from amplifier_module_remote_source.errors import RemoteFetchAuthError, RemoteFetchError
from amplifier_module_remote_source.fetch import fetch_blob
from amplifier_module_remote_source.uri import Origin

NOW = 1_800_000_000.0
ORIGIN = Origin("github.com", "acme", "widgets", "main", "a.dot")
URL = "https://api.github.com/repos/acme/widgets/contents/a.dot"


@pytest.fixture
def sleeps(monkeypatch):
    observed = []

    async def sleep(delay):
        observed.append(delay)

    monkeypatch.setattr("amplifier_module_remote_source.fetch.asyncio.sleep", sleep)
    monkeypatch.setattr(time, "time", lambda: NOW)
    return observed


def success():
    return httpx.Response(
        200,
        json={
            "content": base64.b64encode(b"digraph G {}").decode(),
            "encoding": "base64",
            "sha": "s",
        },
    )


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("status", "headers", "message", "delay"),
    [
        (
            403,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(NOW + 75)},
            "",
            75,
        ),
        (
            403,
            {
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": str(NOW + 75),
                "Retry-After": "12",
            },
            "",
            12,
        ),
        (403, {"Retry-After": "12"}, "", 12),
        (429, {"Retry-After": "12"}, "", 12),
        (429, {}, "", 60),
        (403, {}, "You have exceeded a secondary rate limit.", 60),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "invalid"}, "", 60),
        (403, {"Retry-After": formatdate(NOW + 90, usegmt=True)}, "", 90),
        (429, {"Retry-After": "0"}, "", 0),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(NOW - 1)}, "", 0),
        (
            403,
            {
                "Retry-After": "invalid",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": str(NOW + 75),
            },
            "",
            75,
        ),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "nan"}, "", 60),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "inf"}, "", 60),
    ],
)
async def test_provider_delay_precedes_next_request(
    sleeps, status, headers, message, delay
):
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status, headers=headers, json={"message": message})
        assert sleeps == [delay], (
            "the provider cooldown must finish before another request"
        )
        return success()

    route = respx.get(URL).mock(side_effect=respond)
    result = await fetch_blob(ORIGIN)
    assert route.call_count == 2
    assert result.content == b"digraph G {}"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("value", ["not-a-delay", "-3", "nan", "inf"])
async def test_invalid_retry_after_uses_rate_limit_floor(sleeps, value):
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(403, headers={"Retry-After": value}), success()]
    )
    assert (await fetch_blob(ORIGIN)).content == b"digraph G {}"
    assert route.call_count == 2
    assert sleeps == [60]


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_exhaustion_backs_off_without_a_final_sleep(sleeps):
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            429,
            json={"message": "untrusted-sensitive-body"},
            headers={"x-unrelated": "untrusted-sensitive-header"},
        )
    )
    with pytest.raises(
        RemoteFetchError, match="Retry exhausted after 3 attempts"
    ) as error:
        await fetch_blob(ORIGIN, token="private-test-token")
    assert route.call_count == 3
    assert sleeps == [60, 120]
    assert "HTTP 429" in str(error.value)
    assert "retry_after_seconds=240" in str(error.value)
    assert "sensitive" not in str(error.value)
    assert "private-test-token" not in str(error.value)


@pytest.mark.asyncio
@respx.mock
async def test_primary_exhaustion_retains_numeric_reset_without_secondary_label(sleeps):
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(NOW + 75)},
        )
    )
    with pytest.raises(RemoteFetchError) as error:
        await fetch_blob(ORIGIN)
    assert route.call_count == 3
    assert sleeps == [75, 75]
    assert "primary rate limit" in str(error.value)
    assert "reset_epoch=1800000075" in str(error.value)
    assert "secondary" not in str(error.value)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status", [401, 403])
async def test_permissions_failure_still_fails_without_retry(sleeps, status):
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            status, json={"message": "Resource not accessible by integration"}
        )
    )
    with pytest.raises(RemoteFetchAuthError):
        await fetch_blob(ORIGIN)
    assert route.call_count == 1
    assert sleeps == []


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("transport", [False, True])
async def test_other_transient_failures_keep_short_backoff_and_no_final_sleep(
    sleeps, transport
):
    route = respx.get(URL).mock(
        side_effect=httpx.ConnectError("offline") if transport else None,
        return_value=None if transport else httpx.Response(503),
    )
    with pytest.raises(RemoteFetchError, match="Retry exhausted after 3 attempts"):
        await fetch_blob(ORIGIN)
    assert route.call_count == 3
    assert sleeps == [1, 2]


@pytest.mark.asyncio
@respx.mock
async def test_caller_cancellation_during_cooldown_prevents_another_request(
    monkeypatch,
):
    async def cancel(delay):
        raise asyncio.CancelledError

    monkeypatch.setattr("amplifier_module_remote_source.fetch.asyncio.sleep", cancel)
    route = respx.get(URL).mock(return_value=httpx.Response(429))
    with pytest.raises(asyncio.CancelledError):
        await fetch_blob(ORIGIN)
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("body", [b"forbidden", b"[]", b'{"message": null}'])
async def test_non_rate_limit_forbidden_body_is_not_retried(sleeps, body):
    route = respx.get(URL).mock(return_value=httpx.Response(403, content=body))
    with pytest.raises(RemoteFetchAuthError):
        await fetch_blob(ORIGIN)
    assert route.call_count == 1
    assert sleeps == []


@pytest.mark.asyncio
@respx.mock
async def test_primary_reset_delay_uses_current_time_for_each_response(
    sleeps, monkeypatch
):
    monkeypatch.setattr(time, "time", lambda: NOW + sum(sleeps))
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(
                403,
                headers={
                    "x-ratelimit-remaining": "0",
                    "x-ratelimit-reset": str(NOW + 75),
                },
            ),
            httpx.Response(
                403,
                headers={
                    "x-ratelimit-remaining": "0",
                    "x-ratelimit-reset": str(NOW + 90),
                },
            ),
            success(),
        ]
    )
    assert (await fetch_blob(ORIGIN)).content == b"digraph G {}"
    assert route.call_count == 3
    assert sleeps == [75, 15]


@pytest.mark.asyncio
@respx.mock
async def test_caller_deadline_interrupts_real_provider_cooldown():
    route = respx.get(URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "3600"})
    )
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(fetch_blob(ORIGIN), timeout=0.1)
    assert route.call_count == 1
