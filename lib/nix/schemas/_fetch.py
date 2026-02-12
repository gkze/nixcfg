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

import hashlib
import http.client
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import keyring
from pydantic import BaseModel, ConfigDict, Field, ValidationError

REPO = "NixOS/nix"
SCHEMA_PATH = "doc/manual/source/protocols/json/schema"
API_BASE = f"https://api.github.com/repos/{REPO}"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}"

SCHEMAS_DIR = Path(__file__).resolve().parent
VERSION_FILE = SCHEMAS_DIR / "_version.json"
_HTTP_BAD_REQUEST = 400


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
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token

    # Read from the same keychain entry that ``gh auth token`` uses.
    try:
        raw = keyring.get_password("gh:github.com", "")
    except Exception:  # noqa: BLE001
        return None
    return _unwrap_gh_token(raw) if raw else None


_GO_KEYRING_PREFIX = "go-keyring-base64:"


def _unwrap_gh_token(raw: str) -> str | None:
    """Decode the ``go-keyring-base64:`` wrapper that gh uses in keychains."""
    import base64

    if raw.startswith(_GO_KEYRING_PREFIX):
        raw = base64.b64decode(raw[len(_GO_KEYRING_PREFIX) :]).decode()
    return raw.strip() or None


_GITHUB_TOKEN: str | None = None


def _get_github_token() -> str | None:
    """Lazy-resolve and cache the GitHub token for this process."""
    global _GITHUB_TOKEN  # noqa: PLW0603
    if _GITHUB_TOKEN is None:
        _GITHUB_TOKEN = _resolve_github_token() or ""
    return _GITHUB_TOKEN or None


def _github_get(url: str) -> bytes:
    """Make an authenticated (preferred) or anonymous GET request to GitHub."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = _get_github_token()
    if token:
        headers["Authorization"] = f"token {token}"
    return _https_get(url, headers=headers)


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
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        msg = f"Only absolute HTTPS URLs are allowed, got: {url!r}"
        raise ValueError(msg)

    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    all_headers = {"User-Agent": "nixcfg-schema-fetch"}
    if headers:
        all_headers.update(headers)

    conn = http.client.HTTPSConnection(parsed.netloc, timeout=30)
    try:
        conn.request("GET", path, headers=all_headers)
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()

    if response.status >= _HTTP_BAD_REQUEST:
        msg = f"HTTP {response.status} fetching {url}"
        raise RuntimeError(msg)
    return body


def _sha256(data: bytes) -> str:
    """Return hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def _write_version(commit_sha: str, branch: str, files: list[dict[str, str]]) -> None:
    """Write _version.json with pinned commit info and checksums."""
    checksums: dict[str, str] = {}
    for f in sorted(files, key=lambda x: x["name"]):
        path = SCHEMAS_DIR / f["name"]
        if path.exists():
            checksums[f["name"]] = _sha256(path.read_bytes())

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
    except json.JSONDecodeError, ValidationError:
        return None


def fetch() -> None:
    """Fetch schemas from the latest commit on the default branch."""
    commit_sha, branch = _get_default_branch_head()

    files = _list_schema_files(commit_sha)

    for f in files:
        content = _download_schema(f["download_url"])
        (SCHEMAS_DIR / f["name"]).write_bytes(content)

    _write_version(commit_sha, branch, files)


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


if __name__ == "__main__":
    main()
