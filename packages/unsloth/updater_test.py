"""Contracts for the source-built Unsloth Desktop closure."""

import ast
import base64
import hashlib
import io
import json
import sys
import tarfile
import textwrap
import tomllib
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._nix_source import nix_file_binding_expr
from lib.tests._updater_helpers import collect_events, load_repo_module, run_async
from lib.update.artifacts import GeneratedArtifact
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import CommandResult, UpdateEvent, UpdateEventKind
from lib.update.nix import _build_fetch_from_github_call, _build_package_path_attr_expr
from lib.update.paths import REPO_ROOT
from lib.update.updaters import UpdateContext, VersionInfo

if TYPE_CHECKING:
    from typing import Any

_PACKAGE_DIR = REPO_ROOT / "packages/unsloth"
_VERSION = "0.1.804-beta"
_TAG = f"v{_VERSION}"
_COMMIT = "8c43aed2038721050ca0620f02967e03a9d5aa23"
_RUST_TOOLCHAIN_VERSION = "1.89.0"
_SOURCE_PYTHON_VERSION = "2026.8.22"
_BACKEND_VERSION = "2026.8.22"
_BACKEND_UPLOAD_TIME = "2026-08-22T12:34:56.789Z"
_MANIFEST_URL = (
    f"https://github.com/unslothai/unsloth/releases/download/{_TAG}/latest.json"
)
_BACKEND_URL = (
    "https://files.pythonhosted.org/packages/1b/5c/7645fb279567ab0f81751c284b8c916673e7721aee87acd841478cfab8b7/"
    f"unsloth-{_BACKEND_VERSION}.tar.gz"
)
_SRC_HASH = "sha256-HPQu2gdFx5AMPkejUf5zIZqtx8FTwU+ZG7cxmm6tcp8="
_MANIFEST_HASH = "sha256-yohnJK7S7DEEy5oMYGk/PCz2JzSww08B+1hoRO4PJoY="
_BACKEND_HASH = "sha256-K3wbtbqvMK9iX3qnLhAUCUU6vwYwMjAfHN+v3wV0yd4="
_CLOSURE_HASHES = {
    "cargoHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    "frontendNpmDepsHash": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
    "oxcNpmDepsHash": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
}
_SMOKE_OUTPUT = f"/nix/store/{'a' * 32}-unsloth-candidate-smoke"
_OXC_PACKAGE_PATH = "studio/backend/core/data_recipe/oxc-validator/package.json"
_OXC_LOCK_PATH = "studio/backend/core/data_recipe/oxc-validator/package-lock.json"
_OXC_VALIDATE_PATH = "studio/backend/core/data_recipe/oxc-validator/validate.mjs"
_OXC_CALLER_PATH = "studio/backend/core/data_recipe/local_callable_validators.py"
_CARGO_MANIFEST_PATH = "studio/src-tauri/Cargo.toml"
_SETUP_SH_PATH = "studio/setup.sh"
_SETUP_PS1_PATH = "studio/setup.ps1"
_OXC_SOURCE_PATHS = (
    _OXC_PACKAGE_PATH,
    _OXC_LOCK_PATH,
    _OXC_VALIDATE_PATH,
    _OXC_CALLER_PATH,
    _SETUP_SH_PATH,
    _SETUP_PS1_PATH,
)


def test_unsloth_updater_python_target_matches_nix_backend_selection() -> None:
    """Updater wheel selection and the Nix Python package cannot drift apart."""
    module = _load_updater_module()
    major, minor = module._PYTHON_VERSION.split(".")
    python_attribute = f"python{major}{minor}"
    backend = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "backend.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    formals = {
        argument.name
        for argument in backend.argument_set
        if isinstance(argument, Identifier)
    }

    assert python_attribute in formals
    assert_nix_ast_equal(
        nix_file_binding_expr("packages/unsloth/backend.nix", "python"),
        python_attribute,
    )


def _raw_runtime_evidence() -> dict[str, object]:
    """Return complete version-2 evidence emitted by the host runtime probe."""
    return {
        "appCandidate": f"/nix/store/{'b' * 32}-unsloth-desktop-{_VERSION}",
        "appPid": 100,
        "backendExecutable": (
            f"/nix/store/{'c' * 32}-unsloth-backend-{_BACKEND_VERSION}/bin/unsloth"
        ),
        "backendPid": 200,
        "backendRuntimeEntrypoint": (
            f"/nix/store/{'d' * 32}-unsloth-{_BACKEND_VERSION}-venv/bin/unsloth"
        ),
        "health": {
            "service": "Unsloth UI Backend",
            "status": "healthy",
            "studio_root_id": "e" * 64,
        },
        "listenerAddress": "127.0.0.1:8888",
        "listenerOwnership": "passed",
        "ownedProcessGroups": [100, 200],
        "port": 8888,
        "protectedListenerCount": 1,
        "protectedListenerIdentitySha256": "f" * 64,
        "sandbox": "passed",
        "schemaVersion": 2,
        "sessionId": 100,
        "status": "passed",
        "teardown": "passed",
    }


def _persisted_runtime_evidence() -> dict[str, object]:
    """Return the deterministic version-3 projection consumed by Nix."""
    raw = _raw_runtime_evidence()
    return {
        "appCandidate": raw["appCandidate"],
        "backendExecutable": raw["backendExecutable"],
        "backendRuntimeEntrypoint": raw["backendRuntimeEntrypoint"],
        "health": {
            "service": "Unsloth UI Backend",
            "status": "healthy",
        },
        "listenerOwnership": "passed",
        "sandbox": "passed",
        "schemaVersion": 3,
        "status": "passed",
        "studioRootIdentity": "passed",
        "teardown": "passed",
    }


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/unsloth/updater.py",
        "unsloth_updater_test",
    )


def _load_patch_module() -> ModuleType:
    return load_repo_module(
        "packages/unsloth/patch_nix_managed.py",
        "unsloth_nix_policy_patch_test",
    )


def _manifest_bytes(
    *,
    version: object = _VERSION,
    backend_version: object = _BACKEND_VERSION,
    platforms: object | None = None,
) -> bytes:
    if platforms is None:
        platforms = {
            "darwin-aarch64": {
                "url": (
                    "https://github.com/unslothai/unsloth/releases/download/"
                    f"{_TAG}/Unsloth.app.tar.gz"
                ),
                "signature": "signed-release",
            }
        }
    return json.dumps(
        {
            "version": version,
            "pypi_version": backend_version,
            "platforms": platforms,
        },
        sort_keys=True,
    ).encode()


def _release_payload(manifest: bytes) -> dict[str, object]:
    return {
        "tag_name": _TAG,
        "assets": [
            {
                "name": "latest.json",
                "browser_download_url": _MANIFEST_URL,
                "digest": f"sha256:{hashlib.sha256(manifest).hexdigest()}",
                "size": len(manifest),
            }
        ],
    }


def _pypi_payload(
    *,
    version: object = _BACKEND_VERSION,
    url: object = _BACKEND_URL,
    digest: object = "2b7c1bb5baaf30af625f7aa72e101409453abf063032301f1cdfafdf0574c9de",
    size: object = 96_307_052,
    upload_time: object = _BACKEND_UPLOAD_TIME,
) -> dict[str, object]:
    return {
        "info": {"version": version},
        "urls": [
            {
                "digests": {"sha256": digest},
                "filename": f"unsloth-{_BACKEND_VERSION}.tar.gz",
                "packagetype": "sdist",
                "size": size,
                "upload_time_iso_8601": upload_time,
                "url": url,
            }
        ],
    }


def _metadata(
    *,
    commit: object = _COMMIT,
    manifest_digest: object = "ca886724aed2ec3104cb9a0c60693f3c2cf62734b0c34f01fb586844ee0f2686",
    backend_digest: object = "2b7c1bb5baaf30af625f7aa72e101409453abf063032301f1cdfafdf0574c9de",
    manifest_size: object = 4_379,
    backend_size: object = 96_307_052,
    backend_upload_time: object = _BACKEND_UPLOAD_TIME,
    backend_url: object = _BACKEND_URL,
    backend_version: object = _BACKEND_VERSION,
    manifest_url: object = _MANIFEST_URL,
    rust_toolchain_version: object = _RUST_TOOLCHAIN_VERSION,
    source_python_version: object = _SOURCE_PYTHON_VERSION,
    tag: object = _TAG,
) -> dict[str, object]:
    return {
        "backendDigestHex": backend_digest,
        "backendSize": backend_size,
        "backendUploadTime": backend_upload_time,
        "backendUrl": backend_url,
        "backendVersion": backend_version,
        "commit": commit,
        "manifestDigestHex": manifest_digest,
        "manifestSize": manifest_size,
        "manifestUrl": manifest_url,
        "rustToolchainVersion": rust_toolchain_version,
        "sourcePythonVersion": source_python_version,
        "tag": tag,
    }


def _foundation_hashes(
    *,
    manifest_url: str = _MANIFEST_URL,
    backend_url: str = _BACKEND_URL,
) -> list[HashEntry]:
    return [
        HashEntry.create("srcHash", _SRC_HASH),
        HashEntry.create("sha256", _MANIFEST_HASH, url=manifest_url),
        HashEntry.create("sha256", _BACKEND_HASH, url=backend_url),
    ]


def _candidate_source(module: ModuleType) -> SourceEntry:
    return module.UnslothUpdater().build_result(
        VersionInfo(_VERSION, _metadata()),
        _foundation_hashes(),
    )


