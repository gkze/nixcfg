"""HTTP helpers for update data sources and GitHub APIs."""

import asyncio
import json
import logging
import urllib.parse
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import aiohttp
from githubkit import GitHub
from githubkit.exception import GitHubException
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
)
from tenacity.wait import wait_exponential

from lib import http_utils, json_utils
from lib.update import failures
from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.runtime import active_runtime, current_source, resource_slot

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from tenacity import RetryCallState

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | dict[str, "JSONValue"] | list["JSONValue"]
type JSONDict = dict[str, JSONValue]
type JSONList = list[JSONValue]


def _expect_json_list(payload: JSONValue, *, context: str) -> JSONList:
    try:
        return json_utils.as_json_list(payload, context=context)
    except TypeError as exc:
        msg = f"Expected JSON array from {context}, got {type(payload).__name__}"
        raise RuntimeError(msg) from exc


# Private to this module: only the aiohttp path escalates a 403 carrying a
# Retry-After header into a retry. Every other status policy lives in
# lib/http_utils.py.
_HTTP_FORBIDDEN = 403
_HTTP_REDIRECT_MIN = 300
# Version discovery already exhausts ``fetch_json`` retries. These leftovers
# are runner/CDN resolver flakes, not a missing current pin or a bad payload.
# Discovery runs in-process over aiohttp, so it matches Python resolver and
# aiohttp spellings rather than curl transfer faults.
_DISCOVERY_TRANSIENT_MARKERS = (
    *failures.DNS_RESOLUTION_MARKERS,
    *failures.PYTHON_RESOLVER_MARKERS,
    *failures.AIOHTTP_CONNECT_MARKERS,
)


def is_transient_discovery_failure(error: BaseException) -> bool:
    """Return whether latest-version fetch failed on a retry-exhausted network flake.

    Update 37546260513: prepare-darwin failed the whole run because
    ``wispr-flow`` could not resolve ``dl.wisprflow.com`` while reading
    ``RELEASES.json``. The current pin was already hashed; only discovery
    flaked.
    """
    seen: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__
    if any(
        isinstance(exc, TimeoutError | aiohttp.ClientConnectorError) for exc in seen
    ):
        return True
    return failures.contains_marker(
        " ".join(f"{type(exc).__name__} {exc}" for exc in seen),
        _DISCOVERY_TRANSIENT_MARKERS,
    )


# Bound server-directed sleeps so an untrusted response cannot stall an update forever.
_MAX_RETRY_AFTER_SECONDS = 300.0
logger = logging.getLogger(__name__)


def _get_github_token() -> str | None:
    return http_utils.resolve_github_token(allow_netrc=True, logger=logger)


