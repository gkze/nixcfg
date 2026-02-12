"""HTTP helpers for update data sources and GitHub APIs."""

import asyncio
import json
import netrc
import os
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import aiohttp
from aiohttp_retry import ExponentialRetry, RetryClient

from lib.update.config import UpdateConfig, _resolve_active_config

type JSONDict = dict[str, Any]
type JSONList = list[Any]
type JSONValue = JSONDict | JSONList | str | int | float | bool | None

HTTP_BAD_REQUEST = 400


def _get_github_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    netrc_path = Path.home() / ".netrc"
    if netrc_path.exists():
        try:
            netrc_data = netrc.netrc(str(netrc_path))
            for host in ("api.github.com", "github.com"):
                auth = netrc_data.authenticators(host)
                if auth:
                    return auth[2]  # password field contains token
        except netrc.NetrcParseError, OSError:
            pass
    return None


def _check_github_rate_limit(headers: dict[str, str], url: str) -> None:
    remaining = headers.get("X-RateLimit-Remaining")
    if remaining is None:
        return
    try:
        remaining_value = int(remaining)
    except ValueError:
        return
    if remaining_value > 0:
        return
    reset = headers.get("X-RateLimit-Reset")
    reset_time = "unknown"
    if reset and reset.isdigit():
        reset_time = datetime.fromtimestamp(int(reset), tz=UTC).isoformat()
    msg = f"GitHub API rate limit exceeded for {url}. Resets at {reset_time}."
    raise RuntimeError(
        msg,
    )


def _build_request_headers(url: str, user_agent: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    github_token = _get_github_token()
    if url.startswith("https://api.github.com/") and github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    return headers


def _format_http_error(response: aiohttp.ClientResponse, payload: bytes) -> str:
    error_body = payload.decode(errors="ignore").strip()
    detail = f"HTTP {response.status} {response.reason}"
    if error_body:
        detail = f"{detail}\n{error_body}"
    return detail


def _resolve_timeout_alias(
    *,
    request_timeout: float | None,
    kwargs: dict[str, object],
) -> float | None:
    timeout_alias = kwargs.pop("timeout", None)
    if timeout_alias is not None:
        if request_timeout is not None:
            msg = "Pass only one of 'request_timeout' or legacy 'timeout'"
            raise TypeError(msg)
        if not isinstance(timeout_alias, int | float):
            msg = "timeout must be a number"
            raise TypeError(msg)
        request_timeout = float(timeout_alias)
    if kwargs:
        unknown = ", ".join(sorted(kwargs))
        msg = f"Unexpected keyword argument(s): {unknown}"
        raise TypeError(msg)
    return request_timeout


async def _request(  # noqa: PLR0913
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    request_timeout: float | None = None,
    method: str = "GET",
    retries: int | None = None,
    backoff: float | None = None,
    config: UpdateConfig | None = None,
) -> tuple[bytes, dict[str, str]]:
    config = _resolve_active_config(config)
    if retries is None:
        retries = config.default_retries
    if backoff is None:
        backoff = config.default_retry_backoff
    headers = _build_request_headers(url, user_agent)
    timeout_config = aiohttp.ClientTimeout(
        total=request_timeout or config.default_timeout,
    )

    attempts = max(1, retries)
    retry_options = ExponentialRetry(
        attempts=attempts,
        factor=2.0,
        start_timeout=max(0.0, backoff),
        statuses={429, 500, 502, 503, 504},
        exceptions={aiohttp.ClientError, TimeoutError, asyncio.TimeoutError},
    )

    retry_client = RetryClient(
        client_session=session,
        retry_options=retry_options,
        raise_for_status=False,
    )
    async with retry_client.request(
        method,
        url,
        headers=headers,
        timeout=timeout_config,
        allow_redirects=True,
    ) as response:
        payload = await response.read()
        if response.status < HTTP_BAD_REQUEST:
            return payload, dict(response.headers)
        detail = _format_http_error(response, payload)
        msg = f"Request to {url} failed after {attempts} attempts: {detail}"
        raise RuntimeError(
            msg,
        )


async def fetch_url(
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    request_timeout: float | None = None,
    config: UpdateConfig | None = None,
    **kwargs: object,
) -> bytes:
    """Fetch raw bytes from ``url`` with retry and timeout config."""
    request_timeout = _resolve_timeout_alias(
        request_timeout=request_timeout,
        kwargs=kwargs,
    )
    config = _resolve_active_config(config)
    payload, _ = await _request(
        session,
        url,
        user_agent=user_agent,
        request_timeout=request_timeout,
        config=config,
    )
    return payload


async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    request_timeout: float | None = None,
    config: UpdateConfig | None = None,
    **kwargs: object,
) -> JSONValue:
    """Fetch and decode JSON data from ``url``."""
    request_timeout = _resolve_timeout_alias(
        request_timeout=request_timeout,
        kwargs=kwargs,
    )
    config = _resolve_active_config(config)
    if url.startswith("https://api.github.com/"):
        payload, headers = await _request(
            session,
            url,
            user_agent=user_agent,
            request_timeout=request_timeout,
            config=config,
        )
        _check_github_rate_limit(headers, url)
    else:
        payload = await fetch_url(
            session,
            url,
            user_agent=user_agent,
            request_timeout=request_timeout,
            config=config,
        )
    try:
        return json.loads(payload.decode())
    except json.JSONDecodeError as err:
        msg = f"Invalid JSON response from {url}: {err}"
        raise RuntimeError(msg) from err


def github_raw_url(owner: str, repo: str, rev: str, path: str) -> str:
    """Return a GitHub raw-content URL for a repo path and revision."""
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{rev}/{path}"


def github_api_url(path: str) -> str:
    """Return a GitHub API URL for an API path."""
    return f"https://api.github.com/{path}"


async def fetch_github_api(
    session: aiohttp.ClientSession,
    api_path: str,
    *,
    config: UpdateConfig | None = None,
    **params: str,
) -> JSONValue:
    """Fetch JSON from a GitHub API path with optional query parameters."""
    config = _resolve_active_config(config)
    url = github_api_url(api_path)
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return await fetch_json(
        session,
        url,
        user_agent=config.default_user_agent,
        request_timeout=config.default_timeout,
        config=config,
    )


async def fetch_github_default_branch(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    *,
    config: UpdateConfig | None = None,
) -> str:
    """Fetch the default branch name for a GitHub repository."""
    data = cast(
        "JSONDict",
        await fetch_github_api(session, f"repos/{owner}/{repo}", config=config),
    )
    return data["default_branch"]


async def fetch_github_latest_commit(  # noqa: PLR0913
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    file_path: str,
    branch: str,
    *,
    config: UpdateConfig | None = None,
) -> str:
    """Fetch the latest commit SHA that touched ``file_path`` on ``branch``."""
    data = cast(
        "list[JSONDict]",
        await fetch_github_api(
            session,
            f"repos/{owner}/{repo}/commits",
            path=file_path,
            sha=branch,
            per_page="1",
            config=config,
        ),
    )
    if not data:
        msg = f"No commits found for {owner}/{repo}:{file_path}"
        raise RuntimeError(msg)
    return data[0]["sha"]


__all__ = [
    "JSONDict",
    "JSONList",
    "JSONValue",
    "_request",
    "fetch_github_api",
    "fetch_github_default_branch",
    "fetch_github_latest_commit",
    "fetch_json",
    "fetch_url",
    "github_api_url",
    "github_raw_url",
]