def _oxc_source_payloads(
    *,
    parser_dependency: str = "^0.131.0",
    parser_version: str = "0.131.0",
    oxlint_dependency: str = "^1.65.0",
    oxlint_version: str = "1.65.0",
) -> dict[str, bytes]:
    dependencies = {
        "oxc-parser": parser_dependency,
        "oxlint": oxlint_dependency,
    }
    binding_targets = (
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
    package = {
        "name": "unsloth-oxc-validator-runtime",
        "private": True,
        "version": "0.0.1",
        "type": "module",
        "dependencies": dependencies,
    }
    lock = {
        "name": "unsloth-oxc-validator-runtime",
        "version": "0.0.1",
        "lockfileVersion": 3,
        "packages": {
            "": {
                "name": "unsloth-oxc-validator-runtime",
                "version": "0.0.1",
                "dependencies": dependencies,
            },
            "node_modules/oxc-parser": {
                "dependencies": {"@oxc-project/types": f"^{parser_version}"},
                "version": parser_version,
                "resolved": (
                    "https://registry.npmjs.org/oxc-parser/-/"
                    f"oxc-parser-{parser_version}.tgz"
                ),
                "integrity": (
                    "sha512-SJ3/7ZPbgie8dr5Z9BI/M51zZbpXba+hRSG0MDzVwMW5CRQg2fjY"
                    "E0jHGlLX4eeiibGgC/mzoDFKSDHwVZEHRQ=="
                ),
                "optionalDependencies": {
                    f"@oxc-parser/binding-{target}": parser_version
                    for target in (*binding_targets, "wasm32-wasi")
                },
            },
            "node_modules/@oxc-parser/binding-darwin-arm64": {
                "version": parser_version,
                "resolved": (
                    "https://registry.npmjs.org/@oxc-parser/"
                    "binding-darwin-arm64/-/"
                    f"binding-darwin-arm64-{parser_version}.tgz"
                ),
                "integrity": (
                    "sha512-jukuV6xe5RbQKFo7QD34NDCLDZp4PSOm8rmckhNdH/60ymG5zXbDz"
                    "GBEyc+nTkuLQNama2aSGCt+CPfpjNTqyw=="
                ),
                "optional": True,
                "cpu": ["arm64"],
                "os": ["darwin"],
            },
            "node_modules/oxlint": {
                "version": oxlint_version,
                "resolved": (
                    f"https://registry.npmjs.org/oxlint/-/oxlint-{oxlint_version}.tgz"
                ),
                "integrity": (
                    "sha512-ChUuE3Q7XnAbscvT4XLMsH7HFJmLgLVv9lu+RRgFL5wSXnDqUOzT"
                    "p5IS8qWDBGd/ZDSzQ2tbX8fjAmijlGLC7A=="
                ),
                "optionalDependencies": {
                    f"@oxlint/binding-{target}": oxlint_version
                    for target in binding_targets
                },
            },
            "node_modules/@oxlint/binding-darwin-arm64": {
                "version": oxlint_version,
                "resolved": (
                    "https://registry.npmjs.org/@oxlint/"
                    "binding-darwin-arm64/-/"
                    f"binding-darwin-arm64-{oxlint_version}.tgz"
                ),
                "integrity": (
                    "sha512-pL/mG/5gMzBwp1gdc5+Cwi87F9j3XRnPxHGyVj5Zd+dCEV5YkKt0"
                    "L70PB3EGmEEHxgn4H+jnMS3xLuXs6mZW/Q=="
                ),
                "optional": True,
                "cpu": ["arm64"],
                "os": ["darwin"],
            },
            "node_modules/@oxc-project/types": {
                "funding": {"url": "https://github.com/sponsors/Boshen"},
                "integrity": (
                    "sha512-PgnWDfV0h+b16XNKbXU7Daib/BFSt/J2mEzfYIBu6JB/wNdlU+kVY"
                    "XCkGA1A9fWkTbOgbjh4e6NhPeQOYvFhEA=="
                ),
                "license": "MIT",
                "resolved": (
                    "https://registry.npmjs.org/@oxc-project/types/-/"
                    f"types-{parser_version}.tgz"
                ),
                "version": parser_version,
            },
        },
    }
    locked_packages = cast("dict[str, object]", lock["packages"])
    fallback_integrity = f"sha512-{base64.b64encode(b'x' * 64).decode()}"
    for namespace, version, targets in (
        ("oxc-parser", parser_version, (*binding_targets, "wasm32-wasi")),
        ("oxlint", oxlint_version, binding_targets),
    ):
        for target in targets:
            package_name = f"@{namespace}/binding-{target}"
            basename = package_name.rsplit("/", maxsplit=1)[-1]
            locked_packages.setdefault(
                f"node_modules/{package_name}",
                {
                    "integrity": fallback_integrity,
                    "optional": True,
                    "resolved": (
                        f"https://registry.npmjs.org/{package_name}/-/"
                        f"{basename}-{version}.tgz"
                    ),
                    "version": version,
                },
            )
    return {
        _OXC_PACKAGE_PATH: json.dumps(package, sort_keys=True).encode(),
        _OXC_LOCK_PATH: json.dumps(lock, sort_keys=True).encode(),
        _OXC_VALIDATE_PATH: b"\n".join((
            b'import { spawnSync } from "node:child_process";',
            b'import { performance } from "node:perf_hooks";',
            b'import { parseSync } from "oxc-parser";',
            b"const OXLINT_DEFAULT_BUDGET_MS = 30_000;",
            b"const OXLINT_BUDGET_MARGIN_MS = 2_000;",
            b"const OXLINT_MIN_TIMEOUT_MS = 1_000;",
            b"function mapBudgetMs(value) {",
            b"  const parsed = Number(value);",
            (
                b"  return Number.isFinite(parsed) && parsed > 0 "
                b"? Math.floor(parsed) : OXLINT_DEFAULT_BUDGET_MS;"
            ),
            b"}",
            b"function runLintBatch(entries, budgetMs) {",
            (
                b"  const timeoutMs = Math.floor(budgetMs - "
                b"OXLINT_BUDGET_MARGIN_MS - performance.now());"
            ),
            b"  if (timeoutMs < OXLINT_MIN_TIMEOUT_MS) {",
            (
                b'    return fallbackLintResults(entries, "oxlint skipped: '
                b'validation budget exhausted");'
            ),
            b"  }",
            b'    const oxlintBin = join(TOOL_DIR, "node_modules", ".bin", "oxlint");',
            b"    const oxlintArgs = [",
            b"    ];",
            b"  const exec = spawnSync(oxlintBin, oxlintArgs, {",
            b'    encoding: "utf8",',
            b"    timeout: timeoutMs,",
            b'    killSignal: "SIGKILL",',
            b"  });",
            b"  return exec;",
            b"}",
            (
                b"function runValidation({ codes, lang, mode, codeShape, "
                b"oxlintBudgetMs }) {"
            ),
            b"  const entries = codes;",
            b"  const lintTargets = codes;",
            b"  const lintMap = runLintBatch(entries, oxlintBudgetMs);",
            b"  const combinedLintMap = runLintBatch(lintTargets, oxlintBudgetMs);",
            b"  return [lintMap, combinedLintMap];",
            b"}",
            b"async function main(payload) {",
            b"  const codes = [];",
            b'  const lang = "js";',
            b'  const mode = "syntax+lint";',
            b'  const codeShape = "auto";',
            b"  const oxlintBudgetMs = mapBudgetMs(payload?.timeout_ms);",
            (
                b"  const out = runValidation({ codes, lang, mode, codeShape, "
                b"oxlintBudgetMs });"
            ),
            b"  return out;",
            b"}",
        )),
        _OXC_CALLER_PATH: b"\n".join((
            b"_OXC_TIMEOUT_S = 30",
            b'"timeout_ms": int(_OXC_TIMEOUT_S * 1000),',
            b"timeout = _OXC_TIMEOUT_S,",
            b"except subprocess.TimeoutExpired:",
            (b'return _fallback_results(len(code_values), "OXC validation timed out")'),
        )),
        _SETUP_SH_PATH: (
            b"# \xe2\x94\x80\xe2\x94\x80 oxc-validator runtime \xe2\x94\x80\xe2\x94\x80\n"
            b"# Node, so do not run npm install against an unsuitable/absent system Node.\n"
            b'if [ -d "$_OXC_DIR" ] && [ "${NODE_SOURCE:-}" != skip ] '
            b"&& command -v npm &>/dev/null; then\n"
            b'    run_quiet_no_exit "npm install (oxc validator runtime)" '
            b"npm install --no-fund --no-audit --loglevel=error "
            b'"${_NPM_REGISTRY_ARGS[@]+"${_NPM_REGISTRY_ARGS[@]}"}" '
            b"|| _oxc_install_rc=$?\n"
            b"# No npm on PATH: skip rather than abort; the backend Node resolver degrades\n"
            b'    substep "OXC validator runtime skipped (no npm found); '
            b'code validation degrades until Node is available" "$C_WARN"\n'
            b"# \xe2\x94\x80\xe2\x94\x80 Python venv + deps \xe2\x94\x80\xe2\x94\x80\n"
        ),
        _SETUP_PS1_PATH: (
            b'if ((Test-Path $OxcValidatorDir) -and $NodeSource -ne "skip" -and '
            b"(Get-Command npm -ErrorAction SilentlyContinue)) {\n"
            b"    $oxcInstallExit = Invoke-SetupCommand "
            b"{ npm install @NpmRegistryArgs }\n"
            b'    Write-StudioLine "[ERROR] OXC validator npm install failed '
            b'(exit code $oxcInstallExit)" -ForegroundColor Red\n'
            b"    # No npm on PATH (e.g. a pip install with no system Node and no isolated Node\n"
            b'    substep "OXC validator runtime skipped (no npm found); '
            b'code validation degrades until Node is available" "Yellow"\n'
            b"Remove-AgentInstructionFiles -Roots @(\n"
        ),
    }


def _cargo_manifest_bytes(*, rust_version: str = "1.89") -> bytes:
    return f'[package]\nrust-version = "{rust_version}"\n'.encode()


def _backend_sdist_bytes(source_payloads: dict[str, bytes]) -> bytes:
    sdist_buffer = io.BytesIO()
    with tarfile.open(fileobj=sdist_buffer, mode="w:gz") as archive:
        metadata = b"Metadata-Version: 2.4\n"
        metadata_member = tarfile.TarInfo(f"unsloth-{_BACKEND_VERSION}/PKG-INFO")
        metadata_member.size = len(metadata)
        archive.addfile(metadata_member, io.BytesIO(metadata))
        for path, payload in source_payloads.items():
            member = tarfile.TarInfo(f"unsloth-{_BACKEND_VERSION}/{path}")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return sdist_buffer.getvalue()


def _backend_sdist_with_members(
    members: Iterable[tuple[str, bytes | None]],
) -> bytes:
    sdist_buffer = io.BytesIO()
    with tarfile.open(fileobj=sdist_buffer, mode="w:gz") as archive:
        for path, payload in members:
            member = tarfile.TarInfo(f"unsloth-{_BACKEND_VERSION}/{path}")
            if payload is None:
                member.type = tarfile.DIRTYPE
            else:
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
                continue
            archive.addfile(member)
    return sdist_buffer.getvalue()


def _backend_sdist_with_pax_comment(comment: str) -> bytes:
    sdist_buffer = io.BytesIO()
    with tarfile.open(
        fileobj=sdist_buffer,
        mode="w:gz",
        format=tarfile.PAX_FORMAT,
    ) as archive:
        payload = b"{}"
        member = tarfile.TarInfo(f"unsloth-{_BACKEND_VERSION}/{_OXC_PACKAGE_PATH}")
        member.size = len(payload)
        member.pax_headers = {"comment": comment}
        archive.addfile(member, io.BytesIO(payload))
    return sdist_buffer.getvalue()


def _install_oxc_discovery_fakes(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    source_payloads: dict[str, bytes],
    *,
    sdist_payloads: dict[str, bytes] | None = None,
    sdist_digest: object | None = None,
    sdist_size: object | None = None,
) -> None:
    manifest = _manifest_bytes()
    sdist = _backend_sdist_bytes(sdist_payloads or source_payloads)

    async def github_api(
        _session: object,
        path: str,
        **_kwargs: object,
    ) -> object:
        if path.endswith("releases/latest"):
            return _release_payload(manifest)
        return {"sha": _COMMIT}

    raw_prefix = f"https://raw.githubusercontent.com/unslothai/unsloth/{_COMMIT}/"

    async def fetch_bytes(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> bytes:
        if url == _MANIFEST_URL:
            return manifest
        if url == _BACKEND_URL:
            return sdist
        if url == f"{raw_prefix}unsloth/_version.py":
            return f'__version__ = "{_SOURCE_PYTHON_VERSION}"\n'.encode()
        path = url.removeprefix(raw_prefix)
        if path == _CARGO_MANIFEST_PATH:
            return _cargo_manifest_bytes()
        return source_payloads[path]

    async def fetch_pypi(
        _session: object,
        _url: str,
        **_kwargs: object,
    ) -> object:
        return _pypi_payload(
            digest=(
                hashlib.sha256(sdist).hexdigest()
                if sdist_digest is None
                else sdist_digest
            ),
            size=len(sdist) if sdist_size is None else sdist_size,
        )

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        github_api,
    )
    monkeypatch.setattr(module, "fetch_github_api", github_api)
    monkeypatch.setattr(module, "fetch_url", fetch_bytes)
    monkeypatch.setattr(module, "fetch_json", fetch_pypi)


def _set_json_path(
    payloads: dict[str, bytes],
    source_path: str,
    keys: tuple[str, ...],
    value: object,
) -> None:
    payload = cast("dict[str, Any]", json.loads(payloads[source_path]))
    target = payload
    for key in keys[:-1]:
        target = cast("dict[str, Any]", target[key])
    target[keys[-1]] = value
    payloads[source_path] = json.dumps(payload, sort_keys=True).encode()


def test_unsloth_static_closures_and_export_truth_are_current() -> None:
    """Completed closure evidence must agree with the exported package state."""
    module = _load_updater_module()
    source = SourceEntry.model_validate(
        json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    )
    plan = json.loads((_PACKAGE_DIR / "closure-plan.json").read_text(encoding="utf-8"))
    assert source.version is not None
    assert source.commit is not None
    assert source.urls is not None
    hashes = source.hashes.entries
    assert hashes is not None

    def source_hash(hash_type: str, url: str | None = None) -> str:
        matches = [
            entry.hash
            for entry in hashes
            if entry.hash_type == hash_type and entry.url == url
        ]
        assert len(matches) == 1
        return matches[0]

    manifest_url = source.urls["releaseManifest"]
    backend_url = source.urls["backendSdist"]
    assert plan["app"] == {
        "commit": source.commit,
        "sourceHash": source_hash("srcHash"),
        "tag": f"v{source.version}",
        "version": source.version,
    }
    assert plan["releaseManifest"] == {
        "hash": source_hash("sha256", manifest_url),
        "pypiVersion": plan["backend"]["version"],
        "version": source.version,
    }
    assert plan["backend"]["sdistHash"] == source_hash("sha256", backend_url)
    assert plan["backend"]["sourceTagVersion"]
    assert plan["status"] == "exported-and-validated"
    assert plan["packageExported"] is True
    assert backend_url.endswith(f"/unsloth-{plan['backend']['version']}.tar.gz")
    assert module._PYPI_VERSION_PATTERN.fullmatch(plan["backend"]["sourceTagVersion"])
    assert plan["closurePolicy"] == {
        "allowPlaceholderHashes": False,
        "allowPrebuiltHelperFallbacks": False,
        "allowVendorDesktopBinary": False,
        "requireSourceProvenance": True,
    }
    assert plan["blockers"] == []
    patch_policy = plan["patchPolicy"]
    assert (
        patch_policy["verificationStatus"]
        == "exact-source-replay-and-behavior-tests-passed"
    )
    assert "mutationsBlocked" not in patch_policy
    assert set(patch_policy["desktopMutationsBlocked"]) == {
        "backend-first-run-install-command",
        "backend-repair-command",
        "backend-update-command",
        "launch-agent",
        "tauri-self-update",
    }
    assert (_PACKAGE_DIR / "default.nix").is_file()
    assert module._OXC_SOURCE_PATHS == _OXC_SOURCE_PATHS


def test_unsloth_validates_the_candidate_and_public_export_before_promotion() -> None:
    """A source-only refresh must fail until closure evidence is current."""
    updater = _load_updater_module().UnslothUpdater()

    assert updater.supported_platforms == ("aarch64-darwin",)
    assert updater.derivation_validations == (
        DerivationValidation(
            installable=("path:.#pkgs.{system}.{name}.storePathAppCandidateSmoke"),
            systems=("aarch64-darwin",),
            mode="build",
        ),
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )


def test_unsloth_helper_source_plan_is_complete_and_immutable() -> None:
    """Native helpers and provenance references must use immutable source payloads."""
    sources = json.loads(
        (_PACKAGE_DIR / "runtime-sources.json").read_text(encoding="utf-8")
    )

    assert sources == {
        "fixPathEnv": {
            "commit": "c4c45d503ea115a839aae718d02f79e7c7f0f673",
            "hash": "sha256-bZCpcvb0Lhh5PU23QisWsCqF/b06eXLj7KfUStet1kc=",
            "role": "cargo-lock-git-source-provenance-reference-only",
            "url": (
                "https://github.com/tauri-apps/fix-path-env-rs/archive/"
                "c4c45d503ea115a839aae718d02f79e7c7f0f673.tar.gz"
            ),
        },
        "llamaCpp": {
            "commit": "7a556b8f93d601cb277c0545e3e6166b45ebfac8",
            "hash": "sha256-o3b/1CHyCrF6HF+97Xi+uDd93+USi5eOOMyB1U5jYhk=",
            "tag": "b10472-mix-4b653db",
            "url": (
                "https://github.com/unslothai/llama.cpp/releases/download/"
                "b10472-mix-4b653db/"
                "llama.cpp-source-commit-7a556b8f93d601cb277c0545e3e6166b45ebfac8.tar.gz"
            ),
        },
        "stableDiffusionCpp": {
            "commit": "13b9d92b5e9a1563536c9c980e700470f9ab6702",
            "hash": "sha256-aW/710d1hUbpuMc5AI06Tg5aGuGryoKBR8Wdc7FuZ9U=",
            "submodules": {
                "examples/server/frontend": {
                    "commit": "c4bce3d6b3f236614cca21014f076083b7270ba8",
                    "hash": "sha256-EmVUPQLrq1YM7mc0s78Ukegxxr40NmR1BdaTndePKZM=",
                    "url": (
                        "https://github.com/leejet/sdcpp-webui/archive/"
                        "c4bce3d6b3f236614cca21014f076083b7270ba8.tar.gz"
                    ),
                },
                "ggml": {
                    "commit": "eced84c86f8b012c752c016f7fe789adea168e1e",
                    "hash": "sha256-gDb5MNvpCa/7zs3acTfWcVIHZNQ5aWLSucPfR2yynLA=",
                    "url": (
                        "https://github.com/leejet/ggml/archive/"
                        "eced84c86f8b012c752c016f7fe789adea168e1e.tar.gz"
                    ),
                },
                "thirdparty/libwebm": {
                    "commit": "5bf12267eea773a32fcf4949de52b0add158a8d5",
                    "hash": "sha256-KUBJoD015EgKlKXO2WyA6/1BFFVJZnCcjJfeTxm9lBo=",
                    "url": (
                        "https://github.com/webmproject/libwebm/archive/"
                        "5bf12267eea773a32fcf4949de52b0add158a8d5.tar.gz"
                    ),
                },
                "thirdparty/libwebp": {
                    "commit": "0c9546f7efc61eac7f79ae115c3f99c91c21c443",
                    "hash": "sha256-bLQzBwtEYReQZ7CQGmguThSyIDVPw1iEwbExWt7e/Jk=",
                    "url": (
                        "https://github.com/webmproject/libwebp/archive/"
                        "0c9546f7efc61eac7f79ae115c3f99c91c21c443.tar.gz"
                    ),
                },
            },
            "tag": "master-813-bfbef5b-u13b9d92",
            "url": (
                "https://github.com/unslothai/stable-diffusion.cpp/archive/"
                "13b9d92b5e9a1563536c9c980e700470f9ab6702.tar.gz"
            ),
        },
        "whisperCpp": {
            "commit": "306c88f4d1286aec1bf96e544632897886af5501",
            "hash": "sha256-+kphN5LnSSl8X2hJIQqJ5DvCokk7zY6ESsCpC3om+FY=",
            "tag": "v1.9.2-unsloth.11",
            "url": (
                "https://github.com/unslothai/whisper.cpp/releases/download/"
                "v1.9.2-unsloth.11/"
                "whisper.cpp-source-commit-306c88f4d1286aec1bf96e544632897886af5501.tar.gz"
            ),
        },
    }


def _write_cargo_identity_fixture(tmp_path: Path) -> Path:
    """Write a minimal coherent copy of the two upstream Cargo documents."""
    cargo_root = tmp_path / "studio/src-tauri"
    cargo_root.mkdir(parents=True, exist_ok=True)
    (cargo_root / "Cargo.toml").write_text(
        """[package]
name = "unsloth-studio"
version = "2026.4.8"

[dependencies]
dirs = "6"
regex = "1"
open = "5"
process-wrap = { version = "9", features = ["std"] }
fix-path-env = { git = "https://github.com/tauri-apps/fix-path-env-rs" }
""",
        encoding="utf-8",
    )
    (cargo_root / "Cargo.lock").write_text(
        """version = 4

[[package]]
name = "unsloth-studio"
version = "2026.4.8"
dependencies = [
 "fix-path-env",
]

[[package]]
name = "fix-path-env"
version = "0.0.0"
source = "git+https://github.com/tauri-apps/fix-path-env-rs#c4c45d503ea115a839aae718d02f79e7c7f0f673"
dependencies = [
 "home",
 "strip-ansi-escapes",
 "thiserror 1.0.69",
]
""",
        encoding="utf-8",
    )
    return cargo_root


def test_unsloth_cargo_transform_derives_release_and_git_identities(
    tmp_path: Path,
) -> None:
    """Vendoring inputs must come from the candidate version and immutable lock."""
    module = _load_patch_module()
    cargo_root = _write_cargo_identity_fixture(tmp_path)
    candidate_version = "9.8.7-beta.2"

    assert (
        module.main([
            str(tmp_path),
            "--cargo-only",
            "--desktop-version",
            candidate_version,
        ])
        == 0
    )

    revision = "c4c45d503ea115a839aae718d02f79e7c7f0f673"
    url = "https://github.com/tauri-apps/fix-path-env-rs"
    manifest = tomllib.loads((cargo_root / "Cargo.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((cargo_root / "Cargo.lock").read_text(encoding="utf-8"))
    assert manifest["package"] == {
        "name": "unsloth-studio",
        "version": candidate_version,
    }
    assert manifest["dependencies"]["fix-path-env"] == {
        "git": url,
        "rev": revision,
    }
    packages = {package["name"]: package for package in lock["package"]}
    assert packages["unsloth-studio"]["version"] == candidate_version
    assert packages["fix-path-env"]["source"] == (
        f"git+{url}?rev={revision}#{revision}"
    )


def test_unsloth_cargo_transform_rejects_incoherent_release_identity(
    tmp_path: Path,
) -> None:
    """Source drift must fail before the transformer writes either Cargo file."""
    module = _load_patch_module()
    cargo_root = _write_cargo_identity_fixture(tmp_path)
    original_manifest = (cargo_root / "Cargo.toml").read_text(encoding="utf-8")
    lock_path = cargo_root / "Cargo.lock"
    original_lock = lock_path.read_text(encoding="utf-8")
    lock_path.write_text(
        original_lock.replace('version = "2026.4.8"', 'version = "2026.4.9"', 1),
        encoding="utf-8",
    )
    drifted_lock = lock_path.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="desktop versions disagree"):
        module.patch_cargo_tree(tmp_path, _VERSION)

    assert (cargo_root / "Cargo.toml").read_text(encoding="utf-8") == original_manifest
    assert lock_path.read_text(encoding="utf-8") == drifted_lock


@pytest.mark.parametrize(
    ("document", "anchor", "replacement", "error_type", "message"),
    [
        ("Cargo.toml", "[package]", '["package"]', RuntimeError, r"one \[package\]"),
        (
            "Cargo.toml",
            'version = "2026.4.8"',
            '"version" = "2026.4.8"',
            RuntimeError,
            "one version assignment",
        ),
        (
            "Cargo.lock",
            "[[package]]",
            '[["package"]]',
            RuntimeError,
            "one Cargo.lock package named unsloth-studio",
        ),
        (
            "Cargo.lock",
            'version = "2026.4.8"',
            '"version" = "2026.4.8"',
            RuntimeError,
            "one version assignment",
        ),
        (
            "Cargo.toml",
            'name = "unsloth-studio"',
            'name = "other"',
            RuntimeError,
            "must define package unsloth-studio",
        ),
        (
            "Cargo.toml",
            'version = "2026.4.8"',
            "version = 20260408",
            TypeError,
            "package version must be a string",
        ),
        (
            "Cargo.lock",
            None,
            'version = 4\npackage = ["not-an-object"]\n',
            TypeError,
            "object package entries",
        ),
        (
            "Cargo.lock",
            'name = "unsloth-studio"',
            'name = "other"',
            RuntimeError,
            "one unsloth-studio package",
        ),
        (
            "Cargo.toml",
            "[dependencies]",
            'dependencies = "not-an-object"',
            TypeError,
            "object dependencies",
        ),
        (
            "Cargo.toml",
            'fix-path-env = { git = "https://github.com/tauri-apps/fix-path-env-rs" }',
            'fix-path-env = "not-an-inline-table"',
            TypeError,
            "must be an inline table",
        ),
        (
            "Cargo.toml",
            "https://github.com/tauri-apps/fix-path-env-rs",
            "https://example.invalid/fix-path-env-rs",
            RuntimeError,
            "unexpected Git source",
        ),
        (
            "Cargo.toml",
            'fix-path-env = { git = "https://github.com/tauri-apps/fix-path-env-rs" }',
            (
                'fix-path-env = { git = "https://github.com/tauri-apps/'
                'fix-path-env-rs", branch = "main" }'
            ),
            RuntimeError,
            "unsupported source selectors",
        ),
        (
            "Cargo.lock",
            (
                'source = "git+https://github.com/tauri-apps/fix-path-env-rs'
                '#c4c45d503ea115a839aae718d02f79e7c7f0f673"'
            ),
            "source = 1",
            TypeError,
            "source must be a string",
        ),
        (
            "Cargo.lock",
            "https://github.com/tauri-apps/fix-path-env-rs",
            "https://example.invalid/fix-path-env-rs",
            RuntimeError,
            "immutable supported Git identity",
        ),
        (
            "Cargo.lock",
            "fix-path-env-rs#c4c45d503ea115a839aae718d02f79e7c7f0f673",
            (
                "fix-path-env-rs?rev=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                "#c4c45d503ea115a839aae718d02f79e7c7f0f673"
            ),
            RuntimeError,
            "rev selector disagrees with its locked commit",
        ),
        (
            "Cargo.toml",
            'fix-path-env = { git = "https://github.com/tauri-apps/fix-path-env-rs" }',
            (
                'fix-path-env = { git = "https://github.com/tauri-apps/'
                'fix-path-env-rs", rev = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }'
            ),
            RuntimeError,
            "rev selector disagrees with Cargo.lock",
        ),
    ],
)
def test_unsloth_cargo_transform_fails_closed_on_structural_drift(
    tmp_path: Path,
    document: str,
    anchor: str | None,
    replacement: str,
    error_type: type[Exception],
    message: str,
) -> None:
    """Every ambiguous or incoherent Cargo identity must block all writes."""
    module = _load_patch_module()
    cargo_root = _write_cargo_identity_fixture(tmp_path)
    target = cargo_root / document
    source = target.read_text(encoding="utf-8")
    drifted = replacement if anchor is None else source.replace(anchor, replacement, 1)
    assert drifted != source
    target.write_text(drifted, encoding="utf-8")
    original = {
        name: (cargo_root / name).read_text(encoding="utf-8")
        for name in ("Cargo.toml", "Cargo.lock")
    }

    with pytest.raises(error_type, match=message):
        module.patch_cargo_tree(tmp_path, _VERSION)

    assert {
        name: (cargo_root / name).read_text(encoding="utf-8")
        for name in ("Cargo.toml", "Cargo.lock")
    } == original


def test_unsloth_cargo_transform_rejects_invalid_release_version(
    tmp_path: Path,
) -> None:
    """The generated Cargo patch accepts only release-like desktop versions."""
    module = _load_patch_module()
    _write_cargo_identity_fixture(tmp_path)

    with pytest.raises(RuntimeError, match="invalid Unsloth desktop version"):
        module.patch_cargo_tree(tmp_path, "release/latest")


@pytest.mark.parametrize(
    "arguments",
    [
        ["--cargo-only", "--backend-root", "backend", "--desktop-version", _VERSION],
        ["--cargo-only"],
        ["--desktop-version", _VERSION],
    ],
)
def test_unsloth_cargo_only_cli_rejects_ambiguous_modes(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    """Cargo-only generation must not silently select an incomplete patch mode."""
    with pytest.raises(SystemExit) as raised:
        _load_patch_module().main([str(tmp_path), *arguments])

    assert raised.value.code == 2


def test_unsloth_desktop_patch_uses_the_materialized_frontend_dist() -> None:
    """Tauri must verify the package-owned frontend instead of invoking npm."""
    module = _load_patch_module()
    build_patch = next(
        patch
        for patch in module._PATCHES
        if patch.path == Path("studio/src-tauri/tauri.conf.json")
        and '"beforeBuildCommand"' in patch.old
    )

    assert '"script": "npm run build"' in build_patch.old
    assert '"script": "test -f dist/index.html"' in build_patch.new
    assert '"cwd": "../frontend"' in build_patch.new
    assert "npm" not in build_patch.new


def test_unsloth_runtime_dependency_anchor_has_an_explicit_empty_package() -> None:
    """The runtime closure must not auto-discover repository helper modules."""
    pyproject = tomllib.loads(
        (_PACKAGE_DIR / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert pyproject["build-system"] == {
        "build-backend": "setuptools.build_meta",
        "requires": ["setuptools"],
    }
    assert pyproject["tool"]["setuptools"] == {"py-modules": []}


def test_unsloth_python_lock_is_exactly_scoped_to_darwin_arm64() -> None:
    """The backend closure must be reproducible for the supported Darwin target."""
    source = SourceEntry.model_validate(
        json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    )
    plan = json.loads((_PACKAGE_DIR / "closure-plan.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads(
        (_PACKAGE_DIR / "pyproject.toml").read_text(encoding="utf-8")
    )
    lock = tomllib.loads((_PACKAGE_DIR / "uv.lock").read_text(encoding="utf-8"))
    target = (
        "sys_platform == 'darwin' and platform_machine == 'arm64' "
        "and python_version == '3.12'"
    )

    assert source.urls is not None
    backend_url = source.urls["backendSdist"]
    assert pyproject["project"] == {
        "dependencies": [f"unsloth[studio] @ {backend_url}"],
        "name": "nixcfg-unsloth-runtime",
        "requires-python": "==3.12.*",
        "version": pyproject["project"]["version"],
    }
    assert pyproject["tool"]["uv"] == {
        "environments": [target],
        "override-dependencies": [
            "triton>=3.0.0 ; sys_platform == 'linux'",
            (
                "xformers>=0.0.27.post2 ; sys_platform == 'linux' and "
                "platform_machine == 'x86_64'"
            ),
        ],
        "required-environments": [target],
    }
    assert lock["requires-python"] == "==3.12.*"
    marker = "platform_machine == 'arm64' and sys_platform == 'darwin'"
    assert lock["resolution-markers"] == [marker]
    assert lock["supported-markers"] == [marker]
    assert lock["required-markers"] == [marker]

    packages = {package["name"]: package for package in lock["package"]}
    assert (
        packages["nixcfg-unsloth-runtime"]["version"]
        == (pyproject["project"]["version"])
    )
    assert packages["unsloth"]["version"] == plan["backend"]["version"]
    assert packages["unsloth"]["source"] == {"url": backend_url}
    backend_hashes = [
        entry
        for entry in source.hashes.entries or ()
        if entry.hash_type == "sha256" and entry.url == backend_url
    ]
    assert len(backend_hashes) == 1
    algorithm, encoded = backend_hashes[0].hash.split("-", 1)
    assert algorithm == "sha256"
    assert packages["unsloth"]["sdist"] == {
        "hash": f"sha256:{base64.b64decode(encoded, validate=True).hex()}"
    }
    assert packages["unsloth"]["dependencies"]
    assert packages["unsloth"]["optional-dependencies"]["studio"]
    assert {"torch", "torchvision", "unsloth-zoo"} <= packages.keys()
    assert {"fastapi", "fastmcp", "pymupdf", "sqlite-vec", "uvicorn"} <= (
        packages.keys()
    )
    assert {"triton", "triton-windows", "xformers"}.isdisjoint(packages)

    registry_packages = [
        package
        for package in packages.values()
        if "registry" in package.get("source", {})
    ]
    assert registry_packages
    for package in registry_packages:
        artifacts = [*package.get("wheels", [])]
        if sdist := package.get("sdist"):
            artifacts.append(sdist)
        assert artifacts, package["name"]
        assert all(
            artifact["hash"].startswith("sha256:")
            and len(artifact["hash"]) == len("sha256:") + 64
            for artifact in artifacts
        ), package["name"]


def test_unsloth_python_project_renderer_has_a_stable_local_identity() -> None:
    """Only the source-owned backend URL should vary between generated projects."""
    module = _load_updater_module()
    first = tomllib.loads(module._render_python_project(_BACKEND_URL))
    next_url = _BACKEND_URL.replace(_BACKEND_VERSION, "2026.8.23")
    second = tomllib.loads(module._render_python_project(next_url))

    assert first["project"]["version"] == "0.0.0"
    assert second["project"]["version"] == "0.0.0"
    assert first["project"]["dependencies"] == [f"unsloth[studio] @ {_BACKEND_URL}"]
    assert second["project"]["dependencies"] == [f"unsloth[studio] @ {next_url}"]


def test_unsloth_resolves_release_commit_manifest_and_backend_sdist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery must retain the intentional tag/backend version split."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()
    manifest = _manifest_bytes()
    source_payloads = _oxc_source_payloads()
    sdist = _backend_sdist_bytes(source_payloads)
    api_paths: list[str] = []
    fetched_urls: list[str] = []

    async def github_api(
        _session: object,
        path: str,
        **_kwargs: object,
    ) -> object:
        api_paths.append(path)
        if path.endswith("releases/latest"):
            return _release_payload(manifest)
        return {"sha": _COMMIT}

    async def fetch_bytes(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> bytes:
        fetched_urls.append(url)
        if url == _MANIFEST_URL:
            return manifest
        if url == _BACKEND_URL:
            return sdist
        raw_prefix = f"https://raw.githubusercontent.com/unslothai/unsloth/{_COMMIT}/"
        path = url.removeprefix(raw_prefix)
        if path == "unsloth/_version.py":
            return f'__version__ = "{_SOURCE_PYTHON_VERSION}"\n'.encode()
        if path == _CARGO_MANIFEST_PATH:
            return _cargo_manifest_bytes()
        return source_payloads[path]

    pypi_urls: list[str] = []

    async def fetch_pypi(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> object:
        pypi_urls.append(url)
        return _pypi_payload(
            digest=hashlib.sha256(sdist).hexdigest(),
            size=len(sdist),
        )

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        github_api,
    )
    monkeypatch.setattr(module, "fetch_github_api", github_api)
    monkeypatch.setattr(module, "fetch_url", fetch_bytes)
    monkeypatch.setattr(module, "fetch_json", fetch_pypi)

    info = run_async(updater.fetch_latest(object()))

    assert info == VersionInfo(
        version=_VERSION,
        metadata=_metadata(
            manifest_digest=hashlib.sha256(manifest).hexdigest(),
            manifest_size=len(manifest),
            backend_digest=hashlib.sha256(sdist).hexdigest(),
            backend_size=len(sdist),
        ),
    )
    assert api_paths == [
        "repos/unslothai/unsloth/releases/latest",
        f"repos/unslothai/unsloth/commits/{_TAG}",
    ]
    assert fetched_urls == [
        _MANIFEST_URL,
        (
            "https://raw.githubusercontent.com/unslothai/unsloth/"
            f"{_COMMIT}/unsloth/_version.py"
        ),
        (
            "https://raw.githubusercontent.com/unslothai/unsloth/"
            f"{_COMMIT}/{_CARGO_MANIFEST_PATH}"
        ),
        *(
            f"https://raw.githubusercontent.com/unslothai/unsloth/{_COMMIT}/{path}"
            for path in _OXC_SOURCE_PATHS
        ),
        _BACKEND_URL,
    ]
    assert pypi_urls == [f"https://pypi.org/pypi/unsloth/{_BACKEND_VERSION}/json"]


@pytest.mark.parametrize(
    ("declared", "expected"),
    [("1.89", "1.89.0"), ("1.89.1", "1.89.1"), ("2.0", "2.0.0")],
)
def test_unsloth_derives_exact_rust_toolchain_from_cargo(
    declared: str,
    expected: str,
) -> None:
    """The immutable Cargo manifest owns the Rust toolchain across releases."""
    module = _load_updater_module()

    assert (
        module._rust_toolchain_version(_cargo_manifest_bytes(rust_version=declared))
        == expected
    )


@pytest.mark.parametrize(
    ("manifest", "error_type", "message"),
    [
        (b"not toml =", RuntimeError, "Cargo manifest is invalid"),
        (b"\xff", RuntimeError, "Cargo manifest is invalid"),
        (b"[workspace]\n", TypeError, "Cargo package"),
        (b"[package]\nrust-version = 189\n", TypeError, "rust-version"),
        (
            b'[package]\nrust-version = "nightly"\n',
            RuntimeError,
            "invalid rust-version",
        ),
        (
            b'[package]\nrust-version = "01.89"\n',
            RuntimeError,
            "invalid rust-version",
        ),
    ],
)
def test_unsloth_rejects_ambiguous_rust_toolchain_metadata(
    manifest: bytes,
    error_type: type[Exception],
    message: str,
) -> None:
    """Malformed or non-stable selectors must fail before source hashing."""
    module = _load_updater_module()

    with pytest.raises(error_type, match=message):
        module._rust_toolchain_version(manifest)


def test_unsloth_rejects_an_oxc_dependency_range_incoherent_with_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source-owned dependency range must still resolve through its lock."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads(parser_dependency="^0.132.0")
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC locked runtime"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_accepts_coherent_source_owned_oxc_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routine OXC releases flow from the immutable manifest and lock."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads(
        parser_dependency="^0.132.0",
        parser_version="0.132.1",
        oxlint_dependency="^1.66.0",
        oxlint_version="1.66.2",
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    assert run_async(module.UnslothUpdater().fetch_latest(object())).version == _VERSION


def test_unsloth_accepts_backend_sdist_validator_comment_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explanatory comments are outside the bounded runtime contract."""
    module = _load_updater_module()
    github_payloads = _oxc_source_payloads()
    sdist_payloads = dict(github_payloads)
    sdist_payloads[_OXC_VALIDATE_PATH] += b"\n// sdist-only documentation\n"
    _install_oxc_discovery_fakes(
        monkeypatch,
        module,
        github_payloads,
        sdist_payloads=sdist_payloads,
    )

    assert run_async(module.UnslothUpdater().fetch_latest(object())).version == _VERSION


def test_unsloth_rejects_backend_sdist_oxc_patch_seam_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The packaged sdist must retain the exact seam rewritten by Nix."""
    module = _load_updater_module()
    github_payloads = _oxc_source_payloads()
    sdist_payloads = dict(github_payloads)
    sdist_payloads[_OXC_VALIDATE_PATH] = sdist_payloads[_OXC_VALIDATE_PATH].replace(
        module.OXC_VALIDATOR_PATCH_SEAM.encode(),
        b"",
    )
    _install_oxc_discovery_fakes(
        monkeypatch,
        module,
        github_payloads,
        sdist_payloads=sdist_payloads,
    )

    with pytest.raises(RuntimeError, match="backend sdist OXC source audit"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


@pytest.mark.parametrize(
    "members",
    [
        [(_OXC_PACKAGE_PATH, b"first"), (_OXC_PACKAGE_PATH, b"duplicate")],
        [(_OXC_PACKAGE_PATH, None)],
    ],
)
def test_unsloth_rejects_ambiguous_backend_sdist_oxc_members(
    members: list[tuple[str, bytes | None]],
) -> None:
    """Audited sdist paths must each identify one regular archive member."""
    module = _load_updater_module()

    with pytest.raises(RuntimeError, match="source archive is ambiguous"):
        module._oxc_sources_from_backend_sdist(
            _backend_sdist_with_members(members),
            version=_BACKEND_VERSION,
        )


def test_unsloth_rejects_backend_sdist_oxc_path_alias() -> None:
    """A later non-canonical member must not overwrite an audited source."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    alias_path = _OXC_VALIDATE_PATH.replace("/validate.mjs", "/./validate.mjs")
    members = [*source_payloads.items(), (alias_path, b"unreviewed alias payload")]

    with pytest.raises(RuntimeError, match="source archive is ambiguous"):
        module._oxc_sources_from_backend_sdist(
            _backend_sdist_with_members(members),
            version=_BACKEND_VERSION,
        )


def test_unsloth_bounds_backend_sdist_member_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archive header bombs must stop before unbounded member indexing."""
    module = _load_updater_module()
    monkeypatch.setattr(module, "_MAX_BACKEND_SDIST_MEMBERS", 1, raising=False)

    with pytest.raises(RuntimeError, match="member limit"):
        module._oxc_sources_from_backend_sdist(
            _backend_sdist_with_members([("PKG-INFO", b"one"), ("README", b"two")]),
            version=_BACKEND_VERSION,
        )


def test_unsloth_bounds_backend_sdist_expanded_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declared member sizes must cap decompression work for the whole archive."""
    module = _load_updater_module()
    monkeypatch.setattr(
        module,
        "_MAX_BACKEND_SDIST_EXPANDED_BYTES",
        20_000,
    )

    with pytest.raises(RuntimeError, match="expanded size limit"):
        module._oxc_sources_from_backend_sdist(
            _backend_sdist_with_members([("PKG-INFO", b"x" * 20_001)]),
            version=_BACKEND_VERSION,
        )


def test_unsloth_bounds_backend_sdist_pax_header_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The byte budget must include extension headers hidden by tarfile."""
    module = _load_updater_module()
    monkeypatch.setattr(
        module,
        "_MAX_BACKEND_SDIST_EXPANDED_BYTES",
        1024,
    )

    with pytest.raises(RuntimeError, match="expanded size limit"):
        module._oxc_sources_from_backend_sdist(
            _backend_sdist_with_pax_comment("x" * 10_000),
            version=_BACKEND_VERSION,
        )


def test_unsloth_bounds_backend_sdist_audited_source_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audited members must be bounded before their decompressed bytes are read."""
    module = _load_updater_module()
    monkeypatch.setattr(module, "_MAX_OXC_SOURCE_BYTES", 3, raising=False)

    with pytest.raises(RuntimeError, match="audited source size limit"):
        module._oxc_sources_from_backend_sdist(
            _backend_sdist_with_members([(_OXC_PACKAGE_PATH, b"four")]),
            version=_BACKEND_VERSION,
        )


def test_unsloth_rejects_missing_backend_sdist_oxc_member() -> None:
    """The packaged audit must fail closed when any runtime source is absent."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    del source_payloads[_OXC_VALIDATE_PATH]

    with pytest.raises(RuntimeError, match="source is missing"):
        module._oxc_sources_from_backend_sdist(
            _backend_sdist_bytes(source_payloads),
            version=_BACKEND_VERSION,
        )


def test_unsloth_rejects_invalid_backend_sdist_archive() -> None:
    """Malformed PyPI source archives must not reach candidate validation."""
    module = _load_updater_module()

    with pytest.raises(RuntimeError, match="source archive is invalid"):
        module._oxc_sources_from_backend_sdist(
            b"not a gzip-compressed tar archive",
            version=_BACKEND_VERSION,
        )


@pytest.mark.parametrize(
    ("sdist_digest", "sdist_size"),
    [("0" * 64, None), (None, 1)],
)
def test_unsloth_rejects_backend_sdist_metadata_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    sdist_digest: object | None,
    sdist_size: object | None,
) -> None:
    """The audited bytes must match PyPI's exact digest and size declaration."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    _install_oxc_discovery_fakes(
        monkeypatch,
        module,
        source_payloads,
        sdist_digest=sdist_digest,
        sdist_size=sdist_size,
    )

    with pytest.raises(RuntimeError, match="does not match PyPI metadata"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_oversized_backend_sdist_metadata() -> None:
    """Discovery must reject an sdist too large to audit before downloading it."""
    module = _load_updater_module()

    with pytest.raises(RuntimeError, match="exceeds the audit size limit"):
        module.UnslothUpdater._backend_sdist(
            _pypi_payload(size=(256 * 1024 * 1024) + 1),
            version=_BACKEND_VERSION,
        )


def test_unsloth_rejects_re_pinned_oxc_darwin_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock must retain the native arm64 parser selected on this platform."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    lock = json.loads(source_payloads[_OXC_LOCK_PATH])
    lock["packages"]["node_modules/@oxc-parser/binding-darwin-arm64"]["cpu"] = ["x64"]
    source_payloads[_OXC_LOCK_PATH] = json.dumps(lock, sort_keys=True).encode()
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC darwin-arm64 binding"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_incomplete_oxc_optional_dependency_lock_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every source-owned optional binding must retain fetchable lock metadata."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    lock = json.loads(source_payloads[_OXC_LOCK_PATH])
    runtime = lock["packages"]["node_modules/oxc-parser"]
    runtime["optionalDependencies"]["@oxc-parser/binding-unreviewed"] = "0.131.0"
    lock["packages"]["node_modules/@oxc-parser/binding-unreviewed"] = {
        "integrity": f"sha512-{base64.b64encode(b'x' * 64).decode()}",
        "optional": True,
        "version": "0.131.0",
    }
    source_payloads[_OXC_LOCK_PATH] = json.dumps(lock, sort_keys=True).encode()
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC locked runtime"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_accepts_a_coherent_source_owned_optional_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future OXC binding can flow from a complete immutable lock entry."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    lock = json.loads(source_payloads[_OXC_LOCK_PATH])
    runtime = lock["packages"]["node_modules/oxc-parser"]
    package_name = "@oxc-parser/binding-future"
    runtime["optionalDependencies"][package_name] = "0.131.0"
    lock["packages"][f"node_modules/{package_name}"] = {
        "integrity": f"sha512-{base64.b64encode(b'x' * 64).decode()}",
        "optional": True,
        "resolved": (
            "https://registry.npmjs.org/@oxc-parser/binding-future/-/"
            "binding-future-0.131.0.tgz"
        ),
        "version": "0.131.0",
    }
    source_payloads[_OXC_LOCK_PATH] = json.dumps(lock, sort_keys=True).encode()
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    assert run_async(module.UnslothUpdater().fetch_latest(object())).version == _VERSION


def test_unsloth_accepts_source_owned_oxc_transitive_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid locked metadata may change without duplicating it in the updater."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    lock = json.loads(source_payloads[_OXC_LOCK_PATH])
    lock["packages"]["node_modules/@oxc-project/types"] = {
        "integrity": f"sha512-{base64.b64encode(b'x' * 64).decode()}",
        "resolved": "https://registry.npmjs.org/@oxc-project/types/-/types-0.131.0.tgz",
        "version": "0.131.0",
    }
    source_payloads[_OXC_LOCK_PATH] = json.dumps(lock, sort_keys=True).encode()
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    assert run_async(module.UnslothUpdater().fetch_latest(object())).version == _VERSION


def test_unsloth_rejects_re_pinned_oxc_malformed_integrity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sha512 label alone must not pass as npm source provenance."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    _set_json_path(
        source_payloads,
        _OXC_LOCK_PATH,
        ("packages", "node_modules/oxc-parser", "integrity"),
        "sha512-not-base64!",
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC locked runtime"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_oxc_truncated_integrity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid base64 payload must still contain a full 64-byte SHA-512."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    _set_json_path(
        source_payloads,
        _OXC_LOCK_PATH,
        ("packages", "node_modules/oxc-parser", "integrity"),
        (
            "sha512-SJ3/7ZPbgie8dr5Z9BI/M51zZbpXba+hRSG0MDzVwMW5CRQg2fjY"
            "E0jHGlLX4eeiibGgC/mzoDFKSDHwVZEH"
        ),
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC locked runtime"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


@pytest.mark.parametrize(
    "keys",
    [
        ("packages", "node_modules/oxc-parser", "integrity"),
        (
            "packages",
            "node_modules/@oxc-parser/binding-darwin-arm64",
            "integrity",
        ),
        ("packages", "node_modules/oxlint", "integrity"),
        (
            "packages",
            "node_modules/@oxlint/binding-darwin-arm64",
            "integrity",
        ),
    ],
)
def test_unsloth_accepts_source_owned_valid_oxc_integrity(
    monkeypatch: pytest.MonkeyPatch,
    keys: tuple[str, ...],
) -> None:
    """The immutable lock owns valid package integrity values."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    _set_json_path(
        source_payloads,
        _OXC_LOCK_PATH,
        keys,
        f"sha512-{base64.b64encode(b'x' * 64).decode()}",
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    assert run_async(module.UnslothUpdater().fetch_latest(object())).version == _VERSION


def test_unsloth_rejects_re_pinned_oxc_non_sha512_integrity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater must preserve npm's SHA-512 integrity algorithm contract."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    _set_json_path(
        source_payloads,
        _OXC_LOCK_PATH,
        ("packages", "node_modules/oxc-parser", "integrity"),
        (
            "sha256-SJ3/7ZPbgie8dr5Z9BI/M51zZbpXba+hRSG0MDzVwMW5CRQg2fjY"
            "E0jHGlLX4eeiibGgC/mzoDFKSDHwVZEHRQ=="
        ),
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC locked runtime"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_oxc_setup_mutation_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blocker must stay tied to the exact runtime npm mutation path."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_SETUP_SH_PATH] = b"# npm mutation removed\n"
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC setup.sh npm install"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_additional_oxc_setup_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audited Unix block must not hide another runtime npm install."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_SETUP_SH_PATH] = source_payloads[_SETUP_SH_PATH].replace(
        b"\n",
        b"\nnpm install unexpected-runtime-package\n",
        1,
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC setup.sh npm install"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_split_oxc_setup_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shell continuation must not hide an added runtime npm command."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_SETUP_SH_PATH] = source_payloads[_SETUP_SH_PATH].replace(
        b"\n",
        b"\nnpm " + b"\\" + b"\n  install unexpected-runtime-package\n",
        1,
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC setup.sh npm install"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_quoted_oxc_setup_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quoted shell command indirection must not hide an added npm command."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_SETUP_SH_PATH] = source_payloads[_SETUP_SH_PATH].replace(
        b"\n",
        b"\neval 'npm install unexpected-runtime-package'\n",
        1,
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC setup.sh npm install"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_quoted_hash_oxc_setup_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quoted hash must not make later executable npm text look commented."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_SETUP_SH_PATH] = source_payloads[_SETUP_SH_PATH].replace(
        b"\n",
        b"\nprintf '#'; npm install unexpected-runtime-package\n",
        1,
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC setup.sh npm install"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_oxc_windows_setup_mutation_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows setup path must retain the same audited runtime mutation."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_SETUP_PS1_PATH] = b"# npm mutation removed\n"
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC setup.ps1 npm install"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_additional_oxc_windows_setup_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audited PowerShell block must reject another runtime npm install."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_SETUP_PS1_PATH] = source_payloads[_SETUP_PS1_PATH].replace(
        b"\n",
        (
            b"\n$unexpected = Invoke-SetupCommand "
            b"{ cmd /c npm install unexpected-runtime-package }\n"
        ),
        1,
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC setup.ps1 npm install"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_case_changed_oxc_windows_setup_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PowerShell command matching must remain case-insensitive."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_SETUP_PS1_PATH] = source_payloads[_SETUP_PS1_PATH].replace(
        b"\n",
        b"\nNPM install unexpected-runtime-package\n",
        1,
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC setup.ps1 npm install"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_quoted_oxc_windows_setup_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PowerShell's call operator must not hide a quoted npm executable."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_SETUP_PS1_PATH] = source_payloads[_SETUP_PS1_PATH].replace(
        b"\n",
        b'\n& "npm" install unexpected-runtime-package\n',
        1,
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC setup.ps1 npm install"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_quoted_hash_oxc_windows_setup_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PowerShell must not treat a quoted hash as a source comment."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_SETUP_PS1_PATH] = source_payloads[_SETUP_PS1_PATH].replace(
        b"\n",
        b"\nWrite-Output '#'; NPM install unexpected-runtime-package\n",
        1,
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC setup.ps1 npm install"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_oxc_validator_entrypoint_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation must keep using the locked parser and package-local oxlint."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_OXC_VALIDATE_PATH] = source_payloads[_OXC_VALIDATE_PATH].replace(
        b'from "oxc-parser"', b'from "other-parser"'
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC validator entrypoint"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


@pytest.mark.parametrize(
    "marker",
    [
        b'import { performance } from "node:perf_hooks";',
        b"const OXLINT_DEFAULT_BUDGET_MS = 30_000;",
        b"const OXLINT_BUDGET_MARGIN_MS = 2_000;",
        b"const OXLINT_MIN_TIMEOUT_MS = 1_000;",
        (
            b"return Number.isFinite(parsed) && parsed > 0 "
            b"? Math.floor(parsed) : OXLINT_DEFAULT_BUDGET_MS;"
        ),
        b"budgetMs - OXLINT_BUDGET_MARGIN_MS - performance.now()",
        b"if (timeoutMs < OXLINT_MIN_TIMEOUT_MS) {",
        b"oxlint skipped: validation budget exhausted",
        b"timeout: timeoutMs,",
        b'killSignal: "SIGKILL",',
        b"runLintBatch(entries, oxlintBudgetMs)",
        b"runLintBatch(lintTargets, oxlintBudgetMs)",
        b"const oxlintBudgetMs = mapBudgetMs(payload?.timeout_ms);",
        (b"runValidation({ codes, lang, mode, codeShape, oxlintBudgetMs })"),
    ],
)
def test_unsloth_rejects_re_pinned_oxc_validator_timeout_drift(
    monkeypatch: pytest.MonkeyPatch,
    marker: bytes,
) -> None:
    """Re-pinning must preserve the caller-budgeted hard-kill contract."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_OXC_VALIDATE_PATH] = source_payloads[_OXC_VALIDATE_PATH].replace(
        marker,
        b"",
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC validator timeout"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_re_pinned_oxc_validator_missing_timeout_call_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both lint modes must forward the caller's remaining timeout budget."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_OXC_VALIDATE_PATH] = source_payloads[_OXC_VALIDATE_PATH].replace(
        b"runLintBatch(entries, oxlintBudgetMs)",
        b"runLintBatch(entries)",
        1,
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC validator timeout"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


@pytest.mark.parametrize(
    "marker",
    [
        b"_OXC_TIMEOUT_S = 30",
        b'"timeout_ms": int(_OXC_TIMEOUT_S * 1000),',
        b"timeout = _OXC_TIMEOUT_S,",
        b"except subprocess.TimeoutExpired:",
        b'"OXC validation timed out"',
    ],
)
def test_unsloth_rejects_re_pinned_oxc_caller_timeout_drift(
    monkeypatch: pytest.MonkeyPatch,
    marker: bytes,
) -> None:
    """The Python caller must share the wrapper's timeout and fallback contract."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_OXC_CALLER_PATH] = source_payloads[_OXC_CALLER_PATH].replace(
        marker,
        b"",
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC caller timeout"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_accepts_validator_comment_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upstream comments must not require a new hard-coded source digest."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    source_payloads[_OXC_VALIDATE_PATH] += b"\n// upstream documentation\n"
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    assert run_async(module.UnslothUpdater().fetch_latest(object())).version == _VERSION


@pytest.mark.parametrize("copies", [0, 2])
def test_unsloth_rejects_ambiguous_oxc_validator_patch_seam(
    monkeypatch: pytest.MonkeyPatch,
    copies: int,
) -> None:
    """The updater must find exactly one instance of the Nix transformation seam."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    seam = module.OXC_VALIDATOR_PATCH_SEAM.encode()
    source_payloads[_OXC_VALIDATE_PATH] = source_payloads[_OXC_VALIDATE_PATH].replace(
        seam,
        seam * copies,
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC validator patch seam"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


@pytest.mark.parametrize(
    ("source_path", "keys", "value", "error_type", "message"),
    [
        (_OXC_PACKAGE_PATH, ("name",), "other", RuntimeError, "package identity"),
        (
            _OXC_PACKAGE_PATH,
            ("version",),
            "rolling",
            RuntimeError,
            "package identity",
        ),
        (
            _OXC_PACKAGE_PATH,
            ("dependencies", "oxlint"),
            "",
            RuntimeError,
            "dependencies drifted",
        ),
        (_OXC_PACKAGE_PATH, ("scripts",), {}, RuntimeError, "package shape"),
        (
            _OXC_LOCK_PATH,
            ("lockfileVersion",),
            2,
            RuntimeError,
            "lock identity",
        ),
        (
            _OXC_LOCK_PATH,
            ("packages", "", "name"),
            "other",
            RuntimeError,
            "lock root",
        ),
        (
            _OXC_LOCK_PATH,
            ("packages", "node_modules/oxlint", "version"),
            "1.66.0",
            RuntimeError,
            "locked runtime",
        ),
        (
            _OXC_LOCK_PATH,
            (
                "packages",
                "node_modules/oxlint",
                "optionalDependencies",
                "@oxlint/binding-darwin-arm64",
            ),
            "",
            RuntimeError,
            "locked runtime drifted",
        ),
        (
            _OXC_LOCK_PATH,
            (
                "packages",
                "node_modules/@oxlint/binding-darwin-arm64",
                "optional",
            ),
            False,
            RuntimeError,
            "locked runtime drifted",
        ),
        (
            _OXC_LOCK_PATH,
            (
                "packages",
                "node_modules/oxc-parser",
                "dependencies",
                "@oxc-project/types",
            ),
            1,
            TypeError,
            "dependencies .* drifted",
        ),
    ],
)
def test_unsloth_rejects_re_pinned_oxc_source_structure_drift(
    monkeypatch: pytest.MonkeyPatch,
    source_path: str,
    keys: tuple[str, ...],
    value: object,
    error_type: type[Exception],
    message: str,
) -> None:
    """Digest re-pinning alone cannot weaken the audited source structure."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    _set_json_path(source_payloads, source_path, keys, value)
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(error_type, match=message):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_release_without_immutable_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mutable target branch must not stand in for the tagged source tree."""
    module = _load_updater_module()
    manifest = _manifest_bytes()

    async def release_api(*_args: object, **_kwargs: object) -> object:
        return _release_payload(manifest)

    async def commit_api(*_args: object, **_kwargs: object) -> object:
        return {"sha": "main"}

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        release_api,
    )
    monkeypatch.setattr(module, "fetch_github_api", commit_api)

    with pytest.raises(RuntimeError, match="no immutable source commit"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (b'__version__ = "2026.8.17"\n', "2026.8.17"),
        (b'x = 1\n__version__: str = "ignored"\n__version__ = "ok"\n', "ok"),
    ],
)
def test_unsloth_source_version_uses_one_plain_literal(
    source: bytes, expected: str
) -> None:
    """The updater should mirror setuptools' static source-version contract."""
    assert _load_updater_module()._source_python_version(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        b"x = 1\n",
        b'__version__ = "one"\n__version__ = "two"\n',
        b"__version__ = get_version()\n",
    ],
)
def test_unsloth_rejects_ambiguous_source_version(source: bytes) -> None:
    """Dynamic, absent, or repeated source identities are not immutable evidence."""
    with pytest.raises(RuntimeError, match="exactly one literal"):
        _load_updater_module()._source_python_version(source)


def test_unsloth_release_manifest_asset_is_exact_and_tag_pinned() -> None:
    """The updater must consume the API-authenticated manifest asset only."""
    module = _load_updater_module()
    manifest = _manifest_bytes()
    assert module.UnslothUpdater._release_manifest_asset(
        _release_payload(manifest),
        tag_name=_TAG,
    ) == (_MANIFEST_URL, hashlib.sha256(manifest).hexdigest(), len(manifest))


@pytest.mark.parametrize(
    ("mutate", "error_type", "message"),
    [
        (lambda payload: payload.pop("assets"), TypeError, "no asset list"),
        (lambda payload: payload.update(assets=[]), RuntimeError, "exactly one"),
        (
            lambda payload: cast("list[object]", payload["assets"]).append(
                cast("list[object]", payload["assets"])[0]
            ),
            RuntimeError,
            "exactly one",
        ),
        (
            lambda payload: cast("dict[str, object]", payload["assets"][0]).update(
                browser_download_url="https://example.invalid/latest.json"
            ),
            RuntimeError,
            "not tag-pinned",
        ),
        (
            lambda payload: cast("dict[str, object]", payload["assets"][0]).update(
                digest="md5:bad"
            ),
            RuntimeError,
            "no authoritative",
        ),
        (
            lambda payload: cast("dict[str, object]", payload["assets"][0]).update(
                digest="sha256:not-hex"
            ),
            RuntimeError,
            "invalid SHA-256",
        ),
        (
            lambda payload: cast("dict[str, object]", payload["assets"][0]).update(
                size=0
            ),
            TypeError,
            "invalid size",
        ),
    ],
)
def test_unsloth_rejects_untrusted_release_asset(
    mutate: Callable[[dict[str, object]], object],
    error_type: type[Exception],
    message: str,
) -> None:
    """Malformed manifest assets must fail before any hash can be persisted."""
    module = _load_updater_module()
    payload = _release_payload(_manifest_bytes())
    mutate(payload)
    with pytest.raises(error_type, match=message):
        module.UnslothUpdater._release_manifest_asset(payload, tag_name=_TAG)


def test_unsloth_validates_release_manifest_identity() -> None:
    """A valid manifest should yield the exact public backend release."""
    module = _load_updater_module()
    manifest = _manifest_bytes()
    assert (
        module.UnslothUpdater._validate_manifest(
            manifest,
            version=_VERSION,
            tag_name=_TAG,
            digest_hex=hashlib.sha256(manifest).hexdigest(),
            size=len(manifest),
        )
        == _BACKEND_VERSION
    )


@pytest.mark.parametrize(
    ("manifest", "version", "tag", "digest", "size", "message"),
    [
        (
            _manifest_bytes(),
            _VERSION,
            _TAG,
            "0" * 64,
            len(_manifest_bytes()),
            "digest",
        ),
        (_manifest_bytes(), _VERSION, _TAG, "0" * 64, 1, "size"),
        (
            _manifest_bytes(version="other"),
            _VERSION,
            _TAG,
            None,
            None,
            "does not match",
        ),
        (
            _manifest_bytes(backend_version="rolling"),
            _VERSION,
            _TAG,
            None,
            None,
            "invalid PyPI",
        ),
        (_manifest_bytes(platforms=[]), _VERSION, _TAG, None, None, "platforms"),
        (
            _manifest_bytes(platforms={}),
            _VERSION,
            _TAG,
            None,
            None,
            "darwin-aarch64",
        ),
        (
            _manifest_bytes(
                platforms={"darwin-aarch64": {"url": "", "signature": "sig"}}
            ),
            _VERSION,
            _TAG,
            None,
            None,
            "missing url",
        ),
        (
            _manifest_bytes(
                platforms={
                    "darwin-aarch64": {
                        "url": "https://example.invalid/app.tar.gz",
                        "signature": "sig",
                    }
                }
            ),
            _VERSION,
            _TAG,
            None,
            None,
            "not tag-pinned",
        ),
        (
            _manifest_bytes(
                platforms={
                    "darwin-aarch64": {
                        "url": f"{_MANIFEST_URL}/app.tar.gz",
                        "signature": " ",
                    }
                }
            ),
            _VERSION,
            _TAG,
            None,
            None,
            "signature is empty",
        ),
    ],
)
def test_unsloth_rejects_incoherent_release_manifest(
    manifest: bytes,
    version: str,
    tag: str,
    digest: str | None,
    size: int | None,
    message: str,
) -> None:
    """Every release-manifest trust boundary should fail closed."""
    module = _load_updater_module()
    digest = digest or hashlib.sha256(manifest).hexdigest()
    size = size or len(manifest)
    with pytest.raises((RuntimeError, TypeError), match=message):
        module.UnslothUpdater._validate_manifest(
            manifest,
            version=version,
            tag_name=tag,
            digest_hex=digest,
            size=size,
        )


def test_unsloth_selects_exact_public_backend_sdist() -> None:
    """Only the versioned PyPI source distribution is an admissible backend input."""
    assert _load_updater_module().UnslothUpdater._backend_sdist(
        _pypi_payload(),
        version=_BACKEND_VERSION,
    ) == (
        _BACKEND_URL,
        "2b7c1bb5baaf30af625f7aa72e101409453abf063032301f1cdfafdf0574c9de",
        96_307_052,
        _BACKEND_UPLOAD_TIME,
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "PyPI response"),
        ({"info": [], "urls": []}, "PyPI info"),
        (_pypi_payload(version="other"), "does not describe"),
        ({"info": {"version": _BACKEND_VERSION}, "urls": {}}, "no file list"),
        ({"info": {"version": _BACKEND_VERSION}, "urls": []}, "exactly one"),
        (
            _pypi_payload(url="https://example.invalid/backend.tgz"),
            "not hosted by PyPI",
        ),
        (_pypi_payload(digest="bad"), "invalid SHA-256"),
        (_pypi_payload(size=False), "invalid size"),
    ],
)
def test_unsloth_rejects_incoherent_backend_sdist(
    payload: object,
    message: str,
) -> None:
    """PyPI metadata must identify one immutable source artifact."""
    with pytest.raises((RuntimeError, TypeError), match=message):
        _load_updater_module().UnslothUpdater._backend_sdist(
            payload,
            version=_BACKEND_VERSION,
        )


def test_unsloth_hashes_only_public_source_foundation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hashing must exclude vendor app binaries and unresolved helper closures."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()
    fixed_calls: list[tuple[str, str, object]] = []

    async def fixed_hash(
        name: str,
        expression: str,
        *,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        fixed_calls.append((name, expression, config))
        yield UpdateEvent.status(name, "source")
        yield UpdateEvent.value(name, _SRC_HASH)

    url_calls: list[tuple[str, tuple[str, ...], object]] = []

    async def url_hashes(
        name: str,
        urls: Iterable[str],
        *,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        url_tuple = tuple(urls)
        url_calls.append((name, url_tuple, config))
        yield UpdateEvent.status(name, "urls")
        yield UpdateEvent.value(
            name,
            {_MANIFEST_URL: _MANIFEST_HASH, _BACKEND_URL: _BACKEND_HASH},
        )

    monkeypatch.setattr(module.update_nix, "compute_fixed_output_hash", fixed_hash)
    monkeypatch.setattr(module.update_process, "compute_url_hashes", url_hashes)
    info = VersionInfo(_VERSION, _metadata())

    events = run_async(
        collect_events(
            updater.fetch_hashes(
                info,
                object(),
                context=UpdateContext(current=None, dry_run=True),
            )
        )
    )

    assert len(fixed_calls) == 1
    assert fixed_calls[0][0] == "unsloth"
    assert fixed_calls[0][2] == updater.config
    assert_nix_ast_equal(
        fixed_calls[0][1],
        _build_fetch_from_github_call(
            "unslothai",
            "unsloth",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert url_calls == [
        ("unsloth", (_MANIFEST_URL, _BACKEND_URL), updater.config),
    ]
    values = [event.payload for event in events if event.kind is UpdateEventKind.VALUE]
    assert values == [_foundation_hashes()]
    assert run_async(updater._is_latest(None, info)) is False


def test_unsloth_full_hash_pass_materializes_candidate_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-dry source pass must attach generated artifacts before its result."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()
    package_dir = tmp_path / "unsloth"
    package_dir.mkdir()
    seen: list[tuple[VersionInfo, SourceEntry, dict[str, object], Path]] = []

    async def fixed_hash(
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value("unsloth", _SRC_HASH)

    async def url_hashes(
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value(
            "unsloth",
            {_MANIFEST_URL: _MANIFEST_HASH, _BACKEND_URL: _BACKEND_HASH},
        )

    async def materialize(
        *,
        info: VersionInfo,
        source: SourceEntry,
        metadata: dict[str, object],
        package_dir: Path,
    ) -> AsyncIterator[UpdateEvent]:
        seen.append((info, source, metadata, package_dir))
        yield UpdateEvent.artifact(
            "unsloth",
            GeneratedArtifact.text(package_dir / "pyproject.toml", "candidate\n"),
        )

    monkeypatch.setattr(module.update_nix, "compute_fixed_output_hash", fixed_hash)
    monkeypatch.setattr(module.update_process, "compute_url_hashes", url_hashes)
    monkeypatch.setattr(module, "updater_dir_for", lambda _name: package_dir)
    monkeypatch.setattr(updater, "_materialize_candidate_artifacts", materialize)
    info = VersionInfo(_VERSION, _metadata())
    events = run_async(collect_events(updater.fetch_hashes(info, object())))

    assert seen == [(info, _candidate_source(module), _metadata(), package_dir)]
    assert [event.kind for event in events] == [
        UpdateEventKind.ARTIFACT,
        UpdateEventKind.VALUE,
    ]
    assert events[-1].payload == _foundation_hashes()


def test_unsloth_full_hash_pass_requires_artifact_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Materialization cannot continue without a declared package directory."""
    module = _load_updater_module()

    async def fixed_hash(
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value("unsloth", _SRC_HASH)

    async def url_hashes(
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value(
            "unsloth",
            {_MANIFEST_URL: _MANIFEST_HASH, _BACKEND_URL: _BACKEND_HASH},
        )

    monkeypatch.setattr(module.update_nix, "compute_fixed_output_hash", fixed_hash)
    monkeypatch.setattr(module.update_process, "compute_url_hashes", url_hashes)
    monkeypatch.setattr(module, "updater_dir_for", lambda _name: None)
    with pytest.raises(RuntimeError, match="package directory was not found"):
        run_async(
            collect_events(
                module.UnslothUpdater().fetch_hashes(
                    VersionInfo(_VERSION, _metadata()), object()
                )
            )
        )


def test_unsloth_rejects_url_hashes_that_disagree_with_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful downloader is insufficient when its bytes differ from API metadata."""
    module = _load_updater_module()

    async def fixed_hash(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value("unsloth", _SRC_HASH)

    async def url_hashes(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value(
            "unsloth",
            {_MANIFEST_URL: _MANIFEST_HASH, _BACKEND_URL: _MANIFEST_HASH},
        )

    monkeypatch.setattr(module.update_nix, "compute_fixed_output_hash", fixed_hash)
    monkeypatch.setattr(module.update_process, "compute_url_hashes", url_hashes)

    with pytest.raises(RuntimeError, match="do not match authoritative"):
        run_async(
            collect_events(
                module.UnslothUpdater().fetch_hashes(
                    VersionInfo(_VERSION, _metadata()),
                    object(),
                )
            )
        )


def test_unsloth_build_result_matches_checked_in_foundation() -> None:
    """Updater persistence must retain the checked-in authoritative foundation."""
    checked_in = SourceEntry.model_validate(
        json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    )
    plan = cast(
        "dict[str, Any]",
        json.loads((_PACKAGE_DIR / "closure-plan.json").read_text(encoding="utf-8")),
    )
    assert checked_in.version is not None
    assert checked_in.commit is not None
    assert checked_in.urls is not None
    entries = checked_in.hashes.entries
    assert entries is not None

    def hash_entry(hash_type: str, url: str | None = None) -> HashEntry:
        matches = [
            entry
            for entry in entries
            if entry.hash_type == hash_type and entry.url == url
        ]
        assert len(matches) == 1
        return matches[0]

    def digest_hex(entry: HashEntry) -> str:
        algorithm, encoded = entry.hash.split("-", 1)
        assert algorithm == "sha256"
        return base64.b64decode(encoded, validate=True).hex()

    manifest_url = checked_in.urls["releaseManifest"]
    backend_url = checked_in.urls["backendSdist"]
    metadata = {
        "backendDigestHex": digest_hex(hash_entry("sha256", backend_url)),
        "backendSize": 1,
        "backendUploadTime": _BACKEND_UPLOAD_TIME,
        "backendUrl": backend_url,
        "backendVersion": plan["backend"]["version"],
        "commit": checked_in.commit,
        "manifestDigestHex": digest_hex(hash_entry("sha256", manifest_url)),
        "manifestSize": 1,
        "manifestUrl": manifest_url,
        "rustToolchainVersion": "1.0.0",
        "sourcePythonVersion": plan["backend"]["sourceTagVersion"],
        "tag": plan["app"]["tag"],
    }
    result = (
        _load_updater_module()
        .UnslothUpdater()
        .build_result(
            VersionInfo(checked_in.version, metadata),
            entries,
        )
    )
    assert result.equivalent_to(checked_in.model_copy(update={"pins": None}))
    assert result.pins is None


def test_unsloth_release_varying_inputs_are_source_owned() -> None:
    """Cargo owns Rust selection; frontend correctness is proved by its build."""
    updater = _load_updater_module().UnslothUpdater()

    assert updater.compatibility_pins is None
    result = updater.build_result(
        VersionInfo(_VERSION, _metadata()),
        _foundation_hashes(),
    )
    assert result.pins is None

    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = expect_instance(package.output, Assertion).body
    assert_nix_ast_equal(
        expect_binding(output.scope, "cargoManifest").value,
        """builtins.fromTOML (
          builtins.readFile "${desktopSource}/studio/src-tauri/Cargo.toml"
        )""",
    )
    assert_nix_ast_equal(
        expect_binding(output.scope, "rustToolchainVersion").value,
        "lib.versions.pad 3 cargoManifest.package.rust-version",
    )
    assert_nix_ast_equal(
        expect_binding(output.scope, "rustToolchain").value,
        """(inputs.rust-overlay.lib.mkRustBin { } pkgs)
          .stable.${rustToolchainVersion}.default""",
    )


def test_unsloth_updater_owns_every_release_varying_sidecar() -> None:
    """One materializing updater must own the whole candidate evidence set."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()

    assert updater.materialize_when_current is True
    assert updater.shows_materialize_artifacts_phase is True
    assert updater.required_tools == ("nix", "uv")
    assert updater.get_generated_artifact_files() == (
        "pyproject.toml",
        "uv.lock",
        "closure-hashes.json",
        "closure-plan.json",
        "artifact-validation.json",
    )
    assert module._ARTIFACT_CHECKS[0] == "frontend-source-build"


@pytest.mark.parametrize(
    "value",
    [
        "not-a-timestamp",
        "2026-13-01T00:00:00Z",
    ],
)
def test_unsloth_rejects_invalid_backend_upload_time(value: str) -> None:
    """The uv resolution cutoff must be a real canonical PyPI UTC timestamp."""
    module = _load_updater_module()
    with pytest.raises(RuntimeError, match="invalid upload time"):
        module._canonical_pypi_upload_time(value)


def test_unsloth_closure_plan_is_derived_from_candidate_source() -> None:
    """Persisted plan identities must be relational, never copied constants."""
    module = _load_updater_module()
    source = _candidate_source(module)
    plan = module._closure_plan_payload(
        VersionInfo(_VERSION, _metadata()),
        source,
        _metadata(),
    )

    assert plan["app"] == {
        "commit": _COMMIT,
        "sourceHash": _SRC_HASH,
        "tag": _TAG,
        "version": _VERSION,
    }
    assert plan["backend"] == {
        "sdistHash": _BACKEND_HASH,
        "sourceTagVersion": _SOURCE_PYTHON_VERSION,
        "version": _BACKEND_VERSION,
    }
    assert plan["releaseManifest"] == {
        "hash": _MANIFEST_HASH,
        "pypiVersion": _BACKEND_VERSION,
        "version": _VERSION,
    }


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            SourceEntry(
                version=_VERSION,
                commit=_COMMIT,
                urls={
                    "backendSdist": _BACKEND_URL,
                    "releaseManifest": _MANIFEST_URL,
                },
                hashes={"aarch64-darwin": _SRC_HASH},
            ),
            "structured",
        ),
        (
            SourceEntry(
                version=_VERSION,
                commit=_COMMIT,
                urls={
                    "backendSdist": _BACKEND_URL,
                    "releaseManifest": _MANIFEST_URL,
                },
                hashes=[HashEntry.create("srcHash", _SRC_HASH)],
            ),
            "requires one sha256 hash",
        ),
        (
            SourceEntry(version=_VERSION, hashes=_foundation_hashes()),
            "missing immutable identities",
        ),
    ],
)
def test_unsloth_closure_plan_rejects_incomplete_candidate_source(
    source: SourceEntry,
    message: str,
) -> None:
    """A partial source record cannot become authoritative closure evidence."""
    module = _load_updater_module()
    with pytest.raises(RuntimeError, match=message):
        module._closure_plan_payload(
            VersionInfo(_VERSION, _metadata()),
            source,
            _metadata(),
        )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schemaVersion": 1, "status": "passed"},
        {"schemaVersion": 2, "status": "failed"},
    ],
)
def test_unsloth_rejects_invalid_runtime_evidence(payload: object) -> None:
    """Only a passed versioned host attestation may enter artifact evidence."""
    module = _load_updater_module()
    with pytest.raises((RuntimeError, TypeError), match="runtime evidence"):
        module._runtime_evidence(payload)


def test_unsloth_runtime_evidence_projection_is_deterministic() -> None:
    """Process identities and listener snapshots must not enter generated state."""
    module = _load_updater_module()
    first = _raw_runtime_evidence()
    second = _raw_runtime_evidence()
    second.update({
        "appPid": 300,
        "backendPid": 400,
        "health": {
            "service": "Unsloth UI Backend",
            "status": "healthy",
            "studio_root_id": "a" * 64,
        },
        "listenerAddress": "127.0.0.1:8908",
        "ownedProcessGroups": [300, 400],
        "port": 8908,
        "protectedListenerCount": 2,
        "protectedListenerIdentitySha256": "a" * 64,
        "sessionId": 300,
    })

    assert module._runtime_evidence(first) == _persisted_runtime_evidence()
    assert module._runtime_evidence(second) == _persisted_runtime_evidence()
    assert set(_persisted_runtime_evidence()).isdisjoint({
        "appPid",
        "backendPid",
        "listenerAddress",
        "ownedProcessGroups",
        "port",
        "protectedListenerCount",
        "protectedListenerIdentitySha256",
        "sessionId",
    })
    assert "studio_root_id" not in _persisted_runtime_evidence()["health"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "failed", "status"),
        ("teardown", "failed", "teardown"),
        ("sandbox", "failed", "sandbox"),
        ("listenerOwnership", "failed", "listenerOwnership"),
        ("appCandidate", "/tmp/app", "appCandidate"),
        ("backendExecutable", "/tmp/unsloth", "backendExecutable"),
        (
            "backendExecutable",
            f"/nix/store/{'c' * 32}-unsloth-backend-{_BACKEND_VERSION}",
            "backendExecutable",
        ),
        ("backendRuntimeEntrypoint", "/tmp/unsloth", "backendRuntimeEntrypoint"),
        (
            "backendRuntimeEntrypoint",
            f"/nix/store/{'d' * 32}-unsloth-{_BACKEND_VERSION}-venv",
            "backendRuntimeEntrypoint",
        ),
        ("appPid", 0, "appPid"),
        ("backendPid", True, "backendPid"),
        ("sessionId", 101, "sessionId"),
        ("port", 8765, "port"),
        ("listenerAddress", "0.0.0.0:8888", "listenerAddress"),
        ("ownedProcessGroups", [], "ownedProcessGroups"),
        ("ownedProcessGroups", [100, False], "ownedProcessGroups"),
        ("protectedListenerCount", 0, "protectedListenerCount"),
        ("protectedListenerIdentitySha256", "not-a-digest", "protected listener"),
    ],
)
def test_unsloth_runtime_evidence_validates_raw_fields_before_projection(
    field: str,
    value: object,
    message: str,
) -> None:
    """Every omitted run-specific field must still be checked at the host boundary."""
    module = _load_updater_module()
    evidence = _raw_runtime_evidence()
    evidence[field] = value

    with pytest.raises((RuntimeError, TypeError), match=message):
        module._runtime_evidence(evidence)


@pytest.mark.parametrize(
    ("health", "message"),
    [
        ([], "runtime health"),
        ({"service": "Unsloth UI Backend"}, "schema"),
        (
            {
                "service": "wrong",
                "status": "healthy",
                "studio_root_id": "e" * 64,
            },
            "health contract",
        ),
        (
            {
                "service": "Unsloth UI Backend",
                "status": "wrong",
                "studio_root_id": "e" * 64,
            },
            "health contract",
        ),
        (
            {
                "service": "Unsloth UI Backend",
                "status": "healthy",
                "studio_root_id": "not-hex",
            },
            "health contract",
        ),
    ],
)
def test_unsloth_runtime_evidence_validates_health_before_projection(
    health: object,
    message: str,
) -> None:
    """Persisted health evidence must come from the complete validated contract."""
    module = _load_updater_module()
    evidence = _raw_runtime_evidence()
    evidence["health"] = health

    with pytest.raises((RuntimeError, TypeError), match=message):
        module._runtime_evidence(evidence)


def test_unsloth_candidate_package_args_are_typed_nix_values(tmp_path: Path) -> None:
    """Candidate probes should pass structured values without raw Nix templates."""
    module = _load_updater_module()
    source = _candidate_source(module)
    plan = module._closure_plan_payload(
        VersionInfo(_VERSION, _metadata()), source, _metadata()
    )
    args = module.UnslothUpdater._candidate_package_args(
        python_workspace=tmp_path,
        closure_hashes=_CLOSURE_HASHES,
        closure_plan=plan,
        artifact_validation={"status": "pending"},
    )

    assert set(args) == {
        "artifactValidation",
        "closureHashes",
        "closurePlan",
        "pythonWorkspaceRoot",
    }
    assert_nix_ast_equal(args["pythonWorkspaceRoot"], str(tmp_path))
    assert_nix_ast_equal(
        args["closureHashes"],
        f"builtins.fromJSON {json.dumps(json.dumps(_CLOSURE_HASHES, sort_keys=True, separators=(',', ':')))}",
    )


@pytest.mark.parametrize("stdout", ["not-json", "[]"])
def test_unsloth_json_object_output_rejects_invalid_output(stdout: str) -> None:
    """Command evidence must be exactly one JSON object."""
    module = _load_updater_module()
    with pytest.raises((RuntimeError, TypeError), match="candidate evidence"):
        module._json_object_output(stdout, context="candidate evidence")


@pytest.mark.parametrize("existing_lock", [None, "existing lock\n"])
def test_unsloth_uv_lock_materialization_uses_release_cutoff_and_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_lock: str | None,
) -> None:
    """Uv must resolve in isolation while retaining any compatible lock choices."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    if existing_lock is not None:
        (package_dir / "uv.lock").write_text(existing_lock, encoding="utf-8")
    calls: list[tuple[list[str], object]] = []

    async def run_command(
        args: list[str],
        *,
        options: object,
    ) -> AsyncIterator[UpdateEvent]:
        calls.append((args, options))
        workspace = Path(args[args.index("--directory") + 1])
        assert (workspace / "pyproject.toml").read_text(encoding="utf-8") == (
            "candidate project\n"
        )
        assert (
            (workspace / "uv.lock").read_text(encoding="utf-8")
            if (workspace / "uv.lock").exists()
            else None
        ) == existing_lock
        (workspace / "uv.lock").write_text("resolved lock\n", encoding="utf-8")
        yield UpdateEvent.status("unsloth", "uv")
        yield UpdateEvent.value(
            "unsloth",
            CommandResult(args=args, returncode=0, stdout="", stderr=""),
        )

    monkeypatch.setattr(module.update_process, "run_command", run_command)
    events = run_async(
        collect_events(
            updater._materialize_uv_lock(
                package_dir=package_dir,
                pyproject_text="candidate project\n",
                upload_time=_BACKEND_UPLOAD_TIME,
            )
        )
    )

    assert [
        event.payload for event in events if event.kind is UpdateEventKind.VALUE
    ] == ["resolved lock\n"]
    args, options = calls[0]
    assert args[:3] == ["uv", "-q", "lock"]
    assert "--no-config" not in args
    assert args[-2:] == ["--exclude-newer", _BACKEND_UPLOAD_TIME]
    command_env = cast("Any", options).env
    assert "HOME" not in command_env
    assert command_env["UV_NO_SYSTEM_CONFIG"] == "1"
    assert command_env["UV_PYTHON"] == "3.12"
    assert Path(command_env["UV_CACHE_DIR"]).name == "uv-cache"
    assert {
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    }.issubset(command_env)


@pytest.mark.parametrize(
    ("returncode", "write_lock", "message"),
    [
        (1, False, "Refresh Unsloth Python closure failed"),
        (0, False, "did not produce Unsloth uv.lock"),
    ],
)
def test_unsloth_uv_lock_materialization_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    write_lock: bool,
    message: str,
) -> None:
    """Command failure and missing output must not emit a lock artifact."""
    module = _load_updater_module()

    async def run_command(
        args: list[str],
        *,
        options: object,
    ) -> AsyncIterator[UpdateEvent]:
        _ = options
        if write_lock:
            workspace = Path(args[args.index("--directory") + 1])
            (workspace / "uv.lock").write_text("lock\n", encoding="utf-8")
        yield UpdateEvent.value(
            "unsloth",
            CommandResult(
                args=args,
                returncode=returncode,
                stdout="",
                stderr="uv failed" if returncode else "",
            ),
        )

    monkeypatch.setattr(module.update_process, "run_command", run_command)
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    with pytest.raises(RuntimeError, match=message):
        run_async(
            collect_events(
                module.UnslothUpdater()._materialize_uv_lock(
                    package_dir=package_dir,
                    pyproject_text="candidate\n",
                    upload_time=_BACKEND_UPLOAD_TIME,
                )
            )
        )


def test_unsloth_candidate_closure_hash_uses_in_memory_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dependency probes must see the complete candidate without writing sidecars."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()
    source = _candidate_source(module)
    plan = module._closure_plan_payload(
        VersionInfo(_VERSION, _metadata()), source, _metadata()
    )
    package_args = updater._candidate_package_args(
        python_workspace=tmp_path,
        closure_hashes=_CLOSURE_HASHES,
        closure_plan=plan,
        artifact_validation={"status": "pending"},
    )
    calls: list[tuple[str, str, bool, object]] = []

    async def fixed_hash(
        name: str,
        expression: str,
        *,
        isolate_by_drv_hash: bool,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        calls.append((name, expression, isolate_by_drv_hash, config))
        yield UpdateEvent.value(name, _CLOSURE_HASHES["cargoHash"])

    monkeypatch.setattr(module.update_nix, "compute_fixed_output_hash", fixed_hash)
    events = run_async(
        collect_events(
            updater._compute_candidate_closure_hash(
                attr_path=".appCandidate.cargoDeps",
                source=source,
                package_args=package_args,
            )
        )
    )

    assert [
        event.payload for event in events if event.kind is UpdateEventKind.VALUE
    ] == [_CLOSURE_HASHES["cargoHash"]]
    assert calls[0][0] == "unsloth"
    assert calls[0][2:] == (True, updater.config)
    assert_nix_ast_equal(
        calls[0][1],
        _build_package_path_attr_expr(
            "unsloth",
            ".appCandidate.cargoDeps",
            system="aarch64-darwin",
            package_args=package_args,
            source_overrides={"unsloth": source},
        ),
    )


def test_unsloth_candidate_build_runtime_and_export_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The attestation adapters must build, execute, and evaluate one candidate."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()
    source = _candidate_source(module)
    plan = module._closure_plan_payload(
        VersionInfo(_VERSION, _metadata()), source, _metadata()
    )
    package_args = updater._candidate_package_args(
        python_workspace=tmp_path,
        closure_hashes=_CLOSURE_HASHES,
        closure_plan=plan,
        artifact_validation={"status": "passed"},
    )
    runtime = _raw_runtime_evidence()
    calls: list[list[str]] = []

    async def run_command(
        args: list[str],
        *,
        options: object,
    ) -> AsyncIterator[UpdateEvent]:
        _ = options
        calls.append(args)
        if args[:2] == ["nix", "build"]:
            stdout = f"{_SMOKE_OUTPUT}\n"
        elif args[:2] == ["nix", "eval"]:
            stdout = "true\n"
        else:
            stdout = json.dumps(runtime)
        yield UpdateEvent.status("unsloth", "command")
        yield UpdateEvent.value(
            "unsloth",
            CommandResult(args=args, returncode=0, stdout=stdout, stderr=""),
        )

    monkeypatch.setattr(module.update_process, "run_command", run_command)
    smoke_events = run_async(
        collect_events(
            updater._build_candidate_smoke(
                source=source,
                package_args=package_args,
            )
        )
    )
    runtime_events = run_async(
        collect_events(
            updater._validate_candidate_runtime(
                package_dir=tmp_path,
                smoke_output=_SMOKE_OUTPUT,
            )
        )
    )
    export_events = run_async(
        collect_events(
            updater._validate_candidate_export(
                source=source,
                package_args=package_args,
            )
        )
    )

    assert [
        event.payload for event in smoke_events if event.kind is UpdateEventKind.VALUE
    ] == [_SMOKE_OUTPUT]
    runtime_values = [
        event.payload for event in runtime_events if event.kind is UpdateEventKind.VALUE
    ]
    assert len(runtime_values) == 1
    assert json.loads(cast("str", runtime_values[0])) == _persisted_runtime_evidence()
    assert [
        event.payload for event in export_events if event.kind is UpdateEventKind.VALUE
    ] == ["export-ready"]
    assert calls[0][:7] == [
        "nix",
        "build",
        "-L",
        "--no-link",
        "--print-out-paths",
        "--impure",
        "--expr",
    ]
    assert calls[1] == [
        sys.executable,
        str(tmp_path / "validate_store_runtime.py"),
        "--smoke-result",
        _SMOKE_OUTPUT,
    ]
    assert calls[2][:5] == ["nix", "eval", "--json", "--impure", "--expr"]


@pytest.mark.parametrize(
    "stdout",
    ["", "/nix/store/not-valid\n", f"{_SMOKE_OUTPUT}\n{_SMOKE_OUTPUT}\n"],
)
def test_unsloth_candidate_smoke_requires_one_store_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    """Ambiguous or malformed build output cannot become persisted evidence."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()

    async def run_command(
        args: list[str],
        *,
        options: object,
    ) -> AsyncIterator[UpdateEvent]:
        _ = options
        yield UpdateEvent.value(
            "unsloth",
            CommandResult(args=args, returncode=0, stdout=stdout, stderr=""),
        )

    monkeypatch.setattr(module.update_process, "run_command", run_command)
    with pytest.raises(RuntimeError, match="one Nix store output"):
        run_async(
            collect_events(
                updater._build_candidate_smoke(
                    source=_candidate_source(module),
                    package_args=updater._candidate_package_args(
                        python_workspace=tmp_path,
                        closure_hashes=_CLOSURE_HASHES,
                        closure_plan={},
                        artifact_validation={},
                    ),
                )
            )
        )


@pytest.mark.parametrize(
    ("method", "stdout", "message"),
    [
        ("runtime", "not-json", "runtime validation did not return JSON"),
        ("runtime", '{"schemaVersion":1,"status":"passed"}', "did not pass schema"),
        ("export", "not-json", "export evaluation did not return JSON"),
        ("export", "false", "did not satisfy the export gates"),
    ],
)
def test_unsloth_candidate_attestation_rejects_invalid_command_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    stdout: str,
    message: str,
) -> None:
    """A zero exit code alone cannot authorize candidate evidence."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()

    async def run_command(
        args: list[str],
        *,
        options: object,
    ) -> AsyncIterator[UpdateEvent]:
        _ = options
        yield UpdateEvent.value(
            "unsloth",
            CommandResult(args=args, returncode=0, stdout=stdout, stderr=""),
        )

    monkeypatch.setattr(module.update_process, "run_command", run_command)
    source = _candidate_source(module)
    package_args = updater._candidate_package_args(
        python_workspace=tmp_path,
        closure_hashes=_CLOSURE_HASHES,
        closure_plan={},
        artifact_validation={},
    )
    stream = (
        updater._validate_candidate_runtime(
            package_dir=tmp_path,
            smoke_output=_SMOKE_OUTPUT,
        )
        if method == "runtime"
        else updater._validate_candidate_export(
            source=source,
            package_args=package_args,
        )
    )
    with pytest.raises(RuntimeError, match=message):
        run_async(collect_events(stream))


@pytest.mark.parametrize("method", ["smoke", "runtime", "export"])
def test_unsloth_candidate_commands_propagate_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    """Every external candidate gate must fail closed on command failure."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()

    async def run_command(
        args: list[str],
        *,
        options: object,
    ) -> AsyncIterator[UpdateEvent]:
        _ = options
        yield UpdateEvent.value(
            "unsloth",
            CommandResult(args=args, returncode=1, stdout="", stderr="failed"),
        )

    monkeypatch.setattr(module.update_process, "run_command", run_command)
    source = _candidate_source(module)
    package_args = updater._candidate_package_args(
        python_workspace=tmp_path,
        closure_hashes=_CLOSURE_HASHES,
        closure_plan={},
        artifact_validation={},
    )
    streams = {
        "smoke": updater._build_candidate_smoke(
            source=source,
            package_args=package_args,
        ),
        "runtime": updater._validate_candidate_runtime(
            package_dir=tmp_path,
            smoke_output=_SMOKE_OUTPUT,
        ),
        "export": updater._validate_candidate_export(
            source=source,
            package_args=package_args,
        ),
    }
    with pytest.raises(RuntimeError, match=r"failed \(exit 1\)"):
        run_async(collect_events(streams[method]))


def test_unsloth_resolves_candidate_hashes_in_dependency_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each hash probe must receive all previously resolved candidate hashes."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()
    seen: list[tuple[str, dict[str, object]]] = []
    values = {
        ".frontend.npmDeps": _CLOSURE_HASHES["frontendNpmDepsHash"],
        ".oxcNodeModules.npmDeps": _CLOSURE_HASHES["oxcNpmDepsHash"],
        ".appCandidate.cargoDeps": _CLOSURE_HASHES["cargoHash"],
    }

    async def compute_hash(
        *,
        attr_path: str,
        source: SourceEntry,
        package_args: dict[str, object],
    ) -> AsyncIterator[UpdateEvent]:
        assert source == _candidate_source(module)
        expression = cast("Any", package_args["closureHashes"])
        seen.append((attr_path, json.loads(expression.argument.value)))
        yield UpdateEvent.status("unsloth", f"hash {attr_path}")
        yield UpdateEvent.value("unsloth", values[attr_path])

    monkeypatch.setattr(updater, "_compute_candidate_closure_hash", compute_hash)
    source = _candidate_source(module)
    plan = module._closure_plan_payload(
        VersionInfo(_VERSION, _metadata()), source, _metadata()
    )
    events = run_async(
        collect_events(
            updater._resolve_candidate_closure_hashes(
                source=source,
                python_workspace=tmp_path,
                closure_plan=plan,
            )
        )
    )

    assert seen == [
        (
            ".frontend.npmDeps",
            {
                "cargoHash": None,
                "frontendNpmDepsHash": None,
                "oxcNpmDepsHash": None,
            },
        ),
        (
            ".oxcNodeModules.npmDeps",
            {
                "cargoHash": None,
                "frontendNpmDepsHash": _CLOSURE_HASHES["frontendNpmDepsHash"],
                "oxcNpmDepsHash": None,
            },
        ),
        (
            ".appCandidate.cargoDeps",
            {
                "cargoHash": None,
                "frontendNpmDepsHash": _CLOSURE_HASHES["frontendNpmDepsHash"],
                "oxcNpmDepsHash": _CLOSURE_HASHES["oxcNpmDepsHash"],
            },
        ),
    ]
    assert [
        event.payload for event in events if event.kind is UpdateEventKind.VALUE
    ] == [_CLOSURE_HASHES]
    assert sum(event.kind is UpdateEventKind.STATUS for event in events) == 3


def test_unsloth_attestation_rechecks_final_export_with_runtime_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final gate must consume the exact smoke and host-runtime evidence."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()
    source = _candidate_source(module)
    runtime = _persisted_runtime_evidence()
    seen_final: list[dict[str, object]] = []

    async def smoke(**_kwargs: object) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("unsloth", "smoke")
        yield UpdateEvent.value("unsloth", _SMOKE_OUTPUT)

    async def runtime_gate(**_kwargs: object) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("unsloth", "runtime")
        yield UpdateEvent.value("unsloth", json.dumps(runtime))

    async def export_gate(
        *,
        source: SourceEntry,
        package_args: dict[str, object],
    ) -> AsyncIterator[UpdateEvent]:
        assert source == _candidate_source(module)
        expression = cast("Any", package_args["artifactValidation"])
        seen_final.append(json.loads(expression.argument.value))
        yield UpdateEvent.status("unsloth", "export")
        yield UpdateEvent.value("unsloth", "export-ready")

    monkeypatch.setattr(updater, "_build_candidate_smoke", smoke)
    monkeypatch.setattr(updater, "_validate_candidate_runtime", runtime_gate)
    monkeypatch.setattr(updater, "_validate_candidate_export", export_gate)
    plan = module._closure_plan_payload(
        VersionInfo(_VERSION, _metadata()), source, _metadata()
    )
    events = run_async(
        collect_events(
            updater._attest_candidate(
                source=source,
                package_dir=tmp_path,
                python_workspace=tmp_path,
                closure_hashes=_CLOSURE_HASHES,
                closure_plan=plan,
            )
        )
    )

    assert seen_final == [
        {
            "checks": list(module._ARTIFACT_CHECKS),
            "runtimeEvidence": runtime,
            "runtimeEvidenceSchemaVersion": 3,
            "status": "passed",
            "storePathAppCandidateSmokeOutput": _SMOKE_OUTPUT,
        }
    ]
    values = [event.payload for event in events if event.kind is UpdateEventKind.VALUE]
    assert len(values) == 1
    assert json.loads(cast("str", values[0])) == seen_final[0]
    assert sum(event.kind is UpdateEventKind.STATUS for event in events) == 3


def test_unsloth_attestation_rejects_missing_final_export_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An adapter that does not explicitly attest export readiness must fail closed."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()

    async def smoke(**_kwargs: object) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value("unsloth", _SMOKE_OUTPUT)

    async def runtime_gate(**_kwargs: object) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value("unsloth", json.dumps(_persisted_runtime_evidence()))

    async def export_gate(**_kwargs: object) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value("unsloth", "not-ready")

    monkeypatch.setattr(updater, "_build_candidate_smoke", smoke)
    monkeypatch.setattr(updater, "_validate_candidate_runtime", runtime_gate)
    monkeypatch.setattr(updater, "_validate_candidate_export", export_gate)
    source = _candidate_source(module)
    with pytest.raises(RuntimeError, match="export gate did not pass"):
        run_async(
            collect_events(
                updater._attest_candidate(
                    source=source,
                    package_dir=tmp_path,
                    python_workspace=tmp_path,
                    closure_hashes=_CLOSURE_HASHES,
                    closure_plan=module._closure_plan_payload(
                        VersionInfo(_VERSION, _metadata()), source, _metadata()
                    ),
                )
            )
        )


def test_unsloth_materializes_all_candidate_artifacts_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One artifact event must carry the lock, hashes, plan, and attestation."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    source = _candidate_source(module)
    inspected_workspaces: list[dict[str, str]] = []
    inspected_workspace_paths: list[Path] = []
    validation = module._artifact_validation_payload(
        _SMOKE_OUTPUT,
        _persisted_runtime_evidence(),
    )
    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    symlink_root = tmp_path / "temporary-alias"
    symlink_root.symlink_to(tmp_path, target_is_directory=True)
    temporary_workspace = symlink_root / real_workspace.name
    monkeypatch.setattr(
        module.tempfile,
        "TemporaryDirectory",
        lambda **_kwargs: nullcontext(str(temporary_workspace)),
    )

    async def uv_lock(**kwargs: object) -> AsyncIterator[UpdateEvent]:
        assert kwargs["package_dir"] == package_dir
        assert kwargs["upload_time"] == _BACKEND_UPLOAD_TIME
        project = tomllib.loads(cast("str", kwargs["pyproject_text"]))
        assert project["project"]["dependencies"] == [
            f"unsloth[studio] @ {_BACKEND_URL}"
        ]
        yield UpdateEvent.status("unsloth", "lock")
        yield UpdateEvent.value("unsloth", "version = 1\n")

    async def hashes(
        *,
        source: SourceEntry,
        python_workspace: Path,
        closure_plan: dict[str, object],
    ) -> AsyncIterator[UpdateEvent]:
        assert source == _candidate_source(module)
        inspected_workspace_paths.append(python_workspace)
        inspected_workspaces.append({
            name: (python_workspace / name).read_text(encoding="utf-8")
            for name in ("pyproject.toml", "uv.lock")
        })
        assert closure_plan["app"]["version"] == _VERSION
        yield UpdateEvent.status("unsloth", "hashes")
        yield UpdateEvent.value("unsloth", _CLOSURE_HASHES)

    async def attest(**kwargs: object) -> AsyncIterator[UpdateEvent]:
        assert kwargs["closure_hashes"] == _CLOSURE_HASHES
        yield UpdateEvent.status("unsloth", "attestation")
        yield UpdateEvent.value("unsloth", json.dumps(validation))

    monkeypatch.setattr(updater, "_materialize_uv_lock", uv_lock)
    monkeypatch.setattr(updater, "_resolve_candidate_closure_hashes", hashes)
    monkeypatch.setattr(updater, "_attest_candidate", attest)
    events = run_async(
        collect_events(
            updater._materialize_candidate_artifacts(
                info=VersionInfo(_VERSION, _metadata()),
                source=source,
                metadata=_metadata(),
                package_dir=package_dir,
            )
        )
    )

    artifacts = [
        artifact
        for event in events
        if event.kind is UpdateEventKind.ARTIFACT
        for artifact in cast("list[GeneratedArtifact]", event.payload)
    ]
    assert [artifact.path.name for artifact in artifacts] == [
        "pyproject.toml",
        "uv.lock",
        "closure-hashes.json",
        "closure-plan.json",
        "artifact-validation.json",
    ]
    assert inspected_workspace_paths == [real_workspace.resolve()]
    assert inspected_workspaces[0]["uv.lock"] == "version = 1\n"
    payloads = {artifact.path.name: artifact.content for artifact in artifacts}
    assert json.loads(payloads["closure-hashes.json"]) == _CLOSURE_HASHES
    assert json.loads(payloads["artifact-validation.json"]) == validation
    assert json.loads(payloads["closure-plan.json"])["app"]["commit"] == _COMMIT
    assert sum(event.kind is UpdateEventKind.STATUS for event in events) == 3


@pytest.mark.parametrize(
    ("hashes", "error_type", "message"),
    [
        ({"aarch64-darwin": _SRC_HASH}, TypeError, "structured"),
        ([HashEntry.create("srcHash", _SRC_HASH)], RuntimeError, "one source"),
        (
            [
                HashEntry.create("srcHash", _SRC_HASH),
                HashEntry.create("sha256", _BACKEND_HASH, url=_BACKEND_URL),
                HashEntry.create("sha256", _BACKEND_HASH, url=_MANIFEST_URL),
            ],
            RuntimeError,
            "authoritative metadata",
        ),
    ],
)
def test_unsloth_build_result_rejects_incomplete_or_untrusted_hashes(
    hashes: object,
    error_type: type[Exception],
    message: str,
) -> None:
    """An incomplete foundation must never be serialized as ready."""
    with pytest.raises(error_type, match=message):
        _load_updater_module().UnslothUpdater().build_result(
            VersionInfo(_VERSION, _metadata()),
            cast("Any", hashes),
        )


@pytest.mark.parametrize(
    ("metadata", "error_type", "message"),
    [
        ({}, TypeError, "backendDigestHex"),
        (_metadata(commit="main"), RuntimeError, "immutable source commit"),
        (_metadata(manifest_digest="bad"), RuntimeError, "invalid SHA-256"),
        (_metadata(backend_size=0), TypeError, "invalid backendSize"),
        (
            _metadata(rust_toolchain_version="1.89"),
            RuntimeError,
            "non-canonical Rust toolchain version",
        ),
        (
            _metadata(rust_toolchain_version="nightly"),
            RuntimeError,
            "invalid rust-version",
        ),
    ],
)
def test_unsloth_requires_complete_release_metadata(
    metadata: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    """Manual callers cannot bypass discovery invariants."""
    with pytest.raises(error_type, match=message):
        _load_updater_module().UnslothUpdater().build_result(
            VersionInfo(_VERSION, metadata),
            _foundation_hashes(),
        )


@pytest.mark.parametrize(
    ("version", "metadata", "message"),
    [
        (_VERSION, _metadata(tag="v9.9.9"), "tag does not match version"),
        ("9.9.9", _metadata(), "tag does not match version"),
        (
            _VERSION,
            _metadata(manifest_url=f"{_MANIFEST_URL}?download=1"),
            "manifest URL does not match release tag",
        ),
        (
            _VERSION,
            _metadata(
                backend_url=(
                    "https://example.invalid/packages/0d/99/"
                    "2667fb3be038f4a3a53208c3b55a1fa9f3e62f58bf00c659f647e86df9a5/"
                    f"unsloth-{_BACKEND_VERSION}.tar.gz"
                )
            ),
            "backend URL is not canonical PyPI source",
        ),
        (
            _VERSION,
            _metadata(
                backend_url=(
                    f"https://files.pythonhosted.org/unsloth-{_BACKEND_VERSION}.tar.gz"
                )
            ),
            "backend URL is not canonical PyPI source",
        ),
        (
            _VERSION,
            _metadata(
                backend_url=(
                    "https://files.pythonhosted.org/packages/0d/99/"
                    "2667fb3be038f4a3a53208c3b55a1fa9f3e62f58bf00c659f647e86df9a5/"
                    "unsloth-2026.8.17.tar.gz"
                )
            ),
            "backend URL does not match backend version",
        ),
        (
            _VERSION,
            _metadata(backend_version="rolling"),
            "invalid backend version",
        ),
        (
            _VERSION,
            _metadata(backend_version="2026.13.1"),
            "invalid backend version",
        ),
        (
            _VERSION,
            _metadata(source_python_version="rolling"),
            "invalid source Python version",
        ),
        (
            _VERSION,
            _metadata(source_python_version="2026.13.1"),
            "invalid source Python version",
        ),
        (
            _VERSION,
            _metadata(source_python_version="2026.8.23"),
            "source Python version cannot be newer than backend version",
        ),
        (
            _VERSION,
            _metadata(source_python_version="2026.8.16"),
            "source Python version must match or immediately precede backend version",
        ),
    ],
)
def test_unsloth_persistence_rejects_incoherent_release_metadata(
    version: str,
    metadata: dict[str, object],
    message: str,
) -> None:
    """Persistence must independently enforce release provenance relationships."""
    manifest_url = cast("str", metadata["manifestUrl"])
    backend_url = cast("str", metadata["backendUrl"])

    with pytest.raises(RuntimeError, match=message):
        _load_updater_module().UnslothUpdater().build_result(
            VersionInfo(version, metadata),
            _foundation_hashes(
                manifest_url=manifest_url,
                backend_url=backend_url,
            ),
        )


def _write_patch_fixture(root: Path, module: ModuleType) -> dict[Path, str]:
    originals: dict[Path, str] = {}
    paths = {patch.path for patch in module._PATCHES}
    for relative_path in paths:
        source = "\n/* fixture boundary */\n".join(
            patch.old for patch in module._PATCHES if patch.path == relative_path
        )
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        originals[relative_path] = source
    return originals


def _write_backend_patch_fixture(root: Path, module: ModuleType) -> dict[Path, str]:
    originals: dict[Path, str] = {}
    paths = {patch.path for patch in module._BACKEND_PATCHES}
    for relative_path in paths:
        source = "\n# backend fixture boundary\n".join(
            patch.old
            for patch in module._BACKEND_PATCHES
            if patch.path == relative_path
            for _ in range(patch.expected_matches)
        )
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        originals[relative_path] = source
    return originals


def test_unsloth_policy_patch_applies_declared_desktop_ownership_replacements(
    tmp_path: Path,
) -> None:
    """The patch validates every declared anchor before applying its replacements."""
    module = _load_patch_module()
    originals = _write_patch_fixture(tmp_path, module)
    expected = dict(originals)
    for patch in module._PATCHES:
        expected[patch.path] = expected[patch.path].replace(patch.old, patch.new)

    assert module.main([str(tmp_path)]) == 0

    for relative_path, source in expected.items():
        assert (tmp_path / relative_path).read_text(encoding="utf-8") == source
    assert (tmp_path / module._NIX_MODULE).read_text(encoding="utf-8") == (
        module._NIX_MODULE_SOURCE
    )
    with pytest.raises(RuntimeError, match="already applied"):
        module.patch_tree(tmp_path)


def test_unsloth_policy_patch_omits_root_installers_from_macos_bundle(
    tmp_path: Path,
) -> None:
    """The packaged macOS app must not carry the root mutation entrypoint."""
    module = _load_patch_module()
    _write_patch_fixture(tmp_path, module)
    config_path = tmp_path / "studio/src-tauri/tauri.macos.conf.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """{
  "bundle": {
    "resources": {
      "../../install.sh": "install.sh"
    },
    "macOS": {
      "entitlements": "./Entitlements.plist"
    }
  }
}
""",
        encoding="utf-8",
    )

    assert module.main([str(tmp_path)]) == 0

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "resources" not in config["bundle"]


def test_unsloth_policy_patch_removes_mutable_desktop_manifest_url(
    tmp_path: Path,
) -> None:
    """Managed desktop source must not retain the mutable updater endpoint."""
    module = _load_patch_module()
    _write_patch_fixture(tmp_path, module)
    policy_path = tmp_path / "studio/src-tauri/src/desktop_update_policy.rs"
    mutable_url = (
        "https://github.com/unslothai/unsloth/releases/latest/download/latest.json"
    )
    policy_path.write_text(
        f"""const DESKTOP_UPDATER_MANIFEST_URL: &str =
    "{mutable_url}";

pub(crate) enum DesktopUpdateMode {{
    InApp,
    ManualLinuxPackage,
}}

fn desktop_update_mode() -> DesktopUpdateMode {{
    #[cfg(target_os = "linux")]

        assert_eq!(
            super::DESKTOP_UPDATER_MANIFEST_URL,
            "{mutable_url}"
        );
""",
        encoding="utf-8",
    )

    assert module.main([str(tmp_path)]) == 0

    patched_policy = policy_path.read_text(encoding="utf-8")
    assert mutable_url not in patched_policy


def test_unsloth_policy_patch_applies_fail_closed_backend_replacements(
    tmp_path: Path,
) -> None:
    """Every audited backend mutator has one exact fail-closed replacement."""
    module = _load_patch_module()
    originals = _write_backend_patch_fixture(tmp_path, module)
    expected = dict(originals)
    for patch in module._BACKEND_PATCHES:
        expected[patch.path] = expected[patch.path].replace(patch.old, patch.new)

    module.patch_backend_tree(tmp_path)

    for relative_path, source in expected.items():
        assert (tmp_path / relative_path).read_text(encoding="utf-8") == source


def test_unsloth_policy_patch_runs_the_packaged_studio_backend_in_process(
    tmp_path: Path,
) -> None:
    """Managed Studio launches use the packaged interpreter without installer state."""
    module = _load_patch_module()
    _write_backend_patch_fixture(tmp_path, module)

    module.patch_backend_tree(tmp_path)

    policy_patch = next(
        patch
        for patch in module._BACKEND_PATCHES
        if patch.path == Path("unsloth_cli/commands/studio.py")
        and "studio_venv_dir" in patch.old
    )
    patched_source = (tmp_path / policy_patch.path).read_text(encoding="utf-8")
    assert patched_source.count(policy_patch.new) == 2
    assignment = ast.parse(textwrap.dedent(policy_patch.new)).body[1]
    assert isinstance(assignment, ast.Assign)

    expression = compile(ast.Expression(assignment.value), "<studio-policy>", "eval")
    studio_venv = Path("/Users/test/.unsloth/studio/unsloth_studio")

    def is_in_process(*, managed: bool, prefix: str) -> bool:
        return bool(
            eval(  # noqa: S307
                expression,
                {
                    "os": SimpleNamespace(
                        environ={"UNSLOTH_NIX_MANAGED": "1"} if managed else {}
                    ),
                    "studio_venv_dir": studio_venv,
                    "sys": SimpleNamespace(prefix=prefix),
                },
            )
        )

    assert is_in_process(managed=True, prefix="/nix/store/backend-venv")
    assert is_in_process(managed=False, prefix=f"{studio_venv}/bin/python")
    assert not is_in_process(managed=False, prefix="/nix/store/backend-venv")


def test_unsloth_nix_managed_capabilities_use_the_validated_immutable_closure() -> None:
    """Managed capability checks bypass installer state without changing upstream mode."""
    module = _load_patch_module()
    policy_patch = next(
        patch
        for patch in module._BACKEND_PATCHES
        if patch.path == Path("unsloth_cli/commands/studio.py")
        and '"manifest_ok": True' in patch.new
    )
    upstream_source = textwrap.dedent(
        """
        def _install_state(deep: bool = False) -> dict:
            return _studio_deps.install_state(
                extra_roots=(STUDIO_HOME / "unsloth_studio",),
                deep=deep,
            )
        """
    )
    assert upstream_source.count(policy_patch.old) == 1
    tree = ast.parse(upstream_source.replace(policy_patch.old, policy_patch.new))

    class StudioDeps:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[Path, ...], bool]] = []
            self.result = {"ok": False, "reason": "upstream-state"}

        def install_state(self, *, extra_roots: tuple[Path, ...], deep: bool) -> object:
            self.calls.append((extra_roots, deep))
            return self.result

    def install_state(*, managed: bool, deep: bool) -> tuple[object, StudioDeps]:
        studio_deps = StudioDeps()
        namespace = {
            "STUDIO_HOME": Path("/Users/test/.unsloth/studio"),
            "_studio_deps": studio_deps,
            "os": SimpleNamespace(
                environ={"UNSLOTH_NIX_MANAGED": "1"} if managed else {}
            ),
        }
        exec(  # noqa: S102
            compile(tree, "<studio-install-state-policy>", "exec"),
            namespace,
        )
        function = cast("Callable[[bool], object]", namespace["_install_state"])
        return function(deep), studio_deps

    managed_state, managed_deps = install_state(managed=True, deep=True)
    assert managed_state == {
        "deps_ok": True,
        "manifest_ok": True,
        "missing": [],
        "ok": True,
        "reason": None,
    }
    assert managed_deps.calls == []

    upstream_state, upstream_deps = install_state(managed=False, deep=True)
    assert upstream_state is upstream_deps.result
    assert upstream_deps.calls == [
        ((Path("/Users/test/.unsloth/studio/unsloth_studio"),), True)
    ]


def test_unsloth_nix_backend_environment_disables_upstream_mutators() -> None:
    """The launcher policy must drive every reviewed upstream opt-out gate."""
    module = _load_patch_module()

    assert module.RUNTIME_ENVIRONMENT == {
        "UNSLOTH_DIFFUSION_ATTENTION_INSTALL": "0",
        "UNSLOTH_DIFFUSION_SD_CPP_INSTALL": "0",
        "UNSLOTH_DISABLE_LLMCOMPRESSOR_MAIN": "1",
        "UNSLOTH_DISABLE_LLM_COMPRESSOR_AUTOINSTALL": "1",
        "UNSLOTH_DISABLE_MLX_AUTOREPAIR": "1",
        "UNSLOTH_DISABLE_UPDATE_CHECK": "1",
        "UNSLOTH_NIX_MANAGED": "1",
        "UNSLOTH_SKIP_NODE_INSTALL": "1",
        "UNSLOTH_STUDIO_SKIP_FAST_PATH_HOOKS": "1",
        "UNSLOTH_STUDIO_SKIP_FLASHATTN_INSTALL": "1",
        "UNSLOTH_STUDIO_SKIP_FLA_INSTALL": "1",
        "UNSLOTH_STUDIO_SKIP_TILELANG_INSTALL": "1",
    }


def test_unsloth_nix_policy_guards_low_level_backend_installers() -> None:
    """Every callable subprocess installer must fail before reaching pip or npm."""
    module = _load_patch_module()
    required_guards = {
        ("unsloth/save.py", "install_llama_cpp_clone_non_blocking"),
        ("unsloth/save.py", "install_llama_cpp_old"),
        ("studio/backend/core/training/worker.py", "_uninstall_package"),
        ("studio/backend/core/training/worker.py", "_install_package_wheel_first"),
        ("studio/backend/core/training/worker.py", "_attempt_package_install"),
        ("studio/backend/core/training/worker.py", "_run_pip"),
        ("studio/backend/utils/ssm_runtime.py", "_install_kernel"),
        ("studio/backend/utils/transformers_version.py", "_install_to_dir"),
        ("studio/backend/utils/wheel_utils.py", "install_wheel"),
        ("studio/backend/utils/whisper_cpp_update.py", "_install_latest"),
    }

    missing = {
        (path, function)
        for path, function in required_guards
        if not any(
            patch.path == Path(path)
            and f"def {function}" in patch.old
            and 'os.environ.get("UNSLOTH_NIX_MANAGED") == "1"' in patch.new
            for patch in module._BACKEND_PATCHES
        )
    }

    assert not missing


def test_unsloth_nix_policy_guards_setup_and_helper_installer_entrypoints() -> None:
    """Shipped setup/helper entrypoints must refuse direct managed-mode mutation."""
    module = _load_patch_module()
    required_guards = {
        ("studio/setup.sh", "set -euo pipefail"),
        ("studio/setup.ps1", '$ErrorActionPreference = "Stop"'),
        ("studio/install_node_prebuilt.py", "def install_prebuilt"),
        ("studio/install_llama_prebuilt.py", "def install_prebuilt"),
        ("studio/install_sd_cpp_prebuilt.py", "def install("),
        ("studio/install_whisper_prebuilt.py", "def install_prebuilt"),
    }

    missing = {
        (path, anchor)
        for path, anchor in required_guards
        if not any(
            patch.path == Path(path)
            and anchor in patch.old
            and "UNSLOTH_NIX_MANAGED" in patch.new
            for patch in module._BACKEND_PATCHES
        )
    }

    assert not missing


def test_unsloth_nix_policy_packages_and_invokes_the_owned_oxc_runtime() -> None:
    """The wheel must carry its OXC closure and invoke oxlint without a .bin shim."""
    module = _load_patch_module()
    pyproject_patch = next(
        patch
        for patch in module._BACKEND_PATCHES
        if patch.path == Path("pyproject.toml") and "oxc-validator/*.mjs" in patch.old
    )
    validator_patch = next(
        patch
        for patch in module._BACKEND_PATCHES
        if patch.path
        == Path("studio/backend/core/data_recipe/oxc-validator/validate.mjs")
    )

    assert "oxc-validator/node_modules/**/*" in pyproject_patch.new
    assert "const oxlintBin = process.execPath;" in validator_patch.new
    assert '"node_modules", "oxlint", "bin", "oxlint"' in validator_patch.new
    assert 'node_modules", ".bin", "oxlint"' not in validator_patch.new


def test_unsloth_combined_policy_patch_validates_both_trees_before_writing(
    tmp_path: Path,
) -> None:
    """Backend drift may not leave the desktop source half-patched."""
    module = _load_patch_module()
    desktop_root = tmp_path / "desktop"
    backend_root = tmp_path / "backend"
    desktop_originals = _write_patch_fixture(desktop_root, module)
    backend_originals = _write_backend_patch_fixture(backend_root, module)
    target = module._BACKEND_PATCHES[-1]
    target_path = backend_root / target.path
    drifted = backend_originals[target.path].replace(target.old, "")
    target_path.write_text(drifted, encoding="utf-8")
    backend_originals[target.path] = drifted

    with pytest.raises(RuntimeError, match="found 0"):
        module.main([str(desktop_root), "--backend-root", str(backend_root)])

    assert not (desktop_root / module._NIX_MODULE).exists()
    for relative_path, source in desktop_originals.items():
        assert (desktop_root / relative_path).read_text(encoding="utf-8") == source
    for relative_path, source in backend_originals.items():
        assert (backend_root / relative_path).read_text(encoding="utf-8") == source


def test_unsloth_combined_policy_patch_writes_both_validated_trees(
    tmp_path: Path,
) -> None:
    """A coherent pair is fully patched only after both source trees validate."""
    module = _load_patch_module()
    desktop_root = tmp_path / "desktop"
    backend_root = tmp_path / "backend"
    desktop_expected = _write_patch_fixture(desktop_root, module)
    backend_expected = _write_backend_patch_fixture(backend_root, module)
    for patch in module._PATCHES:
        desktop_expected[patch.path] = desktop_expected[patch.path].replace(
            patch.old, patch.new
        )
    for patch in module._BACKEND_PATCHES:
        backend_expected[patch.path] = backend_expected[patch.path].replace(
            patch.old, patch.new
        )

    assert module.main([str(desktop_root), "--backend-root", str(backend_root)]) == 0

    for relative_path, source in desktop_expected.items():
        assert (desktop_root / relative_path).read_text(encoding="utf-8") == source
    for relative_path, source in backend_expected.items():
        assert (backend_root / relative_path).read_text(encoding="utf-8") == source
    assert (desktop_root / module._NIX_MODULE).read_text(encoding="utf-8") == (
        module._NIX_MODULE_SOURCE
    )


@pytest.mark.parametrize("copies", [0, 2])
def test_unsloth_policy_patch_rejects_source_drift_before_writing(
    tmp_path: Path,
    copies: int,
) -> None:
    """One missing or duplicate upstream anchor must leave every file untouched."""
    module = _load_patch_module()
    originals = _write_patch_fixture(tmp_path, module)
    target = module._PATCHES[-1]
    target_path = tmp_path / target.path
    changed = originals[target.path].replace(target.old, target.old * copies)
    target_path.write_text(changed, encoding="utf-8")
    originals[target.path] = changed

    with pytest.raises(RuntimeError, match=f"found {copies}"):
        module.patch_tree(tmp_path)

    assert not (tmp_path / module._NIX_MODULE).exists()
    for relative_path, source in originals.items():
        assert (tmp_path / relative_path).read_text(encoding="utf-8") == source


def test_unsloth_policy_patch_refuses_existing_unowned_module(
    tmp_path: Path,
) -> None:
    """The source patch may not overwrite an unexpected upstream file."""
    module = _load_patch_module()
    module_path = tmp_path / module._NIX_MODULE
    module_path.parent.mkdir(parents=True)
    module_path.write_text("upstream module\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="refusing to replace"):
        module.patch_tree(tmp_path)
