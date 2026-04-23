"""Fetch Nix JSON schemas from the NixOS/nix repository.

Downloads all YAML schema files from a pinned commit on the default branch
and writes them to the schemas/ directory alongside a _version.json manifest.

Usage:
    # Fetch/refresh schemas from latest commit on default branch:
    python -m lib.nix.schemas._fetch

    # Verify vendored schemas match the pinned commit (for CI):
    python -m lib.nix.schemas._fetch --check
"""

from __future__ import annotations

import functools
import hashlib
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib import http_utils

REPO = "NixOS/nix"
SCHEMA_PATH = "doc/manual/source/protocols/json/schema"
API_BASE = f"https://api.github.com/repos/{REPO}"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}"

SCHEMAS_DIR = Path(__file__).resolve().parent
VERSION_FILE = SCHEMAS_DIR / "_version.json"
_HTTP_TIMEOUT_SECONDS = 5
_HTTP_MAX_ATTEMPTS = 3
_HTTP_BACKOFF_BASE_SECONDS = 0.5
_HTTP_BACKOFF_MAX_SECONDS = 5.0

type ProgressReporter = Callable[[str], None]


class SchemaVersionManifest(BaseModel):
    """Metadata describing the vendored Nix schema snapshot."""

    model_config = ConfigDict(extra="forbid")

    commit: str
    branch: str
    fetched: datetime
    repo: str
    path: str
    checksums: dict[str, str] = Field(default_factory=dict)


def _resolve_github_token() -> str | None:
    """Return a GitHub token from env vars or the gh CLI credential store."""
    return http_utils.resolve_github_token(allow_keyring=True)


_unwrap_gh_token = http_utils.unwrap_go_keyring_token


@functools.lru_cache(maxsize=1)
def _get_github_token() -> str | None:
    """Lazy-resolve and cache the GitHub token for this process."""
    return _resolve_github_token()


def _github_get(url: str) -> bytes:
    """Make an authenticated (preferred) or anonymous GET request to GitHub."""
    return _https_get(
        url,
        headers=http_utils.build_github_headers(
            url,
            accept="application/vnd.github.v3+json",
            auth_scheme="token",
            token=_get_github_token(),
        ),
    )


def _get_default_branch_head() -> tuple[str, str]:
    """Return (commit_sha, branch_name) for the HEAD of the default branch."""
    data = json.loads(_github_get(f"{API_BASE}"))
    branch = data["default_branch"]
    branch_data = json.loads(_github_get(f"{API_BASE}/branches/{branch}"))
    sha = branch_data["commit"]["sha"]
    return sha, branch


def _list_schema_files(commit_sha: str) -> list[dict[str, str]]:
    """List all .yaml schema files at the pinned commit."""
    url = f"{API_BASE}/contents/{SCHEMA_PATH}?ref={commit_sha}"
    entries = json.loads(_github_get(url))
    return [
        {
            "name": e["name"],
            "download_url": f"{RAW_BASE}/{commit_sha}/{SCHEMA_PATH}/{e['name']}",
        }
        for e in entries
        if e["name"].endswith(".yaml")
    ]


def _download_schema(url: str) -> bytes:
    """Download a single schema file."""
    return _https_get(url)


