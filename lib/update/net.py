"""HTTP helpers for update data sources and GitHub APIs."""

import json
import logging
import urllib.parse
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import aiohttp
from githubkit import GitHub
from githubkit.exception import GitHubException
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt
from tenacity.wait import wait_exponential

from lib import http_utils, json_utils
from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.constants import resolve_timeout_alias

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | dict[str, "JSONValue"] | list["JSONValue"]
type JSONDict = dict[str, JSONValue]
type JSONList = list[JSONValue]


def _expect_json_dict(payload: JSONValue, *, context: str) -> JSONDict:
    try:
        return json_utils.as_json_object(payload, context=context)
    except TypeError as exc:
        msg = f"Expected JSON object from {context}, got {type(payload).__name__}"
        raise RuntimeError(msg) from exc


def _expect_json_list(payload: JSONValue, *, context: str) -> JSONList:
    try:
        return json_utils.as_json_list(payload, context=context)
    except TypeError as exc:
        msg = f"Expected JSON array from {context}, got {type(payload).__name__}"
        raise RuntimeError(msg) from exc


def _expect_json_string_field(
    payload: JSONDict,
    field: str,
    *,
    context: str,
) -> str:
    try:
        return json_utils.get_required_str(
            cast("dict[str, object]", payload),
            field,
            context=context,
        )
    except TypeError as exc:
        msg = f"Expected string field {field!r} in {context}"
        raise RuntimeError(msg) from exc


HTTP_BAD_REQUEST = 400
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
logger = logging.getLogger(__name__)
_GITHUB_API_VERSION = "2022-11-28"


class _RetryableStatusError(RuntimeError):
    """HTTP status error that should be retried."""


class _NonRetryableStatusError(RuntimeError):
    """HTTP status error that should fail immediately."""


def _get_github_token() -> str | None:
    return http_utils.resolve_github_token(allow_netrc=True, logger=logger)


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
    return http_utils.build_github_headers(
        url,
        token=_get_github_token(),
        user_agent=user_agent,
    )


def _build_githubkit_client(config: UpdateConfig) -> GitHub:
    """Create a GitHubKit client using the update pipeline defaults."""
    return GitHub(
        _get_github_token(),
        user_agent=config.default_user_agent,
        timeout=config.default_timeout,
    )


async def _fetch_github_repo(owner: str, repo: str, *, config: UpdateConfig) -> object:
    """Fetch one GitHub repository model through GitHubKit."""
    client = _build_githubkit_client(config)
    try:
        return (
            await client.rest(_GITHUB_API_VERSION).repos.async_get(owner, repo)
        ).parsed_data
    except GitHubException as exc:
        msg = f"GitHub repo metadata request failed for {owner}/{repo}: {exc}"
        raise RuntimeError(msg) from exc


async def _fetch_github_commits(
    owner: str,
    repo: str,
    *,
    branch: str,
    config: UpdateConfig,
    file_path: str,
) -> tuple[object, ...]:
    """Fetch recent commits for one path through GitHubKit."""
    client = _build_githubkit_client(config)
    try:
        commits = (
            await client.rest(_GITHUB_API_VERSION).repos.async_list_commits(
                owner,
                repo,
                path=file_path,
                sha=branch,
                per_page=1,
            )
        ).parsed_data
        return tuple(commits)
    except GitHubException as exc:
        msg = f"GitHub commits request failed for {owner}/{repo}:{file_path}: {exc}"
        raise RuntimeError(msg) from exc


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

    async def _perform_request_once() -> tuple[bytes, dict[str, str]]:
        async with session.request(
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
            if response.status in RETRYABLE_STATUSES:
                raise _RetryableStatusError(detail)
            raise _NonRetryableStatusError(detail)

    retryer = AsyncRetrying(
        stop=stop_after_attempt(options.attempts),
        wait=wait_exponential(multiplier=options.backoff, exp_base=2),
        retry=retry_if_exception_type((
            _RetryableStatusError,
            aiohttp.ClientError,
            TimeoutError,
        )),
        reraise=True,
    )

    try:
        async for attempt in retryer:
            with attempt:
                return await _perform_request_once()
    except (
        _NonRetryableStatusError,
        _RetryableStatusError,
        aiohttp.ClientError,
        TimeoutError,
    ) as exc:
        msg = f"Request to {url} failed after {options.attempts} attempts: {exc}"
        raise RuntimeError(msg) from exc
    msg = f"Request to {url} failed after {options.attempts} attempts"
    raise RuntimeError(msg)


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


async def fetch_github_api_paginated(
    session: aiohttp.ClientSession,
    api_path: str,
    *,
    config: UpdateConfig | None = None,
    per_page: int = 100,
    max_pages: int = 10,
    item_limit: int | None = None,
    **params: str,
) -> JSONList:
    """Fetch paginated list data from a GitHub API path.

    Stops early when a page returns fewer than ``per_page`` items.
    """
    if per_page < 1:
        msg = "per_page must be >= 1"
        raise ValueError(msg)
    if max_pages < 1:
        msg = "max_pages must be >= 1"
        raise ValueError(msg)
    if item_limit is not None and item_limit < 1:
        msg = "item_limit must be >= 1 when set"
        raise ValueError(msg)

    config = resolve_active_config(config)
    all_items: JSONList = []

    for page in range(1, max_pages + 1):
        page_items = _expect_json_list(
            await fetch_github_api(
                session,
                api_path,
                config=config,
                **params,
                per_page=str(per_page),
                page=str(page),
            ),
            context=f"GitHub API list {api_path} page {page}",
        )
        all_items.extend(page_items)

        if item_limit is not None and len(all_items) >= item_limit:
            return all_items[:item_limit]
        if len(page_items) < per_page:
            return all_items

    warnings.warn(
        (
            f"GitHub API list {api_path} may be truncated after {max_pages} "
            "page(s); increase max_pages or add an item_limit"
        ),
        stacklevel=2,
    )
    return all_items


async def fetch_github_default_branch(
    _session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    *,
    config: UpdateConfig | None = None,
) -> str:
    """Fetch the default branch name for a GitHub repository."""
    active_config = resolve_active_config(config)
    repo_model = await _fetch_github_repo(owner, repo, config=active_config)
    default_branch = getattr(repo_model, "default_branch", None)
    if not isinstance(default_branch, str):
        msg = f"Expected default_branch in GitHub repo metadata for {owner}/{repo}"
        raise TypeError(msg)
    return default_branch


async def fetch_github_latest_commit(
    _session: aiohttp.ClientSession,
    repository: tuple[str, str],
    *,
    file_path: str,
    branch: str,
    config: UpdateConfig | None = None,
) -> str:
    """Fetch the latest commit SHA that touched ``file_path`` on ``branch``."""
    owner, repo = repository
    active_config = resolve_active_config(config)
    commits = await _fetch_github_commits(
        owner,
        repo,
        branch=branch,
        config=active_config,
        file_path=file_path,
    )
    if not commits:
        msg = f"No commits found for {owner}/{repo}:{file_path}"
        raise RuntimeError(msg)
    sha = getattr(commits[0], "sha", None)
    if not isinstance(sha, str):
        msg = f"Expected commit sha in first commit for {owner}/{repo}:{file_path}"
        raise TypeError(msg)
    return sha


__all__ = [
    "JSONDict",
    "JSONList",
    "JSONScalar",
    "JSONValue",
    "_request",
    "fetch_github_api",
    "fetch_github_api_paginated",
    "fetch_github_default_branch",
    "fetch_github_latest_commit",
    "fetch_json",
    "fetch_url",
    "github_api_url",
    "github_raw_url",
]
