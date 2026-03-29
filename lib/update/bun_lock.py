"""Validate source-package consistency in textual ``bun.lock`` files."""

from __future__ import annotations

import io
import json
import re
import tarfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_OBJECT_DICT_ADAPTER = TypeAdapter(dict[str, object])
_OBJECT_LIST_ADAPTER = TypeAdapter(list[object])
_STRING_ADAPTER = TypeAdapter(str)

_TRAILING_COMMA = re.compile(r",(?=\s*[}\]])")
_EXACT_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]*$")


@dataclass(frozen=True)
class SourcePackageManifest:
    """Minimal package metadata extracted from a source tarball."""

    name: str
    version: str
    url: str


def _as_object_dict(value: object, *, context: str) -> dict[str, object]:
    """Return *value* as ``dict[str, object]`` or raise ``TypeError``."""
    try:
        return _OBJECT_DICT_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        msg = f"Expected JSON object for {context}"
        raise TypeError(msg) from exc


def _as_object_list(value: object, *, context: str) -> list[object]:
    """Return *value* as ``list[object]`` or raise ``TypeError``."""
    try:
        return _OBJECT_LIST_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        msg = f"Expected JSON array for {context}"
        raise TypeError(msg) from exc


def _get_required_str(mapping: dict[str, object], key: str, *, context: str) -> str:
    """Return required string field *key* from *mapping*."""
    if key not in mapping:
        msg = f"Expected string field {key!r} in {context}"
        raise TypeError(msg)
    try:
        return _STRING_ADAPTER.validate_python(mapping[key], strict=True)
    except ValidationError as exc:
        msg = f"Expected string field {key!r} in {context}"
        raise TypeError(msg) from exc


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

    # Source package URLs are limited to explicit HTTP(S) tarballs.
    with urllib.request.urlopen(url) as response:  # noqa: S310
        return response.read()


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


def validate_source_package_exact_versions(
    lock_file: Path,
    *,
    fetch_bytes: Callable[[str], bytes] = _fetch_url_bytes,
) -> None:
    """Reject source-package override graphs that contradict exact dependencies."""
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
                    errors.append(
                        "source override exact-version mismatch: "
                        f"{package_name} requires {dep_name}@{dep_spec_raw}, "
                        f"but override {override_url} provides {manifest.version}"
                    )

    if errors:
        rendered = "\n".join(f"- {error}" for error in errors)
        msg = f"Bun source package validation failed for {lock_file}:\n{rendered}"
        raise RuntimeError(msg)


__all__ = [
    "SourcePackageManifest",
    "validate_source_package_exact_versions",
]
