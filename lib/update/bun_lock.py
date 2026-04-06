"""Validate source-package consistency in textual ``bun.lock`` files."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib import http_utils, json_utils

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_as_object_dict = json_utils.as_object_dict
_as_object_list = json_utils.as_object_list
_get_required_str = json_utils.get_required_str

_TRAILING_COMMA = re.compile(r",(?=\s*[}\]])")
_EXACT_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]*$")
_GITHUB_RELEASE_ASSET_PARTS = 6
_FETCH_RETRIES = 3
_FETCH_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class SourcePackageManifest:
    """Minimal package metadata extracted from a source tarball."""

    name: str
    version: str
    url: str


class BunSourcePackageValidationError(RuntimeError):
    """Raised when a Bun source-package override graph is inconsistent."""


@dataclass(frozen=True)
class SourcePackageExactVersionMismatch:
    """One exact-version dependency that conflicts with a source override."""

    package_name: str
    dependency_name: str
    required_version: str
    current_version: str
    dependency_url: str
    package_url: str | None


def _normalize_textual_json(text: str) -> str:
    """Normalize Bun's textual JSON by removing trailing commas."""
    return _TRAILING_COMMA.sub("", text)


def _load_bun_lock(lock_file: Path) -> dict[str, object]:
    """Load *lock_file* into a normalized JSON object."""
    try:
        raw_text = lock_file.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Failed to read bun lockfile: {lock_file}"
        raise OSError(msg) from exc

    try:
        return _as_object_dict(
            json.loads(_normalize_textual_json(raw_text)),
            context=f"bun.lock {lock_file}",
        )
    except json.JSONDecodeError as exc:
        msg = f"Invalid textual bun.lock JSON: {lock_file}"
        raise ValueError(msg) from exc


def _is_source_url(value: str) -> bool:
    """Return whether *value* points at an external source artifact."""
    return value.startswith(("https://", "http://", "github:", "git+https://"))


def _is_exact_version_spec(spec: str) -> bool:
    """Return whether *spec* is an exact pinned version string."""
    if spec in {"", "*", "latest"}:
        return False
    if spec.startswith((
        "workspace:",
        "file:",
        "link:",
        "npm:",
        "git+",
        "github:",
        "http://",
        "https://",
        "^",
        "~",
        ">",
        "<",
        "=",
    )):
        return False
    return _EXACT_VERSION.fullmatch(spec) is not None


