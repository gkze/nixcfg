"""Contracts for the non-exported Unsloth Desktop source foundation."""

import ast
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import textwrap
import tomllib
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events, load_repo_module, run_async
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.nix import _build_fetch_from_github_call
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

if TYPE_CHECKING:
    from typing import Any

_PACKAGE_DIR = REPO_ROOT / "packages/unsloth"
_VERSION = "0.1.804-beta"
_TAG = f"v{_VERSION}"
_COMMIT = "8c43aed2038721050ca0620f02967e03a9d5aa23"
_SOURCE_PYTHON_VERSION = "2026.8.22"
_BACKEND_VERSION = "2026.8.22"
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
_OXC_PACKAGE_PATH = "studio/backend/core/data_recipe/oxc-validator/package.json"
_OXC_LOCK_PATH = "studio/backend/core/data_recipe/oxc-validator/package-lock.json"
_OXC_VALIDATE_PATH = "studio/backend/core/data_recipe/oxc-validator/validate.mjs"
_OXC_CALLER_PATH = "studio/backend/core/data_recipe/local_callable_validators.py"
_SETUP_SH_PATH = "studio/setup.sh"
_SETUP_PS1_PATH = "studio/setup.ps1"
_OXC_SOURCE_DIGESTS = {
    _OXC_PACKAGE_PATH: "1f77ca9c792bb1b104724a27b142bfd58ca3fe38770d4320c91be75a6883e69e",
    _OXC_LOCK_PATH: "67221354b08c9ff2437f976b54412ce45849ccd4c6373a1bcca6ae9e69705cc2",
    _OXC_VALIDATE_PATH: "d5f06d9e7c51340cd80f2d8f76e8c5398870f640f82ea2f2ea3d926baeca94ad",
    _OXC_CALLER_PATH: "14d66234fd2e54bb1df8330eee49cc105f530b3a5429235e6cfed93e7a32c0eb",
    _SETUP_SH_PATH: "4e4b4f0baf205ce125ae0948c40c6376b7ab1133a2872148d4122c80f9c8e32a",
    _SETUP_PS1_PATH: "03c161c431b44d5d1d1ac9fd7c555302b58e8b9cceb31decee8562a44a159c70",
}
_OXC_SOURCE_AUDIT = hashlib.sha256(
    json.dumps(
        _OXC_SOURCE_DIGESTS,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
).hexdigest()


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


def _select_gnu_patch_executable(
    *,
    environ: Mapping[str, str],
    which: Callable[[str], str | None],
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> str | None:
    explicit = environ.get("NIXCFG_TEST_GNU_PATCH")
    if explicit:
        return explicit

    candidate = which("patch")
    if candidate is None:
        return None
    try:
        version = run(
            [candidate, "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    return candidate if version.stdout.startswith("GNU patch") else None


@pytest.fixture
def gnu_patch_executable() -> str:
    """Provide GNU patch from an explicit test boundary or a verified PATH."""
    executable = _select_gnu_patch_executable(
        environ=os.environ,
        which=shutil.which,
        run=subprocess.run,
    )
    if executable is None:
        pytest.skip(
            "GNU patch semantics require NIXCFG_TEST_GNU_PATCH or GNU patch on PATH"
        )
    return executable


def test_gnu_patch_selection_uses_only_injected_boundaries() -> None:
    """An explicit tool wins; PATH candidates must identify as GNU patch."""
    boundary_calls: list[tuple[str, object]] = []

    def locate(name: str) -> str | None:
        boundary_calls.append(("which", name))
        return "/test/bin/patch"

    def run_version(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        boundary_calls.append(("run", (command, kwargs)))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="GNU patch 2.7.6\n",
            stderr="",
        )

    assert (
        _select_gnu_patch_executable(
            environ={"NIXCFG_TEST_GNU_PATCH": "/controlled/bin/patch"},
            which=locate,
            run=run_version,
        )
        == "/controlled/bin/patch"
    )
    assert boundary_calls == []

    assert (
        _select_gnu_patch_executable(
            environ={},
            which=locate,
            run=run_version,
        )
        == "/test/bin/patch"
    )
    assert boundary_calls == [
        ("which", "patch"),
        (
            "run",
            (
                ["/test/bin/patch", "--version"],
                {
                    "check": False,
                    "capture_output": True,
                    "text": True,
                },
            ),
        ),
    ]

    assert (
        _select_gnu_patch_executable(
            environ={},
            which=lambda _name: "/usr/bin/patch",
            run=lambda command, **_kwargs: subprocess.CompletedProcess(
                command,
                0,
                stdout="patch 2.0-Apple\n",
                stderr="",
            ),
        )
        is None
    )

    assert (
        _select_gnu_patch_executable(
            environ={},
            which=lambda _name: None,
            run=run_version,
        )
        is None
    )

    def unavailable(
        _command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        raise OSError

    assert (
        _select_gnu_patch_executable(
            environ={},
            which=lambda _name: "/unavailable/bin/patch",
            run=unavailable,
        )
        is None
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
) -> dict[str, object]:
    return {
        "info": {"version": version},
        "urls": [
            {
                "digests": {"sha256": digest},
                "filename": f"unsloth-{_BACKEND_VERSION}.tar.gz",
                "packagetype": "sdist",
                "size": size,
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
    backend_url: object = _BACKEND_URL,
    backend_version: object = _BACKEND_VERSION,
    manifest_url: object = _MANIFEST_URL,
    oxc_source_audit: object = _OXC_SOURCE_AUDIT,
    source_python_version: object = _SOURCE_PYTHON_VERSION,
    tag: object = _TAG,
) -> dict[str, object]:
    return {
        "backendDigestHex": backend_digest,
        "backendSize": backend_size,
        "backendUrl": backend_url,
        "backendVersion": backend_version,
        "commit": commit,
        "manifestDigestHex": manifest_digest,
        "manifestSize": manifest_size,
        "manifestUrl": manifest_url,
        "oxcSourceAudit": oxc_source_audit,
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


def _oxc_source_payloads(*, parser_dependency: str = "^0.131.0") -> dict[str, bytes]:
    dependencies = {"oxc-parser": parser_dependency, "oxlint": "^1.65.0"}
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
                "dependencies": {"@oxc-project/types": "^0.131.0"},
                "version": "0.131.0",
                "resolved": (
                    "https://registry.npmjs.org/oxc-parser/-/oxc-parser-0.131.0.tgz"
                ),
                "integrity": (
                    "sha512-SJ3/7ZPbgie8dr5Z9BI/M51zZbpXba+hRSG0MDzVwMW5CRQg2fjY"
                    "E0jHGlLX4eeiibGgC/mzoDFKSDHwVZEHRQ=="
                ),
                "optionalDependencies": {
                    f"@oxc-parser/binding-{target}": "0.131.0"
                    for target in (*binding_targets, "wasm32-wasi")
                },
            },
            "node_modules/@oxc-parser/binding-darwin-arm64": {
                "version": "0.131.0",
                "resolved": (
                    "https://registry.npmjs.org/@oxc-parser/"
                    "binding-darwin-arm64/-/binding-darwin-arm64-0.131.0.tgz"
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
                "version": "1.65.0",
                "resolved": "https://registry.npmjs.org/oxlint/-/oxlint-1.65.0.tgz",
                "integrity": (
                    "sha512-ChUuE3Q7XnAbscvT4XLMsH7HFJmLgLVv9lu+RRgFL5wSXnDqUOzT"
                    "p5IS8qWDBGd/ZDSzQ2tbX8fjAmijlGLC7A=="
                ),
                "optionalDependencies": {
                    f"@oxlint/binding-{target}": "1.65.0" for target in binding_targets
                },
            },
            "node_modules/@oxlint/binding-darwin-arm64": {
                "version": "1.65.0",
                "resolved": (
                    "https://registry.npmjs.org/@oxlint/"
                    "binding-darwin-arm64/-/binding-darwin-arm64-1.65.0.tgz"
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
                    "https://registry.npmjs.org/@oxc-project/types/-/types-0.131.0.tgz"
                ),
                "version": "0.131.0",
            },
        },
    }
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
            b'  const oxlintBin = join(TOOL_DIR, "node_modules", ".bin", "oxlint");',
            b"  const oxlintArgs = [];",
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
    digest_payloads: dict[str, bytes] | None = None,
    sdist_payloads: dict[str, bytes] | None = None,
    sdist_digest: object | None = None,
    sdist_size: object | None = None,
) -> None:
    manifest = _manifest_bytes()
    digest_payloads = digest_payloads or source_payloads
    sdist = _backend_sdist_bytes(sdist_payloads or source_payloads)
    monkeypatch.setattr(
        module,
        "_OXC_SOURCE_DIGESTS",
        {
            path: hashlib.sha256(payload).hexdigest()
            for path, payload in digest_payloads.items()
        },
    )

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
        return source_payloads[url.removeprefix(raw_prefix)]

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
    source = SourceEntry.model_validate(
        json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    )
    plan = json.loads((_PACKAGE_DIR / "closure-plan.json").read_text(encoding="utf-8"))

    assert source.equivalent_to(
        SourceEntry(
            version=_VERSION,
            commit=_COMMIT,
            urls={
                "backendSdist": _BACKEND_URL,
                "releaseManifest": _MANIFEST_URL,
            },
            hashes=HashCollection.from_value(_foundation_hashes()),
        )
    )
    assert plan["status"] == "exported-and-validated"
    assert plan["packageExported"] is True
    assert plan["backend"] == {
        "sdistHash": _BACKEND_HASH,
        "sourceTagVersion": _SOURCE_PYTHON_VERSION,
        "version": _BACKEND_VERSION,
    }
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
    assert _load_updater_module()._OXC_SOURCE_DIGESTS == _OXC_SOURCE_DIGESTS


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
        "node": {
            "hash": "sha256-9tleEKBDHuEGf8aqvp92KQi0cW3TUyTh3bSxRmt2ZZ8=",
            "npmVersion": "11.17.0",
            "version": "24.19.0",
            "url": "https://nodejs.org/dist/v24.19.0/node-v24.19.0.tar.xz",
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


def test_unsloth_cargo_patch_pins_fix_path_env_before_vendoring(
    tmp_path: Path,
    gnu_patch_executable: str,
) -> None:
    """The shared Cargo patch must give the manifest and lock one source ID."""
    module = _load_patch_module()
    _write_patch_fixture(tmp_path, module)

    cargo_root = tmp_path / "studio/src-tauri"
    cargo_root.mkdir(parents=True, exist_ok=True)
    (cargo_root / "Cargo.toml").write_text(
        "[dependencies]\n"
        + "# retained upstream manifest spacing\n" * 29
        + """dirs = "6"
regex = "1"
open = "5"
process-wrap = { version = "9", features = ["std"] }
fix-path-env = { git = "https://github.com/tauri-apps/fix-path-env-rs" }
tauri-plugin-opener = "2.5.4"
tauri-plugin-updater = "2"
tauri-plugin-clipboard-manager = "2"
""",
        encoding="utf-8",
    )
    (cargo_root / "Cargo.lock").write_text(
        "version = 4\n"
        + "# retained upstream lock spacing\n" * 1288
        + """[[package]]
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

    patch_command = [
        gnu_patch_executable,
        "--batch",
        "--forward",
        "--verbose",
        "--fuzz=0",
        "--strip=1",
        f"--directory={tmp_path}",
        f"--input={_PACKAGE_DIR / 'studio-fix-path-env-revision.patch'}",
    ]
    dry_run = subprocess.run(  # noqa: S603
        [*patch_command, "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
    )
    diagnostics = (dry_run.stdout + dry_run.stderr).casefold()
    assert "offset" not in diagnostics
    assert "fuzz" not in diagnostics
    subprocess.run(  # noqa: S603
        patch_command,
        check=True,
        capture_output=True,
        text=True,
    )
    assert module.main([str(tmp_path)]) == 0

    revision = "c4c45d503ea115a839aae718d02f79e7c7f0f673"
    url = "https://github.com/tauri-apps/fix-path-env-rs"
    manifest = tomllib.loads((cargo_root / "Cargo.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((cargo_root / "Cargo.lock").read_text(encoding="utf-8"))
    assert manifest["dependencies"]["fix-path-env"] == {
        "git": url,
        "rev": revision,
    }
    assert lock["package"] == [
        {
            "dependencies": ["home", "strip-ansi-escapes", "thiserror 1.0.69"],
            "name": "fix-path-env",
            "source": f"git+{url}?rev={revision}#{revision}",
            "version": "0.0.0",
        }
    ]


def test_unsloth_cargo_patch_stamps_one_coherent_release_version(
    tmp_path: Path,
    gnu_patch_executable: str,
) -> None:
    """Vendoring and the final policy patch consume one release-stamped lock pair."""
    module = _load_patch_module()
    _write_patch_fixture(tmp_path, module)
    cargo_root = tmp_path / "studio/src-tauri"
    cargo_root.mkdir(parents=True, exist_ok=True)
    (cargo_root / "Cargo.toml").write_text(
        """[package]
name = "unsloth-studio"
# Placeholder, not the released app version. release-desktop.yml rewrites this
# field (and the unsloth-studio entry in Cargo.lock) to the dispatched
# studio_version before every release build, so editing it here changes nothing
# that ships. tauri.conf.json declares no version of its own, which is why the
# app version comes from this field rather than from there. The stale CalVer
# below is also the wrong shape: release builds always write SemVer, e.g. 0.1.52-beta.
version = "2026.4.8"
description = "Unsloth Desktop App"
authors = ["Unsloth AI"]
edition = "2021"

[dependencies]
fix-path-env = { git = "https://github.com/tauri-apps/fix-path-env-rs" }
""",
        encoding="utf-8",
    )
    lock_prefix = "version = 4\n" + "# pinned source lock entry spacing\n" * 5717 + "\n"
    (cargo_root / "Cargo.lock").write_text(
        lock_prefix
        + """[[package]]
name = "unsloth-studio"
version = "2026.4.8"
dependencies = [
 "arboard",
 "base64 0.23.1",
 "dirs 6.0.0",
]
""",
        encoding="utf-8",
    )

    patch_path = _PACKAGE_DIR / "studio-release-version.patch"
    patch_command = [
        gnu_patch_executable,
        "--batch",
        "--forward",
        "--verbose",
        "--fuzz=0",
        "--strip=1",
        f"--directory={tmp_path}",
        f"--input={patch_path}",
    ]
    dry_run = subprocess.run(  # noqa: S603
        [*patch_command, "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
    )
    diagnostics = (dry_run.stdout + dry_run.stderr).casefold()
    assert "offset" not in diagnostics
    assert "fuzz" not in diagnostics
    subprocess.run(  # noqa: S603
        patch_command,
        check=True,
        capture_output=True,
        text=True,
    )

    assert module.main([str(tmp_path)]) == 0

    manifest = tomllib.loads((cargo_root / "Cargo.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((cargo_root / "Cargo.lock").read_text(encoding="utf-8"))
    root_packages = [
        (package["name"], package["version"])
        for package in lock["package"]
        if package["name"] == manifest["package"]["name"]
    ]
    assert (
        manifest["package"]["name"],
        manifest["package"]["version"],
    ) == ("unsloth-studio", _VERSION)
    assert root_packages == [("unsloth-studio", _VERSION)]


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
    pyproject = tomllib.loads(
        (_PACKAGE_DIR / "pyproject.toml").read_text(encoding="utf-8")
    )
    lock = tomllib.loads((_PACKAGE_DIR / "uv.lock").read_text(encoding="utf-8"))
    target = (
        "sys_platform == 'darwin' and platform_machine == 'arm64' "
        "and python_version == '3.12'"
    )

    assert pyproject["project"] == {
        "dependencies": [f"unsloth[studio] @ {_BACKEND_URL}"],
        "name": "nixcfg-unsloth-runtime",
        "requires-python": "==3.12.*",
        "version": "0.1.804b0",
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
    assert len(packages) == 167
    assert packages["unsloth"]["version"] == _BACKEND_VERSION
    assert packages["unsloth"]["source"] == {"url": _BACKEND_URL}
    assert packages["unsloth"]["sdist"] == {
        "hash": "sha256:2b7c1bb5baaf30af625f7aa72e101409453abf063032301f1cdfafdf0574c9de"
    }
    assert len(packages["unsloth"]["dependencies"]) == 27
    assert len(packages["unsloth"]["optional-dependencies"]["studio"]) == 25
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
    assert len(registry_packages) == 165
    assert {
        package["name"] for package in registry_packages if "sdist" not in package
    } == {
        "bitsandbytes",
        "mlx",
        "mlx-metal",
        "sqlite-vec",
        "torch",
        "torchvision",
    }
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


def test_unsloth_resolves_release_commit_manifest_and_backend_sdist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery must retain the intentional tag/backend version split."""
    module = _load_updater_module()
    updater = module.UnslothUpdater()
    manifest = _manifest_bytes()
    source_payloads = _oxc_source_payloads()
    source_digests = {
        path: hashlib.sha256(payload).hexdigest()
        for path, payload in source_payloads.items()
    }
    sdist = _backend_sdist_bytes(source_payloads)
    monkeypatch.setattr(module, "_OXC_SOURCE_DIGESTS", source_digests)
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
            oxc_source_audit=hashlib.sha256(
                json.dumps(
                    source_digests,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
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
        *(
            f"https://raw.githubusercontent.com/unslothai/unsloth/{_COMMIT}/{path}"
            for path in source_digests
        ),
        _BACKEND_URL,
    ]
    assert pypi_urls == [f"https://pypi.org/pypi/unsloth/{_BACKEND_VERSION}/json"]


def test_unsloth_rejects_re_pinned_oxc_dependency_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Digest updates must not silently change the validator runtime contract."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads(parser_dependency="^0.132.0")
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match="OXC validator dependencies"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


def test_unsloth_rejects_backend_sdist_oxc_drift_when_github_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater must audit the OXC files that the packaged sdist executes."""
    module = _load_updater_module()
    github_payloads = _oxc_source_payloads()
    sdist_payloads = dict(github_payloads)
    sdist_payloads[_OXC_VALIDATE_PATH] += b"\n// unreviewed sdist-only drift\n"
    _install_oxc_discovery_fakes(
        monkeypatch,
        module,
        github_payloads,
        sdist_payloads=sdist_payloads,
    )

    with pytest.raises(RuntimeError, match="backend sdist OXC source"):
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


def test_unsloth_rejects_re_pinned_oxc_optional_dependency_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reviewed runtime cannot grow another optional package on re-pin."""
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


def test_unsloth_rejects_re_pinned_oxc_types_transitive_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parser's reachable types package must keep its reviewed source."""
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

    with pytest.raises(RuntimeError, match="OXC project types"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


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
    ("keys", "message"),
    [
        (
            ("packages", "node_modules/oxc-parser", "integrity"),
            "OXC locked runtime",
        ),
        (
            (
                "packages",
                "node_modules/@oxc-parser/binding-darwin-arm64",
                "integrity",
            ),
            "OXC darwin-arm64 binding",
        ),
        (
            ("packages", "node_modules/oxlint", "integrity"),
            "OXC locked runtime",
        ),
        (
            (
                "packages",
                "node_modules/@oxlint/binding-darwin-arm64",
                "integrity",
            ),
            "OXC darwin-arm64 binding",
        ),
    ],
)
def test_unsloth_rejects_re_pinned_oxc_different_valid_integrity(
    monkeypatch: pytest.MonkeyPatch,
    keys: tuple[str, ...],
    message: str,
) -> None:
    """A valid SHA-512 SRI cannot replace any reviewed lockfile digest."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    _set_json_path(
        source_payloads,
        _OXC_LOCK_PATH,
        keys,
        f"sha512-{base64.b64encode(b'x' * 64).decode()}",
    )
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match=message):
        run_async(module.UnslothUpdater().fetch_latest(object()))


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


def test_unsloth_rejects_oxc_source_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetched source bytes must match the commit-scoped digest contract."""
    module = _load_updater_module()
    digest_payloads = _oxc_source_payloads()
    source_payloads = dict(digest_payloads)
    source_payloads[_OXC_VALIDATE_PATH] += b"\n// drift\n"
    _install_oxc_discovery_fakes(
        monkeypatch,
        module,
        source_payloads,
        digest_payloads=digest_payloads,
    )

    with pytest.raises(RuntimeError, match="OXC source digest"):
        run_async(module.UnslothUpdater().fetch_latest(object()))


@pytest.mark.parametrize(
    ("source_path", "keys", "value", "message"),
    [
        (_OXC_PACKAGE_PATH, ("name",), "other", "package identity"),
        (_OXC_PACKAGE_PATH, ("scripts",), {}, "package shape"),
        (_OXC_LOCK_PATH, ("lockfileVersion",), 2, "lock identity"),
        (_OXC_LOCK_PATH, ("packages", "", "name"), "other", "lock root"),
        (
            _OXC_LOCK_PATH,
            ("packages", "node_modules/oxlint", "version"),
            "1.66.0",
            "locked runtime",
        ),
    ],
)
def test_unsloth_rejects_re_pinned_oxc_source_structure_drift(
    monkeypatch: pytest.MonkeyPatch,
    source_path: str,
    keys: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    """Digest re-pinning alone cannot weaken the audited source structure."""
    module = _load_updater_module()
    source_payloads = _oxc_source_payloads()
    _set_json_path(source_payloads, source_path, keys, value)
    _install_oxc_discovery_fakes(monkeypatch, module, source_payloads)

    with pytest.raises(RuntimeError, match=message):
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

    events = run_async(collect_events(updater.fetch_hashes(info, object())))

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
    """Updater persistence should reproduce exactly the reviewed source metadata."""
    result = (
        _load_updater_module()
        .UnslothUpdater()
        .build_result(
            VersionInfo(_VERSION, _metadata()),
            _foundation_hashes(),
        )
    )
    checked_in = SourceEntry.model_validate(
        json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    )
    assert result.equivalent_to(checked_in)


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
            _metadata(oxc_source_audit="0" * 64),
            RuntimeError,
            "exact OXC source audit",
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
        and "def _install_state()" in patch.old
    )
    tree = ast.parse(textwrap.dedent(policy_patch.new))

    class StudioDeps:
        def __init__(self) -> None:
            self.calls: list[tuple[Path, ...]] = []
            self.result = {"ok": False, "reason": "upstream-state"}

        def install_state(self, *, extra_roots: tuple[Path, ...]) -> object:
            self.calls.append(extra_roots)
            return self.result

    def install_state(*, managed: bool) -> tuple[object, StudioDeps]:
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
        function = cast("Callable[[], object]", namespace["_install_state"])
        return function(), studio_deps

    managed_state, managed_deps = install_state(managed=True)
    assert managed_state == {
        "deps_ok": True,
        "manifest_ok": True,
        "missing": [],
        "ok": True,
        "reason": None,
    }
    assert managed_deps.calls == []

    upstream_state, upstream_deps = install_state(managed=False)
    assert upstream_state is upstream_deps.result
    assert upstream_deps.calls == [
        (Path("/Users/test/.unsloth/studio/unsloth_studio"),)
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