def _github_rate_limit_detail(headers: Mapping[str, str], url: str) -> str | None:
    """Describe an exhausted GitHub API quota on an error response."""
    if not url.startswith("https://api.github.com/"):
        return None
    if headers.get("X-RateLimit-Remaining", "").strip() != "0":
        return None
    reset = headers.get("X-RateLimit-Reset")
    reset_time = "unknown"
    if reset and reset.isdigit():
        reset_time = datetime.fromtimestamp(int(reset), tz=UTC).isoformat()
    return f"GitHub API rate limit exceeded for {url}. Resets at {reset_time}."


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
        return (await client.rest().repos.async_get(owner, repo)).parsed_data
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
            await client.rest().repos.async_list_commits(
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


def _parse_retry_after(value: str | None, *, now: datetime) -> float | None:
    """Return a bounded retry delay for an RFC 9110 ``Retry-After`` value."""
    if value is None:
        return None
    candidate = value.strip()
    if candidate.isdigit():
        max_seconds = int(_MAX_RETRY_AFTER_SECONDS)
        normalized = candidate.lstrip("0") or "0"
        if len(normalized) > len(str(max_seconds)):
            return _MAX_RETRY_AFTER_SECONDS
        return float(min(int(normalized), max_seconds))
    try:
        retry_at = parsedate_to_datetime(candidate)
    except TypeError, ValueError, OverflowError:
        return None
    if retry_at.tzinfo is None:
        return None
    delay = (retry_at - now).total_seconds()
    if delay <= 0:
        return None
    return min(delay, _MAX_RETRY_AFTER_SECONDS)


def _retry_wait(backoff: float) -> Callable[[RetryCallState], float]:
    """Prefer a server retry delay and otherwise use exponential backoff."""
    fallback = wait_exponential(multiplier=backoff, exp_base=2)

    def _wait(retry_state: RetryCallState) -> float:
        outcome = retry_state.outcome
        if outcome is not None:
            exception = outcome.exception()
            if (
                isinstance(exception, http_utils.RetryableStatusError)
                and exception.retry_after is not None
            ):
                return exception.retry_after
        return fallback(retry_state)

    return _wait


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    """One completed HTTP exchange: status, final URL, headers, and body."""

    status: int
    url: str
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class _RequestOptions:
    user_agent: str | None
    request_timeout: float
    attempts: int
    backoff: float


def _resolve_request_options(
    *,
    user_agent: str | None,
    request_timeout: float | None,
    retries: int | None,
    backoff: float | None,
    config: UpdateConfig | None,
) -> _RequestOptions:
    """Fill unset request knobs from the active update config."""
    resolved_config = resolve_active_config(config)
    return _RequestOptions(
        user_agent=user_agent,
        request_timeout=request_timeout or resolved_config.default_timeout,
        attempts=max(
            1, retries if retries is not None else resolved_config.default_retries
        ),
        backoff=max(
            0.0,
            backoff if backoff is not None else resolved_config.default_retry_backoff,
        ),
    )


_HTTPS_ONLY = frozenset({"https"})
_HTTP_MOVED_PERMANENTLY = 301
_HTTP_FOUND = 302
_HTTP_SEE_OTHER = 303
_REDIRECT_STATUSES = frozenset({
    _HTTP_MOVED_PERMANENTLY,
    _HTTP_FOUND,
    _HTTP_SEE_OTHER,
    307,
    308,
})
_MAX_REDIRECTS = 10
_SENSITIVE_REDIRECT_HEADERS = frozenset({
    "authorization",
    "cookie",
    "proxy-authorization",
})


def _validated_redirect_location(url: str, location: str) -> str:
    """Join and validate a redirect target before it can be requested."""
    target = urllib.parse.urljoin(url, location)
    http_utils.validate_request_target(target, allowed_schemes=_HTTPS_ONLY, attempts=1)
    return target


def _validate_response_chain(
    response: aiohttp.ClientResponse, *, attempts: int
) -> None:
    """Validate every URL exposed by a response, including redirect history."""
    for hop in (*getattr(response, "history", ()), response):
        http_utils.validate_request_target(
            str(hop.url), allowed_schemes=_HTTPS_ONLY, attempts=attempts
        )


def _same_origin(first: str, second: str) -> bool:
    """Compare URL origins using their default ports when omitted."""
    first_parts = urllib.parse.urlsplit(first)
    second_parts = urllib.parse.urlsplit(second)
    first_port = first_parts.port or (443 if first_parts.scheme == "https" else 80)
    second_port = second_parts.port or (443 if second_parts.scheme == "https" else 80)
    return (
        first_parts.scheme == second_parts.scheme
        and first_parts.hostname == second_parts.hostname
        and first_port == second_port
    )


def _redirect_headers(
    headers: Mapping[str, str], *, current_url: str, next_url: str
) -> dict[str, str]:
    """Apply aiohttp's cross-origin credential stripping policy."""
    redirected = dict(headers)
    if not _same_origin(current_url, next_url):
        for name in tuple(redirected):
            if name.lower() in _SENSITIVE_REDIRECT_HEADERS:
                del redirected[name]
    return redirected


def _response_result(
    response: aiohttp.ClientResponse,
    payload: bytes,
    *,
    request_url: str,
    response_url: str,
) -> HTTPResponse:
    """Return a successful response or classify its status for the retryer."""
    if response.status < http_utils.HTTP_BAD_REQUEST:
        return HTTPResponse(
            status=response.status,
            url=response_url,
            headers=response.headers,
            body=payload,
        )
    detail = http_utils.format_http_error(
        response.status,
        # aiohttp types reason as str | None; str() keeps the existing
        # diagnostic text for the unset case.
        str(response.reason),
        payload,
    )
    retry_after = _parse_retry_after(
        response.headers.get("Retry-After"),
        now=datetime.now(tz=UTC),
    )
    rate_limit = _github_rate_limit_detail(response.headers, request_url)
    if rate_limit is not None and retry_after is None:
        # An exhausted primary quota only recovers at its reset time.
        raise http_utils.NonRetryableStatusError(
            response.status, f"{detail}; {rate_limit}"
        )
    if http_utils.is_retryable_status(response.status) or (
        response.status == _HTTP_FORBIDDEN and retry_after is not None
    ):
        raise http_utils.RetryableStatusError(
            response.status,
            detail,
            retry_after=retry_after,
        )
    raise http_utils.NonRetryableStatusError(response.status, detail)


async def request(
    session: aiohttp.ClientSession,
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    json_body: object = None,
    allow_redirects: bool = True,
    user_agent: str | None = None,
    request_timeout: float | None = None,
    retries: int | None = None,
    backoff: float | None = None,
    config: UpdateConfig | None = None,
) -> HTTPResponse:
    """Send one HTTPS request with retries, Retry-After, and download admission.

    Responses below 400 are returned, including unfollowed 3xx redirects when
    ``allow_redirects`` is false. Redirects must stay on HTTPS.
    """
    options = _resolve_request_options(
        user_agent=user_agent,
        request_timeout=request_timeout,
        retries=retries,
        backoff=backoff,
        config=config,
    )
    http_utils.validate_request_target(
        url,
        allowed_schemes=_HTTPS_ONLY,
        attempts=options.attempts,
    )
    request_headers = {
        **_build_request_headers(url, options.user_agent),
        **(headers or {}),
    }
    timeout_config = aiohttp.ClientTimeout(
        total=options.request_timeout,
    )

    async def _perform_request_once() -> HTTPResponse:
        current_url = url
        current_method = method.upper()
        current_headers = dict(request_headers)
        current_json = json_body
        redirect_count = 0
        history: list[aiohttp.ClientResponse] = []

        while True:
            async with session.request(
                current_method,
                current_url,
                headers=current_headers,
                json=current_json,
                timeout=timeout_config,
                allow_redirects=False,
            ) as response:
                _validate_response_chain(response, attempts=options.attempts)
                location = response.headers.get("Location")
                follow_location = location or response.headers.get("URI")
                if (
                    allow_redirects
                    and response.status in _REDIRECT_STATUSES
                    and follow_location
                ):
                    next_url = _validated_redirect_location(
                        current_url, follow_location
                    )
                    redirect_count += 1
                    if redirect_count >= _MAX_REDIRECTS:
                        raise aiohttp.TooManyRedirects(
                            response.request_info,
                            tuple(history),
                        )
                    next_method, next_json = current_method, current_json
                    if (
                        response.status == _HTTP_SEE_OTHER and current_method != "HEAD"
                    ) or (
                        response.status in {_HTTP_MOVED_PERMANENTLY, _HTTP_FOUND}
                        and current_method == "POST"
                    ):
                        next_method = "GET"
                        next_json = None
                        current_headers = {
                            name: value
                            for name, value in current_headers.items()
                            if name.lower() != "content-length"
                        }
                    next_headers = _redirect_headers(
                        current_headers,
                        current_url=current_url,
                        next_url=next_url,
                    )
                    history.append(response)
                    current_url = next_url
                    current_method = next_method
                    current_headers = next_headers
                    current_json = next_json
                    continue

                return _response_result(
                    response,
                    await response.read(),
                    request_url=url,
                    response_url=str(response.url),
                )

    retryer = AsyncRetrying(
        stop=stop_after_attempt(options.attempts),
        wait=_retry_wait(options.backoff),
        retry=retry_if_exception_type((
            http_utils.RetryableStatusError,
            aiohttp.ClientError,
            TimeoutError,
        )),
        reraise=True,
    )

    try:
        async for attempt in retryer:
            with attempt:
                runtime = active_runtime()
                config = (
                    runtime.config
                    if runtime is not None
                    else resolve_active_config(None)
                )
                async with resource_slot(
                    "download", source=current_source(), config=config
                ) as timing:
                    response = await asyncio.wait_for(
                        _perform_request_once(), timeout=options.request_timeout
                    )
                    timing.input_bytes += len(response.body)
                    return response
    except (
        http_utils.NonRetryableStatusError,
        http_utils.RetryableStatusError,
        aiohttp.ClientError,
        TimeoutError,
    ) as exc:
        msg = f"Request to {url} failed after {options.attempts} attempts: {exc}"
        raise RuntimeError(msg) from exc
    msg = f"Request to {url} failed after {options.attempts} attempts"
    raise RuntimeError(msg)


async def fetch_redirect_location(
    session: aiohttp.ClientSession,
    url: str,
    *,
    method: str = "HEAD",
    config: UpdateConfig | None = None,
) -> str:
    """Return the absolute ``Location`` of the redirect ``url`` answers with."""
    response = await request(
        session, url, method=method, allow_redirects=False, config=config
    )
    location = response.headers.get("Location")
    if not _HTTP_REDIRECT_MIN <= response.status < http_utils.HTTP_BAD_REQUEST:
        msg = f"Expected a redirect from {url}, got HTTP {response.status}"
        raise RuntimeError(msg)
    if not location:
        msg = f"Redirect from {url} did not include Location"
        raise RuntimeError(msg)
    return _validated_redirect_location(url, location)


async def fetch_url(
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    request_timeout: float | None = None,
    config: UpdateConfig | None = None,
) -> bytes:
    """Fetch raw bytes from ``url`` with retry and timeout config."""
    response = await request(
        session,
        url,
        user_agent=user_agent,
        request_timeout=request_timeout,
        config=config,
    )
    return response.body


async def fetch_headers(
    session: aiohttp.ClientSession,
    url: str,
    *,
    request_timeout: float | None = None,
    config: UpdateConfig | None = None,
) -> Mapping[str, str]:
    """Send a HEAD request and return the case-insensitive response headers."""
    response = await request(
        session,
        url,
        method="HEAD",
        request_timeout=request_timeout,
        config=config,
    )
    return response.headers


def decode_json(payload: bytes, *, context: str) -> JSONValue:
    """Decode a JSON document, naming ``context`` on failure."""
    try:
        return json.loads(payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        msg = f"Invalid JSON response from {context}: {err}"
        raise RuntimeError(msg) from err


async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    request_timeout: float | None = None,
    config: UpdateConfig | None = None,
) -> JSONValue:
    """Fetch and decode JSON data from ``url``."""
    payload = await fetch_url(
        session,
        url,
        user_agent=user_agent,
        request_timeout=request_timeout,
        config=config,
    )
    return decode_json(payload, context=url)


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
    "HTTPResponse",
    "JSONDict",
    "JSONList",
    "JSONScalar",
    "JSONValue",
    "decode_json",
    "fetch_github_api",
    "fetch_github_api_paginated",
    "fetch_github_default_branch",
    "fetch_github_latest_commit",
    "fetch_json",
    "fetch_redirect_location",
    "fetch_url",
    "github_api_url",
    "github_raw_url",
    "is_transient_discovery_failure",
    "request",
]
