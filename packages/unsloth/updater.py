"""Source-only release updater for the blocked Unsloth Desktop foundation."""

import ast
import base64
import binascii
import datetime
import gzip
import hashlib
import io
import json
import posixpath
import re
import tarfile
import urllib.parse
import zlib
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update import nix as update_nix
from lib.update import process as update_process
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_hash_mapping,
    expect_str,
    require_value,
)
from lib.update.net import fetch_github_api, fetch_json, fetch_url, github_raw_url
from lib.update.nix import _build_fetch_from_github_expr
from lib.update.updaters import (
    GitHubReleaseUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import metadata_as_mapping

if TYPE_CHECKING:
    from typing import BinaryIO

    import aiohttp

    from lib.update.events import EventStream


class _BinaryReader(Protocol):
    def read(self, size: int = -1) -> bytes: ...


_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_HEX_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PYPI_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){2}$")
_NPM_COMMAND_PATTERN = re.compile(r"\bnpm\b", re.IGNORECASE)
_MANIFEST_ASSET_NAME = "latest.json"
_DARWIN_PLATFORM = "darwin-aarch64"
_EXPECTED_HASH_ENTRY_COUNT = 3
_EXPECTED_URL_HASH_ENTRY_COUNT = 2
_MAX_BACKEND_SDIST_BYTES = 256 * 1024 * 1024
_MAX_BACKEND_SDIST_MEMBERS = 50_000
_MAX_BACKEND_SDIST_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
_MAX_OXC_SOURCE_BYTES = 1024 * 1024
_OXC_PACKAGE_PATH = "studio/backend/core/data_recipe/oxc-validator/package.json"
_OXC_LOCK_PATH = "studio/backend/core/data_recipe/oxc-validator/package-lock.json"
_OXC_VALIDATE_PATH = "studio/backend/core/data_recipe/oxc-validator/validate.mjs"
_OXC_CALLER_PATH = "studio/backend/core/data_recipe/local_callable_validators.py"
_SETUP_SH_PATH = "studio/setup.sh"
_SETUP_PS1_PATH = "studio/setup.ps1"
_SETUP_SH_OXC_START = "# ── oxc-validator runtime ──"
_SETUP_SH_OXC_END = "# ── Python venv + deps ──"
_SETUP_SH_OXC_INSTALL = (
    'run_quiet_no_exit "npm install (oxc validator runtime)" '
    "npm install --no-fund --no-audit --loglevel=error "
    '"${_NPM_REGISTRY_ARGS[@]+"${_NPM_REGISTRY_ARGS[@]}"}" '
    "|| _oxc_install_rc=$?"
)
_SETUP_SH_OXC_GUARD = (
    'if [ -d "$_OXC_DIR" ] && [ "${NODE_SOURCE:-}" != skip ] '
    "&& command -v npm &>/dev/null; then"
)
_SETUP_SH_OXC_SKIP = (
    'substep "OXC validator runtime skipped (no npm found); code validation '
    'degrades until Node is available" "$C_WARN"'
)
_SETUP_SH_OXC_INSTALL_COMMENT = (
    "# Node, so do not run npm install against an unsuitable/absent system Node."
)
_SETUP_SH_OXC_SKIP_COMMENT = (
    "# No npm on PATH: skip rather than abort; the backend Node resolver degrades"
)
_SETUP_PS1_OXC_START = (
    'if ((Test-Path $OxcValidatorDir) -and $NodeSource -ne "skip" -and '
    "(Get-Command npm -ErrorAction SilentlyContinue)) {"
)
_SETUP_PS1_OXC_END = "Remove-AgentInstructionFiles -Roots @("
_SETUP_PS1_OXC_INSTALL = (
    "$oxcInstallExit = Invoke-SetupCommand { npm install @NpmRegistryArgs }"
)
_SETUP_PS1_OXC_ERROR = (
    'Write-StudioLine "[ERROR] OXC validator npm install failed '
    '(exit code $oxcInstallExit)" -ForegroundColor Red'
)
_SETUP_PS1_OXC_SKIP = (
    'substep "OXC validator runtime skipped (no npm found); code validation '
    'degrades until Node is available" "Yellow"'
)
_SETUP_PS1_OXC_SKIP_COMMENT = (
    "# No npm on PATH (e.g. a pip install with no system Node and no isolated Node"
)
_SETUP_SH_OXC_NPM_LINES = (
    _SETUP_SH_OXC_INSTALL_COMMENT,
    _SETUP_SH_OXC_GUARD,
    _SETUP_SH_OXC_INSTALL,
    _SETUP_SH_OXC_SKIP_COMMENT,
    _SETUP_SH_OXC_SKIP,
)
_SETUP_PS1_OXC_NPM_LINES = (
    _SETUP_PS1_OXC_INSTALL,
    _SETUP_PS1_OXC_ERROR,
    _SETUP_PS1_OXC_SKIP_COMMENT,
    _SETUP_PS1_OXC_SKIP,
)
_OXC_LOCKFILE_VERSION = 3
_OXC_PACKAGE_IDENTITY: dict[str, object] = {
    "name": "unsloth-oxc-validator-runtime",
    "private": True,
    "type": "module",
    "version": "0.0.1",
}
_OXC_SOURCE_DIGESTS = {
    _OXC_PACKAGE_PATH: "1f77ca9c792bb1b104724a27b142bfd58ca3fe38770d4320c91be75a6883e69e",
    _OXC_LOCK_PATH: (
        "67221354b08c9ff2437f976b54412ce45849ccd4c6373a1bcca6ae9e69705cc2"
    ),
    _OXC_VALIDATE_PATH: (
        "d5f06d9e7c51340cd80f2d8f76e8c5398870f640f82ea2f2ea3d926baeca94ad"
    ),
    _OXC_CALLER_PATH: (
        "14d66234fd2e54bb1df8330eee49cc105f530b3a5429235e6cfed93e7a32c0eb"
    ),
    _SETUP_SH_PATH: (
        "4e4b4f0baf205ce125ae0948c40c6376b7ab1133a2872148d4122c80f9c8e32a"
    ),
    _SETUP_PS1_PATH: (
        "03c161c431b44d5d1d1ac9fd7c555302b58e8b9cceb31decee8562a44a159c70"
    ),
}
_OXC_DEPENDENCIES = {"oxc-parser": "^0.131.0", "oxlint": "^1.65.0"}
_OXC_BINDING_TARGETS = (
    "android-arm-eabi",
    "android-arm64",
    "darwin-arm64",
    "darwin-x64",
    "freebsd-x64",
    "linux-arm-gnueabihf",
    "linux-arm-musleabihf",
    "linux-arm64-gnu",
    "linux-arm64-musl",
    "linux-ppc64-gnu",
    "linux-riscv64-gnu",
    "linux-riscv64-musl",
    "linux-s390x-gnu",
    "linux-x64-gnu",
    "linux-x64-musl",
    "openharmony-arm64",
    "win32-arm64-msvc",
    "win32-ia32-msvc",
    "win32-x64-msvc",
)
_OXC_OPTIONAL_DEPENDENCIES = {
    "node_modules/oxc-parser": {
        f"@oxc-parser/binding-{target}": "0.131.0"
        for target in (*_OXC_BINDING_TARGETS, "wasm32-wasi")
    },
    "node_modules/oxlint": {
        f"@oxlint/binding-{target}": "1.65.0" for target in _OXC_BINDING_TARGETS
    },
}
_OXC_RUNTIME_DEPENDENCIES = {
    "node_modules/oxc-parser": {"@oxc-project/types": "^0.131.0"},
    "node_modules/oxlint": None,
}
_OXC_PROJECT_TYPES_PATH = "node_modules/@oxc-project/types"
_OXC_PROJECT_TYPES = {
    "funding": {"url": "https://github.com/sponsors/Boshen"},
    "integrity": (
        "sha512-PgnWDfV0h+b16XNKbXU7Daib/BFSt/J2mEzfYIBu6JB/wNdlU+kVYXCk"
        "GA1A9fWkTbOgbjh4e6NhPeQOYvFhEA=="
    ),
    "license": "MIT",
    "resolved": "https://registry.npmjs.org/@oxc-project/types/-/types-0.131.0.tgz",
    "version": "0.131.0",
}


