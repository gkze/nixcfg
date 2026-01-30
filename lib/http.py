"""HTTP and API utilities for update tools."""

from __future__ import annotations

import asyncio
import functools
import json
import netrc
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    import aiohttp

from lib.config import get_config
from lib.exceptions import NetworkError, RateLimitError


DEFAULT_USER_AGENT = "update.py"


@functools.cache
def get_github_token() -> str | None:
    """Get GitHub token from GITHUB_TOKEN env var or ~/.netrc (cached)."""
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
        except (netrc.NetrcParseError, OSError):
            pass
    return None


def check_github_rate_limit(headers: Mapping[str, str], url: str) -> None:
    """Check GitHub API rate limit headers and raise if exceeded."""
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
        reset_time = datetime.fromtimestamp(int(reset), tz=timezone.utc).isoformat()
    raise RateLimitError(
        f"GitHub API rate limit exceeded for {url}. Resets at {reset_time}.",
        url=url,
        reset_time=reset_time,
    )


async def request(
    session: "aiohttp.ClientSession",
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float | None = None,
    method: str = "GET",
    retries: int | None = None,
    backoff: float | None = None,
) -> tuple[bytes, Mapping[str, str]]:
    """Make an HTTP request with retries and GitHub auth support.

    Returns (response_body, headers).
    Raises NetworkError on failure.
    """
    import aiohttp as aio

    config = get_config()
    retries = retries if retries is not None else config.limits.max_retries
    backoff = backoff if backoff is not None else config.limits.retry_backoff
    timeout = timeout if timeout is not None else config.timeouts.http_request

    headers: dict[str, str] = {}
    if user_agent:
        headers["User-Agent"] = user_agent

    # Add GitHub API authentication if available
    github_token = get_github_token()
    if url.startswith("https://api.github.com/") and github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    timeout_config = aio.ClientTimeout(total=timeout)

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            async with session.request(
                method,
                url,
                headers=headers,
                timeout=timeout_config,
                allow_redirects=True,
            ) as response:
                payload = await response.read()
                if response.status >= 400:
                    error_body = payload.decode(errors="ignore").strip()
                    detail = f"HTTP {response.status} {response.reason}"
                    if error_body:
                        detail = f"{detail}\n{error_body}"
                    # Don't retry client errors (4xx), only server errors (5xx)
                    if response.status < 500:
                        raise NetworkError(
                            f"Request to {url} failed: {detail}",
                            url=url,
                            status_code=response.status,
                        )
                    last_error = NetworkError(
                        f"Request to {url} failed: {detail}",
                        url=url,
                        status_code=response.status,
                    )
                else:
                    return payload, response.headers
        except (aio.ClientError, asyncio.TimeoutError) as e:
            last_error = e

        # Exponential backoff before retry
        if attempt < retries - 1:
            await asyncio.sleep(backoff * (2**attempt))

    raise NetworkError(
        f"Request to {url} failed after {retries} attempts: {last_error}",
        url=url,
    )


async def fetch_url(
    session: "aiohttp.ClientSession",
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float | None = None,
) -> bytes:
    """Fetch raw bytes from a URL."""
    payload, _headers = await request(
        session, url, user_agent=user_agent, timeout=timeout
    )
    return payload


async def fetch_json(
    session: "aiohttp.ClientSession",
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float | None = None,
) -> dict:
    """Fetch and parse JSON from a URL."""
    if url.startswith("https://api.github.com/"):
        payload, headers = await request(
            session, url, user_agent=user_agent, timeout=timeout
        )
        check_github_rate_limit(headers, url)
    else:
        payload = await fetch_url(session, url, user_agent=user_agent, timeout=timeout)
    try:
        return json.loads(payload.decode())
    except json.JSONDecodeError as err:
        raise NetworkError(
            f"Invalid JSON response from {url}: {err}",
            url=url,
        ) from err


# =============================================================================
# GitHub API Helpers
# =============================================================================


def github_raw_url(owner: str, repo: str, rev: str, path: str) -> str:
    """Build URL for raw file content from GitHub."""
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{rev}/{path}"


def github_api_url(path: str) -> str:
    """Build GitHub API URL from path (e.g., 'repos/owner/repo')."""
    return f"https://api.github.com/{path}"


async def fetch_github_api(
    session: "aiohttp.ClientSession", path: str, **params: str
) -> dict:
    """Fetch from GitHub API with standard options."""
    url = github_api_url(path)
    if params:
        url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    return await fetch_json(
        session,
        url,
        user_agent=DEFAULT_USER_AGENT,
        timeout=get_config().timeouts.http_request,
    )


async def fetch_github_default_branch(
    session: "aiohttp.ClientSession", owner: str, repo: str
) -> str:
    """Get the default branch for a GitHub repository."""
    data = await fetch_github_api(session, f"repos/{owner}/{repo}")
    return data["default_branch"]


async def fetch_github_latest_commit(
    session: "aiohttp.ClientSession",
    owner: str,
    repo: str,
    file_path: str,
    branch: str,
) -> str:
    """Get the latest commit SHA that modified a specific file."""
    url = github_api_url(f"repos/{owner}/{repo}/commits")
    url = f"{url}?path={urllib.parse.quote(file_path)}&sha={branch}&per_page=1"
    data = await fetch_json(
        session,
        url,
        user_agent=DEFAULT_USER_AGENT,
        timeout=get_config().timeouts.http_request,
    )
    if not data:
        raise NetworkError(
            f"No commits found for {owner}/{repo}:{file_path}",
            url=url,
        )
    return data[0]["sha"]


async def fetch_github_latest_version_ref(
    session: "aiohttp.ClientSession",
    owner: str,
    repo: str,
    prefix: str,
) -> str | None:
    """Fetch the latest version ref from GitHub matching the given prefix.

    Strategy: try releases API first (non-draft, non-prerelease, sorted by
    date), then fall back to the tags API if no releases exist or none match.
    """
    # 1. Releases API (paginated, newest first)
    try:
        releases = await fetch_github_api(
            session, f"repos/{owner}/{repo}/releases", per_page="20"
        )
        for release in releases:
            if release.get("draft") or release.get("prerelease"):
                continue
            tag = release.get("tag_name", "")
            if tag.startswith(prefix):
                return tag
    except (NetworkError, KeyError):
        pass  # No releases endpoint or API error

    # 2. Tags API fallback (repos that use tags without GitHub Releases)
    try:
        tags = await fetch_github_api(
            session, f"repos/{owner}/{repo}/tags", per_page="30"
        )
        for tag_info in tags:
            if (tag := tag_info.get("name", "")).startswith(prefix):
                return tag
    except (NetworkError, KeyError):
        pass

    return None
