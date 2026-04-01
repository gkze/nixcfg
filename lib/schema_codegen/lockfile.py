"""Deterministic codegen manifest lockfile materialization helpers."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import yaml

from lib import http_utils
from lib.schema_codegen.models._generated import CodegenLockfile, CodegenManifest

DEFAULT_LOCKFILE_NAME = "codegen.lock.json"
_GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
_GITHUB_API_BASE = "https://api.github.com"
_GITHUB_COMMIT_SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")

type JsonScalar = str | int | bool | None
type CanonicalJsonValue = (
    JsonScalar | list[CanonicalJsonValue] | dict[str, CanonicalJsonValue]
)
type ProgressReporter = Callable[[str], None]


def _emit_progress(progress: ProgressReporter | None, message: str) -> None:
    """Invoke the optional lockfile progress reporter."""
    if progress is None:
        return
    progress(message)


def _ensure_mapping(value: object, *, context: str) -> Mapping[str, object]:
    """Return ``value`` as a string-keyed mapping or raise ``TypeError``."""
    if not isinstance(value, Mapping):
        msg = f"Expected object for {context}, got {type(value).__name__}"
        raise TypeError(msg)
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            msg = f"Expected string key in {context}, got {type(key).__name__}"
            raise TypeError(msg)
        result[key] = item
    return result


def _ensure_string(value: object, *, context: str) -> str:
    """Return ``value`` as ``str`` or raise ``TypeError``."""
    if not isinstance(value, str):
        msg = f"Expected string for {context}, got {type(value).__name__}"
        raise TypeError(msg)
    return value


def _load_manifest_payload(path: Path) -> Mapping[str, object]:
    """Load a canonical codegen manifest from YAML or JSON."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _ensure_mapping(payload, context=f"manifest {path}")


def load_codegen_manifest(*, manifest_path: Path) -> CodegenManifest:
    """Load and validate a canonical codegen manifest document."""
    resolved_path = manifest_path.expanduser().resolve()
    return CodegenManifest.model_validate(_load_manifest_payload(resolved_path))


def _normalize_posix_string(value: str) -> str:
    """Normalize one path-like string to the lockfile POSIX representation."""
    raw = value.replace("\\", "/")
    if raw == "":
        msg = "Path string must not be empty"
        raise RuntimeError(msg)
    leading_slash = raw.startswith("/")
    parts = [part for part in PurePosixPath(raw).parts if part not in {"", "."}]
    if leading_slash:
        if not parts:
            return "/"
        return "/" + "/".join(parts)
    if not parts:
        return "."
    return "/".join(parts)


def _normalize_relative_posix_path(*, path: Path, start: Path) -> str:
    """Return a normalized relative POSIX path from ``start`` to ``path``."""
    return _normalize_posix_string(os.path.relpath(path, start))


def _is_regular_file(path: Path) -> bool:
    """Return whether ``path`` is a non-symlink regular file."""
    return path.is_file() and not path.is_symlink()


def _compile_include_pattern(pattern: str) -> re.Pattern[str]:
    """Compile one source include glob with POSIX path semantics."""
    return re.compile(glob.translate(pattern, recursive=True, include_hidden=True))


def _iter_materialized_directory_files(
    *,
    source_root: Path,
    include_patterns: tuple[str, ...],
) -> tuple[tuple[str, Path], ...]:
    """Return matched regular files relative to ``source_root`` in sorted order."""
    if not source_root.exists():
        msg = f"Directory source path does not exist: {source_root}"
        raise RuntimeError(msg)
    if not source_root.is_dir():
        msg = f"Directory source path is not a directory: {source_root}"
        raise RuntimeError(msg)

    compiled = tuple(_compile_include_pattern(pattern) for pattern in include_patterns)
    matched: dict[str, Path] = {}
    for path in sorted(source_root.rglob("*")):
        if path.is_dir():
            continue
        if path.is_symlink():
            msg = f"Directory source includes unsupported symlink: {path}"
            raise RuntimeError(msg)
        if not path.is_file():
            msg = f"Directory source includes unsupported non-regular file: {path}"
            raise RuntimeError(msg)
        relative_path = path.relative_to(source_root).as_posix()
        if compiled and not any(regex.fullmatch(relative_path) for regex in compiled):
            continue
        matched[relative_path] = path
    return tuple(sorted(matched.items()))


def _hash_directory_materialization(
    *,
    source_root: Path,
    include_patterns: tuple[str, ...],
) -> str:
    """Hash the directory file set per the v1 lockfile specification."""
    records: list[bytes] = []
    for relative_path, path in _iter_materialized_directory_files(
        source_root=source_root,
        include_patterns=include_patterns,
    ):
        file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(f"{relative_path}\0{file_sha256}\n".encode())
    return hashlib.sha256(b"".join(records)).hexdigest()


