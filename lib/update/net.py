"""HTTP helpers for update data sources and GitHub APIs."""

import asyncio
import json
import netrc
import os
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
from aiohttp_retry import ExponentialRetry, RetryClient

from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.constants import resolve_timeout_alias

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | dict[str, "JSONValue"] | list["JSONValue"]
type JSONDict = dict[str, JSONValue]
type JSONList = list[JSONValue]


def _expect_json_dict(payload: JSONValue, *, context: str) -> JSONDict:
    if isinstance(payload, dict):
        return payload
    msg = f"Expected JSON object from {context}, got {type(payload).__name__}"
    raise RuntimeError(msg)


def _expect_json_list(payload: JSONValue, *, context: str) -> JSONList:
    if isinstance(payload, list):
        return payload
    msg = f"Expected JSON array from {context}, got {type(payload).__name__}"
    raise RuntimeError(msg)


def _expect_json_string_field(
    payload: JSONDict,
    field: str,
    *,
    context: str,
) -> str:
    value = payload.get(field)
    if isinstance(value, str):
        return value
    msg = f"Expected string field {field!r} in {context}"
    raise RuntimeError(msg)


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
    return resolve_timeout_alias(
        named_timeout=request_timeout,
        named_timeout_label="request_timeout",
        kwargs=kwargs,
    )


def _pop_optional_str(kwargs: dict[str, object], key: str) -> str | None:
    value = kwargs.pop(key, None)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    msg = f"{key} must be a string"
    raise TypeError(msg)


def _pop_optional_numeric(kwargs: dict[str, object], key: str) -> float | None:
    value = kwargs.pop(key, None)
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    msg = f"{key} must be numeric"
    raise TypeError(msg)


def _pop_optional_int(kwargs: dict[str, object], key: str) -> int | None:
    value = kwargs.pop(key, None)
    if value is None:
        return None
    if isinstance(value, int):
        return value
    msg = f"{key} must be an integer"
    raise TypeError(msg)


def _pop_optional_config(kwargs: dict[str, object]) -> UpdateConfig | None:
    value = kwargs.pop("config", None)
    if value is None:
        return None
    if isinstance(value, UpdateConfig):
        return value
    msg = "config must be an UpdateConfig"
    raise TypeError(msg)


@dataclass(frozen=True)
class _RequestOptions:
    method: str
    user_agent: str | None
    request_timeout: float
    attempts: int
    backoff: float


def _parse_request_options(kwargs: dict[str, object]) -> _RequestOptions:
    user_agent = _pop_optional_str(kwargs, "user_agent")
    request_timeout = _pop_optional_numeric(kwargs, "request_timeout")
    method = kwargs.pop("method", "GET")
    if not isinstance(method, str):
        msg = "method must be a string"
        raise TypeError(msg)
    retries = _pop_optional_int(kwargs, "retries")
    backoff = _pop_optional_numeric(kwargs, "backoff")
    config = resolve_active_config(_pop_optional_config(kwargs))

    if kwargs:
        unknown = ", ".join(sorted(kwargs))
        msg = f"Unexpected keyword argument(s): {unknown}"
        raise TypeError(msg)

    timeout = request_timeout or config.default_timeout
    attempts = max(1, retries if retries is not None else config.default_retries)
    resolved_backoff = (
        max(0.0, backoff)
        if backoff is not None
        else max(0.0, config.default_retry_backoff)
    )
    return _RequestOptions(
        method=method,
        user_agent=user_agent,
        request_timeout=timeout,
        attempts=attempts,
        backoff=resolved_backoff,
    )


async def _request(
    session: aiohttp.ClientSession,
    url: str,
    **kwargs: object,
) -> tuple[bytes, dict[str, str]]:
    options = _parse_request_options(kwargs)
    headers = _build_request_headers(url, options.user_agent)
    timeout_config = aiohttp.ClientTimeout(
        total=options.request_timeout,
    )

    retry_options = ExponentialRetry(
        attempts=options.attempts,
        factor=2.0,
        start_timeout=options.backoff,
        statuses={429, 500, 502, 503, 504},
        exceptions={aiohttp.ClientError, TimeoutError, asyncio.TimeoutError},
    )

    retry_client = RetryClient(
        client_session=session,
        retry_options=retry_options,
        raise_for_status=False,
    )

    async with retry_client.request(
        options.method,
        url,
        headers=headers,
        timeout=timeout_config,
        allow_redirects=True,
    ) as response:
        payload = await response.read()
        if response.status < HTTP_BAD_REQUEST:
            return payload, dict(response.headers)
        detail = _format_http_error(response, payload)
        msg = f"Request to {url} failed after {options.attempts} attempts: {detail}"
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
    config = resolve_active_config(config)
    payload, _ = await _request(
        session,
        url,
        user_agent=user_agent,
        request_timeout=request_timeout,
        config=config,
    )
    return payload


async def fetch_headers(
    session: aiohttp.ClientSession,
    url: str,
    *,
    request_timeout: float | None = None,
    config: UpdateConfig | None = None,
) -> dict[str, str]:
    """Send a HEAD request and return the response headers."""
    config = resolve_active_config(config)
    _, headers = await _request(
        session,
        url,
        method="HEAD",
        request_timeout=request_timeout,
        config=config,
    )
    return headers


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
    config = resolve_active_config(config)
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
    config = resolve_active_config(config)
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
    data = _expect_json_dict(
        await fetch_github_api(session, f"repos/{owner}/{repo}", config=config),
        context=f"GitHub repo metadata for {owner}/{repo}",
    )
    return _expect_json_string_field(
        data,
        "default_branch",
        context=f"GitHub repo metadata for {owner}/{repo}",
    )


async def fetch_github_latest_commit(
    session: aiohttp.ClientSession,
    repository: tuple[str, str],
    *,
    file_path: str,
    branch: str,
    config: UpdateConfig | None = None,
) -> str:
    """Fetch the latest commit SHA that touched ``file_path`` on ``branch``."""
    owner, repo = repository
    data = _expect_json_list(
        await fetch_github_api(
            session,
            f"repos/{owner}/{repo}/commits",
            path=file_path,
            sha=branch,
            per_page="1",
            config=config,
        ),
        context=f"GitHub commits for {owner}/{repo}:{file_path}",
    )
    if not data:
        msg = f"No commits found for {owner}/{repo}:{file_path}"
        raise RuntimeError(msg)
    first_commit = _expect_json_dict(
        data[0],
        context=f"first commit for {owner}/{repo}:{file_path}",
    )
    return _expect_json_string_field(
        first_commit,
        "sha",
        context=f"first commit for {owner}/{repo}:{file_path}",
    )


__all__ = [
    "JSONDict",
    "JSONList",
    "JSONScalar",
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
