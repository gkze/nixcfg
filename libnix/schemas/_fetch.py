"""Fetch Nix JSON schemas from the NixOS/nix repository.

Downloads all YAML schema files from a pinned commit on the default branch
and writes them to the schemas/ directory alongside a _version.txt manifest.

Usage:
    # Fetch/refresh schemas from latest commit on default branch:
    python -m libnix.schemas._fetch

    # Verify vendored schemas match the pinned commit (for CI):
    python -m libnix.schemas._fetch --check
"""

from __future__ import annotations

import hashlib
import http.client
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

REPO = "NixOS/nix"
SCHEMA_PATH = "doc/manual/source/protocols/json/schema"
API_BASE = f"https://api.github.com/repos/{REPO}"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}"

SCHEMAS_DIR = Path(__file__).resolve().parent
VERSION_FILE = SCHEMAS_DIR / "_version.txt"
_HTTP_BAD_REQUEST = 400


def _github_get(url: str) -> bytes:
    """Make an authenticated or anonymous GET request to GitHub."""
    return _https_get(url, headers={"Accept": "application/vnd.github.v3+json"})


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

    conn = http.client.HTTPSConnection(parsed.netloc, timeout=30)
    try:
        conn.request("GET", path, headers=headers or {})
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
    """Write _version.txt with pinned commit info and file checksums."""
    lines = [
        f"commit: {commit_sha}",
        f"branch: {branch}",
        f"fetched: {datetime.now(UTC).isoformat()}",
        f"repo: {REPO}",
        f"path: {SCHEMA_PATH}",
        "",
        "# File checksums (sha256):",
    ]
    for f in sorted(files, key=lambda x: x["name"]):
        path = SCHEMAS_DIR / f["name"]
        if path.exists():
            lines.append(f"{_sha256(path.read_bytes())}  {f['name']}")
    VERSION_FILE.write_text("\n".join(lines) + "\n")


def _parse_version() -> dict[str, str]:
    """Parse _version.txt into a dict of key-value pairs."""
    result: dict[str, str] = {}
    if not VERSION_FILE.exists():
        return result
    for line in VERSION_FILE.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if ": " in line and not line.startswith(" "):
            key, _, value = line.partition(": ")
            result[key.strip()] = value.strip()
    return result


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
    if "commit" not in version:
        return False

    commit_sha = version["commit"]

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