def _build_http_headers(url: str) -> dict[str, str]:
    """Return request headers for lockfile fetches."""
    return http_utils.build_github_headers(
        url,
        token=http_utils.resolve_github_token(
            allow_keyring=True,
            allow_netrc=True,
        ),
        user_agent="nixcfg-codegen-lockfile",
    )


def _fetch_https_bytes(url: str) -> tuple[bytes, Mapping[str, str]]:
    """Fetch one HTTPS URL and return response bytes plus response headers."""
    try:
        return http_utils.fetch_url_bytes(
            url,
            headers=_build_http_headers(url),
            timeout=30.0,
        )
    except ValueError as exc:
        msg = f"Only absolute HTTPS URLs are supported, got {url!r}"
        raise RuntimeError(msg) from exc
    except http_utils.SyncRequestError as exc:
        msg = f"Failed to fetch {url}: {exc.detail}"
        raise RuntimeError(msg) from exc


def _fetch_https_json(url: str) -> Mapping[str, object]:
    """Fetch one HTTPS JSON document and return it as a mapping."""
    payload, _headers = _fetch_https_bytes(url)
    try:
        decoded = json.loads(payload.decode())
    except json.JSONDecodeError as exc:
        msg = f"Invalid JSON response from {url}: {exc}"
        raise RuntimeError(msg) from exc
    return _ensure_mapping(decoded, context=f"JSON response from {url}")


def _resolve_github_commit(owner: str, repo: str, ref: str) -> str:
    """Resolve a GitHub ref to the immutable commit SHA to store in the lockfile."""
    if _GITHUB_COMMIT_SHA_PATTERN.fullmatch(ref):
        return ref
    quoted_ref = quote(ref, safe="")
    payload = _fetch_https_json(
        f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{quoted_ref}"
    )
    sha = _ensure_string(
        payload.get("sha"), context=f"commit SHA for {owner}/{repo}@{ref}"
    )
    if not _GITHUB_COMMIT_SHA_PATTERN.fullmatch(sha):
        msg = f"GitHub commit lookup for {owner}/{repo}@{ref} returned invalid SHA {sha!r}"
        raise RuntimeError(msg)
    return sha


def _utcnow() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(tz=UTC)


def _build_locked_directory_source(
    *,
    manifest_dir: Path,
    source_name: str,
    source: Mapping[str, object],
    include_metadata: bool,
) -> dict[str, object]:
    """Materialize one locked directory source entry."""
    raw_path = _ensure_string(source.get("path"), context=f"source {source_name}.path")
    include_patterns = source.get("include")
    patterns: tuple[str, ...] = ()
    if include_patterns is not None:
        if not isinstance(include_patterns, list):
            msg = f"Expected array for source {source_name}.include"
            raise TypeError(msg)
        patterns = tuple(
            _ensure_string(item, context=f"source {source_name}.include[]")
            for item in include_patterns
        )
    absolute_path = Path(raw_path)
    if not absolute_path.is_absolute():
        absolute_path = (manifest_dir / absolute_path).resolve()
    normalized_path = _normalize_relative_posix_path(
        path=absolute_path, start=manifest_dir
    )
    locked: dict[str, object] = {
        "kind": "directory",
        "path": normalized_path,
        "content_sha256": _hash_directory_materialization(
            source_root=absolute_path,
            include_patterns=patterns,
        ),
    }
    if include_metadata:
        locked["generated_at"] = _utcnow()
    return locked


