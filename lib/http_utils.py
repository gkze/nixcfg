"""Shared HTTP and GitHub auth helpers."""

from __future__ import annotations

import base64
import netrc
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
import keyring
from keyring.errors import KeyringError
from tenacity import (
    AsyncRetrying,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
)
from tenacity.wait import wait_exponential

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping

    from tenacity.wait import WaitBaseT

HTTP_BAD_REQUEST = 400
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_GO_KEYRING_PREFIX = "go-keyring-base64:"


class _RetryableStatusError(RuntimeError):
    """HTTP status error that should be retried."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(detail)


class _NonRetryableStatusError(RuntimeError):
    """HTTP status error that should fail immediately."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(detail)


@dataclass(frozen=True)
class SyncRequestError(RuntimeError):
    """A failed synchronous HTTP request after validation and retries."""

    url: str
    attempts: int
    kind: str
    detail: str
    status: int | None = None

    def __str__(self) -> str:
        """Render the underlying transport/status detail."""
        return self.detail


def unwrap_go_keyring_token(raw: str) -> str | None:
    """Decode the ``go-keyring-base64:`` wrapper used by ``gh``."""
    if raw.startswith(_GO_KEYRING_PREFIX):
        raw = base64.b64decode(raw[len(_GO_KEYRING_PREFIX) :]).decode()
    return raw.strip() or None