def _https_get(url: str, headers: dict[str, str] | None = None) -> bytes:
    """Fetch a URL over HTTPS only and return response bytes."""
    if _HTTP_MAX_ATTEMPTS < 1:
        msg = f"Failed fetching {url}"
        raise RuntimeError(msg)

    request_headers = {"User-Agent": "nixcfg-schema-fetch"}
    if headers:
        request_headers.update(headers)

    try:
        payload, _response_headers = http_utils.fetch_url_bytes(
            url,
            headers=request_headers,
            attempts=_HTTP_MAX_ATTEMPTS,
            backoff=_HTTP_BACKOFF_BASE_SECONDS,
            max_backoff=_HTTP_BACKOFF_MAX_SECONDS,
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except ValueError as exc:
        message = str(exc)
        if message.startswith("Only absolute HTTPS URLs"):
            raise ValueError(message) from exc
        if message.startswith("Could not parse host from URL"):
            raise ValueError(message) from exc
        raise
    except http_utils.RequestError as exc:
        if exc.kind == "timeout":
            msg = f"Timed out fetching {url} after {exc.attempts} attempt(s)"
            raise RuntimeError(msg) from exc
        if exc.kind == "network":
            msg = f"Network error fetching {url} after {exc.attempts} attempt(s): {exc.detail}"
            raise RuntimeError(msg) from exc
        if exc.kind == "status":
            msg = f"HTTP {exc.status} fetching {url}"
            if exc.attempts > 1:
                msg = f"{msg} after {exc.attempts} attempt(s)"
            raise RuntimeError(msg) from exc
        msg = f"Failed fetching {url}"
        raise RuntimeError(msg) from exc
    else:
        return payload


def _emit_progress(progress: ProgressReporter | None, message: str) -> None:
    """Invoke the optional fetch progress reporter."""
    if progress is None:
        return
    progress(message)


def _write_version(commit_sha: str, branch: str, files: list[dict[str, str]]) -> None:
    """Write _version.json with pinned commit info and checksums."""
    checksums: dict[str, str] = {}
    for f in sorted(files, key=lambda x: x["name"]):
        path = SCHEMAS_DIR / f["name"]
        if path.exists():
            checksums[f["name"]] = hashlib.sha256(path.read_bytes()).hexdigest()

    manifest = SchemaVersionManifest(
        commit=commit_sha,
        branch=branch,
        fetched=datetime.now(UTC),
        repo=REPO,
        path=SCHEMA_PATH,
        checksums=checksums,
    )
    VERSION_FILE.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )


def _parse_version() -> SchemaVersionManifest | None:
    """Parse _version.json into a validated manifest model."""
    if not VERSION_FILE.exists():
        return None

    try:
        payload = json.loads(VERSION_FILE.read_text())
        return SchemaVersionManifest.model_validate(payload)
    except (json.JSONDecodeError, ValidationError):
        return None


def fetch(*, progress: ProgressReporter | None = None) -> None:
    """Fetch schemas from the latest commit on the default branch."""
    _emit_progress(progress, f"Resolving default branch head for {REPO}.")
    commit_sha, branch = _get_default_branch_head()

    _emit_progress(progress, f"Listing schema files for {branch}@{commit_sha}.")
    files = _list_schema_files(commit_sha)
    total = len(files)
    _emit_progress(
        progress, f"Fetching {total} schema file(s) from {branch}@{commit_sha}."
    )

    for index, f in enumerate(files, start=1):
        _emit_progress(progress, f"Downloading {index}/{total}: {f['name']}")
        content = _download_schema(f["download_url"])
        (SCHEMAS_DIR / f["name"]).write_bytes(content)

    _emit_progress(progress, "Updating schema version manifest.")
    _write_version(commit_sha, branch, files)
    _emit_progress(progress, "Schema fetch complete.")


def check() -> bool:
    """Verify vendored schemas match the pinned commit. Returns True if OK."""
    version = _parse_version()
    if version is None:
        return False

    commit_sha = version.commit

    files = _list_schema_files(commit_sha)
    ok = True

    # Check all remote files are present locally with matching content
    for f in files:
        local_path = SCHEMAS_DIR / f["name"]
        if not local_path.exists():
            ok = False
            continue

        remote_content = _download_schema(f["download_url"])
        local_content = local_path.read_bytes()
        if remote_content != local_content:
            ok = False
        else:
            pass

    # Check for stale local files not in remote
    remote_names = {f["name"] for f in files}
    for local_file in SCHEMAS_DIR.glob("*.yaml"):
        if local_file.name not in remote_names:
            ok = False

    if ok:
        pass
    else:
        pass

    return ok


def main() -> None:
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    else:
        fetch()


if __name__ == "__main__":  # pragma: no cover
    main()
