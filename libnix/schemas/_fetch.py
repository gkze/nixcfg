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
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = "NixOS/nix"
SCHEMA_PATH = "doc/manual/source/protocols/json/schema"
API_BASE = f"https://api.github.com/repos/{REPO}"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}"

SCHEMAS_DIR = Path(__file__).resolve().parent
VERSION_FILE = SCHEMAS_DIR / "_version.txt"


def _github_get(url: str) -> bytes:
    """Make an authenticated or anonymous GET request to GitHub."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github.v3+json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


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
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _sha256(data: bytes) -> str:
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
    print(f"Fetching default branch HEAD for {REPO}...")
    commit_sha, branch = _get_default_branch_head()
    print(f"  branch: {branch}")
    print(f"  commit: {commit_sha}")

    print(f"Listing schema files at {SCHEMA_PATH}...")
    files = _list_schema_files(commit_sha)
    print(f"  found {len(files)} YAML schemas")

    for f in files:
        print(f"  downloading {f['name']}...")
        content = _download_schema(f["download_url"])
        (SCHEMAS_DIR / f["name"]).write_bytes(content)

    _write_version(commit_sha, branch, files)
    print(f"Done. Wrote {len(files)} schemas + _version.txt")


def check() -> bool:
    """Verify vendored schemas match the pinned commit. Returns True if OK."""
    version = _parse_version()
    if "commit" not in version:
        print("ERROR: _version.txt missing or has no commit field", file=sys.stderr)
        return False

    commit_sha = version["commit"]
    print(f"Checking vendored schemas against commit {commit_sha}...")

    files = _list_schema_files(commit_sha)
    ok = True

    # Check all remote files are present locally with matching content
    for f in files:
        local_path = SCHEMAS_DIR / f["name"]
        if not local_path.exists():
            print(f"  MISSING: {f['name']}", file=sys.stderr)
            ok = False
            continue

        remote_content = _download_schema(f["download_url"])
        local_content = local_path.read_bytes()
        if remote_content != local_content:
            print(f"  MISMATCH: {f['name']}", file=sys.stderr)
            ok = False
        else:
            print(f"  OK: {f['name']}")

    # Check for stale local files not in remote
    remote_names = {f["name"] for f in files}
    for local_file in SCHEMAS_DIR.glob("*.yaml"):
        if local_file.name not in remote_names:
            print(f"  STALE: {local_file.name} (not in remote)", file=sys.stderr)
            ok = False

    if ok:
        print("All schemas match pinned commit.")
    else:
        print("Schema check FAILED.", file=sys.stderr)

    return ok


def main() -> None:
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    else:
        fetch()


if __name__ == "__main__":
    main()
