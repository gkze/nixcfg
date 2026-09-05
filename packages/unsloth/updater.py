"""Atomic release updater for the source-built Unsloth Desktop closure."""

import ast
import asyncio
import base64
import binascii
import datetime
import gzip
import hashlib
import io
import json
import posixpath
import re
import sys
import tarfile
import tempfile
import tomllib
import urllib.parse
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import StringPrimitive

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update import nix as update_nix
from lib.update import process as update_process
from lib.update.artifacts import GeneratedArtifact
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_command_result,
    expect_hash_mapping,
    expect_str,
    raise_failed_command,
    require_value,
)
from lib.update.net import fetch_github_api, fetch_json, fetch_url, github_raw_url
from lib.update.nix import _build_fetch_from_github_expr, _build_package_path_attr_expr
from lib.update.nix_expr import select_attrs
from lib.update.npm_semver import require_npm_version_matches_spec
from lib.update.paths import updater_dir_for
from lib.update.updaters import (
    GitHubReleaseUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.core import _coerce_context
from lib.update.updaters.materialization import MaterializesArtifactsMixin
from lib.update.updaters.metadata import metadata_as_mapping
from packages.unsloth.patch_nix_managed import (
    OXC_VALIDATOR_PATCH_SEAM,
    OXC_VALIDATOR_PATH,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import BinaryIO

    import aiohttp
    from nix_manipulator.expressions.expression import NixExpression

    from lib.update.events import EventStream


class _BinaryReader(Protocol):
    def read(self, size: int = -1) -> bytes: ...


_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_HEX_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PYPI_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){2}$")
_PYPI_UPLOAD_TIME_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?Z$"
)
_NIX_STORE_OUTPUT_PATTERN = re.compile(r"^/nix/store/[0-9a-z]{32}-[^/]+$")
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
_OXC_VALIDATE_PATH = OXC_VALIDATOR_PATH.as_posix()
_OXC_CALLER_PATH = "studio/backend/core/data_recipe/local_callable_validators.py"
_CARGO_MANIFEST_PATH = "studio/src-tauri/Cargo.toml"
_PYTHON_PROJECT_VERSION = "0.0.0"
_PYTHON_VERSION = "3.12"
_PYTHON_TARGET = (
    "sys_platform == 'darwin' and platform_machine == 'arm64' "
    f"and python_version == '{_PYTHON_VERSION}'"
)
_GENERATED_ARTIFACT_FILES = (
    "pyproject.toml",
    "uv.lock",
    "closure-hashes.json",
    "closure-plan.json",
    "artifact-validation.json",
)
_ARTIFACT_CHECKS = (
    "frontend-source-build",
    "oxc-valid-and-invalid-programs",
    "python-import-and-cli",
    "native-helper-arm64-and-help",
    "app-plist-architecture-signature",
    "store-path-app-candidate-backend-smoke",
    "contained-direct-store-path-app-runtime",
    "no-updater-or-runtime-installer-endpoints",
)
_RAW_RUNTIME_EVIDENCE_SCHEMA_VERSION = 2
_RUNTIME_EVIDENCE_SCHEMA_VERSION = 3
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
_OXC_PACKAGE_NAME = "unsloth-oxc-validator-runtime"
_OXC_SOURCE_PATHS = (
    _OXC_PACKAGE_PATH,
    _OXC_LOCK_PATH,
    _OXC_VALIDATE_PATH,
    _OXC_CALLER_PATH,
    _SETUP_SH_PATH,
    _SETUP_PS1_PATH,
)
_OXC_DARWIN_BINDINGS = {
    "oxc-parser": "@oxc-parser/binding-darwin-arm64",
    "oxlint": "@oxlint/binding-darwin-arm64",
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


def _npm_registry_url(package: str, version: str) -> str:
    """Return the canonical npm registry tarball for one locked package."""
    basename = package.rsplit("/", maxsplit=1)[-1]
    return f"https://registry.npmjs.org/{package}/-/{basename}-{version}.tgz"


def _audit_locked_npm_package(
    locked_packages: dict[str, object],
    *,
    package: str,
    spec: str,
    context: str,
) -> tuple[dict[str, object], str]:
    """Validate one source-owned lock entry and return its exact version."""
    path = f"node_modules/{package}"
    locked = _require_object(locked_packages.get(path), context=context)
    version = _require_string(locked, "version", context=context)
    try:
        require_npm_version_matches_spec(version, spec, context=f"Unsloth {context}")
    except RuntimeError as exc:
        msg = f"Unsloth {context} drifted"
        raise RuntimeError(msg) from exc
    if locked.get("resolved") != _npm_registry_url(package, version):
        msg = f"Unsloth {context} drifted"
        raise RuntimeError(msg)
    _require_sha512_sri(locked, "integrity", context=context)
    return locked, version


def _audit_oxc_lock(
    payload: bytes,
    *,
    package_version: str,
    dependencies: dict[str, str],
) -> None:
    lock = _require_object(
        json.loads(payload.decode("utf-8")),
        context="OXC validator package-lock.json",
    )
    if (
        lock.get("name") != _OXC_PACKAGE_NAME
        or lock.get("version") != package_version
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
    if (
        lock_root.get("dependencies") != dependencies
        or lock_root.get("name") != _OXC_PACKAGE_NAME
        or lock_root.get("version") != package_version
    ):
        msg = "Unsloth OXC validator lock root drifted"
        raise RuntimeError(msg)

    for runtime, darwin_binding in _OXC_DARWIN_BINDINGS.items():
        runtime_path = f"node_modules/{runtime}"
        locked_runtime, _runtime_version = _audit_locked_npm_package(
            locked_packages,
            package=runtime,
            spec=dependencies[runtime],
            context=f"OXC locked runtime {runtime_path}",
        )
        optional_dependencies = _require_object(
            locked_runtime.get("optionalDependencies"),
            context=f"OXC optional dependencies for {runtime_path}",
        )
        binding_namespace = f"@{runtime}/binding-"
        locked_bindings: dict[str, dict[str, object]] = {}
        for binding_name, raw_spec in optional_dependencies.items():
            if (
                not isinstance(binding_name, str)
                or not binding_name.startswith(binding_namespace)
                or not isinstance(raw_spec, str)
                or not raw_spec
            ):
                msg = f"Unsloth OXC locked runtime drifted: {runtime_path}"
                raise RuntimeError(msg)
            binding_path = f"node_modules/{binding_name}"
            binding, _binding_version = _audit_locked_npm_package(
                locked_packages,
                package=binding_name,
                spec=raw_spec,
                context=f"OXC locked runtime {binding_path}",
            )
            if binding.get("optional") is not True:
                msg = f"Unsloth OXC locked runtime drifted: {binding_path}"
                raise RuntimeError(msg)
            locked_bindings[binding_name] = binding

        binding_path = f"node_modules/{darwin_binding}"
        binding = locked_bindings.get(darwin_binding)
        if (
            binding is None
            or binding.get("cpu") != ["arm64"]
            or binding.get("os") != ["darwin"]
        ):
            msg = f"Unsloth OXC darwin-arm64 binding drifted: {binding_path}"
            raise RuntimeError(msg)

        runtime_dependencies = _require_object(
            locked_runtime.get("dependencies", {}),
            context=f"OXC dependencies for {runtime_path}",
        )
        for dependency, raw_spec in runtime_dependencies.items():
            if not isinstance(dependency, str) or not isinstance(raw_spec, str):
                msg = f"Unsloth OXC dependencies for {runtime_path} drifted"
                raise TypeError(msg)
            _audit_locked_npm_package(
                locked_packages,
                package=dependency,
                spec=raw_spec,
                context=f"OXC transitive dependency {dependency}",
            )


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
    if validator.count(OXC_VALIDATOR_PATCH_SEAM) != 1:
        msg = "Unsloth OXC validator patch seam is ambiguous or incompatible"
        raise RuntimeError(msg)
    validator_contract = (
        'import { spawnSync } from "node:child_process";',
        'import { parseSync } from "oxc-parser";',
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


def _audit_oxc_sources(payloads: dict[str, bytes]) -> None:
    package = _require_object(
        json.loads(payloads[_OXC_PACKAGE_PATH].decode("utf-8")),
        context="OXC validator package.json",
    )
    if (
        package.get("name") != _OXC_PACKAGE_NAME
        or package.get("private") is not True
        or package.get("type") != "module"
    ):
        msg = "Unsloth OXC validator package identity drifted"
        raise RuntimeError(msg)
    package_version = _require_string(
        package,
        "version",
        context="OXC validator package identity",
    )
    try:
        require_npm_version_matches_spec(
            package_version,
            package_version,
            context="Unsloth OXC validator package version",
        )
    except RuntimeError as exc:
        msg = "Unsloth OXC validator package identity drifted"
        raise RuntimeError(msg) from exc
    dependencies = _require_object(
        package.get("dependencies"),
        context="OXC validator dependencies",
    )
    if set(dependencies) != set(_OXC_DARWIN_BINDINGS) or not all(
        isinstance(value, str) and value for value in dependencies.values()
    ):
        msg = "Unsloth OXC validator dependencies drifted"
        raise RuntimeError(msg)
    if "scripts" in package:
        msg = "Unsloth OXC validator package shape drifted"
        raise RuntimeError(msg)

    _audit_oxc_lock(
        payloads[_OXC_LOCK_PATH],
        package_version=package_version,
        dependencies=cast("dict[str, str]", dependencies),
    )
    _audit_oxc_runtime_sources(payloads)


def _canonical_rust_toolchain_version(minimum: str) -> str:
    match = re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?",
        minimum,
    )
    if match is None:
        msg = f"Unsloth Cargo package has invalid rust-version {minimum!r}"
        raise RuntimeError(msg)
    patch = match.group(3) or "0"
    return f"{match.group(1)}.{match.group(2)}.{patch}"


def _rust_toolchain_version(cargo_manifest: bytes) -> str:
    """Resolve the exact stable toolchain from Cargo's source-owned minimum."""
    try:
        parsed = tomllib.loads(cargo_manifest.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        msg = "Unsloth Cargo manifest is invalid"
        raise RuntimeError(msg) from exc
    manifest = _require_object(parsed, context="Cargo manifest")
    package = _require_object(manifest.get("package"), context="Cargo package")
    minimum = _require_string(package, "rust-version", context="Cargo package")
    return _canonical_rust_toolchain_version(minimum)


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
    archive_paths = {f"unsloth-{version}/{path}": path for path in _OXC_SOURCE_PATHS}
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

    missing = sorted(set(_OXC_SOURCE_PATHS) - sources.keys())
    if missing:
        msg = f"Unsloth backend sdist OXC source is missing {missing[0]}"
        raise RuntimeError(msg)
    return sources


def _canonical_pypi_upload_time(value: str) -> str:
    """Validate the immutable PyPI upload timestamp used as uv's cutoff."""
    if _PYPI_UPLOAD_TIME_PATTERN.fullmatch(value) is None:
        msg = f"Unsloth backend sdist has invalid upload time {value!r}"
        raise RuntimeError(msg)
    try:
        datetime.datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        msg = f"Unsloth backend sdist has invalid upload time {value!r}"
        raise RuntimeError(msg) from exc
    return value


def _render_python_project(backend_url: str) -> str:
    """Render the stable local project around one immutable backend sdist."""
    requirement = json.dumps(f"unsloth[studio] @ {backend_url}")
    target = json.dumps(_PYTHON_TARGET)
    return f"""[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "nixcfg-unsloth-runtime"
version = "{_PYTHON_PROJECT_VERSION}"
requires-python = "=={_PYTHON_VERSION}.*"
dependencies = [
  {requirement},
]

[tool.uv]
environments = [
  {target},
]
override-dependencies = [
  "triton>=3.0.0 ; sys_platform == 'linux'",
  "xformers>=0.0.27.post2 ; sys_platform == 'linux' and platform_machine == 'x86_64'",
]
required-environments = [
  {target},
]

[tool.setuptools]
py-modules = []
"""


def _source_hash(source: SourceEntry, hash_type: str, url: str | None = None) -> str:
    """Return one uniquely identified hash from a candidate source entry."""
    entries = source.hashes.entries
    if entries is None:
        msg = "Unsloth candidate source hashes must be structured"
        raise RuntimeError(msg)
    matches = [
        entry.hash
        for entry in entries
        if entry.hash_type == hash_type and entry.url == url
    ]
    if len(matches) != 1:
        msg = f"Unsloth candidate requires one {hash_type} hash for {url!r}"
        raise RuntimeError(msg)
    return matches[0]


def _closure_plan_payload(
    info: VersionInfo,
    source: SourceEntry,
    metadata: dict[str, object],
) -> dict[str, object]:
    """Build release-relational closure evidence for the candidate source."""
    if source.commit is None or source.urls is None:
        msg = "Unsloth candidate source is missing immutable identities"
        raise RuntimeError(msg)
    backend_url = source.urls["backendSdist"]
    manifest_url = source.urls["releaseManifest"]
    backend_version = cast("str", metadata["backendVersion"])
    return {
        "app": {
            "commit": source.commit,
            "sourceHash": _source_hash(source, "srcHash"),
            "tag": cast("str", metadata["tag"]),
            "version": info.version,
        },
        "backend": {
            "sdistHash": _source_hash(source, "sha256", backend_url),
            "sourceTagVersion": cast("str", metadata["sourcePythonVersion"]),
            "version": backend_version,
        },
        "blockers": [],
        "closurePolicy": {
            "allowPlaceholderHashes": False,
            "allowPrebuiltHelperFallbacks": False,
            "allowVendorDesktopBinary": False,
            "requireSourceProvenance": True,
        },
        "packageExported": True,
        "patchPolicy": {
            "backendEnvironment": "UNSLOTH_DISABLE_UPDATE_CHECK=1",
            "backendPathCompileVariable": "UNSLOTH_NIX_BACKEND",
            "desktopMutationsBlocked": [
                "backend-first-run-install-command",
                "backend-repair-command",
                "backend-update-command",
                "launch-agent",
                "tauri-self-update",
            ],
            "managedModeCompileVariable": "UNSLOTH_NIX_MANAGED",
            "verificationStatus": "exact-source-replay-and-behavior-tests-passed",
        },
        "releaseManifest": {
            "hash": _source_hash(source, "sha256", manifest_url),
            "pypiVersion": backend_version,
            "version": info.version,
        },
        "status": "exported-and-validated",
    }


def _runtime_store_identity(
    evidence: dict[str, object],
    key: str,
    *,
    suffix: str = "",
) -> str:
    """Require one runtime identity to name an exact Nix store output or file."""
    value = _require_string(evidence, key, context="runtime evidence")
    if suffix and not value.endswith(suffix):
        msg = f"Unsloth runtime evidence has invalid {key}"
        raise RuntimeError(msg)
    store_output = value.removesuffix(suffix) if suffix else value
    if _NIX_STORE_OUTPUT_PATTERN.fullmatch(store_output) is None:
        msg = f"Unsloth runtime evidence has invalid {key}"
        raise RuntimeError(msg)
    return value


def _runtime_evidence(payload: object) -> dict[str, object]:
    """Validate raw host evidence, then retain only deterministic semantics."""
    evidence = _require_object(payload, context="runtime evidence")
    expected_fields = {
        "appCandidate",
        "appPid",
        "backendExecutable",
        "backendPid",
        "backendRuntimeEntrypoint",
        "health",
        "listenerAddress",
        "listenerOwnership",
        "ownedProcessGroups",
        "port",
        "protectedListenerCount",
        "protectedListenerIdentitySha256",
        "sandbox",
        "schemaVersion",
        "sessionId",
        "status",
        "teardown",
    }
    if (
        evidence.get("schemaVersion") != _RAW_RUNTIME_EVIDENCE_SCHEMA_VERSION
        or set(evidence) != expected_fields
    ):
        msg = "Unsloth runtime evidence did not pass schema version 2"
        raise RuntimeError(msg)

    for gate in ("status", "teardown", "sandbox", "listenerOwnership"):
        if evidence.get(gate) != "passed":
            msg = f"Unsloth runtime evidence did not pass {gate}"
            raise RuntimeError(msg)

    app_candidate = _runtime_store_identity(evidence, "appCandidate")
    backend_executable = _runtime_store_identity(
        evidence,
        "backendExecutable",
        suffix="/bin/unsloth",
    )
    backend_runtime_entrypoint = _runtime_store_identity(
        evidence,
        "backendRuntimeEntrypoint",
        suffix="/bin/unsloth",
    )

    app_pid = _require_positive_int(evidence, "appPid", context="runtime evidence")
    _require_positive_int(evidence, "backendPid", context="runtime evidence")
    session_id = _require_positive_int(
        evidence,
        "sessionId",
        context="runtime evidence",
    )
    if session_id != app_pid:
        msg = "Unsloth runtime evidence sessionId does not match appPid"
        raise RuntimeError(msg)

    port = _require_positive_int(evidence, "port", context="runtime evidence")
    if port not in range(8888, 8909):
        msg = "Unsloth runtime evidence has invalid port"
        raise RuntimeError(msg)
    listener_address = _require_string(
        evidence,
        "listenerAddress",
        context="runtime evidence",
    )
    if listener_address != f"127.0.0.1:{port}":
        msg = "Unsloth runtime evidence has invalid listenerAddress"
        raise RuntimeError(msg)

    process_groups = evidence.get("ownedProcessGroups")
    if (
        not isinstance(process_groups, list)
        or not process_groups
        or any(
            not isinstance(group, int) or isinstance(group, bool) or group <= 0
            for group in process_groups
        )
    ):
        msg = "Unsloth runtime evidence has invalid ownedProcessGroups"
        raise TypeError(msg)

    _require_positive_int(
        evidence,
        "protectedListenerCount",
        context="runtime evidence",
    )
    protected_digest = _require_string(
        evidence,
        "protectedListenerIdentitySha256",
        context="runtime evidence",
    )
    if _HEX_SHA256_PATTERN.fullmatch(protected_digest) is None:
        msg = "Unsloth runtime evidence has invalid protected listener identity"
        raise RuntimeError(msg)

    health = _require_object(evidence.get("health"), context="runtime health")
    if set(health) != {"service", "status", "studio_root_id"}:
        msg = "Unsloth runtime health does not match its schema"
        raise RuntimeError(msg)
    service = _require_string(health, "service", context="runtime health")
    health_status = _require_string(health, "status", context="runtime health")
    studio_root_id = _require_string(
        health,
        "studio_root_id",
        context="runtime health",
    )
    if (
        service != "Unsloth UI Backend"
        or health_status != "healthy"
        or re.fullmatch(r"[0-9a-f]+", studio_root_id, re.ASCII) is None
    ):
        msg = "Unsloth runtime evidence has invalid health contract"
        raise RuntimeError(msg)

    return {
        "appCandidate": app_candidate,
        "backendExecutable": backend_executable,
        "backendRuntimeEntrypoint": backend_runtime_entrypoint,
        "health": {
            "service": service,
            "status": health_status,
        },
        "listenerOwnership": "passed",
        "sandbox": "passed",
        "schemaVersion": _RUNTIME_EVIDENCE_SCHEMA_VERSION,
        "status": "passed",
        "studioRootIdentity": "passed",
        "teardown": "passed",
    }


def _artifact_validation_payload(
    smoke_output: str,
    runtime_evidence: dict[str, object],
) -> dict[str, object]:
    """Build the persisted export evidence consumed by package gates."""
    return {
        "checks": list(_ARTIFACT_CHECKS),
        "runtimeEvidence": runtime_evidence,
        "runtimeEvidenceSchemaVersion": _RUNTIME_EVIDENCE_SCHEMA_VERSION,
        "status": "passed",
        "storePathAppCandidateSmokeOutput": smoke_output,
    }


def _json_nix_expression(payload: object) -> NixExpression:
    """Construct a Nix expression for typed JSON data without string templates."""
    return FunctionCall(
        name=select_attrs(Identifier(name="builtins"), "fromJSON"),
        argument=StringPrimitive(
            value=json.dumps(payload, sort_keys=True, separators=(",", ":"))
        ),
    )


def _json_object_output(stdout: str, *, context: str) -> dict[str, object]:
    """Parse one command's stdout as a JSON object."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        msg = f"{context} did not return JSON"
        raise RuntimeError(msg) from exc
    return _require_object(payload, context=context)


@register_updater
class UnslothUpdater(MaterializesArtifactsMixin, GitHubReleaseUpdater):
    """Track exact public desktop and backend sources behind closure gates."""

    name = "unsloth"
    GITHUB_OWNER = "unslothai"
    GITHUB_REPO = "unsloth"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    required_tools = ("nix", "uv")
    generated_artifact_files = _GENERATED_ARTIFACT_FILES
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
    ) -> tuple[str, str, int, str]:
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
        upload_time = _canonical_pypi_upload_time(
            _require_string(sdist, "upload_time_iso_8601", context="PyPI sdist")
        )
        return url, digest_hex, size, upload_time

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
        cargo_manifest = await fetch_url(
            session,
            github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                commit,
                _CARGO_MANIFEST_PATH,
            ),
            config=self.config,
        )
        rust_toolchain_version = _rust_toolchain_version(cargo_manifest)
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
            for path in _OXC_SOURCE_PATHS
        }
        _audit_oxc_sources(oxc_source_payloads)
        pypi_payload = await fetch_json(
            session,
            f"https://pypi.org/pypi/unsloth/{backend_version}/json",
            config=self.config,
        )
        (
            backend_url,
            backend_digest_hex,
            backend_size,
            backend_upload_time,
        ) = self._backend_sdist(
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
            _audit_oxc_sources(backend_oxc_sources)
        except RuntimeError as exc:
            msg = f"Unsloth backend sdist OXC source audit failed: {exc}"
            raise RuntimeError(msg) from exc
        return VersionInfo(
            version=version,
            metadata={
                "backendDigestHex": backend_digest_hex,
                "backendSize": backend_size,
                "backendUploadTime": backend_upload_time,
                "backendUrl": backend_url,
                "backendVersion": backend_version,
                "commit": commit,
                "manifestDigestHex": manifest_digest_hex,
                "manifestSize": manifest_size,
                "manifestUrl": manifest_url,
                "rustToolchainVersion": rust_toolchain_version,
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
            "backendUploadTime",
            "backendUrl",
            "backendVersion",
            "commit",
            "manifestDigestHex",
            "manifestUrl",
            "rustToolchainVersion",
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
        _canonical_pypi_upload_time(cast("str", metadata["backendUploadTime"]))
        rust_toolchain_version = cast("str", metadata["rustToolchainVersion"])
        if (
            _canonical_rust_toolchain_version(rust_toolchain_version)
            != rust_toolchain_version
        ):
            msg = "Unsloth release metadata has a non-canonical Rust toolchain version"
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

    @staticmethod
    def _candidate_package_args(
        *,
        python_workspace: Path,
        closure_hashes: Mapping[str, str | None],
        closure_plan: dict[str, object],
        artifact_validation: dict[str, object],
    ) -> dict[str, NixExpression]:
        """Return in-memory package inputs for pre-persistence candidate probes."""
        return {
            "artifactValidation": _json_nix_expression(artifact_validation),
            "closureHashes": _json_nix_expression(closure_hashes),
            "closurePlan": _json_nix_expression(closure_plan),
            "pythonWorkspaceRoot": NixPath(path=str(python_workspace)),
        }

    async def _materialize_uv_lock(
        self,
        *,
        package_dir: Path,
        pyproject_text: str,
        upload_time: str,
    ) -> EventStream:
        """Resolve the candidate Python closure with the release upload cutoff."""
        with tempfile.TemporaryDirectory(prefix="unsloth-uv-lock-") as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            pyproject_path = workspace / "pyproject.toml"
            lock_path = workspace / "uv.lock"
            await asyncio.to_thread(
                pyproject_path.write_text,
                pyproject_text,
                encoding="utf-8",
            )
            existing_lock = package_dir / "uv.lock"
            if existing_lock.is_file():
                existing_text = await asyncio.to_thread(
                    existing_lock.read_text,
                    encoding="utf-8",
                )
                await asyncio.to_thread(
                    lock_path.write_text,
                    existing_text,
                    encoding="utf-8",
                )

            command_drain = ValueDrain()
            async for event in drain_value_events(
                update_process.run_command(
                    [
                        "uv",
                        "-q",
                        "lock",
                        "--directory",
                        str(workspace),
                        "--exclude-newer",
                        upload_time,
                    ],
                    options=update_process.RunCommandOptions(
                        source=self.name,
                        error="uv lock did not return output",
                        # Keep the candidate's [tool.uv] overrides while excluding
                        # user and system configuration from the resolution.
                        env={
                            "UV_CACHE_DIR": str(root / "uv-cache"),
                            "UV_NO_SYSTEM_CONFIG": "1",
                            "UV_PYTHON": _PYTHON_VERSION,
                            "XDG_CACHE_HOME": str(root / "xdg-cache"),
                            "XDG_CONFIG_HOME": str(root / "xdg-config"),
                            "XDG_DATA_HOME": str(root / "xdg-data"),
                            "XDG_STATE_HOME": str(root / "xdg-state"),
                        },
                        config=self.config,
                    ),
                ),
                command_drain,
                parse=expect_command_result,
            ):
                yield event
            result = require_value(command_drain, "Missing Unsloth uv lock result")
            raise_failed_command("Refresh Unsloth Python closure", result)
            if not lock_path.is_file():
                msg = "uv lock did not produce Unsloth uv.lock"
                raise RuntimeError(msg)
            lock_text = await asyncio.to_thread(lock_path.read_text, encoding="utf-8")
        yield UpdateEvent.value(self.name, lock_text)

    async def _compute_candidate_closure_hash(
        self,
        *,
        attr_path: str,
        source: SourceEntry,
        package_args: dict[str, NixExpression],
    ) -> EventStream:
        """Compute one dependency hash against the complete candidate identity."""
        expression = _build_package_path_attr_expr(
            self.name,
            attr_path,
            system=self.DARWIN_PLATFORM,
            package_args=package_args,
            source_overrides={self.name: source},
        )
        async for event in update_nix.compute_fixed_output_hash(
            self.name,
            expression,
            isolate_by_drv_hash=True,
            config=self.config,
        ):
            yield event

    async def _build_candidate_smoke(
        self,
        *,
        source: SourceEntry,
        package_args: dict[str, NixExpression],
    ) -> EventStream:
        """Build and return the candidate smoke output before artifact promotion."""
        expression = _build_package_path_attr_expr(
            self.name,
            ".storePathAppCandidateSmoke",
            system=self.DARWIN_PLATFORM,
            package_args=package_args,
            source_overrides={self.name: source},
        )
        command_drain = ValueDrain()
        async for event in drain_value_events(
            update_process.run_command(
                [
                    "nix",
                    "build",
                    "-L",
                    "--no-link",
                    "--print-out-paths",
                    "--impure",
                    "--expr",
                    expression,
                ],
                options=update_process.RunCommandOptions(
                    source=self.name,
                    error="candidate smoke build did not return output",
                    config=self.config,
                ),
            ),
            command_drain,
            parse=expect_command_result,
        ):
            yield event
        result = require_value(command_drain, "Missing Unsloth smoke build result")
        raise_failed_command("Build Unsloth candidate smoke", result)
        outputs = result.stdout.splitlines()
        if len(outputs) != 1 or _NIX_STORE_OUTPUT_PATTERN.fullmatch(outputs[0]) is None:
            msg = "Unsloth candidate smoke build did not return one Nix store output"
            raise RuntimeError(msg)
        yield UpdateEvent.value(self.name, outputs[0])

    async def _validate_candidate_runtime(
        self,
        *,
        package_dir: Path,
        smoke_output: str,
    ) -> EventStream:
        """Run the contained host-runtime gate and return its evidence."""
        command_drain = ValueDrain()
        async for event in drain_value_events(
            update_process.run_command(
                [
                    sys.executable,
                    str(package_dir / "validate_store_runtime.py"),
                    "--smoke-result",
                    smoke_output,
                ],
                options=update_process.RunCommandOptions(
                    source=self.name,
                    error="runtime validation did not return output",
                    config=self.config,
                ),
            ),
            command_drain,
            parse=expect_command_result,
        ):
            yield event
        result = require_value(command_drain, "Missing Unsloth runtime result")
        raise_failed_command("Validate Unsloth candidate runtime", result)
        evidence = _runtime_evidence(
            _json_object_output(result.stdout, context="runtime validation")
        )
        yield UpdateEvent.value(
            self.name,
            json.dumps(evidence, sort_keys=True, separators=(",", ":")),
        )

    async def _validate_candidate_export(
        self,
        *,
        source: SourceEntry,
        package_args: dict[str, NixExpression],
    ) -> EventStream:
        """Require the final in-memory artifacts to open every export gate."""
        expression = _build_package_path_attr_expr(
            self.name,
            ".exportReady",
            system=self.DARWIN_PLATFORM,
            package_args=package_args,
            source_overrides={self.name: source},
        )
        command_drain = ValueDrain()
        async for event in drain_value_events(
            update_process.run_command(
                ["nix", "eval", "--json", "--impure", "--expr", expression],
                options=update_process.RunCommandOptions(
                    source=self.name,
                    error="candidate export evaluation did not return output",
                    config=self.config,
                ),
            ),
            command_drain,
            parse=expect_command_result,
        ):
            yield event
        result = require_value(command_drain, "Missing Unsloth export evaluation")
        raise_failed_command("Evaluate Unsloth candidate export", result)
        try:
            export_ready = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            msg = "Unsloth candidate export evaluation did not return JSON"
            raise RuntimeError(msg) from exc
        if export_ready is not True:
            msg = "Unsloth candidate artifacts did not satisfy the export gates"
            raise RuntimeError(msg)
        yield UpdateEvent.value(self.name, "export-ready")

    async def _resolve_candidate_closure_hashes(
        self,
        *,
        source: SourceEntry,
        python_workspace: Path,
        closure_plan: dict[str, object],
    ) -> EventStream:
        """Resolve all release-varying fixed-output hashes in dependency order."""
        closure_hashes: dict[str, str | None] = {
            "cargoHash": None,
            "frontendNpmDepsHash": None,
            "oxcNpmDepsHash": None,
        }
        resolved_hashes: dict[str, str] = {}
        for key, attr_path in (
            ("frontendNpmDepsHash", ".frontend.npmDeps"),
            ("oxcNpmDepsHash", ".oxcNodeModules.npmDeps"),
            ("cargoHash", ".appCandidate.cargoDeps"),
        ):
            package_args = self._candidate_package_args(
                python_workspace=python_workspace,
                closure_hashes=closure_hashes,
                closure_plan=closure_plan,
                artifact_validation={"status": "pending"},
            )
            hash_drain = ValueDrain[str]()
            async for event in drain_value_events(
                self._compute_candidate_closure_hash(
                    attr_path=attr_path,
                    source=source,
                    package_args=package_args,
                ),
                hash_drain,
                parse=expect_str,
            ):
                yield event
            resolved_hashes[key] = require_value(
                hash_drain,
                f"Missing Unsloth {key} output",
            )
            closure_hashes[key] = resolved_hashes[key]
        yield UpdateEvent.value(self.name, resolved_hashes)

    async def _attest_candidate(
        self,
        *,
        source: SourceEntry,
        package_dir: Path,
        python_workspace: Path,
        closure_hashes: Mapping[str, str | None],
        closure_plan: dict[str, object],
    ) -> EventStream:
        """Build, run, and open the export gates for one complete candidate."""
        pending_args = self._candidate_package_args(
            python_workspace=python_workspace,
            closure_hashes=closure_hashes,
            closure_plan=closure_plan,
            artifact_validation={"status": "pending"},
        )
        smoke_drain = ValueDrain[str]()
        async for event in drain_value_events(
            self._build_candidate_smoke(source=source, package_args=pending_args),
            smoke_drain,
            parse=expect_str,
        ):
            yield event
        smoke_output = require_value(
            smoke_drain,
            "Missing Unsloth candidate smoke output",
        )

        runtime_drain = ValueDrain[str]()
        async for event in drain_value_events(
            self._validate_candidate_runtime(
                package_dir=package_dir,
                smoke_output=smoke_output,
            ),
            runtime_drain,
            parse=expect_str,
        ):
            yield event
        artifact_validation = _artifact_validation_payload(
            smoke_output,
            _json_object_output(
                require_value(
                    runtime_drain,
                    "Missing Unsloth candidate runtime evidence",
                ),
                context="runtime validation",
            ),
        )
        final_args = self._candidate_package_args(
            python_workspace=python_workspace,
            closure_hashes=closure_hashes,
            closure_plan=closure_plan,
            artifact_validation=artifact_validation,
        )
        export_drain = ValueDrain[str]()
        async for event in drain_value_events(
            self._validate_candidate_export(source=source, package_args=final_args),
            export_drain,
            parse=expect_str,
        ):
            yield event
        if (
            require_value(
                export_drain,
                "Missing Unsloth candidate export result",
            )
            != "export-ready"
        ):
            msg = "Unsloth candidate export gate did not pass"
            raise RuntimeError(msg)
        yield UpdateEvent.value(
            self.name,
            json.dumps(artifact_validation, sort_keys=True, separators=(",", ":")),
        )

    async def _materialize_candidate_artifacts(
        self,
        *,
        info: VersionInfo,
        source: SourceEntry,
        metadata: dict[str, object],
        package_dir: Path,
    ) -> EventStream:
        """Produce every release-varying sidecar from one candidate authority."""
        backend_url = cast("str", metadata["backendUrl"])
        pyproject_text = _render_python_project(backend_url)
        lock_drain = ValueDrain[str]()
        async for event in drain_value_events(
            self._materialize_uv_lock(
                package_dir=package_dir,
                pyproject_text=pyproject_text,
                upload_time=cast("str", metadata["backendUploadTime"]),
            ),
            lock_drain,
            parse=expect_str,
        ):
            yield event
        lock_text = require_value(lock_drain, "Missing Unsloth uv.lock content")
        closure_plan = _closure_plan_payload(info, source, metadata)

        with tempfile.TemporaryDirectory(
            prefix="unsloth-python-workspace-"
        ) as temp_dir:
            # Nix rejects path literals with symlinked ancestors. macOS may
            # expose the temporary root as /tmp even though it resolves to
            # /private/tmp, so hand Nix the canonical path.
            python_workspace = await asyncio.to_thread(Path(temp_dir).resolve)
            for name, content in (
                ("pyproject.toml", pyproject_text),
                ("uv.lock", lock_text),
            ):
                await asyncio.to_thread(
                    (python_workspace / name).write_text,
                    content,
                    encoding="utf-8",
                )
            hashes_drain = ValueDrain[dict[str, str]]()
            async for event in drain_value_events(
                self._resolve_candidate_closure_hashes(
                    source=source,
                    python_workspace=python_workspace,
                    closure_plan=closure_plan,
                ),
                hashes_drain,
                parse=expect_hash_mapping,
            ):
                yield event
            closure_hashes = require_value(
                hashes_drain,
                "Missing Unsloth closure hashes",
            )
            validation_drain = ValueDrain[str]()
            async for event in drain_value_events(
                self._attest_candidate(
                    source=source,
                    package_dir=package_dir,
                    python_workspace=python_workspace,
                    closure_hashes=closure_hashes,
                    closure_plan=closure_plan,
                ),
                validation_drain,
                parse=expect_str,
            ):
                yield event
            artifact_validation = _json_object_output(
                require_value(
                    validation_drain,
                    "Missing Unsloth artifact validation",
                ),
                context="artifact validation",
            )

        yield UpdateEvent.artifact(
            self.name,
            [
                GeneratedArtifact.text(package_dir / "pyproject.toml", pyproject_text),
                GeneratedArtifact.text(package_dir / "uv.lock", lock_text),
                GeneratedArtifact.json(
                    package_dir / "closure-hashes.json", closure_hashes
                ),
                GeneratedArtifact.json(package_dir / "closure-plan.json", closure_plan),
                GeneratedArtifact.json(
                    package_dir / "artifact-validation.json",
                    artifact_validation,
                ),
            ],
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash sources and atomically materialize the complete candidate closure."""
        _ = session
        resolved_context = _coerce_context(context)
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
        foundation_hashes = [
            HashEntry.create("srcHash", source_hash),
            HashEntry.create("sha256", url_hashes[manifest_url], url=manifest_url),
            HashEntry.create("sha256", url_hashes[backend_url], url=backend_url),
        ]
        if resolved_context.dry_run:
            yield UpdateEvent.value(self.name, foundation_hashes)
            return

        package_dir = updater_dir_for(self.name)
        if package_dir is None:
            msg = "Unsloth package directory was not found"
            raise RuntimeError(msg)
        source = self.build_result(info, foundation_hashes)
        async for event in self._materialize_candidate_artifacts(
            info=info,
            source=source,
            metadata=metadata,
            package_dir=package_dir,
        ):
            yield event
        yield UpdateEvent.value(self.name, foundation_hashes)

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