def _fetch_url_bytes(url: str) -> bytes:
    """Fetch *url* and return the response body."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"https", "http"}:
        msg = f"Unsupported source package URL scheme: {url}"
        raise ValueError(msg)
    if _FETCH_RETRIES < 1:
        msg = f"Failed to fetch source package URL: {url}"
        raise RuntimeError(msg)

    try:
        payload, _headers = http_utils.fetch_url_bytes(
            url,
            allowed_schemes=frozenset({"http", "https"}),
            attempts=_FETCH_RETRIES,
            backoff=1.0,
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
    except http_utils.RequestError as exc:
        if exc.kind in {"network", "timeout"}:
            raise OSError(exc.detail) from exc
        if exc.kind == "status":
            msg = f"HTTP {exc.status} fetching {url}"
            if exc.attempts > 1:
                msg = f"{msg} after {exc.attempts} attempt(s)"
            raise RuntimeError(msg) from exc
        msg = f"Failed to fetch source package URL: {url}"
        raise RuntimeError(msg) from exc
    else:
        return payload


def _package_json_member_name(names: list[str]) -> str | None:
    """Return the most likely package.json entry from tar member names."""
    if "package/package.json" in names:
        return "package/package.json"
    if "package.json" in names:
        return "package.json"

    candidates = sorted(
        name
        for name in names
        if name.endswith("/package.json") or name == "package.json"
    )
    if not candidates:
        return None
    return min(candidates, key=lambda name: (name.count("/"), len(name)))


def _read_source_package_manifest(
    url: str,
    *,
    fetch_bytes: Callable[[str], bytes] = _fetch_url_bytes,
) -> SourcePackageManifest:
    """Read ``package.json`` metadata from one tarball source package."""
    try:
        archive_bytes = fetch_bytes(url)
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            package_json_name = _package_json_member_name(archive.getnames())
            if package_json_name is None:
                msg = f"Source package archive does not contain package.json: {url}"
                raise ValueError(msg)

            member = archive.extractfile(package_json_name)
            if member is None:
                msg = f"Failed to extract package.json from source package: {url}"
                raise ValueError(msg)

            manifest = _as_object_dict(
                json.loads(member.read().decode("utf-8")),
                context=f"package manifest for {url}",
            )
    except (tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"Invalid source package archive metadata: {url}"
        raise ValueError(msg) from exc

    return SourcePackageManifest(
        name=_get_required_str(manifest, "name", context=f"package manifest for {url}"),
        version=_get_required_str(
            manifest, "version", context=f"package manifest for {url}"
        ),
        url=url,
    )


def _collect_source_package_mismatches(
    lock_file: Path,
    *,
    fetch_bytes: Callable[[str], bytes] = _fetch_url_bytes,
) -> tuple[list[str], list[SourcePackageExactVersionMismatch]]:
    """Collect source override name and exact-version inconsistencies."""
    lock_data = _load_bun_lock(lock_file)
    overrides = _as_object_dict(
        lock_data.get("overrides", {}), context="bun.lock overrides"
    )
    packages = _as_object_dict(
        lock_data.get("packages", {}), context="bun.lock packages"
    )

    source_overrides = {
        name: value
        for name, raw_value in overrides.items()
        if isinstance(raw_value, str)
        for value in (raw_value,)
        if _is_source_url(value)
    }

    manifest_cache: dict[str, SourcePackageManifest] = {}
    errors: list[str] = []
    mismatches: list[SourcePackageExactVersionMismatch] = []

    for override_name, url in sorted(source_overrides.items()):
        manifest = _read_source_package_manifest(url, fetch_bytes=fetch_bytes)
        manifest_cache[override_name] = manifest
        if manifest.name != override_name:
            errors.append(
                "source override package name mismatch: "
                f"{override_name} -> {url} declares {manifest.name}@{manifest.version}"
            )

    for package_name, raw_entry in sorted(packages.items()):
        if not isinstance(raw_entry, list):
            continue

        entry = _as_object_list(raw_entry, context=f"bun.lock package {package_name}")
        metadata_obj = entry[1] if len(entry) > 1 else None
        if not isinstance(metadata_obj, dict):
            continue
        metadata = _as_object_dict(
            metadata_obj,
            context=f"metadata for bun.lock package {package_name}",
        )

        for dep_field in ("dependencies", "optionalDependencies"):
            raw_deps = metadata.get(dep_field)
            if raw_deps is None:
                continue

            deps = _as_object_dict(
                raw_deps,
                context=f"{dep_field} for bun.lock package {package_name}",
            )
            for dep_name, dep_spec_raw in sorted(deps.items()):
                if not isinstance(dep_spec_raw, str):
                    continue
                if not _is_exact_version_spec(dep_spec_raw):
                    continue

                override_url = source_overrides.get(dep_name)
                if override_url is None:
                    continue

                manifest = manifest_cache[dep_name]
                if manifest.version != dep_spec_raw:
                    mismatches.append(
                        SourcePackageExactVersionMismatch(
                            package_name=package_name,
                            dependency_name=dep_name,
                            required_version=dep_spec_raw,
                            current_version=manifest.version,
                            dependency_url=override_url,
                            package_url=source_overrides.get(package_name),
                        )
                    )

    return errors, mismatches


def _load_json_object(path: Path, *, context: str) -> dict[str, object]:
    """Load one UTF-8 JSON object from *path*."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Failed to read {context}: {path}"
        raise OSError(msg) from exc

    try:
        return _as_object_dict(json.loads(raw_text), context=context)
    except json.JSONDecodeError as exc:
        msg = f"Invalid JSON object in {context}: {path}"
        raise ValueError(msg) from exc