def resolve_github_token(
    *,
    allow_keyring: bool = False,
    allow_netrc: bool = False,
    logger: logging.Logger | None = None,
) -> str | None:
    """Resolve a GitHub token from configured local credential sources."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token

    if allow_keyring:
        try:
            raw = keyring.get_password("gh:github.com", "")
        except KeyringError, RuntimeError:
            raw = None
        if raw:
            token = unwrap_go_keyring_token(raw)
            if token:
                return token

    if allow_netrc:
        netrc_path = Path.home() / ".netrc"
        if netrc_path.exists():
            try:
                netrc_data = netrc.netrc(str(netrc_path))
                for host in ("api.github.com", "github.com"):
                    auth = netrc_data.authenticators(host)
                    if auth:
                        return auth[2]
            except (netrc.NetrcParseError, OSError) as exc:
                if logger is not None:
                    logger.warning(
                        "Failed to parse %s for GitHub token discovery: %s",
                        netrc_path,
                        exc,
                    )
    return None


def build_github_headers(
    url: str,
    *,
    accept: str | None = None,
    auth_scheme: str = "Bearer",
    token: str | None = None,
    user_agent: str | None = None,
) -> dict[str, str]:
    """Return GitHub-aware request headers for ``url``."""
    headers: dict[str, str] = {}
    if accept:
        headers["Accept"] = accept
    if user_agent:
        headers["User-Agent"] = user_agent
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"{auth_scheme} {token}"
    return headers


def _format_http_error(response: httpx.Response, payload: bytes) -> str:
    detail = f"HTTP {response.status_code} {response.reason_phrase}"
    body = payload.decode(errors="ignore").strip()
    if body:
        detail = f"{detail}\n{body}"
    return detail


def _raise_status_error(status: int, detail: str) -> None:
    """Raise the appropriate status exception for ``status``."""
    if status in RETRYABLE_STATUSES:
        raise _RetryableStatusError(status, detail)
    raise _NonRetryableStatusError(status, detail)


def _validate_request_target(
    url: str,
    *,
    allowed_schemes: frozenset[str],
    attempts: int,
) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in allowed_schemes or not parsed.netloc:
        schemes = "/".join(sorted(allowed_schemes))
        msg = f"Only absolute {schemes.upper()} URLs are allowed, got: {url!r}"
        raise ValueError(msg)
    if parsed.hostname is None:
        msg = f"Could not parse host from URL: {url!r}"
        raise ValueError(msg)
    if attempts < 1:
        msg = f"Expected at least one HTTP attempt for {url}"
        raise RuntimeError(msg)


def _retry_wait(
    *,
    backoff: float,
    max_backoff: float | None,
) -> WaitBaseT:
    return (
        wait_exponential(
            multiplier=max(0.0, backoff),
            exp_base=2,
            max=max(0.0, max_backoff),
        )
        if max_backoff is not None
        else wait_exponential(multiplier=max(0.0, backoff), exp_base=2)
    )


def fetch_url_bytes(
    url: str,
    *,
    allowed_schemes: frozenset[str] = frozenset({"https"}),
    attempts: int = 3,
    backoff: float = 1.0,
    max_backoff: float | None = None,
    headers: Mapping[str, str] | None = None,
    method: str = "GET",
    timeout: float = 30.0,
) -> tuple[bytes, dict[str, str]]:
    """Fetch ``url`` and return response bytes plus response headers."""
    _validate_request_target(url, allowed_schemes=allowed_schemes, attempts=attempts)

    all_headers = dict(headers or {})
    attempt_count = 0
    retryer = Retrying(
        stop=stop_after_attempt(attempts),
        wait=_retry_wait(backoff=backoff, max_backoff=max_backoff),
        retry=retry_if_exception_type((
            _RetryableStatusError,
            httpx.TransportError,
        )),
        reraise=True,
    )

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            for attempt in retryer:
                with attempt:
                    attempt_count += 1
                    response = client.request(method, url, headers=all_headers)
                    payload = response.content
                    if response.status_code < HTTP_BAD_REQUEST:
                        return payload, dict(response.headers)
                    detail = _format_http_error(response, payload)
                    _raise_status_error(response.status_code, detail)
    except _NonRetryableStatusError as exc:
        raise SyncRequestError(
            url=url,
            attempts=attempt_count,
            kind="status",
            detail=str(exc),
            status=exc.status,
        ) from exc
    except _RetryableStatusError as exc:
        raise SyncRequestError(
            url=url,
            attempts=attempt_count,
            kind="status",
            detail=str(exc),
            status=exc.status,
        ) from exc
    except httpx.TimeoutException as exc:
        raise SyncRequestError(
            url=url,
            attempts=attempt_count,
            kind="timeout",
            detail=str(exc),
        ) from exc
    except httpx.TransportError as exc:
        raise SyncRequestError(
            url=url,
            attempts=attempt_count,
            kind="network",
            detail=str(exc),
        ) from exc

    msg = f"Failed fetching {url}"
    raise RuntimeError(msg)


async def fetch_url_bytes_async(
    url: str,
    *,
    allowed_schemes: frozenset[str] = frozenset({"https"}),
    attempts: int = 3,
    backoff: float = 1.0,
    max_backoff: float | None = None,
    headers: Mapping[str, str] | None = None,
    cookies: httpx.Cookies | None = None,
    method: str = "GET",
    request_timeout: float = 30.0,
    client: httpx.AsyncClient | None = None,
) -> tuple[bytes, dict[str, str]]:
    """Fetch ``url`` asynchronously and return response bytes plus headers."""
    _validate_request_target(url, allowed_schemes=allowed_schemes, attempts=attempts)

    all_headers = dict(headers or {})
    attempt_count = 0
    retryer = AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=_retry_wait(backoff=backoff, max_backoff=max_backoff),
        retry=retry_if_exception_type((
            _RetryableStatusError,
            httpx.TransportError,
        )),
        reraise=True,
    )

    async def _run(async_client: httpx.AsyncClient) -> tuple[bytes, dict[str, str]]:
        nonlocal attempt_count
        async for attempt in retryer:
            with attempt:
                attempt_count += 1
                response = await async_client.request(
                    method,
                    url,
                    headers=all_headers,
                    cookies=cookies,
                    timeout=request_timeout,
                )
                payload = response.content
                if response.status_code < HTTP_BAD_REQUEST:
                    return payload, dict(response.headers)
                detail = _format_http_error(response, payload)
                _raise_status_error(response.status_code, detail)
        msg = f"Failed fetching {url}"
        raise RuntimeError(msg)

    try:
        if client is not None:
            return await _run(client)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=request_timeout,
        ) as async_client:
            return await _run(async_client)
    except _NonRetryableStatusError as exc:
        raise SyncRequestError(
            url=url,
            attempts=attempt_count,
            kind="status",
            detail=str(exc),
            status=exc.status,
        ) from exc
    except _RetryableStatusError as exc:
        raise SyncRequestError(
            url=url,
            attempts=attempt_count,
            kind="status",
            detail=str(exc),
            status=exc.status,
        ) from exc
    except httpx.TimeoutException as exc:
        raise SyncRequestError(
            url=url,
            attempts=attempt_count,
            kind="timeout",
            detail=str(exc),
        ) from exc
    except httpx.TransportError as exc:
        raise SyncRequestError(
            url=url,
            attempts=attempt_count,
            kind="network",
            detail=str(exc),
        ) from exc
