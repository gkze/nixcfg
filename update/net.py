import asyncio
import json
import netrc
import os
import random
import urllib.parse
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, cast

import aiohttp

from update.config import UpdateConfig, _resolve_active_config

type JSONDict = dict[str, Any]
type JSONList = list[Any]
type JSONValue = JSONDict | JSONList | str | int | float | bool | None


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
    raise RuntimeError(msg)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return max(0.0, float(value))
    try:
        parsed = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delay = (parsed - datetime.now(UTC)).total_seconds()
    return max(0.0, delay)


def _apply_retry_jitter(delay: float, *, jitter_ratio: float) -> float:
    if jitter_ratio <= 0:
        return delay
    jitter_ratio = min(jitter_ratio, 1.0)
    low = max(0.0, 1.0 - jitter_ratio)
    high = 1.0 + jitter_ratio
    return max(0.0, delay * random.uniform(low, high))  # noqa: S311 — jitter, not crypto


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


async def _request(
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float | None = None,
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
    timeout_config = aiohttp.ClientTimeout(total=timeout or config.default_timeout)

    last_error: Exception | None = None
    for attempt in range(retries):
        retry_after_delay: float | None = None
        try:
            async with session.request(
                method,
                url,
                headers=headers,
                timeout=timeout_config,
                allow_redirects=True,
            ) as response:
                payload = await response.read()
                if response.status < 400:
                    return payload, dict(response.headers)
                detail = _format_http_error(response, payload)
                error = RuntimeError(f"Request to {url} failed: {detail}")
                if response.status == 429:
                    last_error = error
                    retry_after_delay = _parse_retry_after(
                        response.headers.get("Retry-After")
                    )
                elif response.status < 500:
                    raise error
                else:
                    last_error = error
        except (TimeoutError, aiohttp.ClientError) as exc:
            last_error = exc

        if attempt < retries - 1:
            delay = retry_after_delay
            if delay is None:
                delay = backoff * (2**attempt)
                delay = _apply_retry_jitter(
                    delay, jitter_ratio=config.retry_jitter_ratio
                )
            await asyncio.sleep(delay)

    msg = f"Request to {url} failed after {retries} attempts: {last_error}"
    raise RuntimeError(msg)


async def fetch_url(
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float | None = None,
    config: UpdateConfig | None = None,
) -> bytes:
    config = _resolve_active_config(config)
    payload, _ = await _request(
        session, url, user_agent=user_agent, timeout=timeout, config=config
    )
    return payload


async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float | None = None,
    config: UpdateConfig | None = None,
) -> JSONValue:
    config = _resolve_active_config(config)
    if url.startswith("https://api.github.com/"):
        payload, headers = await _request(
            session, url, user_agent=user_agent, timeout=timeout, config=config
        )
        _check_github_rate_limit(headers, url)
    else:
        payload = await fetch_url(
            session, url, user_agent=user_agent, timeout=timeout, config=config
        )
    try:
        return json.loads(payload.decode())
    except json.JSONDecodeError as err:
        msg = f"Invalid JSON response from {url}: {err}"
        raise RuntimeError(msg) from err


def github_raw_url(owner: str, repo: str, rev: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{rev}/{path}"


def github_api_url(path: str) -> str:
    return f"https://api.github.com/{path}"


async def fetch_github_api(
    session: aiohttp.ClientSession,
    api_path: str,
    *,
    config: UpdateConfig | None = None,
    **params: str,
) -> JSONValue:
    config = _resolve_active_config(config)
    url = github_api_url(api_path)
    if params:
        url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    return await fetch_json(
        session,
        url,
        user_agent=config.default_user_agent,
        timeout=config.default_timeout,
        config=config,
    )


async def fetch_github_default_branch(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    *,
    config: UpdateConfig | None = None,
) -> str:
    data = cast(
        "JSONDict",
        await fetch_github_api(session, f"repos/{owner}/{repo}", config=config),
    )
    return data["default_branch"]


async def fetch_github_latest_commit(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    file_path: str,
    branch: str,
    *,
    config: UpdateConfig | None = None,
) -> str:
    data = cast(
        "list[JSONDict]",
        await fetch_github_api(
            session,
            f"repos/{owner}/{repo}/commits",
            path=urllib.parse.quote(file_path),
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