def _require_string(mapping: dict[str, object], key: str, *, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        msg = f"Unsloth {context} is missing {key}"
        raise TypeError(msg)
    return value


def _require_positive_int(mapping: dict[str, object], key: str, *, context: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        msg = f"Unsloth {context} has invalid {key}"
        raise TypeError(msg)
    return value


def _require_object(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"Unsloth {context} is not a JSON object"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _sri_from_hex(digest: str) -> str:
    if _HEX_SHA256_PATTERN.fullmatch(digest) is None:
        msg = f"Unsloth metadata has invalid SHA-256 digest {digest!r}"
        raise RuntimeError(msg)
    return f"sha256-{base64.b64encode(bytes.fromhex(digest)).decode()}"


def _require_sha512_sri(
    mapping: dict[str, object],
    key: str,
    *,
    context: str,
) -> str:
    value = _require_string(mapping, key, context=context)
    if not value.startswith("sha512-"):
        msg = f"Unsloth {context} has invalid {key}"
        raise RuntimeError(msg)
    try:
        digest = base64.b64decode(value.removeprefix("sha512-"), validate=True)
    except binascii.Error as exc:
        msg = f"Unsloth {context} has invalid {key}"
        raise RuntimeError(msg) from exc
    if len(digest) != hashlib.sha512().digest_size:
        msg = f"Unsloth {context} has invalid {key}"
        raise RuntimeError(msg)
    return value


def _calver_date(version: str, *, label: str) -> datetime.date:
    if _PYPI_VERSION_PATTERN.fullmatch(version) is None:
        msg = f"Unsloth release metadata has invalid {label} version {version!r}"
        raise RuntimeError(msg)
    try:
        return datetime.date(*(int(part) for part in version.split(".")))
    except ValueError as exc:
        msg = f"Unsloth release metadata has invalid {label} version {version!r}"
        raise RuntimeError(msg) from exc


def _source_python_version(source: bytes) -> str:
    tree = ast.parse(source.decode("utf-8"), filename="unsloth/_version.py")
    versions: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id == "__version__"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            versions.append(node.value.value)
    if len(versions) != 1:
        msg = "Unsloth source must define exactly one literal __version__"
        raise RuntimeError(msg)
    return versions[0]


def _audit_oxc_lock(payload: bytes) -> None:
    lock = _require_object(
        json.loads(payload.decode("utf-8")),
        context="OXC validator package-lock.json",
    )
    if (
        lock.get("name") != _OXC_PACKAGE_IDENTITY["name"]
        or lock.get("version") != _OXC_PACKAGE_IDENTITY["version"]
        or lock.get("lockfileVersion") != _OXC_LOCKFILE_VERSION
    ):
        msg = "Unsloth OXC validator lock identity drifted"
        raise RuntimeError(msg)
    locked_packages = _require_object(
        lock.get("packages"),
        context="OXC validator locked packages",
    )
    lock_root = _require_object(
        locked_packages.get(""),
        context="OXC validator lock root",
    )
    if lock_root != {
        "dependencies": _OXC_DEPENDENCIES,
        "name": _OXC_PACKAGE_IDENTITY["name"],
        "version": _OXC_PACKAGE_IDENTITY["version"],
    }:
        msg = "Unsloth OXC validator lock root drifted"
        raise RuntimeError(msg)

    locked_runtimes = {
        "node_modules/oxc-parser": (
            "0.131.0",
            "https://registry.npmjs.org/oxc-parser/-/oxc-parser-0.131.0.tgz",
            (
                "sha512-SJ3/7ZPbgie8dr5Z9BI/M51zZbpXba+hRSG0MDzVwMW5CRQg2fjY"
                "E0jHGlLX4eeiibGgC/mzoDFKSDHwVZEHRQ=="
            ),
            "@oxc-parser/binding-darwin-arm64",
            (
                "sha512-jukuV6xe5RbQKFo7QD34NDCLDZp4PSOm8rmckhNdH/60ymG5zXbDz"
                "GBEyc+nTkuLQNama2aSGCt+CPfpjNTqyw=="
            ),
        ),
        "node_modules/oxlint": (
            "1.65.0",
            "https://registry.npmjs.org/oxlint/-/oxlint-1.65.0.tgz",
            (
                "sha512-ChUuE3Q7XnAbscvT4XLMsH7HFJmLgLVv9lu+RRgFL5wSXnDqUOzT"
                "p5IS8qWDBGd/ZDSzQ2tbX8fjAmijlGLC7A=="
            ),
            "@oxlint/binding-darwin-arm64",
            (
                "sha512-pL/mG/5gMzBwp1gdc5+Cwi87F9j3XRnPxHGyVj5Zd+dCEV5YkKt0"
                "L70PB3EGmEEHxgn4H+jnMS3xLuXs6mZW/Q=="
            ),
        ),
    }
    for path, (
        version,
        resolved,
        runtime_integrity,
        darwin_dependency,
        binding_integrity,
    ) in locked_runtimes.items():
        locked_runtime = _require_object(
            locked_packages.get(path),
            context=f"OXC locked runtime {path}",
        )
        optional_dependencies = _require_object(
            locked_runtime.get("optionalDependencies"),
            context=f"OXC optional dependencies for {path}",
        )
        if (
            locked_runtime.get("version") != version
            or locked_runtime.get("resolved") != resolved
            or optional_dependencies != _OXC_OPTIONAL_DEPENDENCIES[path]
            or locked_runtime.get("dependencies") != _OXC_RUNTIME_DEPENDENCIES[path]
            or _require_sha512_sri(
                locked_runtime,
                "integrity",
                context=f"OXC locked runtime {path}",
            )
            != runtime_integrity
        ):
            msg = f"Unsloth OXC locked runtime drifted: {path}"
            raise RuntimeError(msg)

        binding_path = f"node_modules/{darwin_dependency}"
        binding = _require_object(
            locked_packages.get(binding_path),
            context=f"OXC darwin-arm64 binding {binding_path}",
        )
        binding_name = darwin_dependency.rsplit("/", maxsplit=1)[1]
        expected_binding_url = (
            "https://registry.npmjs.org/"
            f"{darwin_dependency}/-/{binding_name}-{version}.tgz"
        )
        if (
            binding.get("version") != version
            or binding.get("resolved") != expected_binding_url
            or binding.get("optional") is not True
            or binding.get("cpu") != ["arm64"]
            or binding.get("os") != ["darwin"]
            or _require_sha512_sri(
                binding,
                "integrity",
                context=f"OXC darwin-arm64 binding {binding_path}",
            )
            != binding_integrity
        ):
            msg = f"Unsloth OXC darwin-arm64 binding drifted: {binding_path}"
            raise RuntimeError(msg)

    project_types = _require_object(
        locked_packages.get(_OXC_PROJECT_TYPES_PATH),
        context="OXC project types",
    )
    if (
        _require_sha512_sri(
            project_types,
            "integrity",
            context="OXC project types",
        )
        != _OXC_PROJECT_TYPES["integrity"]
        or project_types != _OXC_PROJECT_TYPES
    ):
        msg = "Unsloth OXC project types drifted"
        raise RuntimeError(msg)


def _npm_source_lines(block: str) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in block.splitlines()
        if _NPM_COMMAND_PATTERN.search(line) is not None
    )


def _audit_oxc_runtime_sources(payloads: dict[str, bytes]) -> None:
    setup_sh = payloads[_SETUP_SH_PATH].decode("utf-8")
    setup_sh_blocks = re.findall(
        rf"(?ms)^{re.escape(_SETUP_SH_OXC_START)}$\n"
        rf"(.*?)^{re.escape(_SETUP_SH_OXC_END)}$",
        setup_sh,
    )
    if len(setup_sh_blocks) != 1:
        msg = "Unsloth OXC setup.sh npm install mutation drifted"
        raise RuntimeError(msg)
    setup_sh_installs = _npm_source_lines(setup_sh_blocks[0])
    if setup_sh_installs != _SETUP_SH_OXC_NPM_LINES:
        msg = "Unsloth OXC setup.sh npm install mutation drifted"
        raise RuntimeError(msg)

    setup_ps1 = payloads[_SETUP_PS1_PATH].decode("utf-8")
    setup_ps1_blocks = re.findall(
        rf"(?ms)^{re.escape(_SETUP_PS1_OXC_START)}$\n"
        rf"(.*?)^{re.escape(_SETUP_PS1_OXC_END)}$",
        setup_ps1,
    )
    if len(setup_ps1_blocks) != 1:
        msg = "Unsloth OXC setup.ps1 npm install mutation drifted"
        raise RuntimeError(msg)
    setup_ps1_installs = _npm_source_lines(setup_ps1_blocks[0])
    if setup_ps1_installs != _SETUP_PS1_OXC_NPM_LINES:
        msg = "Unsloth OXC setup.ps1 npm install mutation drifted"
        raise RuntimeError(msg)

    validator = payloads[_OXC_VALIDATE_PATH].decode("utf-8")
    validator_contract = (
        'import { spawnSync } from "node:child_process";',
        'import { parseSync } from "oxc-parser";',
        'const oxlintBin = join(TOOL_DIR, "node_modules", ".bin", "oxlint");',
        "const exec = spawnSync(oxlintBin, oxlintArgs, {",
    )
    if any(validator.count(marker) != 1 for marker in validator_contract):
        msg = "Unsloth OXC validator entrypoint drifted"
        raise RuntimeError(msg)

    timeout_contract = {
        'import { performance } from "node:perf_hooks";': 1,
        "const OXLINT_DEFAULT_BUDGET_MS = 30_000;": 1,
        "const OXLINT_BUDGET_MARGIN_MS = 2_000;": 1,
        "const OXLINT_MIN_TIMEOUT_MS = 1_000;": 1,
        (
            "return Number.isFinite(parsed) && parsed > 0 "
            "? Math.floor(parsed) : OXLINT_DEFAULT_BUDGET_MS;"
        ): 1,
        "budgetMs - OXLINT_BUDGET_MARGIN_MS - performance.now()": 1,
        "if (timeoutMs < OXLINT_MIN_TIMEOUT_MS) {": 1,
        "oxlint skipped: validation budget exhausted": 1,
        "timeout: timeoutMs,": 1,
        'killSignal: "SIGKILL",': 1,
        "runLintBatch(entries, oxlintBudgetMs)": 1,
        "runLintBatch(lintTargets, oxlintBudgetMs)": 1,
        "const oxlintBudgetMs = mapBudgetMs(payload?.timeout_ms);": 1,
        "runValidation({ codes, lang, mode, codeShape, oxlintBudgetMs })": 2,
    }
    if any(
        validator.count(marker) != expected_count
        for marker, expected_count in timeout_contract.items()
    ):
        msg = "Unsloth OXC validator timeout contract drifted"
        raise RuntimeError(msg)

    caller = payloads[_OXC_CALLER_PATH].decode("utf-8")
    caller_timeout_contract = (
        "_OXC_TIMEOUT_S = 30",
        '"timeout_ms": int(_OXC_TIMEOUT_S * 1000),',
        "timeout = _OXC_TIMEOUT_S,",
        "except subprocess.TimeoutExpired:",
        '"OXC validation timed out"',
    )
    if any(caller.count(marker) != 1 for marker in caller_timeout_contract):
        msg = "Unsloth OXC caller timeout contract drifted"
        raise RuntimeError(msg)


def _oxc_source_audit(payloads: dict[str, bytes]) -> str:
    for path, expected_digest in _OXC_SOURCE_DIGESTS.items():
        _sri_from_hex(expected_digest)
        if hashlib.sha256(payloads[path]).hexdigest() != expected_digest:
            msg = f"Unsloth OXC source digest does not match {path}"
            raise RuntimeError(msg)

    package = _require_object(
        json.loads(payloads[_OXC_PACKAGE_PATH].decode("utf-8")),
        context="OXC validator package.json",
    )
    if {
        key: package.get(key) for key in _OXC_PACKAGE_IDENTITY
    } != _OXC_PACKAGE_IDENTITY:
        msg = "Unsloth OXC validator package identity drifted"
        raise RuntimeError(msg)
    dependencies = _require_object(
        package.get("dependencies"),
        context="OXC validator dependencies",
    )
    if dependencies != _OXC_DEPENDENCIES:
        msg = "Unsloth OXC validator dependencies drifted"
        raise RuntimeError(msg)
    if set(package) != {*_OXC_PACKAGE_IDENTITY, "dependencies"}:
        msg = "Unsloth OXC validator package shape drifted"
        raise RuntimeError(msg)

    _audit_oxc_lock(payloads[_OXC_LOCK_PATH])
    _audit_oxc_runtime_sources(payloads)

    contract = json.dumps(
        _OXC_SOURCE_DIGESTS,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(contract).hexdigest()


class _ByteBudgetReader:
    """Expose a streaming reader that fails before its byte budget is exceeded."""

    def __init__(self, reader: _BinaryReader, *, max_bytes: int) -> None:
        self._reader = reader
        self._max_bytes = max_bytes
        self._bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        remaining = self._max_bytes - self._bytes_read
        request_size = remaining + 1 if size < 0 else min(size, remaining + 1)
        payload = self._reader.read(request_size)
        self._bytes_read += len(payload)
        if self._bytes_read > self._max_bytes:
            msg = "Unsloth backend sdist exceeds the expanded size limit"
            raise RuntimeError(msg)
        return payload


def _oxc_sources_from_backend_sdist(
    payload: bytes,
    *,
    version: str,
) -> dict[str, bytes]:
    """Read the exact audited runtime sources from the packaged PyPI sdist."""
    archive_paths = {f"unsloth-{version}/{path}": path for path in _OXC_SOURCE_DIGESTS}
    sources: dict[str, bytes] = {}
    member_count = 0
    expanded_size = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as decompressed:
            bounded = _ByteBudgetReader(
                decompressed,
                max_bytes=_MAX_BACKEND_SDIST_EXPANDED_BYTES,
            )
            with tarfile.open(
                fileobj=cast("BinaryIO", bounded),
                mode="r|",
            ) as archive:
                for member in archive:
                    member_count += 1
                    if member_count > _MAX_BACKEND_SDIST_MEMBERS:
                        msg = "Unsloth backend sdist exceeds the archive member limit"
                        raise RuntimeError(msg)
                    expanded_size += member.size
                    if expanded_size > _MAX_BACKEND_SDIST_EXPANDED_BYTES:
                        msg = "Unsloth backend sdist exceeds the expanded size limit"
                        raise RuntimeError(msg)

                    canonical_name = posixpath.normpath(member.name).lstrip("/")
                    source_path = archive_paths.get(canonical_name)
                    if source_path is None:
                        continue
                    if (
                        member.name != canonical_name
                        or source_path in sources
                        or not member.isfile()
                    ):
                        msg = (
                            "Unsloth backend sdist OXC source archive is ambiguous: "
                            f"{source_path}"
                        )
                        raise RuntimeError(msg)
                    if member.size > _MAX_OXC_SOURCE_BYTES:
                        msg = (
                            "Unsloth backend sdist exceeds the audited source size limit: "
                            f"{source_path}"
                        )
                        raise RuntimeError(msg)
                    extracted = cast("BinaryIO", archive.extractfile(member))
                    sources[source_path] = extracted.read()
    except (EOFError, OSError, tarfile.TarError, zlib.error) as exc:
        msg = "Unsloth backend sdist OXC source archive is invalid"
        raise RuntimeError(msg) from exc

    missing = sorted(set(_OXC_SOURCE_DIGESTS) - sources.keys())
    if missing:
        msg = f"Unsloth backend sdist OXC source is missing {missing[0]}"
        raise RuntimeError(msg)
    return sources


@register_updater
class UnslothUpdater(GitHubReleaseUpdater):
    """Track exact public desktop and backend sources behind closure gates."""

    name = "unsloth"
    GITHUB_OWNER = "unslothai"
    GITHUB_REPO = "unsloth"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    derivation_validations = (
        DerivationValidation(
            installable=("path:.#pkgs.{system}.{name}.storePathAppCandidateSmoke"),
            systems=(DARWIN_PLATFORM,),
            mode="build",
        ),
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=(DARWIN_PLATFORM,),
            mode="build",
        ),
    )

    @staticmethod
    def _release_manifest_asset(
        payload: dict[str, object],
        *,
        tag_name: str,
    ) -> tuple[str, str, int]:
        assets = payload.get("assets")
        if not isinstance(assets, list):
            msg = f"Unsloth release {tag_name} has no asset list"
            raise TypeError(msg)
        matches = [
            _require_object(asset, context="release asset")
            for asset in assets
            if isinstance(asset, dict)
            and cast("dict[str, object]", asset).get("name") == _MANIFEST_ASSET_NAME
        ]
        if len(matches) != 1:
            msg = (
                f"Unsloth release {tag_name} must contain exactly one "
                f"{_MANIFEST_ASSET_NAME} asset"
            )
            raise RuntimeError(msg)
        asset = matches[0]
        url = _require_string(asset, "browser_download_url", context="release asset")
        expected_url = (
            f"https://github.com/{UnslothUpdater.GITHUB_OWNER}/"
            f"{UnslothUpdater.GITHUB_REPO}/releases/download/{tag_name}/"
            f"{_MANIFEST_ASSET_NAME}"
        )
        if url != expected_url:
            msg = f"Unsloth release manifest URL is not tag-pinned: {url}"
            raise RuntimeError(msg)
        digest = _require_string(asset, "digest", context="release asset")
        digest_prefix = "sha256:"
        if not digest.startswith(digest_prefix):
            msg = "Unsloth release manifest has no authoritative SHA-256 digest"
            raise RuntimeError(msg)
        digest_hex = digest.removeprefix(digest_prefix)
        _sri_from_hex(digest_hex)
        size = _require_positive_int(asset, "size", context="release asset")
        return url, digest_hex, size

    @staticmethod
    def _validate_manifest(
        payload: bytes,
        *,
        version: str,
        tag_name: str,
        digest_hex: str,
        size: int,
    ) -> str:
        if len(payload) != size:
            msg = "Unsloth release manifest size does not match GitHub metadata"
            raise RuntimeError(msg)
        if hashlib.sha256(payload).hexdigest() != digest_hex:
            msg = "Unsloth release manifest digest does not match GitHub metadata"
            raise RuntimeError(msg)
        manifest = _require_object(
            json.loads(payload.decode("utf-8")),
            context="release manifest",
        )
        manifest_version = _require_string(
            manifest, "version", context="release manifest"
        )
        if manifest_version != version:
            msg = (
                f"Unsloth manifest version {manifest_version!r} does not match "
                f"release version {version!r}"
            )
            raise RuntimeError(msg)
        backend_version = _require_string(
            manifest,
            "pypi_version",
            context="release manifest",
        )
        if _PYPI_VERSION_PATTERN.fullmatch(backend_version) is None:
            msg = f"Unsloth manifest has invalid PyPI version {backend_version!r}"
            raise RuntimeError(msg)
        platforms = _require_object(
            manifest.get("platforms"),
            context="release manifest platforms",
        )
        darwin = _require_object(
            platforms.get(_DARWIN_PLATFORM),
            context="darwin-aarch64 manifest entry",
        )
        artifact_url = _require_string(darwin, "url", context="darwin manifest entry")
        signature = _require_string(
            darwin,
            "signature",
            context="darwin manifest entry",
        )
        expected_prefix = (
            f"https://github.com/{UnslothUpdater.GITHUB_OWNER}/"
            f"{UnslothUpdater.GITHUB_REPO}/releases/download/{tag_name}/"
        )
        if not artifact_url.startswith(expected_prefix):
            msg = f"Unsloth darwin artifact URL is not tag-pinned: {artifact_url}"
            raise RuntimeError(msg)
        if not signature.strip():
            msg = "Unsloth darwin manifest signature is empty"
            raise RuntimeError(msg)
        return backend_version

    @staticmethod
    def _backend_sdist(
        payload: object,
        *,
        version: str,
    ) -> tuple[str, str, int]:
        metadata = _require_object(payload, context="PyPI response")
        info = _require_object(metadata.get("info"), context="PyPI info")
        if _require_string(info, "version", context="PyPI info") != version:
            msg = f"Unsloth PyPI response does not describe backend {version}"
            raise RuntimeError(msg)
        urls = metadata.get("urls")
        if not isinstance(urls, list):
            msg = "Unsloth PyPI response has no file list"
            raise TypeError(msg)
        expected_filename = f"unsloth-{version}.tar.gz"
        candidates = [
            _require_object(item, context="PyPI file")
            for item in urls
            if isinstance(item, dict)
            and cast("dict[str, object]", item).get("packagetype") == "sdist"
            and cast("dict[str, object]", item).get("filename") == expected_filename
        ]
        if len(candidates) != 1:
            msg = f"Unsloth backend {version} must have exactly one public source sdist"
            raise RuntimeError(msg)
        sdist = candidates[0]
        url = _require_string(sdist, "url", context="PyPI sdist")
        if not url.startswith("https://files.pythonhosted.org/"):
            msg = f"Unsloth backend sdist is not hosted by PyPI: {url}"
            raise RuntimeError(msg)
        digests = _require_object(sdist.get("digests"), context="PyPI sdist digests")
        digest_hex = _require_string(digests, "sha256", context="PyPI sdist digests")
        _sri_from_hex(digest_hex)
        size = _require_positive_int(sdist, "size", context="PyPI sdist")
        if size > _MAX_BACKEND_SDIST_BYTES:
            msg = "Unsloth backend sdist exceeds the audit size limit"
            raise RuntimeError(msg)
        return url, digest_hex, size

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve one coherent public desktop release and backend sdist."""
        payload = await self._fetch_latest_release_payload(session)
        tag_name = self._release_tag_from_payload(payload)
        version = self._normalize_release_version(tag_name)
        tag_path = urllib.parse.quote(tag_name, safe="")
        commit_payload = await fetch_github_api(
            session,
            f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/commits/{tag_path}",
            config=self.config,
        )
        commit_object = _require_object(commit_payload, context="commit response")
        commit = _require_string(commit_object, "sha", context="commit response")
        if _COMMIT_PATTERN.fullmatch(commit) is None:
            msg = f"Unsloth release {tag_name} has no immutable source commit"
            raise RuntimeError(msg)

        manifest_url, manifest_digest_hex, manifest_size = self._release_manifest_asset(
            payload,
            tag_name=tag_name,
        )
        manifest_bytes = await fetch_url(session, manifest_url, config=self.config)
        backend_version = self._validate_manifest(
            manifest_bytes,
            version=version,
            tag_name=tag_name,
            digest_hex=manifest_digest_hex,
            size=manifest_size,
        )
        source_version_bytes = await fetch_url(
            session,
            github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                commit,
                "unsloth/_version.py",
            ),
            config=self.config,
        )
        source_python_version = _source_python_version(source_version_bytes)
        oxc_source_payloads = {
            path: await fetch_url(
                session,
                github_raw_url(
                    self.GITHUB_OWNER,
                    self.GITHUB_REPO,
                    commit,
                    path,
                ),
                config=self.config,
            )
            for path in _OXC_SOURCE_DIGESTS
        }
        oxc_source_audit = _oxc_source_audit(oxc_source_payloads)
        pypi_payload = await fetch_json(
            session,
            f"https://pypi.org/pypi/unsloth/{backend_version}/json",
            config=self.config,
        )
        backend_url, backend_digest_hex, backend_size = self._backend_sdist(
            pypi_payload,
            version=backend_version,
        )
        backend_bytes = await fetch_url(session, backend_url, config=self.config)
        if len(backend_bytes) != backend_size:
            msg = "Unsloth backend sdist does not match PyPI metadata"
            raise RuntimeError(msg)
        if hashlib.sha256(backend_bytes).hexdigest() != backend_digest_hex:
            msg = "Unsloth backend sdist does not match PyPI metadata"
            raise RuntimeError(msg)
        try:
            backend_oxc_sources = _oxc_sources_from_backend_sdist(
                backend_bytes,
                version=backend_version,
            )
            _oxc_source_audit(backend_oxc_sources)
        except RuntimeError as exc:
            msg = f"Unsloth backend sdist OXC source audit failed: {exc}"
            raise RuntimeError(msg) from exc
        return VersionInfo(
            version=version,
            metadata={
                "backendDigestHex": backend_digest_hex,
                "backendSize": backend_size,
                "backendUrl": backend_url,
                "backendVersion": backend_version,
                "commit": commit,
                "manifestDigestHex": manifest_digest_hex,
                "manifestSize": manifest_size,
                "manifestUrl": manifest_url,
                "oxcSourceAudit": oxc_source_audit,
                "sourcePythonVersion": source_python_version,
                "tag": tag_name,
            },
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Revalidate release evidence even when the version has not changed."""
        _ = (context, info)
        return False

    @classmethod
    def _src_expr(cls, commit: str) -> str:
        return _build_fetch_from_github_expr(
            cls.GITHUB_OWNER,
            cls.GITHUB_REPO,
            rev=commit,
            fetch_submodules=False,
        )

    @classmethod
    def _required_metadata(cls, info: VersionInfo) -> dict[str, object]:
        metadata = metadata_as_mapping(
            info.metadata, context="Unsloth release metadata"
        )
        required_strings = (
            "backendDigestHex",
            "backendUrl",
            "backendVersion",
            "commit",
            "manifestDigestHex",
            "manifestUrl",
            "oxcSourceAudit",
            "sourcePythonVersion",
            "tag",
        )
        for key in required_strings:
            _require_string(metadata, key, context="release metadata")
        for key in ("backendSize", "manifestSize"):
            _require_positive_int(metadata, key, context="release metadata")
        commit = cast("str", metadata["commit"])
        if _COMMIT_PATTERN.fullmatch(commit) is None:
            msg = "Unsloth release metadata is missing an immutable source commit"
            raise RuntimeError(msg)
        _sri_from_hex(cast("str", metadata["backendDigestHex"]))
        _sri_from_hex(cast("str", metadata["manifestDigestHex"]))
        expected_oxc_audit = hashlib.sha256(
            json.dumps(
                _OXC_SOURCE_DIGESTS,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if metadata["oxcSourceAudit"] != expected_oxc_audit:
            msg = "Unsloth release metadata lacks the exact OXC source audit"
            raise RuntimeError(msg)

        tag = cast("str", metadata["tag"])
        if tag != f"v{info.version}":
            msg = "Unsloth release metadata tag does not match version"
            raise RuntimeError(msg)

        manifest_url = cast("str", metadata["manifestUrl"])
        expected_manifest_url = (
            f"https://github.com/{cls.GITHUB_OWNER}/{cls.GITHUB_REPO}/"
            f"releases/download/{tag}/{_MANIFEST_ASSET_NAME}"
        )
        if manifest_url != expected_manifest_url:
            msg = "Unsloth release manifest URL does not match release tag"
            raise RuntimeError(msg)

        backend_version = cast("str", metadata["backendVersion"])
        backend_version_date = _calver_date(backend_version, label="backend")
        source_python_version = cast("str", metadata["sourcePythonVersion"])
        source_version_date = _calver_date(source_python_version, label="source Python")
        if source_version_date > backend_version_date:
            msg = "Unsloth source Python version cannot be newer than backend version"
            raise RuntimeError(msg)
        if (backend_version_date - source_version_date).days > 1:
            msg = (
                "Unsloth source Python version must match or immediately precede "
                "backend version"
            )
            raise RuntimeError(msg)

        backend_url = cast("str", metadata["backendUrl"])
        parsed_backend_url = urllib.parse.urlsplit(backend_url)
        if (
            parsed_backend_url.scheme != "https"
            or parsed_backend_url.netloc != "files.pythonhosted.org"
            or parsed_backend_url.query
            or parsed_backend_url.fragment
        ):
            msg = "Unsloth backend URL is not canonical PyPI source"
            raise RuntimeError(msg)
        backend_path_match = re.fullmatch(
            r"/packages/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}/([^/]+)",
            parsed_backend_url.path,
        )
        if backend_path_match is None:
            msg = "Unsloth backend URL is not canonical PyPI source"
            raise RuntimeError(msg)
        expected_backend_filename = f"unsloth-{backend_version}.tar.gz"
        if backend_path_match.group(1) != expected_backend_filename:
            msg = "Unsloth backend URL does not match backend version"
            raise RuntimeError(msg)
        return metadata

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash only the exact public source tree, manifest, and backend sdist."""
        _ = (session, context)
        metadata = self._required_metadata(info)
        commit = cast("str", metadata["commit"])

        source_drain = ValueDrain[str]()
        async for event in drain_value_events(
            update_nix.compute_fixed_output_hash(
                self.name,
                self._src_expr(commit),
                config=self.config,
            ),
            source_drain,
            parse=expect_str,
        ):
            yield event
        source_hash = require_value(source_drain, "Missing Unsloth srcHash output")

        manifest_url = cast("str", metadata["manifestUrl"])
        backend_url = cast("str", metadata["backendUrl"])
        url_drain = ValueDrain[dict[str, str]]()
        async for event in drain_value_events(
            update_process.compute_url_hashes(
                self.name,
                (manifest_url, backend_url),
                config=self.config,
            ),
            url_drain,
            parse=expect_hash_mapping,
        ):
            yield event
        url_hashes = require_value(url_drain, "Missing Unsloth URL hash output")
        expected_hashes = {
            manifest_url: _sri_from_hex(cast("str", metadata["manifestDigestHex"])),
            backend_url: _sri_from_hex(cast("str", metadata["backendDigestHex"])),
        }
        if url_hashes != expected_hashes:
            msg = "Unsloth URL hashes do not match authoritative release metadata"
            raise RuntimeError(msg)
        yield UpdateEvent.value(
            self.name,
            [
                HashEntry.create("srcHash", source_hash),
                HashEntry.create("sha256", url_hashes[manifest_url], url=manifest_url),
                HashEntry.create("sha256", url_hashes[backend_url], url=backend_url),
            ],
        )

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist only complete and authoritative foundation hashes."""
        metadata = self._required_metadata(info)
        collection = HashCollection.from_value(hashes)
        entries = collection.entries
        if entries is None:
            msg = "Unsloth updater requires structured source hash entries"
            raise TypeError(msg)
        manifest_url = cast("str", metadata["manifestUrl"])
        backend_url = cast("str", metadata["backendUrl"])
        expected = {
            ("sha256", manifest_url): _sri_from_hex(
                cast("str", metadata["manifestDigestHex"]),
            ),
            ("sha256", backend_url): _sri_from_hex(
                cast("str", metadata["backendDigestHex"]),
            ),
        }
        source_entries = [entry for entry in entries if entry.hash_type == "srcHash"]
        url_entries = [entry for entry in entries if entry.hash_type == "sha256"]
        if (
            len(entries) != _EXPECTED_HASH_ENTRY_COUNT
            or len(source_entries) != 1
            or len(url_entries) != _EXPECTED_URL_HASH_ENTRY_COUNT
        ):
            msg = "Unsloth updater requires one source hash and two URL hashes"
            raise RuntimeError(msg)
        actual = {(entry.hash_type, entry.url): entry.hash for entry in url_entries}
        if actual != expected:
            msg = "Unsloth persisted URL hashes do not match authoritative metadata"
            raise RuntimeError(msg)
        return SourceEntry(
            version=info.version,
            commit=cast("str", metadata["commit"]),
            urls={
                "backendSdist": backend_url,
                "releaseManifest": manifest_url,
            },
            hashes=collection,
        )


if __name__ == "__main__":  # pragma: no cover -- updater registry entrypoint
    _MESSAGE = "Run through nixcfg update"
    raise SystemExit(_MESSAGE)