def _build_locked_url_source(
    *,
    source_name: str,
    source: Mapping[str, object],
    include_metadata: bool,
) -> dict[str, object]:
    """Materialize one locked URL source entry."""
    uri = _ensure_string(source.get("uri"), context=f"source {source_name}.uri")
    payload, headers = _fetch_https_bytes(uri)
    locked: dict[str, object] = {
        "kind": "url",
        "uri": uri,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if include_metadata:
        locked["fetched_at"] = _utcnow()
        etag = headers.get("etag")
        if etag:
            locked["etag"] = etag
        last_modified = headers.get("last-modified") or headers.get("Last-Modified")
        if last_modified:
            locked["last_modified"] = last_modified
    return locked


def _build_locked_github_raw_source(
    *,
    source_name: str,
    source: Mapping[str, object],
    include_metadata: bool,
) -> dict[str, object]:
    """Materialize one locked GitHub raw source entry."""
    owner = _ensure_string(source.get("owner"), context=f"source {source_name}.owner")
    repo = _ensure_string(source.get("repo"), context=f"source {source_name}.repo")
    requested_ref = _ensure_string(
        source.get("ref"), context=f"source {source_name}.ref"
    )
    source_path = _normalize_posix_string(
        _ensure_string(source.get("path"), context=f"source {source_name}.path")
    )
    resolved_ref = _resolve_github_commit(owner, repo, requested_ref)
    uri = f"{_GITHUB_RAW_BASE}/{owner}/{repo}/{resolved_ref}/{source_path}"
    payload, _headers = _fetch_https_bytes(uri)
    locked: dict[str, object] = {
        "kind": "github-raw",
        "owner": owner,
        "repo": repo,
        "ref": resolved_ref,
        "path": source_path,
        "uri": uri,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    metadata = source.get("metadata")
    metadata_map = (
        _ensure_mapping(metadata, context=f"source {source_name}.metadata")
        if metadata is not None
        else None
    )
    if include_metadata:
        locked["fetched_at"] = _utcnow()
        if metadata_map is not None:
            for field in ("tag", "package", "package_version"):
                value = metadata_map.get(field)
                if value is not None:
                    locked[field] = _ensure_string(
                        value,
                        context=f"source {source_name}.metadata.{field}",
                    )
    return locked


def build_codegen_lockfile(
    *,
    manifest_path: Path,
    lockfile_path: Path | None = None,
    include_metadata: bool = False,
    progress: ProgressReporter | None = None,
) -> CodegenLockfile:
    """Build a validated v1 codegen lockfile from one canonical manifest."""
    resolved_manifest_path = manifest_path.expanduser().resolve()
    resolved_lockfile_path = (
        lockfile_path.expanduser().resolve()
        if lockfile_path is not None
        else resolved_manifest_path.with_name(DEFAULT_LOCKFILE_NAME)
    )
    manifest = load_codegen_manifest(manifest_path=resolved_manifest_path)
    manifest_payload = _ensure_mapping(
        manifest.model_dump(mode="json"),
        context=f"validated manifest {resolved_manifest_path}",
    )
    sources = _ensure_mapping(
        manifest_payload.get("sources"), context="manifest.sources"
    )
    manifest_dir = resolved_manifest_path.parent
    locked_sources: dict[str, object] = {}
    _emit_progress(
        progress, f"Locking {len(sources)} source(s) from {resolved_manifest_path}"
    )
    for source_name in sorted(sources):
        source = _ensure_mapping(
            sources[source_name], context=f"manifest.sources.{source_name}"
        )
        kind = _ensure_string(
            source.get("kind"), context=f"manifest.sources.{source_name}.kind"
        )
        _emit_progress(progress, f"Resolving source {source_name} ({kind})")
        if kind == "directory":
            locked_sources[source_name] = _build_locked_directory_source(
                manifest_dir=manifest_dir,
                source_name=source_name,
                source=source,
                include_metadata=include_metadata,
            )
        elif kind == "url":
            locked_sources[source_name] = _build_locked_url_source(
                source_name=source_name,
                source=source,
                include_metadata=include_metadata,
            )
        elif kind == "github-raw":
            locked_sources[source_name] = _build_locked_github_raw_source(
                source_name=source_name,
                source=source,
                include_metadata=include_metadata,
            )
        else:
            msg = f"Unsupported source kind {kind!r} for source {source_name}"
            raise RuntimeError(msg)
    payload: dict[str, object] = {
        "version": 1,
        "manifest_path": _normalize_relative_posix_path(
            path=resolved_manifest_path,
            start=resolved_lockfile_path.parent,
        ),
        "sources": locked_sources,
    }
    if include_metadata:
        payload["generated_at"] = _utcnow()
    return CodegenLockfile.model_validate(payload)


def _ensure_canonical_json_value(
    value: object,
    *,
    context: str,
) -> CanonicalJsonValue:
    """Validate that ``value`` fits the canonical lockfile JSON subset."""
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        msg = f"Canonical JSON does not permit float values in {context}"
        raise TypeError(msg)
    if isinstance(value, list):
        return [
            _ensure_canonical_json_value(item, context=f"{context}[]") for item in value
        ]
    mapping = _ensure_mapping(value, context=context)
    return {
        key: _ensure_canonical_json_value(item, context=f"{context}.{key}")
        for key, item in mapping.items()
    }


def render_codegen_lockfile(lockfile: CodegenLockfile) -> str:
    """Render one lockfile as canonical JSON with a trailing newline."""
    payload = _ensure_canonical_json_value(
        lockfile.model_dump(mode="json", exclude_none=True),
        context="codegen lockfile",
    )
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def write_codegen_lockfile(
    *,
    manifest_path: Path,
    lockfile_path: Path | None = None,
    include_metadata: bool = False,
    progress: ProgressReporter | None = None,
) -> Path:
    """Build and write a canonical codegen lockfile next to its manifest."""
    resolved_manifest_path = manifest_path.expanduser().resolve()
    output_path = (
        lockfile_path.expanduser().resolve()
        if lockfile_path is not None
        else resolved_manifest_path.with_name(DEFAULT_LOCKFILE_NAME)
    )
    lockfile = build_codegen_lockfile(
        manifest_path=resolved_manifest_path,
        lockfile_path=output_path,
        include_metadata=include_metadata,
        progress=progress,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_codegen_lockfile(lockfile), encoding="utf-8")
    return output_path


__all__ = [
    "DEFAULT_LOCKFILE_NAME",
    "build_codegen_lockfile",
    "load_codegen_manifest",
    "render_codegen_lockfile",
    "write_codegen_lockfile",
]
