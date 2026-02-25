"""Resolve Deno dependencies from ``deno.lock`` v5 into a flat manifest.

Parses the lock file, fetches JSR ``_meta.json`` files to discover source
file lists, computes Deno cache paths, and produces a JSON manifest that
a Nix builder can consume to construct a deterministic ``DENO_DIR`` from
individual ``fetchurl`` calls.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

import aiohttp
from pydantic import TypeAdapter, ValidationError
from yarl import URL

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Manifest types
# ---------------------------------------------------------------------------

JSR_REGISTRY = "https://jsr.io"
NPM_REGISTRY = "https://registry.npmjs.org"

# Concurrency limit for fetching JSR meta files.
JSR_FETCH_CONCURRENCY = 20

_OBJECT_DICT_ADAPTER = TypeAdapter(dict[str, object])
_OBJECT_LIST_ADAPTER = TypeAdapter(list[object])
_STRING_ADAPTER = TypeAdapter(str)
_PACKAGE_MAP_ADAPTER = TypeAdapter(dict[str, dict[str, object]])


def _as_object_dict(value: object, *, context: str) -> dict[str, object]:
    """Return *value* as a ``dict[str, object]`` or raise ``TypeError``."""
    try:
        return _OBJECT_DICT_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        msg = f"Expected JSON object for {context}"
        raise TypeError(msg) from exc


def _as_object_list(value: object, *, context: str) -> list[object]:
    """Return *value* as a list or raise ``TypeError``."""
    try:
        return _OBJECT_LIST_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        msg = f"Expected JSON array for {context}"
        raise TypeError(msg) from exc


def _get_required_str(mapping: dict[str, object], key: str, *, context: str) -> str:
    """Get required string field *key* from *mapping*."""
    if key not in mapping:
        msg = f"Expected string field {key!r} in {context}"
        raise TypeError(msg)
    try:
        return _STRING_ADAPTER.validate_python(mapping[key], strict=True)
    except ValidationError as exc:
        msg = f"Expected string field {key!r} in {context}"
        raise TypeError(msg) from exc


def _as_package_map(value: object, *, context: str) -> dict[str, dict[str, object]]:
    """Normalize a package map keyed by package name/version."""
    try:
        return _PACKAGE_MAP_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        msg = f"Expected package map for {context}"
        raise TypeError(msg) from exc


@dataclass(frozen=True)
class JsrFile:
    """A single source file from a JSR package."""

    url: str
    sha256: str
    cache_path: str
    media_type: str


@dataclass(frozen=True)
class JsrPackage:
    """A resolved JSR package with all its source files."""

    name: str
    version: str
    integrity: str
    files: list[JsrFile]


@dataclass(frozen=True)
class NpmPackage:
    """A resolved npm package tarball."""

    name: str
    version: str
    integrity: str
    tarball_url: str
    cache_path: str


@dataclass
class DenoDepManifest:
    """Complete resolved dependency manifest for Nix consumption."""

    lock_version: str
    jsr_packages: list[JsrPackage] = field(default_factory=list)
    npm_packages: list[NpmPackage] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-friendly dict."""
        return asdict(self)

    def save(self, path: Path) -> None:
        """Write the manifest to *path*."""
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
        path.write_text(payload, encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> DenoDepManifest:
        """Load a manifest from *path*."""
        raw_manifest = json.loads(path.read_text(encoding="utf-8"))
        try:
            return _MANIFEST_ADAPTER.validate_python(raw_manifest, strict=False)
        except ValidationError as exc:
            msg = f"Invalid Deno dependency manifest: {path}"
            raise TypeError(msg) from exc


_MANIFEST_ADAPTER = TypeAdapter(DenoDepManifest)


# ---------------------------------------------------------------------------
# Deno cache path computation
# ---------------------------------------------------------------------------


def _url_to_cache_path(url: str) -> str:
    """Compute the Deno cache file path for an HTTPS URL.

    Deno stores cached files at ``remote/{scheme}/{host}/{sha256_of_path}``.
    The hash is computed over the *raw* URL path + query (fragment ignored).
    """
    parsed = URL(url)
    if parsed.scheme != "https" or parsed.host is None:
        msg = f"Expected https URL: {url}"
        raise ValueError(msg)
    path_qs = parsed.raw_path_qs or "/"
    path_hash = hashlib.sha256(path_qs.encode()).hexdigest()
    return f"remote/https/{parsed.host}/{path_hash}"


def _guess_media_type(path: str) -> str:
    """Guess the MIME type Deno uses for a file based on extension."""
    if path.endswith((".ts", ".tsx")):
        return "text/typescript"
    if path.endswith((".js", ".jsx", ".mjs")):
        return "text/javascript"
    guessed_type, _encoding = mimetypes.guess_type(path, strict=False)
    if guessed_type is not None:
        return guessed_type
    return "text/plain"


# ---------------------------------------------------------------------------
# JSR resolution
# ---------------------------------------------------------------------------


async def _fetch_jsr_meta(
    client: aiohttp.ClientSession,
    scope: str,
    name: str,
    version: str,
) -> dict[str, object]:
    """Fetch the ``_meta.json`` for a JSR package version."""
    url = f"{JSR_REGISTRY}/{scope}/{name}/{version}_meta.json"
    async with client.get(url) as resp:
        resp.raise_for_status()
        return _as_object_dict(
            await resp.json(),
            context=f"jsr meta for {scope}/{name}@{version}",
        )


async def _resolve_jsr_package(
    client: aiohttp.ClientSession,
    pkg_key: str,
    pkg_info: dict[str, object],
) -> JsrPackage:
    """Resolve a single JSR package into its files."""
    # pkg_key is like "@cliffy/ansi@1.0.0-rc.8"
    scope, rest = pkg_key.split("/", 1)
    name, version = rest.rsplit("@", 1)

    meta = await _fetch_jsr_meta(client, scope, name, version)
    manifest = _as_object_dict(
        meta.get("manifest", {}),
        context=f"manifest for {pkg_key}",
    )

    files: list[JsrFile] = []
    for file_path, file_info_obj in sorted(manifest.items()):
        file_info = _as_object_dict(
            file_info_obj,
            context=f"manifest file entry {pkg_key}:{file_path}",
        )
        url = f"{JSR_REGISTRY}/{scope}/{name}/{version}{file_path}"
        checksum = _get_required_str(
            file_info,
            "checksum",
            context=f"manifest file entry {pkg_key}:{file_path}",
        )
        # JSR checksums are "sha256-<hex>" format
        sha256_hex = checksum.removeprefix("sha256-")
        cache_path = _url_to_cache_path(url)
        media_type = _guess_media_type(file_path)
        files.append(
            JsrFile(
                url=url,
                sha256=sha256_hex,
                cache_path=cache_path,
                media_type=media_type,
            )
        )

    # Also cache the meta.json and version_meta.json themselves
    # (Deno fetches these during module resolution).
    # We fetch them here and compute sha256 so that Nix fetchurl can verify.
    for meta_path in [
        f"/{scope}/{name}/meta.json",
        f"/{scope}/{name}/{version}_meta.json",
    ]:
        meta_url = f"{JSR_REGISTRY}{meta_path}"
        cache_path = _url_to_cache_path(meta_url)
        async with client.get(meta_url) as meta_resp:
            meta_resp.raise_for_status()
            meta_content = await meta_resp.read()
        meta_sha256 = hashlib.sha256(meta_content).hexdigest()
        files.append(
            JsrFile(
                url=meta_url,
                sha256=meta_sha256,
                cache_path=cache_path,
                media_type="application/json",
            )
        )

    return JsrPackage(
        name=f"{scope}/{name}",
        version=version,
        integrity=_get_required_str(
            pkg_info, "integrity", context=f"jsr package {pkg_key}"
        ),
        files=files,
    )


async def _resolve_all_jsr(
    lock_jsr: dict[str, dict[str, object]],
) -> list[JsrPackage]:
    """Resolve all JSR packages from the lock file."""
    sem = asyncio.Semaphore(JSR_FETCH_CONCURRENCY)

    async def _with_sem(coro: Coroutine[None, None, JsrPackage]) -> JsrPackage:
        async with sem:
            return await coro

    timeout = aiohttp.ClientTimeout(total=30.0)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        tasks = [
            _with_sem(_resolve_jsr_package(client, pkg_key, pkg_info))
            for pkg_key, pkg_info in lock_jsr.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    packages: list[JsrPackage] = []
    for pkg_key, result in zip(lock_jsr, results, strict=True):
        if isinstance(result, BaseException):
            log.error("Failed to resolve JSR package %s: %s", pkg_key, result)
            raise result
        packages.append(result)

    return sorted(packages, key=lambda p: (p.name, p.version))


# ---------------------------------------------------------------------------
# NPM resolution
# ---------------------------------------------------------------------------


def _parse_npm_pkg_key(pkg_key: str) -> tuple[str, str]:
    """Parse an npm lock key into (name, version).

    Handles scoped packages (``@scope/name@version``) and peer-dep
    qualifiers (``name@version_peer@peerversion``).
    """
    # Strip peer dep qualifier (everything after first underscore in version)
    at_idx = pkg_key.index("@", 1) if pkg_key.startswith("@") else pkg_key.index("@")

    full_name = pkg_key[:at_idx]
    version_part = pkg_key[at_idx + 1 :]
    # Strip peer dep qualifier
    version = version_part.split("_")[0] if "_" in version_part else version_part
    return full_name, version


def _npm_tarball_url(name: str, version: str) -> str:
    """Compute the npm tarball URL for a package."""
    # For scoped packages: @scope/name -> @scope/name/-/name-version.tgz
    # For unscoped: name -> name/-/name-version.tgz
    basename = name.rsplit("/", maxsplit=1)[-1]
    return f"{NPM_REGISTRY}/{name}/-/{basename}-{version}.tgz"


def _resolve_all_npm(lock_npm: dict[str, dict[str, object]]) -> list[NpmPackage]:
    """Resolve all npm packages from the lock file."""
    seen: set[tuple[str, str]] = set()
    packages: list[NpmPackage] = []

    for pkg_key, pkg_info in lock_npm.items():
        name, version = _parse_npm_pkg_key(pkg_key)
        key = (name, version)
        if key in seen:
            continue
        seen.add(key)

        tarball_url = _npm_tarball_url(name, version)
        cache_path = f"npm/registry.npmjs.org/{name}/{version}"

        packages.append(
            NpmPackage(
                name=name,
                version=version,
                integrity=_get_required_str(
                    pkg_info,
                    "integrity",
                    context=f"npm package {pkg_key}",
                ),
                tarball_url=tarball_url,
                cache_path=cache_path,
            )
        )

    return sorted(packages, key=lambda p: (p.name, p.version))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_deno_deps(lock_path: Path) -> DenoDepManifest:
    """Resolve all dependencies from a ``deno.lock`` file.

    Parameters
    ----------
    lock_path:
        Path to the ``deno.lock`` file.

    Returns
    -------
    DenoDepManifest
        A flat manifest of all JSR files and npm tarballs to fetch.

    """
    with lock_path.open() as f:
        lock = _as_object_dict(json.load(f), context="deno.lock")

    version = str(lock.get("version", ""))
    if version not in ("4", "5"):
        log.warning("Unexpected deno.lock version %s (expected 4 or 5)", version)

    lock_jsr = _as_package_map(lock.get("jsr", {}), context="deno.lock.jsr")
    lock_npm = _as_package_map(lock.get("npm", {}), context="deno.lock.npm")

    log.info(
        "Resolving %d JSR + %d npm packages from %s",
        len(lock_jsr),
        len(lock_npm),
        lock_path,
    )

    jsr_packages = await _resolve_all_jsr(lock_jsr)
    npm_packages = _resolve_all_npm(lock_npm)

    total_jsr_files = sum(len(p.files) for p in jsr_packages)
    log.info(
        "Resolved %d JSR packages (%d files) + %d npm packages",
        len(jsr_packages),
        total_jsr_files,
        len(npm_packages),
    )

    return DenoDepManifest(
        lock_version=version,
        jsr_packages=jsr_packages,
        npm_packages=npm_packages,
    )


def resolve_deno_deps_sync(lock_path: Path) -> DenoDepManifest:
    """Run :func:`resolve_deno_deps` synchronously."""
    return asyncio.run(resolve_deno_deps(lock_path))