def _parse_github_release_asset(url: str) -> tuple[str, str, str, str, str, str] | None:
    """Parse GitHub release asset URLs into components."""
    parsed = urllib.parse.urlsplit(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != _GITHUB_RELEASE_ASSET_PARTS:
        return None
    owner, repo, releases, download, tag, asset = path_parts
    if releases != "releases" or download != "download":
        return None
    return parsed.scheme, parsed.netloc, owner, repo, tag, asset


def _rewrite_github_release_asset_version(
    url: str,
    *,
    current_version: str,
    required_version: str,
    release_tag: str,
) -> str | None:
    """Rewrite one GitHub release asset to a sibling version under *release_tag*."""
    parsed = _parse_github_release_asset(url)
    if parsed is None:
        return None

    scheme, netloc, owner, repo, _old_tag, asset = parsed
    suffix = f"-{current_version}.tgz"
    if not asset.endswith(suffix):
        return None

    asset_prefix = asset.removesuffix(suffix)
    new_asset = f"{asset_prefix}-{required_version}.tgz"
    new_path = f"/{owner}/{repo}/releases/download/{release_tag}/{new_asset}"
    return urllib.parse.urlunsplit((scheme, netloc, new_path, "", ""))


def _write_json_object(path: Path, payload: dict[str, object]) -> None:
    """Write *payload* as pretty JSON with a trailing newline."""
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _heal_package_json_source_resolutions(
    package_json_path: Path,
    lock_file: Path,
    *,
    fetch_bytes: Callable[[str], bytes] = _fetch_url_bytes,
) -> bool:
    """Patch source-package resolutions in ``package.json`` when healable."""
    package_data = _load_json_object(package_json_path, context="package.json")
    resolution_field = "resolutions" if "resolutions" in package_data else "overrides"
    resolutions = _as_object_dict(
        package_data.get(resolution_field, {}),
        context=f"package.json {resolution_field}",
    )
    errors, mismatches = _collect_source_package_mismatches(
        lock_file, fetch_bytes=fetch_bytes
    )
    if errors:
        return False

    updates: dict[str, str] = {}
    for mismatch in mismatches:
        if mismatch.package_url is None:
            continue
        dependent_release = _parse_github_release_asset(mismatch.package_url)
        if dependent_release is None:
            continue

        candidate_url = _rewrite_github_release_asset_version(
            mismatch.dependency_url,
            current_version=mismatch.current_version,
            required_version=mismatch.required_version,
            release_tag=dependent_release[4],
        )
        if candidate_url is None:
            continue

        candidate_manifest = _read_source_package_manifest(
            candidate_url,
            fetch_bytes=fetch_bytes,
        )
        if (
            candidate_manifest.name != mismatch.dependency_name
            or candidate_manifest.version != mismatch.required_version
        ):
            continue

        existing = updates.get(mismatch.dependency_name)
        if existing is not None and existing != candidate_url:
            return False
        updates[mismatch.dependency_name] = candidate_url

    if not updates:
        return False

    changed = False
    for dep_name, candidate_url in sorted(updates.items()):
        current_value = resolutions.get(dep_name)
        if current_value == candidate_url:
            continue
        resolutions[dep_name] = candidate_url
        changed = True

    if not changed:
        return False

    package_data[resolution_field] = resolutions
    _write_json_object(package_json_path, package_data)
    return True


def _run_bun_lockfile_refresh(workspace_dir: Path, bun_executable: str) -> None:
    """Regenerate ``bun.lock`` in *workspace_dir* using Bun itself."""
    with (
        tempfile.TemporaryDirectory(prefix="bun-lock-home-") as home_dir,
        tempfile.TemporaryDirectory(prefix="bun-lock-cache-") as cache_dir,
        tempfile.TemporaryDirectory(prefix="bun-lock-config-") as config_dir,
        tempfile.TemporaryDirectory(prefix="bun-lock-data-") as data_dir,
        tempfile.TemporaryDirectory(prefix="bun-lock-state-") as state_dir,
    ):
        env = os.environ | {
            "HOME": home_dir,
            "XDG_CACHE_HOME": cache_dir,
            "XDG_CONFIG_HOME": config_dir,
            "XDG_DATA_HOME": data_dir,
            "XDG_STATE_HOME": state_dir,
        }
        completed = subprocess.run(  # noqa: S603
            [bun_executable, "install", "--lockfile-only", "--ignore-scripts"],
            cwd=workspace_dir,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
    if completed.returncode != 0:
        details = (
            completed.stderr.strip() or completed.stdout.strip() or "command failed"
        )
        msg = (
            f"Failed to regenerate bun.lock in {workspace_dir} with {bun_executable}:\n"
            f"{details}"
        )
        raise RuntimeError(msg)


def prepare_source_package_lock(
    workspace_dir: Path,
    lock_file: Path,
    *,
    bun_executable: str = "bun",
    validate: Callable[[Path], None] | None = None,
    relock: Callable[[Path, str], None] | None = None,
) -> bool:
    """Validate *lock_file* and relock once when source overrides disagree."""
    validate_lock = (
        validate if validate is not None else validate_source_package_exact_versions
    )
    relock_lock = relock if relock is not None else _run_bun_lockfile_refresh

    try:
        validate_lock(lock_file)
    except BunSourcePackageValidationError:
        relock_lock(workspace_dir, bun_executable)
        try:
            validate_lock(lock_file)
        except BunSourcePackageValidationError:
            package_json_path = workspace_dir / "package.json"
            if (
                not package_json_path.is_file()
                or not _heal_package_json_source_resolutions(
                    package_json_path,
                    lock_file,
                )
            ):
                raise
            relock_lock(workspace_dir, bun_executable)
            validate_lock(lock_file)
        return True

    return False


def validate_source_package_exact_versions(
    lock_file: Path,
    *,
    fetch_bytes: Callable[[str], bytes] = _fetch_url_bytes,
) -> None:
    """Reject source-package override graphs that contradict exact dependencies."""
    errors, mismatches = _collect_source_package_mismatches(
        lock_file, fetch_bytes=fetch_bytes
    )
    for mismatch in mismatches:
        errors.append(
            "source override exact-version mismatch: "
            f"{mismatch.package_name} requires "
            f"{mismatch.dependency_name}@{mismatch.required_version}, "
            f"but override {mismatch.dependency_url} provides {mismatch.current_version}"
        )

    if errors:
        rendered = "\n".join(f"- {error}" for error in errors)
        msg = f"Bun source package validation failed for {lock_file}:\n{rendered}"
        raise BunSourcePackageValidationError(msg)


__all__ = [
    "BunSourcePackageValidationError",
    "SourcePackageExactVersionMismatch",
    "SourcePackageManifest",
    "prepare_source_package_lock",
    "validate_source_package_exact_versions",
]
