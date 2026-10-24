"""Fetch Nix JSON schemas from the NixOS/nix repository.

Downloads all YAML schema files from a pinned commit on the default branch
and writes them to the schemas/ directory alongside a _version.json manifest.

Usage:
    # Fetch/refresh schemas from latest commit on default branch:
    python -m lib.nix.schemas._fetch

    # Verify vendored schemas against _version.json checksums (offline, for CI):
    python -m lib.nix.schemas._fetch --check
"""


import functools
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib import codegen_utils, http_utils
from lib.update import io as update_io

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

ProgressReporter = codegen_utils.ProgressReporter
_emit_progress = codegen_utils.emit_progress


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_version(
    commit_sha: str,
    branch: str,
    files: list[dict[str, str]],
    *,
    schemas_dir: Path,
    version_file: Path,
) -> None:
    """Stage _version.json with pinned commit info and staged checksums."""
    checksums: dict[str, str] = {}
    for f in sorted(files, key=lambda x: x["name"]):
        path = schemas_dir / f["name"]
        if path.exists():
            checksums[f["name"]] = _sha256(path)

    manifest = SchemaVersionManifest(
        commit=commit_sha,
        branch=branch,
        fetched=datetime.now(UTC),
        repo=REPO,
        path=SCHEMA_PATH,
        checksums=checksums,
    )
    update_io.atomic_write_text(
        version_file,
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
    )


class _SnapshotRollbackError(RuntimeError):
    """A publish failure whose backup must remain available for recovery."""


_RECOVERY_BACKUP_ATTRIBUTE = "_schema_fetch_recovery_backup"


def _managed_schema_paths() -> dict[str, Path]:
    """Return the live artifacts owned by the upstream schema snapshot."""
    paths = {path.name: path for path in SCHEMAS_DIR.glob("*.yaml")}
    if VERSION_FILE.exists():
        paths[VERSION_FILE.name] = VERSION_FILE
    return paths


def _restore_schema_snapshot(backup_dir: Path, backups: dict[str, Path]) -> None:
    """Restore a complete prior file set after a failed publication."""
    for name, path in _managed_schema_paths().items():
        if name not in backups:
            path.unlink()
    for name in backups:
        backup = backup_dir / name
        target = SCHEMAS_DIR / name
        update_io.atomic_write_bytes(target, backup.read_bytes())
        target.chmod(backup.stat().st_mode & 0o777)


def _publish_schema_snapshot(staging_dir: Path, backup_dir: Path) -> None:
    """Publish a staged file set, restoring the prior snapshot on any failure."""
    backups = _managed_schema_paths()
    backup_dir.mkdir()
    for name, path in backups.items():
        shutil.copy2(path, backup_dir / name)

    staged = {path.name: path for path in staging_dir.iterdir() if path.is_file()}
    try:
        for name in sorted(staged.keys() - {VERSION_FILE.name}):
            update_io.atomic_write_bytes(
                SCHEMAS_DIR / name,
                staged[name].read_bytes(),
            )
        for name in sorted(backups.keys() - staged.keys()):
            backups[name].unlink()
        update_io.atomic_write_bytes(
            VERSION_FILE,
            (staging_dir / VERSION_FILE.name).read_bytes(),
        )
    except BaseException as publish_error:
        try:
            _restore_schema_snapshot(backup_dir, backups)
        except BaseException as rollback_error:  # noqa: BLE001 - preserve all failures
            msg = (
                f"Schema snapshot publication failed: {publish_error}; "
                f"rollback also failed: {rollback_error}; "
                f"recovery backup retained at {backup_dir}"
            )
            interruption = (
                publish_error
                if not isinstance(publish_error, Exception)
                else rollback_error
                if not isinstance(rollback_error, Exception)
                else None
            )
            if interruption is not None:
                # Tell fetch() to retain recovery evidence without wrapping the interrupt.
                setattr(interruption, _RECOVERY_BACKUP_ATTRIBUTE, backup_dir)
                interruption.add_note(msg)
                raise interruption from None
            raise _SnapshotRollbackError(msg) from publish_error
        raise


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

    workspace = Path(tempfile.mkdtemp(prefix=".schema-fetch-", dir=SCHEMAS_DIR))
    preserve_workspace = False
    try:
        staging_dir = workspace / "staged"
        staging_dir.mkdir()
        for index, f in enumerate(files, start=1):
            _emit_progress(progress, f"Downloading {index}/{total}: {f['name']}")
            content = _download_schema(f["download_url"])
            (staging_dir / f["name"]).write_bytes(content)

        _emit_progress(progress, "Updating schema version manifest.")
        _write_version(
            commit_sha,
            branch,
            files,
            schemas_dir=staging_dir,
            version_file=staging_dir / VERSION_FILE.name,
        )
        try:
            _publish_schema_snapshot(staging_dir, workspace / "backup")
        except BaseException as exc:
            preserve_workspace = isinstance(exc, _SnapshotRollbackError) or (
                getattr(exc, _RECOVERY_BACKUP_ATTRIBUTE, None) is not None
            )
            raise
    finally:
        _cleanup_fetch_workspace(workspace, preserve=preserve_workspace)
    _emit_progress(progress, "Schema fetch complete.")


def _cleanup_fetch_workspace(workspace: Path, *, preserve: bool) -> None:
    """Keep recovery evidence only after a rollback failure."""
    if preserve:
        return
    shutil.rmtree(workspace, ignore_errors=True)


def _drift(name: str, local: str | None, recorded: str | None) -> str:
    if local is None:
        return f"{name}: missing"
    if recorded is None:
        return f"{name}: not recorded in {VERSION_FILE.name}"
    return f"{name}: checksum differs from {VERSION_FILE.name}"


def check() -> bool:
    """Verify vendored schemas against the checksums recorded at fetch time.

    Reports each drifted file on stderr. No network access is needed: the
    manifest pins the upstream commit and the sha256 of every fetched file.
    """
    version = _parse_version()
    if version is None or not version.checksums:
        sys.stderr.write(f"{VERSION_FILE.name} is missing, invalid, or empty.\n")
        return False

    local = {path.name: _sha256(path) for path in SCHEMAS_DIR.glob("*.yaml")}
    drift = [
        _drift(name, local.get(name), version.checksums.get(name))
        for name in sorted(local.keys() | version.checksums.keys())
        if local.get(name) != version.checksums.get(name)
    ]
    for line in drift:
        sys.stderr.write(f"Vendored schema drift: {line}\n")
    return not drift


def main() -> None:
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    else:
        fetch()


if __name__ == "__main__":  # pragma: no cover -- CLI entrypoint
    main()
