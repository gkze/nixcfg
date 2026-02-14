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
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

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

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        return asdict(self)

    def save(self, path: Path) -> None:
        """Write the manifest to *path*."""
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
        path.write_text(payload)

    @classmethod
    def load(cls, path: Path) -> DenoDepManifest:
        """Load a manifest from *path*."""
        data = json.loads(path.read_text())
        return cls(
            lock_version=data["lock_version"],
            jsr_packages=[
                JsrPackage(
                    name=p["name"],
                    version=p["version"],
                    integrity=p["integrity"],
                    files=[JsrFile(**f) for f in p["files"]],
                )
                for p in data.get("jsr_packages", [])
            ],
            npm_packages=[NpmPackage(**p) for p in data.get("npm_packages", [])],
        )


# ---------------------------------------------------------------------------
# Deno cache path computation
# ---------------------------------------------------------------------------


def _url_to_cache_path(url: str) -> str:
    """Compute the Deno cache file path for an HTTPS URL.

    Deno stores cached files at ``remote/{scheme}/{host}/{sha256_of_path}``.
    The hash is computed over the *raw* URL path + query (fragment ignored).
    """
    # Parse URL manually (avoid urllib overhead for this simple case)
    if not url.startswith("https://"):
        msg = f"Expected https URL: {url}"
        raise ValueError(msg)
    rest = url[len("https://") :]
    host, _, path = rest.partition("/")
    path = "/" + path
    # Strip fragment
    path = path.split("#")[0]
    path_hash = hashlib.sha256(path.encode()).hexdigest()
    return f"remote/https/{host}/{path_hash}"


def _guess_media_type(path: str) -> str:
    """Guess the MIME type Deno uses for a file based on extension."""
    if path.endswith((".ts", ".tsx")):
        return "text/typescript"
    if path.endswith((".js", ".jsx", ".mjs")):
        return "text/javascript"
    if path.endswith(".json"):
        return "application/json"
    if path.endswith(".wasm"):
        return "application/wasm"
    return "text/plain"


# ---------------------------------------------------------------------------
# JSR resolution
# ---------------------------------------------------------------------------


async def _fetch_jsr_meta(
    client: httpx.AsyncClient,
    scope: str,
    name: str,
    version: str,
) -> dict[str, Any]:
    """Fetch the ``_meta.json`` for a JSR package version."""
    url = f"{JSR_REGISTRY}/{scope}/{name}/{version}_meta.json"
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.json()


async def _resolve_jsr_package(
    client: httpx.AsyncClient,
    pkg_key: str,
    pkg_info: dict[str, Any],
) -> JsrPackage:
    """Resolve a single JSR package into its files."""
    # pkg_key is like "@cliffy/ansi@1.0.0-rc.8"
    scope, rest = pkg_key.split("/", 1)
    name, version = rest.rsplit("@", 1)

    meta = await _fetch_jsr_meta(client, scope, name, version)
    manifest = meta.get("manifest", {})

    files: list[JsrFile] = []
    for file_path, file_info in sorted(manifest.items()):
        url = f"{JSR_REGISTRY}/{scope}/{name}/{version}{file_path}"
        checksum = file_info["checksum"]
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
        meta_resp = await client.get(meta_url)
        meta_resp.raise_for_status()
        meta_sha256 = hashlib.sha256(meta_resp.content).hexdigest()
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
        integrity=pkg_info["integrity"],
        files=files,
    )


async def _resolve_all_jsr(
    lock_jsr: dict[str, Any],
) -> list[JsrPackage]:
    """Resolve all JSR packages from the lock file."""
    sem = asyncio.Semaphore(JSR_FETCH_CONCURRENCY)

    async def _with_sem(coro: Coroutine[None, None, JsrPackage]) -> JsrPackage:
        async with sem:
            return await coro

    async with httpx.AsyncClient(timeout=30.0) as client:
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


def _resolve_all_npm(lock_npm: dict[str, Any]) -> list[NpmPackage]:
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
                integrity=pkg_info["integrity"],
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
        lock = json.load(f)

    version = str(lock.get("version", ""))
    if version not in ("4", "5"):
        log.warning("Unexpected deno.lock version %s (expected 4 or 5)", version)

    lock_jsr = lock.get("jsr", {})
    lock_npm = lock.get("npm", {})

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
