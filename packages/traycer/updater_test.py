"""Focused contracts for the source-built Traycer macOS package."""

import ast
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import textwrap
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import (
    BooleanPrimitive,
    IntegerPrimitive,
    StringPrimitive,
)
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._shell_ast import (
    ParsedShell,
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
from lib.tests._updater_helpers import (
    collect_events,
    load_repo_module,
    run_async,
)
from lib.update.artifacts import GeneratedArtifact
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    CommandResult,
    UpdateEvent,
    UpdateEventKind,
    expect_artifact_updates,
    expect_source_hashes,
)
from lib.update.nix import _build_fetch_from_github_call
from lib.update.paths import REPO_ROOT
from lib.update.updaters import UpdateContext, VersionInfo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from lib.update.process import RunCommandOptions

_VERSION = "1.2.0"
_PUBLIC_COMMIT = "85ee596fffab4c9aa72b6bddc73a0020839ed5ae"
_BUILD_COMMIT = "5198516d395fedc25c5f702263a3e4a72b05a655"
_HOST_ARCHIVE_URL = (
    "https://github.com/traycerai/traycer/releases/download/host-v1.2.0/"
    "traycer-host-macos-arm64.tar.gz"
)
_HOST_SIGNATURE_URL = f"{_HOST_ARCHIVE_URL}.minisig"
_HOST_ARCHIVE_SHA256 = "sha256-Zs+B55nYJRRm407BO2FZAHy7EGncCR1tx14Qoo1UaTk="
_HOST_ARCHIVE_HEX_SHA256 = (
    "66cf81e799d8251466e34ec13b6159007cbb1069dc091d6dc75e10a28d546939"
)
_HOST_SIGNATURE_SHA256 = "sha256-VW+v5cO8X2oqe85V9sssbGGxOalHpy5E9l29ncojQ50="
_HOST_MINISIGN_PUBLIC_KEY = "RWSEfvU5EZoZYQTQUOVHeQFv3poThl1VM7FZLkNQr0Zu0FyL2x+u2O2l"
_HOST_MINISIGN_KEY_ID = "847ef539119a1961"
_HOST_MINISIGN_TRUSTED_COMMENT = "traycer-host 1.2.0 darwin-arm64"
_HOST_INSTALL_ID = "608ac4aa-4c3c-558e-94a9-679ab22baccc"
_HOST_INSTALL_SENTINEL_TIMESTAMP = "1970-01-01T00:00:00.000Z"
_BUN_URL = (
    "https://github.com/oven-sh/bun/releases/download/bun-v1.3.12/"
    "bun-darwin-aarch64.zip"
)
_BUN_SHA256 = "sha256-bEu4fdAT7RqNahbjV6PQlJWf1VMLTXBh9/NoDDx86hw="
_BUN_SIZE = 22_264_502
_SRC_HASH = "sha256-4omVaCSGxrr8oG2MfCtXmTiyemVL1dF3cItUOrYoKGM="
_HOST_RUNTIME_RELATIVE_EXECUTABLE = "host-runtime/traycer-host"
_PACKAGE_DIR = REPO_ROOT / "packages/traycer"
_DESKTOP_NATIVE_CODE_OBJECTS = (
    (
        "Contents/Resources/cli/darwin-arm64/traycer",
        "traycer",
        ("arm64",),
        "Contents/Resources/cli/darwin-arm64/traycer",
    ),
    (
        "Contents/Resources/app.asar.unpacked/node_modules/font-list/libs/"
        "darwin/fontlist",
        "fontlist",
        ("arm64", "x86_64"),
        "Contents/Resources/app.asar.unpacked/node_modules/font-list/libs/"
        "darwin/fontlist",
    ),
    (
        "Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers/"
        "chrome_crashpad_handler",
        "chrome_crashpad_handler",
        ("arm64",),
        "Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers/"
        "chrome_crashpad_handler",
    ),
    *tuple(
        (
            "Contents/Frameworks/Electron Framework.framework/Versions/A/"
            f"Libraries/{name}",
            name.removesuffix(".dylib"),
            ("arm64",),
            "Contents/Frameworks/Electron Framework.framework/Versions/A/"
            f"Libraries/{name}",
        )
        for name in (
            "libEGL.dylib",
            "libGLESv2.dylib",
            "libffmpeg.dylib",
            "libvk_swiftshader.dylib",
        )
    ),
    (
        "Contents/Frameworks/Mantle.framework/Versions/A/Mantle",
        "com.electron.mantle",
        ("arm64",),
        "Contents/Frameworks/Mantle.framework",
    ),
    (
        "Contents/Frameworks/ReactiveObjC.framework/Versions/A/ReactiveObjC",
        "com.electron.reactive",
        ("arm64",),
        "Contents/Frameworks/ReactiveObjC.framework",
    ),
    (
        "Contents/Frameworks/Squirrel.framework/Versions/A/Resources/ShipIt",
        "ShipIt",
        ("arm64",),
        "Contents/Frameworks/Squirrel.framework/Versions/A/Resources/ShipIt",
    ),
    (
        "Contents/Frameworks/Squirrel.framework/Versions/A/Squirrel",
        "com.github.Squirrel",
        ("arm64",),
        "Contents/Frameworks/Squirrel.framework",
    ),
    (
        "Contents/Frameworks/Electron Framework.framework/Versions/A/"
        "Electron Framework",
        "com.github.Electron.framework",
        ("arm64",),
        "Contents/Frameworks/Electron Framework.framework",
    ),
    *tuple(
        (
            f"Contents/Frameworks/{bundle}.app/Contents/MacOS/{bundle}",
            identifier,
            ("arm64",),
            f"Contents/Frameworks/{bundle}.app",
        )
        for bundle, identifier in (
            ("Traycer Helper", "ai.traycer.desktop.helper"),
            ("Traycer Helper (GPU)", "ai.traycer.desktop.helper.GPU"),
            ("Traycer Helper (Plugin)", "ai.traycer.desktop.helper.Plugin"),
            ("Traycer Helper (Renderer)", "ai.traycer.desktop.helper.Renderer"),
        )
    ),
    (
        "Contents/MacOS/Traycer",
        "ai.traycer.desktop",
        ("arm64",),
        ".",
    ),
)


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/traycer/updater.py",
        "traycer_updater_dedicated_test",
    )


def _load_patch_module() -> ModuleType:
    return load_repo_module(
        "packages/traycer/patch_nix_managed.py",
        "traycer_nix_policy_patch_test",
    )


def _load_renderer_key_validator_module() -> ModuleType:
    return load_repo_module(
        "packages/traycer/validate_renderer_storage_key.py",
        "traycer_renderer_storage_key_validator_test",
    )


def _nix_identifier_names(expression: object) -> set[str]:
    """Collect semantic identifier nodes without relying on rendered source text."""
    names: set[str] = set()
    pending = [expression]
    seen: set[int] = set()
    ignored_fields = {"after", "before", "scope", "scope_state"}
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, Identifier):
            names.add(current.name)
        if isinstance(current, (list, tuple)):
            pending.extend(current)
        elif is_dataclass(current):
            pending.extend(
                getattr(current, field.name)
                for field in fields(current)
                if field.name not in ignored_fields
            )
    return names


def _nix_binding_names(expression: object) -> set[str]:
    """Collect semantic attribute and let-binding names from a Nix AST."""
    names: set[str] = set()
    pending = [expression]
    seen: set[int] = set()
    ignored_fields = {"after", "before", "scope", "scope_state"}
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, Binding) and isinstance(current.name, str):
            names.add(current.name)
        if isinstance(current, (list, tuple)):
            pending.extend(current)
        elif is_dataclass(current):
            pending.extend(
                getattr(current, field.name)
                for field in fields(current)
                if field.name not in ignored_fields
            )
    return names


def _parse_python_heredoc(body: str) -> ast.Module:
    """Parse a shell heredoc body after removing its Nix source indentation."""
    lines = body.splitlines()
    normalized = lines[0]
    if len(lines) > 1:
        remainder = "\n".join(lines[1:])
        normalized = f"{normalized}\n{textwrap.dedent(remainder)}"
    return ast.parse(normalized)


def _assert_desktop_native_audit_script(audit_script: str) -> None:
    """Assert the semantic native-dependency and entitlement audit contract."""
    audit_tree = _parse_python_heredoc(audit_script)
    function_names = {
        node.name for node in ast.walk(audit_tree) if isinstance(node, ast.FunctionDef)
    }
    assert {
        "expand_macho_path",
        "initial_dependency_context",
        "macho_rpaths",
        "resolve_dependency",
        "run_otool",
    } <= function_names
    string_literals = {
        node.value
        for node in ast.walk(audit_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert {
        "@executable_path",
        "@loader_path",
        "@rpath/",
        "cmd LC_RPATH",
        r"\bEXECUTE\b",
        r"^CodeDirectory .* flags=[^()]+\(([^)]+)\)",
        r"^\s*name (.+) \(offset [0-9]+\)$",
        r"^\s*path (.+) \(offset [0-9]+\)$",
        "Contents/MacOS/Traycer",
    } <= string_literals

    run_otool = next(
        node
        for node in ast.walk(audit_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run_otool"
    )
    open_calls = [
        node
        for node in ast.walk(run_otool)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "open"
    ]
    assert len(open_calls) == 1
    assert [ast.unparse(argument) for argument in open_calls[0].args] == ["'rb'"]
    otool_subprocess_calls = [
        node
        for node in ast.walk(run_otool)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
    ]
    assert len(otool_subprocess_calls) == 1
    pass_fds = next(
        keyword
        for keyword in otool_subprocess_calls[0].keywords
        if keyword.arg == "pass_fds"
    )
    assert ast.unparse(pass_fds.value) == "(stream.fileno(),)"
    assert any(
        isinstance(node, ast.JoinedStr) and ast.unparse(node).startswith("f'/dev/fd/")
        for node in ast.walk(otool_subprocess_calls[0])
    )

    for caller_name in ("macho_load_commands", "is_executable"):
        caller = next(
            node
            for node in ast.walk(audit_tree)
            if isinstance(node, ast.FunctionDef) and node.name == caller_name
        )
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_otool"
            for node in ast.walk(caller)
        )

    initial_context = next(
        node
        for node in ast.walk(audit_tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "initial_dependency_context"
    )
    context_if = expect_instance(initial_context.body[0], ast.If)
    assert ast.unparse(context_if.test) == "is_executable(path)"
    assert ast.unparse(expect_instance(context_if.body[0], ast.Return).value) == (
        "(path.parent, ())"
    )
    assert ast.unparse(expect_instance(initial_context.body[1], ast.Return).value) == (
        "(application_executable_dir, application_rpaths)"
    )
    entitlement_calls = [
        node
        for node in ast.walk(audit_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and node.args
        and isinstance(node.args[0], ast.List)
        and node.args[0].elts
        and isinstance(node.args[0].elts[0], ast.Constant)
        and node.args[0].elts[0].value == "/usr/bin/codesign"
    ]
    assert len(entitlement_calls) == 1
    entitlement_arguments = expect_instance(entitlement_calls[0].args[0], ast.List)
    assert [
        expect_instance(argument, ast.Constant).value
        for argument in entitlement_arguments.elts[:5]
    ] == [
        "/usr/bin/codesign",
        "-d",
        "--entitlements",
        "-",
        "--xml",
    ]
    entitlement_guards = [
        node
        for node in ast.walk(audit_tree)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "is_executable(candidate)"
    ]
    assert len(entitlement_guards) == 1
    entitlement_guard = entitlement_guards[0]
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "loads"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "plistlib"
        for statement in entitlement_guard.body
        for node in ast.walk(statement)
    )
    assert any(
        isinstance(node, ast.Name) and node.id == "entitlement_payload"
        for statement in entitlement_guard.orelse
        for node in ast.walk(statement)
    )


def _release(
    component: str,
    *,
    oss_ref: str,
    assets: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    tag = f"{component}-v{_VERSION}"
    provenance_name = "release-provenance.json"
    provenance = {
        "schemaVersion": 1,
        "component": component,
        "version": _VERSION,
        "releaseChannel": "stable",
        "buildRepo": "traycerai/traycer-internal",
        "buildSha": _BUILD_COMMIT,
        "ossRepo": "traycerai/traycer",
        "ossRef": oss_ref,
        "workflowRunId": "30490588211",
    }
    if component == "cli":
        provenance["supportedHostVersion"] = _VERSION
    return {
        "tag_name": tag,
        "assets": [
            {
                "name": provenance_name,
                "browser_download_url": f"https://example.invalid/{tag}/{provenance_name}",
            },
            *(assets or []),
        ],
        "_provenance": provenance,
    }


def test_traycer_renderer_storage_key_validator_accepts_expected_bundle(
    tmp_path: Path,
) -> None:
    """The packaged renderer must contain the exact injected build key."""
    module = _load_renderer_key_validator_module()
    renderer = tmp_path / "renderer"
    renderer.mkdir()
    expected_key = hashlib.sha256(b"traycer-renderer-key-test").hexdigest()
    (renderer / "index.js").write_text(
        f'const desktopStorageKey = "{expected_key}";',
        encoding="utf-8",
    )
    (renderer / "shell.html").write_text("<main>Traycer</main>", encoding="utf-8")
    (renderer / "ignored.css").write_text(expected_key, encoding="utf-8")

    assert module.validate_renderer_bundle(renderer, expected_key) == (2, 1)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b'const key = "VITE_DESKTOP_LOCAL_STORAGE_KEY";', "unresolved"),
        (b'const key = "traycer-desktop-default-secret";', "fallback"),
        (b'const key = "absent";', "does not contain"),
    ],
)
def test_traycer_renderer_storage_key_validator_rejects_unsafe_bundles(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    """Missing, unresolved, and fallback key material must fail promotion."""
    module = _load_renderer_key_validator_module()
    renderer = tmp_path / "renderer"
    renderer.mkdir()
    (renderer / "index.js").write_bytes(payload)
    expected_key = hashlib.sha256(b"traycer-rejected-renderer-key-test").hexdigest()

    with pytest.raises(module.RendererKeyValidationError, match=message) as raised:
        module.validate_renderer_bundle(renderer, expected_key)

    assert expected_key not in str(raised.value)


@pytest.mark.parametrize(
    "expected_key",
    [
        "",
        "a" * 63,
        "a" * 65,
        "*" * 64,
        "traycer-desktop-default-secret",
    ],
)
def test_traycer_renderer_storage_key_validator_rejects_invalid_expected_key(
    tmp_path: Path,
    expected_key: str,
) -> None:
    """The validator must reject absent, malformed, or public fallback inputs."""
    module = _load_renderer_key_validator_module()

    with pytest.raises(module.RendererKeyValidationError, match="64 base64-alphabet"):
        module.validate_renderer_bundle(tmp_path, expected_key)


def test_traycer_renderer_storage_key_validator_rejects_missing_assets(
    tmp_path: Path,
) -> None:
    """A missing renderer or compiled-script inventory cannot pass promotion."""
    module = _load_renderer_key_validator_module()
    expected_key = hashlib.sha256(b"traycer-missing-renderer-test").hexdigest()

    with pytest.raises(module.RendererKeyValidationError, match="not a directory"):
        module.validate_renderer_bundle(tmp_path / "missing", expected_key)
    with pytest.raises(module.RendererKeyValidationError, match="no compiled assets"):
        module.validate_renderer_bundle(tmp_path, expected_key)


def test_traycer_renderer_storage_key_validator_cli_is_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The Nix-facing adapter reports status without printing bundle key material."""
    module = _load_renderer_key_validator_module()
    renderer = tmp_path / "renderer"
    renderer.mkdir()
    expected_key = hashlib.sha256(b"traycer-renderer-cli-test").hexdigest()
    (renderer / "index.js").write_text(expected_key, encoding="utf-8")
    monkeypatch.setenv("VITE_DESKTOP_LOCAL_STORAGE_KEY", expected_key)

    assert module.main(["validate_renderer_storage_key.py", str(renderer)]) == 0
    success = capsys.readouterr()
    assert success.out == "validated 1 Traycer renderer assets (1 key occurrence)\n"
    assert expected_key not in success.out

    monkeypatch.delenv("VITE_DESKTOP_LOCAL_STORAGE_KEY")
    assert module.main(["validate_renderer_storage_key.py", str(renderer)]) == 1
    failure = capsys.readouterr()
    assert "VITE_DESKTOP_LOCAL_STORAGE_KEY is not set" in failure.err
    assert expected_key not in failure.err

    monkeypatch.setenv("VITE_DESKTOP_LOCAL_STORAGE_KEY", expected_key)
    (renderer / "index.js").write_text("missing production key", encoding="utf-8")
    assert module.main(["validate_renderer_storage_key.py", str(renderer)]) == 1
    invalid_bundle = capsys.readouterr()
    assert "does not contain the expected production storage key" in invalid_bundle.err
    assert expected_key not in invalid_bundle.err

    monkeypatch.setattr(module.sys, "argv", ["validate_renderer_storage_key.py"])
    assert module.main() == 2


def test_traycer_resolves_coordinated_release_to_exact_public_and_host_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery must validate all public and closed-runtime release identities."""
    module = _load_updater_module()
    updater = module.TraycerUpdater()
    releases = {
        "bun-v1.3.12": {
            "tag_name": "bun-v1.3.12",
            "assets": [
                {
                    "name": "bun-darwin-aarch64.zip",
                    "browser_download_url": _BUN_URL,
                    "size": _BUN_SIZE,
                    "digest": (
                        "sha256:6c4bb87dd013ed1a8d6a16e357a3d094959fd5530b4d7061f7f3680c3c7cea1c"
                    ),
                },
            ],
        },
        "desktop-v1.2.0": _release("desktop", oss_ref=_PUBLIC_COMMIT),
        "cli-v1.2.0": _release("cli", oss_ref=_PUBLIC_COMMIT),
        "host-v1.2.0": _release(
            "host",
            oss_ref=_PUBLIC_COMMIT,
            assets=[
                {
                    "name": "traycer-host-macos-arm64.tar.gz",
                    "browser_download_url": _HOST_ARCHIVE_URL,
                    "size": 76_162_681,
                    "digest": (
                        "sha256:66cf81e799d8251466e34ec13b6159007cbb1069dc091d6dc75e10a28d546939"
                    ),
                },
                {
                    "name": "traycer-host-macos-arm64.tar.gz.minisig",
                    "browser_download_url": _HOST_SIGNATURE_URL,
                    "size": 293,
                    "digest": (
                        "sha256:556fafe5c3bc5f6a2a7bce55f6cb2c6c61b139a947a72e44f65dbd9dca23439d"
                    ),
                },
            ],
        ),
    }

    async def api_payload(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> object:
        assert config == updater.config
        return releases[path.rsplit("/", 1)[-1]]

    async def json_payload(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> object:
        assert config == updater.config
        if url.endswith("/package.json"):
            return {
                "packageManager": "bun@1.3.12",
                "catalog": {"electron": "^42.9.1"},
            }
        for release in releases.values():
            provenance = release.get("_provenance")
            if provenance is not None and f"/{release['tag_name']}/" in url:
                return provenance
        raise AssertionError(url)

    monkeypatch.setattr(module, "fetch_github_api", api_payload)
    monkeypatch.setattr(module, "fetch_json", json_payload)

    assert run_async(updater.fetch_latest(object())) == VersionInfo(
        version=_VERSION,
        metadata={
            "bunHash": _BUN_SHA256,
            "bunSize": str(_BUN_SIZE),
            "bunUrl": _BUN_URL,
            "bunVersion": "1.3.12",
            "commit": _PUBLIC_COMMIT,
            "electronVersion": "42.9.1",
            "hostArchiveHash": _HOST_ARCHIVE_SHA256,
            "hostArchiveSize": "76162681",
            "hostArchiveUrl": _HOST_ARCHIVE_URL,
            "hostMinisignKeyId": _HOST_MINISIGN_KEY_ID,
            "hostMinisignPublicKey": _HOST_MINISIGN_PUBLIC_KEY,
            "hostMinisignTrustedComment": _HOST_MINISIGN_TRUSTED_COMMENT,
            "hostSignatureHash": _HOST_SIGNATURE_SHA256,
            "hostSignatureSize": "293",
            "hostSignatureUrl": _HOST_SIGNATURE_URL,
            "unverifiedPrivateBuildCommit": _BUILD_COMMIT,
        },
    )


def test_traycer_updater_is_pinned_not_latest_tracking() -> None:
    """The documented exception may not silently advance to another release."""
    module = _load_updater_module()
    updater = module.TraycerUpdater()

    assert updater.PINNED_VERSION == _VERSION
    assert updater.PINNED_PUBLIC_COMMIT == _PUBLIC_COMMIT
    assert updater.UNVERIFIED_PRIVATE_BUILD_COMMIT == _BUILD_COMMIT
    assert not hasattr(updater, "PINNED_BUILD_COMMIT")
    assert run_async(updater._is_latest(None, VersionInfo(_VERSION))) is False


def test_traycer_rejects_malformed_release_identity_payloads() -> None:
    """Every release identity field must be typed, unique, and immutable."""
    updater = _load_updater_module().TraycerUpdater

    with pytest.raises(TypeError, match="not a JSON object"):
        updater._require_object([], context="release")
    with pytest.raises(TypeError, match="invalid url"):
        updater._require_string({"url": 1}, "url", context="release")
    with pytest.raises(TypeError, match="invalid assets"):
        updater._asset({"assets": None}, "asset", context="release")
    with pytest.raises(RuntimeError, match="exactly one"):
        updater._asset({"assets": []}, "asset", context="release")
    with pytest.raises(RuntimeError, match="exactly one"):
        updater._asset(
            {"assets": [{"name": "asset"}, {"name": "asset"}]},
            "asset",
            context="release",
        )
    assert updater._asset(
        {"assets": [None, {"name": "asset"}]},
        "asset",
        context="release",
    ) == {"name": "asset"}
    with pytest.raises(TypeError, match="no immutable SHA-256 digest"):
        updater._sri_from_github_digest(None, context="asset")
    with pytest.raises(RuntimeError, match="invalid SHA-256 digest"):
        updater._sri_from_github_digest("sha256:not-hex", context="asset")


@pytest.mark.parametrize(
    ("payload", "error", "message"),
    [
        (
            {"packageManager": "bun@0.0.0", "catalog": {}},
            RuntimeError,
            "requires Bun",
        ),
        (
            {"packageManager": "bun@1.3.12", "catalog": None},
            TypeError,
            "no Electron catalog",
        ),
        (
            {
                "packageManager": "bun@1.3.12",
                "catalog": {"electron": "^0.0.0"},
            },
            RuntimeError,
            "requires Electron",
        ),
    ],
)
def test_traycer_rejects_root_toolchain_manifest_drift(
    payload: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    """The source tree may not silently select different build tools."""
    with pytest.raises(error, match=message):
        _load_updater_module().TraycerUpdater._validate_root_manifest(payload)


def test_traycer_labels_unsigned_private_provenance_as_unverified() -> None:
    """Unsigned provenance is checked for drift but never promoted to identity."""
    updater = _load_updater_module().TraycerUpdater
    release = _release("desktop", oss_ref=_PUBLIC_COMMIT)
    provenance = release["_provenance"]
    assert isinstance(provenance, dict)
    drifted = dict(provenance)
    drifted["buildSha"] = "0" * 40

    with pytest.raises(RuntimeError, match="unverified release provenance"):
        updater._validate_unverified_provenance(drifted, component="desktop")


def test_traycer_rejects_release_tag_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both Traycer and Bun release endpoints must return the requested tag."""
    module = _load_updater_module()
    updater = module.TraycerUpdater()

    async def wrong_tag(
        _session: object,
        _path: str,
        *,
        config: object,
    ) -> object:
        assert config == updater.config
        return {"tag_name": "wrong"}

    monkeypatch.setattr(module, "fetch_github_api", wrong_tag)
    with pytest.raises(RuntimeError, match="Traycer desktop release tag"):
        run_async(updater._release(object(), "desktop"))
    with pytest.raises(RuntimeError, match="Bun release tag"):
        run_async(updater._bun_release(object()))


@pytest.mark.parametrize(
    ("mutation", "error", "message"),
    [
        ("url", RuntimeError, "URL drifted"),
        ("size", RuntimeError, "size drifted"),
        ("missing-digest", TypeError, "no immutable SHA-256 digest"),
        ("invalid-digest", RuntimeError, "invalid SHA-256 digest"),
        ("digest", RuntimeError, "digest drifted"),
    ],
)
def test_traycer_rejects_fixed_asset_identity_drift(
    mutation: str,
    error: type[Exception],
    message: str,
) -> None:
    """URL, byte size, and GitHub digest jointly identify every fixed asset."""
    updater = _load_updater_module().TraycerUpdater
    asset: dict[str, object] = {
        "name": "asset.zip",
        "browser_download_url": (
            "https://github.com/owner/repo/releases/download/v1/asset.zip"
        ),
        "size": 10,
        "digest": "sha256:" + ("00" * 32),
    }
    expected_hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    if mutation == "url":
        asset["browser_download_url"] = "https://example.invalid/asset.zip"
    elif mutation == "size":
        asset["size"] = 11
    elif mutation == "missing-digest":
        asset["digest"] = None
    elif mutation == "invalid-digest":
        asset["digest"] = "sha256:not-hex"
    else:
        asset["digest"] = "sha256:" + ("11" * 32)

    with pytest.raises(error, match=message):
        updater._validated_release_asset(
            {"assets": [asset]},
            owner="owner",
            repo="repo",
            tag="v1",
            name="asset.zip",
            size=10,
            expected_hash=expected_hash,
            context="Fixed release",
        )


def _version_info() -> VersionInfo:
    return VersionInfo(
        version=_VERSION,
        metadata={
            "bunHash": _BUN_SHA256,
            "bunSize": str(_BUN_SIZE),
            "bunUrl": _BUN_URL,
            "bunVersion": "1.3.12",
            "commit": _PUBLIC_COMMIT,
            "electronVersion": "42.9.1",
            "hostArchiveHash": _HOST_ARCHIVE_SHA256,
            "hostArchiveSize": "76162681",
            "hostArchiveUrl": _HOST_ARCHIVE_URL,
            "hostMinisignKeyId": _HOST_MINISIGN_KEY_ID,
            "hostMinisignPublicKey": _HOST_MINISIGN_PUBLIC_KEY,
            "hostMinisignTrustedComment": _HOST_MINISIGN_TRUSTED_COMMENT,
            "hostSignatureHash": _HOST_SIGNATURE_SHA256,
            "hostSignatureSize": "293",
            "hostSignatureUrl": _HOST_SIGNATURE_URL,
            "unverifiedPrivateBuildCommit": _BUILD_COMMIT,
        },
    )


@pytest.mark.parametrize(
    "key",
    [
        "bunHash",
        "bunSize",
        "bunVersion",
        "electronVersion",
        "hostArchiveHash",
        "hostArchiveSize",
        "hostMinisignKeyId",
        "hostMinisignPublicKey",
        "hostMinisignTrustedComment",
        "hostSignatureHash",
        "hostSignatureSize",
        "unverifiedPrivateBuildCommit",
    ],
)
def test_traycer_rejects_fixed_metadata_drift(key: str) -> None:
    """Each non-URL identity value is independently fail-closed."""
    updater = _load_updater_module().TraycerUpdater
    info = _version_info()
    assert isinstance(info.metadata, dict)
    metadata = dict(info.metadata)
    metadata[key] = "drifted"

    with pytest.raises(RuntimeError, match=f"metadata {key} drifted"):
        updater._validate_info(VersionInfo(version=_VERSION, metadata=metadata))


@pytest.mark.parametrize("key", ["bunUrl", "hostArchiveUrl", "hostSignatureUrl"])
def test_traycer_rejects_fixed_url_drift(key: str) -> None:
    """Every persisted URL must remain on its exact pinned release path."""
    updater = _load_updater_module().TraycerUpdater
    info = _version_info()
    assert isinstance(info.metadata, dict)
    metadata = dict(info.metadata)
    metadata[key] = "https://example.invalid/drifted"

    with pytest.raises(RuntimeError, match=f"metadata {key} drifted"):
        updater._validate_info(VersionInfo(version=_VERSION, metadata=metadata))


def test_traycer_rejects_version_commit_and_metadata_shape_drift() -> None:
    """The updater must reject incomplete or differently sourced state."""
    updater = _load_updater_module().TraycerUpdater
    info = _version_info()
    assert isinstance(info.metadata, dict)

    with pytest.raises(RuntimeError, match="metadata version drifted"):
        updater._validate_info(
            VersionInfo(version="0.0.0", metadata=dict(info.metadata))
        )
    metadata = dict(info.metadata)
    metadata["commit"] = "0" * 40
    with pytest.raises(RuntimeError, match="missing the pinned public commit"):
        updater._validate_info(VersionInfo(version=_VERSION, metadata=metadata))
    with pytest.raises(TypeError, match="metadata is not a JSON object"):
        updater._metadata_string(
            VersionInfo(version=_VERSION, metadata=[]),
            "bunHash",
        )
    metadata = dict(info.metadata)
    metadata["bunHash"] = None
    with pytest.raises(TypeError, match="invalid bunHash"):
        updater._validate_info(VersionInfo(version=_VERSION, metadata=metadata))


def test_traycer_materializes_bun_closure_and_emits_validated_fixed_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hashing must regenerate Bun artifacts and retain both Host identities."""
    module = _load_updater_module()
    updater = module.TraycerUpdater()
    fetched_urls: list[str] = []
    commands: list[list[str]] = []
    validated_graphs: list[tuple[str, str]] = []
    hash_expressions: list[str] = []
    normalized_inputs: list[str] = []

    async def fetch_url(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> bytes:
        fetched_urls.append(url)
        return b"exact traycer bun lock\n"

    async def run_command(
        args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        assert options.source == "traycer"
        commands.append(args)
        Path(args[-1]).write_text("{ generated = true; }\n", encoding="utf-8")
        yield UpdateEvent.status("traycer", "bun2nix started")
        yield UpdateEvent.value(
            "traycer",
            CommandResult(args=args, returncode=0, stdout="", stderr=""),
        )

    async def compute_hash(
        source: str,
        expr: str,
        *,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        assert source == "traycer"
        assert config == updater.config
        hash_expressions.append(expr)
        yield UpdateEvent.status(source, "source hash started")
        yield UpdateEvent.value(source, _SRC_HASH)

    def validate_generated_bun_graph(lock_path: Path, nix_path: Path) -> None:
        validated_graphs.append((
            lock_path.read_text(encoding="utf-8"),
            nix_path.read_text(encoding="utf-8"),
        ))

    def normalize_bun_nix_path(path: Path) -> None:
        normalized_inputs.append(path.read_text(encoding="utf-8"))
        path.write_text("{ normalized = true; }\n", encoding="utf-8")

    monkeypatch.setattr(module.update_net, "fetch_url", fetch_url)
    monkeypatch.setattr(module, "run_command", run_command)
    monkeypatch.setattr(
        module,
        "_validate_generated_bun_graph",
        validate_generated_bun_graph,
    )
    monkeypatch.setattr(module, "normalize_bun_nix_path", normalize_bun_nix_path)
    monkeypatch.setattr(module.update_nix, "compute_fixed_output_hash", compute_hash)

    events = run_async(collect_events(updater.fetch_hashes(_version_info(), object())))

    assert fetched_urls == [
        f"https://raw.githubusercontent.com/traycerai/traycer/{_PUBLIC_COMMIT}/bun.lock"
    ]
    assert len(commands) == 1
    command = commands[0]
    assert command[:4] == [
        "nix",
        "run",
        "path:.#pkgs.aarch64-darwin.bun2nix",
        "--",
    ]
    assert command[4:6] == ["--lock-file", command[5]]
    assert command[6:8] == ["--copy-prefix", "./"]
    assert command[8:10] == ["--output-file", command[9]]
    assert validated_graphs == [
        ("exact traycer bun lock\n", "{ generated = true; }\n"),
    ]
    artifact_event = next(
        event for event in events if event.kind is UpdateEventKind.ARTIFACT
    )
    assert expect_artifact_updates(artifact_event.payload) == [
        GeneratedArtifact.text(_PACKAGE_DIR / "bun.lock", "exact traycer bun lock\n"),
        GeneratedArtifact.text(
            _PACKAGE_DIR / "bun.nix",
            "{ normalized = true; }\n",
        ),
    ]
    assert normalized_inputs == ["{ generated = true; }\n"]
    assert len(hash_expressions) == 1
    assert_nix_ast_equal(
        hash_expressions[0],
        _build_fetch_from_github_call(
            "traycerai",
            "traycer",
            rev=_PUBLIC_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert expect_source_hashes(events[-1].payload) == [
        HashEntry.create("srcHash", _SRC_HASH),
        HashEntry.create(
            "sha256",
            _BUN_SHA256,
            platform="aarch64-darwin",
            url=_BUN_URL,
        ),
        HashEntry.create(
            "sha256",
            _HOST_ARCHIVE_SHA256,
            platform="aarch64-darwin",
            url=_HOST_ARCHIVE_URL,
        ),
        HashEntry.create(
            "sha256",
            _HOST_SIGNATURE_SHA256,
            platform="aarch64-darwin",
            url=_HOST_SIGNATURE_URL,
        ),
    ]


def test_traycer_updater_validates_the_checked_in_bun_graph() -> None:
    """The updater boundary must use the same exact graph oracle as CI."""
    module = _load_updater_module()
    module._validate_generated_bun_graph(
        _PACKAGE_DIR / "bun.lock",
        _PACKAGE_DIR / "bun.nix",
    )


def test_traycer_dry_run_skips_bun_artifact_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run validation must not fetch or regenerate checked-in artifacts."""
    module = _load_updater_module()
    updater = module.TraycerUpdater()

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("artifact materialization ran during dry-run")

    async def compute_hash(
        source: str,
        _expr: str,
        *,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        assert config == updater.config
        yield UpdateEvent.value(source, _SRC_HASH)

    monkeypatch.setattr(module.update_net, "fetch_url", forbidden)
    monkeypatch.setattr(module, "run_command", forbidden)
    monkeypatch.setattr(module.update_nix, "compute_fixed_output_hash", compute_hash)

    events = run_async(
        collect_events(
            updater.fetch_hashes(
                _version_info(),
                object(),
                context=UpdateContext(current=None, dry_run=True),
            )
        )
    )

    assert all(event.kind is not UpdateEventKind.ARTIFACT for event in events)
    assert expect_source_hashes(events[-1].payload)[0] == HashEntry.create(
        "srcHash",
        _SRC_HASH,
    )


def test_traycer_fails_when_package_directory_is_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifact generation may not write outside the discovered package lane."""
    module = _load_updater_module()
    updater = module.TraycerUpdater()

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("artifact fetch ran without a package directory")

    monkeypatch.setattr(module, "updater_dir_for", lambda _name: None)
    monkeypatch.setattr(module.update_net, "fetch_url", forbidden)

    with pytest.raises(RuntimeError, match="Package directory not found"):
        run_async(collect_events(updater.fetch_hashes(_version_info(), object())))


def test_traycer_fails_when_bun2nix_omits_its_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful command result without bun.nix is not materialization."""
    module = _load_updater_module()
    updater = module.TraycerUpdater()

    async def fetch_url(
        _session: object,
        _url: str,
        **_kwargs: object,
    ) -> bytes:
        return b"exact traycer bun lock\n"

    async def run_command_without_output(
        args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        assert options.source == "traycer"
        yield UpdateEvent.value(
            "traycer",
            CommandResult(args=args, returncode=0, stdout="", stderr=""),
        )

    monkeypatch.setattr(module.update_net, "fetch_url", fetch_url)
    monkeypatch.setattr(module, "run_command", run_command_without_output)

    with pytest.raises(RuntimeError, match="did not produce bun.nix"):
        run_async(collect_events(updater.fetch_hashes(_version_info(), object())))


def test_traycer_persists_mixed_provenance_without_claiming_build_identity() -> None:
    """sources.json state must distinguish public source from closed Host bytes."""
    module = _load_updater_module()
    hashes = [
        HashEntry.create("srcHash", _SRC_HASH),
        HashEntry.create(
            "sha256",
            _BUN_SHA256,
            platform="aarch64-darwin",
            url=_BUN_URL,
        ),
        HashEntry.create(
            "sha256",
            _HOST_ARCHIVE_SHA256,
            platform="aarch64-darwin",
            url=_HOST_ARCHIVE_URL,
        ),
        HashEntry.create(
            "sha256",
            _HOST_SIGNATURE_SHA256,
            platform="aarch64-darwin",
            url=_HOST_SIGNATURE_URL,
        ),
    ]

    assert module.TraycerUpdater().build_result(
        _version_info(),
        hashes,
    ) == SourceEntry.model_validate({
        "version": _VERSION,
        "commit": _PUBLIC_COMMIT,
        "electronVersion": "42.9.1",
        "urls": {
            "bun": _BUN_URL,
            "hostArchive": _HOST_ARCHIVE_URL,
            "hostSignature": _HOST_SIGNATURE_URL,
        },
        "hashes": HashCollection.from_value(hashes),
    })


def test_traycer_checked_in_sources_are_the_exact_updater_result() -> None:
    """The non-exported package metadata must be reproducible from its updater."""
    source = SourceEntry.model_validate_json(
        (_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8")
    )
    entries = source.hashes.entries
    assert entries is not None
    expected_hashes = [
        HashEntry.create(
            "sha256",
            _BUN_SHA256,
            platform="aarch64-darwin",
            url=_BUN_URL,
        ),
        HashEntry.create(
            "sha256",
            _HOST_ARCHIVE_SHA256,
            platform="aarch64-darwin",
            url=_HOST_ARCHIVE_URL,
        ),
        HashEntry.create(
            "sha256",
            _HOST_SIGNATURE_SHA256,
            platform="aarch64-darwin",
            url=_HOST_SIGNATURE_URL,
        ),
        HashEntry.create("srcHash", _SRC_HASH),
    ]

    assert source == _load_updater_module().TraycerUpdater().build_result(
        _version_info(),
        expected_hashes,
    )
    assert source.hashes.equivalent_to(HashCollection.from_value(expected_hashes))
    assert all(
        not entry.hash.startswith(HashCollection.FAKE_HASH_PREFIX) for entry in entries
    )
    assert not any(entry.hash_type == "nodeModulesHash" for entry in entries)


def test_traycer_generated_bun_nix_has_the_exact_dependency_inventory() -> None:
    """The updater-owned graph must retain every tarball and in-root workspace."""
    bun_nix = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "bun.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    assert {
        formal.name for formal in bun_nix.argument_set if isinstance(formal, Identifier)
    } == {
        "copyPathToStore",
        "fetchurl",
    }

    dependencies = expect_instance(bun_nix.output, AttributeSet)
    assert len(dependencies.values) == 1_869
    fetchurl_count = 0
    workspace_paths: dict[str, str] = {}
    for binding in dependencies.values:
        call = expect_instance(binding.value, FunctionCall)
        callee = expect_instance(call.name, Identifier)
        if callee.name == "fetchurl":
            fetchurl_count += 1
            continue
        assert callee.name == "copyPathToStore"
        workspace_path = expect_instance(call.argument, NixPath)
        workspace_paths[json.loads(binding.name)] = workspace_path.path

    assert fetchurl_count == 1_864
    assert workspace_paths == {
        "@traycer-clients/desktop": "./clients/desktop",
        "@traycer-clients/gui-app": "./clients/gui-app",
        "@traycer-clients/shared": "./clients/shared",
        "@traycer-clients/traycer-cli": "./clients/traycer-cli",
        "@traycer/protocol": "./protocol",
    }


def test_traycer_package_materializes_bun_dependencies_offline() -> None:
    """The exact source workspace must consume bun.nix without a networked FOD."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    formal_names = {
        formal.name for formal in package.argument_set if isinstance(formal, Identifier)
    }
    assert {"inputs", "pkgs"} <= formal_names

    final = expect_instance(package.output, IfExpression)
    scope_bindings = list(final.scope)
    scope_names = {
        binding.name for binding in scope_bindings if isinstance(binding, Binding)
    }
    assert {
        "bunDeps",
        "srcWithBun",
    } <= scope_names
    assert {
        "bunNixForExactSource",
        "copyBunWorkspacePathToStore",
    }.isdisjoint(scope_names)
    assert {
        "desktopNodeModulesHash",
        "sourceWithNodeModules",
    }.isdisjoint(scope_names)
    assert {
        "desktopNodeModulesHash",
        "sourceWithNodeModules",
    }.isdisjoint(_nix_identifier_names(scope_bindings))
    assert {
        "__darwinAllowLocalNetworking",
        "outputHash",
        "outputHashAlgo",
        "outputHashMode",
    }.isdisjoint(_nix_binding_names(scope_bindings))

    assert_nix_ast_equal(
        expect_binding(final.scope, "bunDeps").value,
        """pkgs.callPackage ./bun-cache.nix {
          inherit bun2nix traycerSource;
          bun = bunExact;
          bun2nixSource = inputs.bun2nix;
        }""",
    )

    src_with_bun = expect_instance(
        expect_binding(final.scope, "srcWithBun").value,
        FunctionCall,
    )
    assert_nix_ast_equal(src_with_bun.name, "stdenvNoCC.mkDerivation")
    source_arguments = expect_instance(src_with_bun.argument, AttributeSet)
    assert {
        "__darwinAllowLocalNetworking",
        "buildPhase",
        "nativeBuildInputs",
        "outputHash",
        "outputHashAlgo",
        "outputHashMode",
    }.isdisjoint(_nix_binding_names(source_arguments))
    assert_nix_ast_equal(
        expect_binding(source_arguments.values, "src").value, "traycerSource"
    )
    for attribute in ("dontUnpack", "dontFixup"):
        assert (
            expect_instance(
                expect_binding(source_arguments.values, attribute).value,
                BooleanPrimitive,
            ).value
            is True
        )
    source_install = expect_instance(
        expect_binding(source_arguments.values, "installPhase").value,
        IndentedString,
    )
    source_shell = parse_shell(indented_string_body(source_install.rebuild()))
    assert 'cp -R "$src"/. "$out"' in command_texts(source_shell, "cp")
    assert 'cp __NIX_INTERP__ "$out/bun.lock"' in command_texts(source_shell, "cp")
    assert command_texts(source_shell, "bun") == []

    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    real_arguments = expect_instance(real_package.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(real_arguments.values, "src").value, "srcWithBun"
    )
    assert_nix_ast_equal(
        expect_binding(real_arguments.values, "nativeBuildInputs").value,
        "[ bunExact bun2nix.hook cctools makeWrapper nodejs_24 python3 ]",
    )
    assert_nix_ast_equal(
        expect_binding(real_arguments.values, "bunInstallFlags").value,
        """[
          "--offline"
          "--linker=isolated"
          "--backend=symlink"
          "--frozen-lockfile"
        ]""",
    )
    assert "bunDeps" in {
        name.name
        for inherited in real_arguments.values
        if isinstance(inherited, Inherit)
        for name in inherited.names
    }
    pre_install = expect_instance(
        expect_binding(
            real_arguments.values,
            "preBunNodeModulesInstallPhase",
        ).value,
        IndentedString,
    )
    pre_install_shell = parse_shell(indented_string_body(pre_install.rebuild()))
    assert 'export PATH="__NIX_INTERP__/bin:$PATH"' in command_texts(
        pre_install_shell,
        "export",
    )
    assert "hash -r" in command_texts(pre_install_shell, "hash")
    assert 'test "$(command -v bun)" = "__NIX_INTERP__"' in command_texts(
        pre_install_shell, "test"
    )
    assert 'test "$(bun --version)" = "__NIX_INTERP__"' in command_texts(
        pre_install_shell, "test"
    )
    assert (
        expect_instance(
            expect_binding(real_arguments.values, "dontRunLifecycleScripts").value,
            BooleanPrimitive,
        ).value
        is True
    )
    assert_nix_ast_equal(
        expect_binding(real_arguments.values, "disallowedReferences").value,
        "[ bunDeps bunExact srcWithBun traycerSource ] ++ bunDeps.nixcfg.shardOutputs",
    )
    passthru = expect_binding(real_arguments.values, "passthru").value
    assert {"bunDeps", "srcWithBun"} <= _nix_identifier_names(passthru)


def test_traycer_runtime_output_symlinks_stay_relative_and_internal(
    tmp_path: Path,
) -> None:
    """The final package must not retain build-cache or external symlinks."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    real_arguments = expect_instance(real_package.argument, AttributeSet)
    install_check = expect_instance(
        expect_binding(real_arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    install_check_body = indented_string_body(install_check.rebuild())
    rendered_install_check = textwrap.dedent(install_check_body)
    syntax_check = subprocess.run(
        ["/bin/bash", "-n"],
        capture_output=True,
        check=False,
        input=rendered_install_check,
        text=True,
    )
    assert syntax_check.returncode == 0, syntax_check.stderr
    assert syntax_check.stderr == ""
    install_shell = parse_shell(install_check_body)
    audit_scripts = [
        node_text(node, install_shell.sanitized)
        for node in iter_nodes(install_shell.tree.root_node, "heredoc_body")
        if "Traycer output contains an absolute symlink"
        in node_text(node, install_shell.sanitized)
    ]
    assert len(audit_scripts) == 1
    raw_script = audit_scripts[0]
    indented_line = next(line for line in raw_script.splitlines()[1:] if line)
    indent = len(indented_line) - len(indented_line.lstrip())
    script = textwrap.dedent(f"{' ' * indent}{raw_script}")

    output = tmp_path / "output"
    payload = output / "share" / "payload"
    payload.mkdir(parents=True)
    (payload / "data").write_text("exact", encoding="utf-8")
    link = output / "bin" / "traycer"
    link.parent.mkdir()
    link.symlink_to("../share/payload/data")

    def run_audit() -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [sys.executable, "-", str(output)],
            input=script,
            text=True,
            capture_output=True,
            check=False,
        )

    assert run_audit().returncode == 0

    link.unlink()
    link.symlink_to(payload / "data")
    absolute = run_audit()
    assert absolute.returncode != 0
    assert "absolute symlink" in absolute.stderr

    link.unlink()
    link.symlink_to("../share/missing")
    broken = run_audit()
    assert broken.returncode != 0
    assert "does not resolve" in broken.stderr

    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    link.unlink()
    link.symlink_to("../../outside")
    escaped = run_audit()
    assert escaped.returncode != 0
    assert "escapes the output" in escaped.stderr


def test_traycer_install_check_requires_the_font_list_native_helper() -> None:
    """The packaged app must retain the exact universal font-list helper."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    real_arguments = expect_instance(real_package.argument, AttributeSet)
    install_phase = expect_instance(
        expect_binding(real_arguments.values, "installPhase").value,
        IndentedString,
    )
    install_phase_shell = parse_shell(indented_string_body(install_phase.rebuild()))
    install_commands = {
        " ".join(command.split())
        for command in command_texts(install_phase_shell, "install")
    }
    assert (
        "install -m0644 clients/desktop/node_modules/font-list/LICENSE "
        '"$out/share/licenses/__NIX_INTERP__/font-list-LICENSE"' in install_commands
    )
    install_check = expect_instance(
        expect_binding(real_arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    install_shell = parse_shell(indented_string_body(install_check.rebuild()))

    assignments = {
        node_text(node, install_shell.sanitized)
        for node in iter_nodes(install_shell.tree.root_node, "variable_assignment")
    }
    assert (
        'fontListHelper="$resources/app.asar.unpacked/node_modules/'
        'font-list/libs/darwin/fontlist"'
    ) in assignments
    assert (
        'fontListLicense="$out/share/licenses/__NIX_INTERP__/font-list-LICENSE"'
    ) in assignments
    assert 'test -x "$fontListHelper"' in command_texts(install_shell, "test")
    assert 'test -f "$fontListLicense"' in command_texts(install_shell, "test")
    assert not any(
        '"$fontListHelper"' in command
        for command in command_texts(install_shell, "/usr/bin/lipo")
    )


def test_traycer_cli_link_is_relative_and_resolves_to_the_bundled_cli() -> None:
    """The exported CLI link must not embed its own immutable output path."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    real_arguments = expect_instance(real_package.argument, AttributeSet)

    install_phase = expect_instance(
        expect_binding(real_arguments.values, "installPhase").value,
        IndentedString,
    )
    install_shell = parse_shell(indented_string_body(install_phase.rebuild()))
    assert command_texts(install_shell, "ln") == [
        "ln -s \\\n"
        '        "../Applications/__NIX_INTERP__/Contents/Resources/cli/'
        'darwin-arm64/traycer" \\\n'
        '        "$out/bin/traycer"'
    ]

    install_check = expect_instance(
        expect_binding(real_arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    install_check_shell = parse_shell(indented_string_body(install_check.rebuild()))
    tests = command_texts(install_check_shell, "test")
    assert (
        'test "$(readlink "$out/bin/traycer")" = '
        '"../Applications/__NIX_INTERP__/Contents/Resources/cli/darwin-arm64/traycer"'
        in tests
    )
    assert 'test "$(realpath "$out/bin/traycer")" = "$(realpath "$cli")"' in tests


def test_traycer_desktop_provenance_inventory_and_signing_are_explicit() -> None:
    """Desktop promotion must pin provenance, inventory, and signing order."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    assert (
        expect_instance(
            expect_binding(final.scope, "minimumMacOSVersion").value,
            StringPrimitive,
        ).value
        == "14.0"
    )
    assert (
        expect_instance(
            expect_binding(final.scope, "desktopEntitlementsRelativePath").value,
            StringPrimitive,
        ).value
        == "clients/desktop/resources/bundle/entitlements.mac.plist"
    )

    provenance = expect_instance(
        expect_binding(final.scope, "mixedProvenance").value,
        AttributeSet,
    )
    electron = expect_instance(
        expect_binding(provenance.values, "electron").value,
        AttributeSet,
    )
    electron_names = {
        binding.name for binding in electron.values if isinstance(binding.name, str)
    }
    assert electron_names == {"provenance", "sourceBuilt", "version"}
    assert (
        expect_instance(
            expect_binding(electron.values, "provenance").value,
            StringPrimitive,
        ).value
        == "official-prebuilt-runtime"
    )
    assert (
        expect_instance(
            expect_binding(electron.values, "sourceBuilt").value,
            BooleanPrimitive,
        ).value
        is False
    )

    native_objects = expect_instance(
        expect_binding(final.scope, "desktopNativeCodeObjects").value,
        NixList,
    )
    actual_objects: list[tuple[str, str, tuple[str, ...], str]] = []
    for item in native_objects.value:
        code_object = expect_instance(item, AttributeSet)
        architectures = expect_instance(
            expect_binding(code_object.values, "architectures").value,
            NixList,
        )
        actual_objects.append((
            expect_instance(
                expect_binding(code_object.values, "path").value,
                StringPrimitive,
            ).value,
            expect_instance(
                expect_binding(code_object.values, "identifier").value,
                StringPrimitive,
            ).value,
            tuple(
                expect_instance(architecture, StringPrimitive).value
                for architecture in architectures.value
            ),
            expect_instance(
                expect_binding(code_object.values, "signTarget").value,
                StringPrimitive,
            ).value,
        ))
    assert tuple(actual_objects) == _DESKTOP_NATIVE_CODE_OBJECTS
    assert len({item[0] for item in actual_objects}) == 17

    assert_nix_ast_equal(
        expect_binding(
            final.scope,
            "desktopPreSignedNativePath",
        ).value,
        '"Contents/Resources/app.asar.unpacked/node_modules/font-list/'
        'libs/darwin/fontlist"',
    )
    assert_nix_ast_equal(
        expect_binding(
            final.scope,
            "desktopPostFixupSigningCodeObjects",
        ).value,
        "lib.filter (codeObject: codeObject.path != desktopPreSignedNativePath) "
        "desktopNativeCodeObjects",
    )
    signing_expression = expect_binding(
        final.scope,
        "desktopSigningCommands",
    ).value
    assert {
        "codeObject",
        "desktopPostFixupSigningCodeObjects",
        "lib",
    } <= _nix_identifier_names(signing_expression)
    signing_strings: list[IndentedString] = []
    pending = [signing_expression]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, IndentedString):
            signing_strings.append(current)
        if isinstance(current, (list, tuple)):
            pending.extend(current)
        elif is_dataclass(current):
            pending.extend(
                getattr(current, field.name)
                for field in fields(current)
                if field.name not in {"after", "before", "scope", "scope_state"}
            )
    assert len(signing_strings) == 1
    signing_shell = parse_shell(indented_string_body(signing_strings[0].rebuild()))
    assert command_texts(signing_shell, "/usr/bin/codesign") == [
        "/usr/bin/codesign \\\n"
        "      --force \\\n"
        "      --sign - \\\n"
        "      --identifier __NIX_INTERP__ \\\n"
        "      --options runtime \\\n"
        '      --entitlements "$entitlements" \\\n'
        '      "$app/__NIX_INTERP__"'
    ]
    inventory_expression = expect_binding(
        final.scope,
        "desktopNativeInventory",
    ).value
    assert {
        "codeObject",
        "desktopNativeCodeObjects",
        "lib",
    } <= _nix_identifier_names(inventory_expression)


def _assert_traycer_asar_inventory_contract(tree: ast.Module) -> None:
    """Assert exact ASAR inventory and byte coverage in the generated verifier."""
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    literal_assignments = {
        target.id: ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {"expected_asar_files", "font_list_unpacked_files"}
    }
    assert literal_assignments == {
        "expected_asar_files": {
            "dist/main/index.js",
            "dist/preload/index.js",
            "package.json",
            "node_modules/font-list/index.js",
            "node_modules/font-list/libs/core.js",
            "node_modules/font-list/libs/darwin/fontlist",
            "node_modules/font-list/libs/darwin/index.js",
            "node_modules/font-list/libs/standardize.js",
            "node_modules/font-list/package.json",
        },
        "font_list_unpacked_files": {"node_modules/font-list/libs/darwin/fontlist"},
    }
    assert {
        "tray.png",
        "tray@2x.png",
        "trayTemplate.png",
        "trayTemplate@2x.png",
    } <= string_literals
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert {"json", "struct"} <= imported_modules
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "unpack_from"
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rglob"
        for node in ast.walk(tree)
    )
    padding_assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "padding_size"
            for target in node.targets
        )
    ]
    assert len(padding_assignments) == 1
    assert ast.dump(padding_assignments[0].value) == ast.dump(
        ast.parse("(-header_json_size) % 4", mode="eval").body
    )
    comparisons = {
        ast.dump(node) for node in ast.walk(tree) if isinstance(node, ast.Compare)
    }
    assert (
        ast.dump(
            ast.parse(
                "header_data_size != 4 + header_json_size + padding_size",
                mode="eval",
            ).body
        )
        in comparisons
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "padding_size"
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "any"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "header_padding"
        for node in ast.walk(tree)
    )
    packed_range_assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "packed_ranges"
            for target in node.targets
        )
    ]
    assert len(packed_range_assignments) == 1
    assert isinstance(packed_range_assignments[0].value, ast.List)
    assert packed_range_assignments[0].value.elts == []
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "packed_ranges"
        and node.func.attr == "append"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Tuple)
        and len(node.args[0].elts) == 3
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "packed_ranges"
        and node.func.attr == "sort"
        for node in ast.walk(tree)
    )
    packed_data_size_assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "packed_data_size"
            for target in node.targets
        )
    ]
    assert len(packed_data_size_assignments) == 1
    assert ast.dump(packed_data_size_assignments[0].value) == ast.dump(
        ast.parse("asar_size - asar_data_offset", mode="eval").body
    )
    packed_range_comparisons = {
        ast.dump(node) for node in ast.walk(tree) if isinstance(node, ast.Compare)
    }
    assert (
        ast.dump(ast.parse("packed_ranges[0][0] != 0", mode="eval").body)
        in packed_range_comparisons
    )
    assert (
        ast.dump(
            ast.parse("packed_ranges[-1][1] != packed_data_size", mode="eval").body
        )
        in packed_range_comparisons
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "zip"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "packed_ranges"
        for node in ast.walk(tree)
    )


def _assert_traycer_font_list_materialization_contract(
    build_shell: ParsedShell,
) -> None:
    """Assert the workspace dependency is copied and signed before packaging."""
    build_variable_assignments = {
        node_text(node, build_shell.sanitized)
        for node in iter_nodes(build_shell.tree.root_node, "variable_assignment")
    }
    assert {
        'fontListModule="clients/desktop/node_modules/font-list"',
        'fontListIsolatedTarget="../../../node_modules/.bun/'
        'font-list@2.1.0/node_modules/font-list"',
        'fontListInstalled="$(realpath "$fontListModule")"',
        'fontListBuildHelper="$fontListModule/libs/darwin/fontlist"',
        'entitlements="__NIX_INTERP__/__NIX_INTERP__"',
    } <= build_variable_assignments
    test_commands = command_texts(build_shell, "test")
    assert 'test -L "$fontListModule"' in test_commands
    assert (
        'test "$(readlink "$fontListModule")" = "$fontListIsolatedTarget"'
    ) in test_commands
    assert 'test -d "$fontListInstalled"' in test_commands
    assert any(
        '-R "$fontListInstalled"/. "$fontListWritable"/' in command
        for command in command_texts(build_shell, "cp")
    )
    package_identity_commands = [
        command for command in test_commands if "font-list@2.1.0" in command
    ]
    assert len(package_identity_commands) == 2
    assert any(
        '"$fontListInstalled/package.json"' in command
        for command in package_identity_commands
    )
    assert any(
        '"$fontListWritable/package.json"' in command
        for command in package_identity_commands
    )
    pre_builder_codesign = command_texts(build_shell, "/usr/bin/codesign")
    assert pre_builder_codesign == [
        "/usr/bin/codesign --force --sign - --identifier fontlist "
        '--options runtime --entitlements "$entitlements" "$fontListBuildHelper"',
        '/usr/bin/codesign --verify --strict --verbose=2 "$fontListBuildHelper"',
    ]


def _assert_traycer_build_phase_contract(real_arguments: AttributeSet) -> None:
    """Assert the real package tests before narrowing packaged dependencies."""
    build_environment = expect_instance(
        expect_binding(real_arguments.values, "env").value,
        BinaryExpression,
    )
    environment_overrides = expect_instance(
        build_environment.right,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(
            environment_overrides.values,
            "MACOSX_DEPLOYMENT_TARGET",
        ).value,
        "minimumMacOSVersion",
    )
    assert_nix_ast_equal(
        expect_binding(
            environment_overrides.values,
            "VITE_DESKTOP_LOCAL_STORAGE_KEY",
        ).value,
        "desktopLocalStorageKey",
    )
    build_phase = expect_instance(
        expect_binding(real_arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))
    bun_commands = command_texts(build_shell, "bun")
    assert "bun run --cwd clients/traycer-cli compile" in bun_commands
    assert "bun run --cwd clients/desktop compile" in bun_commands
    builder = [
        command
        for command in bun_commands
        if "x --no-install electron-builder" in command
    ]
    assert len(builder) == 1
    assert "-c.mac.minimumSystemVersion=" not in builder[0]
    build_assignments = {
        node_text(node, build_shell.sanitized)
        for node in iter_nodes(build_shell.tree.root_node, "variable_assignment")
    }
    assert (
        'defaultApp="$electronDistDir/Electron.app/Contents/Resources/default_app.asar"'
        in build_assignments
    )
    build_commands = [
        (node.start_byte, node_text(node, build_shell.sanitized))
        for node in iter_nodes(build_shell.tree.root_node, "command")
    ]
    copy_dist_positions = [
        position for position, command in build_commands if command == "__NIX_INTERP__"
    ]
    default_app_removal_positions = [
        position
        for position, command in build_commands
        if command == 'rm "$defaultApp"'
    ]
    builder_positions = [
        position
        for position, command in build_commands
        if "x --no-install electron-builder" in command
    ]
    assert len(copy_dist_positions) == 1
    assert len(default_app_removal_positions) == 1
    assert len(builder_positions) == 1
    assert (
        copy_dist_positions[0] < default_app_removal_positions[0] < builder_positions[0]
    )
    assert 'test -f "$defaultApp"' in command_texts(build_shell, "test")
    _assert_traycer_font_list_materialization_contract(build_shell)
    dependency_scripts = [
        node_text(node, build_shell.sanitized)
        for node in iter_nodes(build_shell.tree.root_node, "heredoc_body")
        if "expected_dependencies" in node_text(node, build_shell.sanitized)
    ]
    assert len(dependency_scripts) == 1
    dependency_tree = _parse_python_heredoc(dependency_scripts[0])
    literal_assignments = {
        target.id: ast.literal_eval(node.value)
        for node in ast.walk(dependency_tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {"expected_dependencies", "packaged_dependencies"}
    }
    assert literal_assignments == {
        "expected_dependencies": {
            "@sentry/browser": "catalog:",
            "@sentry/electron": "catalog:",
            "electron-log": "catalog:",
            "electron-updater": "^6.8.9",
            "encrypt-storage": "catalog:",
            "font-list": "^2.1.0",
            "react": "catalog:",
            "react-dom": "catalog:",
        },
        "packaged_dependencies": {"font-list": "^2.1.0"},
    }
    dependency_script_positions = [
        node.start_byte
        for node in iter_nodes(build_shell.tree.root_node, "heredoc_body")
        if "expected_dependencies" in node_text(node, build_shell.sanitized)
    ]
    for prerequisite in (
        "bun run --cwd clients/desktop build:app",
        "nix-managed-command-policy.test.ts",
        "nix-managed-updater-policy.test.ts",
        "nix-managed-host-controller-policy.test.ts",
        "check-cli-resource.cjs",
        "check-bundle-icons.cjs",
        "check-tray-assets.cjs",
        "codesign --force --sign - --identifier fontlist",
    ):
        prerequisite_positions = [
            position for position, command in build_commands if prerequisite in command
        ]
        assert len(prerequisite_positions) == 1
        assert prerequisite_positions[0] < dependency_script_positions[0]
    assert dependency_script_positions[0] < builder_positions[0]
    vitest_commands = command_texts(
        build_shell,
        "../../node_modules/.bin/vitest",
    )
    assert len(vitest_commands) == 3
    assert all("run" in command for command in vitest_commands)
    assert any(
        "nix-managed-command-policy.test.ts" in command for command in vitest_commands
    )
    assert any(
        "nix-managed-updater-policy.test.ts" in command for command in vitest_commands
    )
    assert any(
        "nix-managed-host-controller-policy.test.ts" in command
        for command in vitest_commands
    )


def test_traycer_desktop_build_and_bundle_audit_contract_is_explicit() -> None:
    """Desktop promotion must compile, test, and audit the complete bundle."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    desktop_storage_key = expect_instance(
        expect_binding(final.scope, "desktopLocalStorageKey").value,
        StringPrimitive,
    )
    assert len(desktop_storage_key.value) == 64
    assert (
        hashlib.sha256(desktop_storage_key.value.encode()).hexdigest()
        == "f7ed5773a12228344502090c3740481a09edab5a5a698700d763e9b7a73ed065"
    )
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    real_arguments = expect_instance(real_package.argument, AttributeSet)
    _assert_traycer_build_phase_contract(real_arguments)

    post_patch = expect_instance(
        expect_binding(real_arguments.values, "postPatch").value,
        IndentedString,
    )
    patch_shell = parse_shell(indented_string_body(post_patch.rebuild()))
    test_installs = [
        command
        for command in command_texts(patch_shell, "install")
        if "nix-managed-" in command
    ]
    assert len(test_installs) == 3

    post_fixup = expect_instance(
        expect_binding(real_arguments.values, "postFixup").value,
        IndentedString,
    )
    post_fixup_shell = parse_shell(indented_string_body(post_fixup.rebuild()))
    post_fixup_assignments = {
        node_text(node, post_fixup_shell.sanitized)
        for node in iter_nodes(
            post_fixup_shell.tree.root_node,
            "variable_assignment",
        )
    }
    assert "entitlements=__NIX_INTERP__/__NIX_INTERP__" in post_fixup_assignments
    assert not any(
        assignment.startswith("defaultApp=") for assignment in post_fixup_assignments
    )
    assert command_texts(post_fixup_shell, "rm") == []
    assert "__NIX_INTERP__" in command_texts(post_fixup_shell, "__NIX_INTERP__")
    assert not any(
        "--deep" in command and "--sign" in command
        for command in command_texts(post_fixup_shell, "/usr/bin/codesign")
    )

    install_check = expect_instance(
        expect_binding(real_arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    install_shell = parse_shell(indented_string_body(install_check.rebuild()))
    assert any(
        command == '__NIX_INTERP__ __NIX_INTERP__ "$resources/renderer"'
        for command in command_texts(install_shell, "__NIX_INTERP__")
    )
    plist_commands = command_texts(install_shell, "/usr/libexec/PlistBuddy")
    assert any("Print :LSMinimumSystemVersion" in command for command in plist_commands)
    assert not any(
        "$resources/app.asar" in command
        for command in command_texts(install_shell, "grep")
    )
    build_path_audits = [
        node_text(node, install_shell.sanitized)
        for node in iter_nodes(install_shell.tree.root_node, "heredoc_body")
        if "Traycer artifact retains a source/cache/build path token"
        in node_text(node, install_shell.sanitized)
    ]
    assert len(build_path_audits) == 1
    build_path_tree = _parse_python_heredoc(build_path_audits[0])
    byte_literals = {
        node.value
        for node in ast.walk(build_path_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, bytes)
    }
    assert {
        b"/nix/var/nix/builds/",
        b"/private/tmp/",
        b"traycer-src-with-bun",
        b".bun/",
    } <= byte_literals
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_bytes"
        for node in ast.walk(build_path_tree)
    )
    assert {
        "ElectronAsarIntegrity",
        "Resources/app.asar",
        "SHA256",
    } <= {
        node.value
        for node in ast.walk(build_path_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "hexdigest"
        for node in ast.walk(build_path_tree)
    )
    artifact_inventory_scripts = [
        node_text(node, install_shell.sanitized)
        for node in iter_nodes(install_shell.tree.root_node, "heredoc_body")
        if "expected_asar_files" in node_text(node, install_shell.sanitized)
    ]
    assert len(artifact_inventory_scripts) == 1
    artifact_inventory_tree = _parse_python_heredoc(artifact_inventory_scripts[0])
    _assert_traycer_asar_inventory_contract(artifact_inventory_tree)
    inventory_heredocs = [
        node_text(node, install_shell.sanitized)
        for node in iter_nodes(install_shell.tree.root_node, "heredoc_body")
        if "__NIX_INTERP__" in node_text(node, install_shell.sanitized)
    ]
    assert inventory_heredocs
    audit_scripts = [
        node_text(node, install_shell.sanitized)
        for node in iter_nodes(install_shell.tree.root_node, "heredoc_body")
        if "LC_BUILD_VERSION" in node_text(node, install_shell.sanitized)
    ]
    assert len(audit_scripts) == 1
    audit_script = audit_scripts[0]
    for required in (
        "plistlib",
        "required_entitlements",
        "com.apple.security.cs.allow-jit",
        "com.apple.security.cs.allow-unsigned-executable-memory",
        "com.apple.security.cs.allow-dyld-environment-variables",
        "com.apple.security.cs.disable-library-validation",
        "com.apple.security.device.audio-input",
        "LC_VERSION_MIN_MACOSX",
        "/nix/store/",
    ):
        assert required in audit_script
    _assert_desktop_native_audit_script(audit_script)


def test_traycer_final_sea_policy_probes_are_isolated_and_exhaustive() -> None:
    """The built CLI must refuse every mutable route without touching state."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    arguments = expect_instance(real_package.argument, AttributeSet)
    install_check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(install_check.rebuild()))
    probes = command_texts(shell, "runManagedProbe")
    assert probes == [
        'runManagedProbe host-install host install --from "$policyRoot/missing-host.tar.gz"',
        "runManagedProbe host-apply host apply",
        "runManagedProbe host-purge-stage host purge-stage --expected-stage-fingerprint synthetic-stage",
        "runManagedProbe host-stamp-runtime host stamp-runtime --expected-install-generation synthetic-install --observed-pid 1 --observed-started-at 1970-01-01T00:00:00.000Z --observed-runtime-version __NIX_INTERP__",
        "runManagedProbe host-update host update",
        "runManagedProbe host-download host download latest",
        "runManagedProbe host-uninstall host uninstall",
        "runManagedProbe service-install host service install",
        "runManagedProbe service-uninstall host service uninstall",
        "runManagedProbe cli-upgrade cli upgrade --dry-run --target __NIX_INTERP__",
        'runManagedProbe cli-mark-source cli mark-source --source desktop --binary-path "$cli" --installed-version __NIX_INTERP__',
        "runManagedProbe cli-finalize-upgrade cli finalize-upgrade",
        'runManagedProbe cli-re-anchor cli re-anchor --binary-path "$cli" --installed-version __NIX_INTERP__',
    ]
    assignments = {
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "variable_assignment")
    }
    for name in (
        'HOME="$policyRoot/home"',
        'XDG_CONFIG_HOME="$policyRoot/xdg-config"',
        'XDG_STATE_HOME="$policyRoot/xdg-state"',
        'XDG_CACHE_HOME="$policyRoot/xdg-cache"',
        'TMPDIR="$policyRoot/tmp"',
        'TRAYCER_LAUNCHCTL_LOG="$policyRoot/launchctl.log"',
        "CI=1",
        "TRAYCER_NONINTERACTIVE=1",
        "NO_COLOR=1",
    ):
        assert name in assignments
    assert any(
        'PATH="$policyRoot/fake-bin:$PATH"' in command
        for command in command_texts(shell, "export")
    )
    assert any(
        '"$policyRoot/before.json" "$policyRoot/after.json"' in command
        for command in command_texts(shell, "diff")
    )
    assert 'chmod 0600 "$policyRoot/home/.traycer/cli/cli.log"' in command_texts(
        shell, "chmod"
    )
    snapshot_scripts = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "heredoc_body")
        if "def describe(root)" in node_text(node, shell.sanitized)
    ]
    assert len(snapshot_scripts) == 1
    snapshot_tree = _parse_python_heredoc(snapshot_scripts[0])
    mutable_log_condition = ast.dump(
        ast.parse('path == root / "cli.log"', mode="eval").body
    )
    assert any(
        isinstance(node, ast.If)
        and ast.dump(node.test) == mutable_log_condition
        and any(
            isinstance(value, ast.Constant) and value.value == "mutable-log"
            for statement in node.body
            for value in ast.walk(statement)
        )
        for node in ast.walk(snapshot_tree)
    )
    assert 'test ! -e "$TRAYCER_LAUNCHCTL_LOG"' in command_texts(shell, "test")


def test_traycer_package_is_exported_only_after_literal_evidence_is_complete() -> None:
    """Promoted evidence and the app-free CLI must stay literal and fail-closed."""
    assert_nix_ast_equal(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        "import ./package.nix",
    )
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    formal_names = {
        formal.name for formal in package.argument_set if isinstance(formal, Identifier)
    }
    assert "runCommand" in formal_names
    assert formal_names.isdisjoint({
        "verifiedHostCodesignIdentity",
        "desktopBundleValidationComplete",
    })

    final = expect_instance(package.output, IfExpression)
    assert_nix_ast_equal(
        expect_binding(final.scope, "verifiedHostCodesignIdentity").value,
        """{
          teamIdentifier = "7YVZ56DZ74";
          identifier = "traycer-host";
          designatedRequirement = ''identifier "traycer-host" and anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */ and certificate leaf[subject.OU] = "7YVZ56DZ74"'';
          executableSha256 = "4977de1ec618e272c4701e004de9aee0efea32b3b72fe42012ef0016fe6bf48c";
        }""",
    )
    assert (
        expect_instance(
            expect_binding(final.scope, "desktopBundleValidationComplete").value,
            BooleanPrimitive,
        ).value
        is True
    )

    common_passthru = expect_instance(
        expect_binding(final.scope, "commonPassthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(common_passthru.values, "hostOwnership").value,
        """
        {
          runtimeStoreOwner = "nix";
          serviceRegistrationOwner = "nix";
          lifecycleIntegrationComplete = true;
          packageExported = true;
          mutableHostInstallAllowed = false;
          mutableCliUpgradeAllowed = false;
          desktopSelfUpdateAllowed = false;
        }
        """,
    )

    condition = expect_instance(final.condition, BinaryExpression)
    assert condition.operator.name == "=="
    assert expect_instance(condition.left, Identifier).name == "unresolvedBuildGates"
    assert expect_instance(condition.right, NixList).value == []
    assert expect_instance(final.consequence, Identifier).name == "realPackage"
    assert expect_instance(final.alternative, Identifier).name == "blockedPackage"
    unresolved = expect_binding(final.scope, "unresolvedBuildGates").value
    assert {
        "verifiedHostCodesignIdentity",
        "desktopBundleValidationComplete",
    } <= _nix_identifier_names(unresolved)

    blocked_package = expect_instance(
        expect_binding(final.scope, "blockedPackage").value,
        FunctionCall,
    )
    blocked_arguments = expect_instance(blocked_package.argument, AttributeSet)
    blocked_passthru = expect_binding(blocked_arguments.values, "passthru").value
    assert_nix_ast_equal(
        blocked_passthru,
        "commonPassthru // { inherit hostRuntime; desktopAuditPackage = realPackage; }",
    )
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    real_arguments = expect_instance(real_package.argument, AttributeSet)
    real_passthru = expect_binding(real_arguments.values, "passthru").value
    assert "desktopAuditPackage" not in _nix_binding_names(real_passthru)
    real_overrides = expect_instance(real_passthru, BinaryExpression)
    assert real_overrides.operator.name == "//"
    real_passthru_values = expect_instance(real_overrides.right, AttributeSet)
    cli_package = expect_binding(real_passthru_values.values, "cliPackage").value
    assert_nix_ast_equal(
        "{ runCommand, pname, version, realPackage, appBundleName }: "
        + cli_package.rebuild(),
        r"""
        { runCommand, pname, version, realPackage, appBundleName }:
        runCommand "${pname}-cli-${version}" { } ''
          mkdir -p "$out/bin"
          ln -s \
            "${realPackage}/Applications/${appBundleName}/Contents/Resources/cli/darwin-arm64/traycer" \
            "$out/bin/${pname}"
        ''
        """,
    )

    literal_strings = {
        "expectedHostArchiveSha256": (
            "66cf81e799d8251466e34ec13b6159007cbb1069dc091d6dc75e10a28d546939"
        ),
        "expectedHostSignatureSha256": (
            "556fafe5c3bc5f6a2a7bce55f6cb2c6c61b139a947a72e44f65dbd9dca23439d"
        ),
        "expectedHostMinisignPublicKey": _HOST_MINISIGN_PUBLIC_KEY,
        "expectedHostMinisignKeyId": _HOST_MINISIGN_KEY_ID,
        "expectedHostMinisignTrustedComment": _HOST_MINISIGN_TRUSTED_COMMENT,
        "expectedBunSha256": (
            "6c4bb87dd013ed1a8d6a16e357a3d094959fd5530b4d7061f7f3680c3c7cea1c"
        ),
        "unverifiedPrivateBuildCommit": _BUILD_COMMIT,
    }
    for binding_name, expected in literal_strings.items():
        assert (
            expect_instance(
                expect_binding(final.scope, binding_name).value,
                StringPrimitive,
            ).value
            == expected
        )
    assert (
        expect_instance(
            expect_binding(final.scope, "expectedHostArchiveSize").value,
            IntegerPrimitive,
        ).value
        == 76_162_681
    )
    assert (
        expect_instance(
            expect_binding(final.scope, "expectedHostArchiveMemberCount").value,
            IntegerPrimitive,
        ).value
        == 2_954
    )
    assert (
        expect_instance(
            expect_binding(final.scope, "expectedHostArchiveFileCount").value,
            IntegerPrimitive,
        ).value
        == 2_748
    )
    assert (
        expect_instance(
            expect_binding(final.scope, "expectedHostSignatureSize").value,
            IntegerPrimitive,
        ).value
        == 293
    )
    assert (
        expect_instance(
            expect_binding(final.scope, "expectedBunSize").value,
            IntegerPrimitive,
        ).value
        == _BUN_SIZE
    )


def test_traycer_numeric_evidence_is_rendered_explicitly_for_shell() -> None:
    """Integer evidence must cross the Nix-to-shell boundary through toString."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    host_runtime = expect_instance(
        expect_binding(final.scope, "hostRuntime").value,
        FunctionCall,
    )
    arguments = expect_instance(host_runtime.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    numeric_bindings = {
        "expectedHostArchiveSize",
        "expectedHostSignatureSize",
        "expectedHostArchiveMemberCount",
        "expectedHostArchiveFileCount",
        "expectedHostMachOCount",
        "expectedHostUniversalMachOCount",
        "expectedHostThinX8664MachOCount",
    }
    usages: dict[str, list[object]] = {name: [] for name in numeric_bindings}
    for match in re.finditer(r"(?<!'')\$\{([^{}\n]+)\}", build_phase.value):
        expression = parse_nix_expr(match.group(1))
        for name in numeric_bindings & _nix_identifier_names(expression):
            usages[name].append(expression)

    for name, expressions in usages.items():
        assert expressions, name
        for expression in expressions:
            rendered = expect_instance(expression, FunctionCall)
            assert expect_instance(rendered.name, Identifier).name == "toString"
            assert expect_instance(rendered.argument, Identifier).name == name

    bun_exact = expect_instance(
        expect_binding(final.scope, "bunExact").value,
        FunctionCall,
    )
    bun_arguments = expect_instance(bun_exact.argument, AttributeSet)
    bun_meta = expect_instance(
        expect_binding(bun_arguments.values, "meta").value,
        AttributeSet,
    )
    assert (
        expect_instance(
            expect_binding(bun_meta.values, "mainProgram").value,
            StringPrimitive,
        ).value
        == "bun"
    )
    bun_install = expect_instance(
        expect_binding(bun_arguments.values, "installPhase").value,
        IndentedString,
    )
    bun_size_use = next(
        parse_nix_expr(match.group(1))
        for match in re.finditer(r"(?<!'')\$\{([^{}\n]+)\}", bun_install.value)
        if "expectedBunSize" in _nix_identifier_names(parse_nix_expr(match.group(1)))
    )
    rendered_bun_size = expect_instance(bun_size_use, FunctionCall)
    assert expect_instance(rendered_bun_size.name, Identifier).name == "toString"
    assert (
        expect_instance(rendered_bun_size.argument, Identifier).name
        == "expectedBunSize"
    )

    provenance = expect_instance(
        expect_binding(final.scope, "mixedProvenance").value,
        AttributeSet,
    )
    desktop = expect_instance(
        expect_binding(provenance.values, "desktopAndCli").value,
        AttributeSet,
    )
    assert (
        expect_instance(
            expect_binding(desktop.values, "vendorShippedByteIdentityClaimed").value,
            BooleanPrimitive,
        ).value
        is False
    )
    private_build = expect_instance(
        expect_binding(provenance.values, "privateBuildReference").value,
        AttributeSet,
    )
    assert (
        expect_instance(
            expect_binding(private_build.values, "verified").value,
            BooleanPrimitive,
        ).value
        is False
    )
    assert (
        expect_instance(
            expect_binding(private_build.values, "sourceOrBinaryIdentityClaimed").value,
            BooleanPrimitive,
        ).value
        is False
    )

    blocked_package = expect_instance(
        expect_binding(final.scope, "blockedPackage").value,
        FunctionCall,
    )
    blocked_arguments = expect_instance(blocked_package.argument, AttributeSet)
    blocked_passthru = expect_instance(
        expect_binding(blocked_arguments.values, "passthru").value,
        BinaryExpression,
    )
    assert blocked_passthru.operator.name == "//"
    assert expect_instance(blocked_passthru.left, Identifier).name == "commonPassthru"
    host_passthru = expect_instance(blocked_passthru.right, AttributeSet)
    assert "hostRuntime" in {
        name.name
        for inherited in host_passthru.values
        if isinstance(inherited, Inherit)
        for name in inherited.names
    }


def test_traycer_host_runtime_preserves_managed_executable_layout() -> None:
    """The Host output and managed source patch must share one executable path."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    assert (
        expect_instance(
            expect_binding(final.scope, "hostRuntimeRelativeExecutable").value,
            StringPrimitive,
        ).value
        == _HOST_RUNTIME_RELATIVE_EXECUTABLE
    )

    host_runtime = expect_instance(
        expect_binding(final.scope, "hostRuntime").value,
        FunctionCall,
    )
    arguments = expect_instance(host_runtime.argument, AttributeSet)
    assert (
        expect_instance(
            expect_binding(arguments.values, "dontFixup").value,
            BooleanPrimitive,
        ).value
        is True
    )
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))
    assert 'mkdir -p "$out/host-runtime"' in command_texts(build_shell, "mkdir")
    assert 'cp -R "$hostRoot"/. "$out/host-runtime"' in command_texts(
        build_shell,
        "cp",
    )
    assert 'test -x "$out/__NIX_INTERP__"' in command_texts(build_shell, "test")


def test_traycer_host_runtime_verifies_every_authenticated_macho() -> None:
    """Every discovered Mach-O must retain a strict vendor signature and Team ID."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    for binding_name, expected in {
        "expectedHostMachOCount": 14,
        "expectedHostUniversalMachOCount": 0,
        "expectedHostThinX8664MachOCount": 0,
    }.items():
        assert (
            expect_instance(
                expect_binding(final.scope, binding_name).value,
                IntegerPrimitive,
            ).value
            == expected
        )

    host_runtime = expect_instance(
        expect_binding(final.scope, "hostRuntime").value,
        FunctionCall,
    )
    arguments = expect_instance(host_runtime.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))

    assert 'find "$out/host-runtime" -type f -print0' in command_texts(
        build_shell,
        "find",
    )
    assert command_texts(build_shell, "/usr/bin/file") == [
        '/usr/bin/file -b "$candidate"'
    ]
    codesign_commands = command_texts(build_shell, "/usr/bin/codesign")
    assert '/usr/bin/codesign --verify --strict --verbose=2 "$candidate"' in (
        codesign_commands
    )
    assert not any("--deep" in command for command in codesign_commands)
    assert command_texts(build_shell, "/usr/bin/lipo") == []

    test_commands = command_texts(build_shell, "test")
    assert 'test "$candidateTeamIdentifier" = __NIX_INTERP__' in test_commands
    assert 'test "$machOCount" -eq __NIX_INTERP__' in test_commands
    assert 'test "$universalMachOCount" -eq __NIX_INTERP__' in test_commands
    assert 'test "$thinX8664MachOCount" -eq __NIX_INTERP__' in test_commands
    assert 'test "$actualIdentifier" = __NIX_INTERP__' in test_commands
    assert 'test "$actualRequirement" = __NIX_INTERP__' in test_commands


def test_traycer_host_bundled_ripgrep_runs_pcre2_in_an_empty_environment() -> None:
    """Exercise the authenticated ripgrep through its supported dyld fallback."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    for binding_name, expected in {
        "hostRipgrepRelativeExecutable": (
            "host-runtime/resources/providers/ripgrep/darwin-arm64/rg"
        ),
        "expectedHostRipgrepVersion": "15.2.0",
        "expectedHostRipgrepPcre2Feature": "features:+pcre2",
    }.items():
        assert (
            expect_instance(
                expect_binding(final.scope, binding_name).value,
                StringPrimitive,
            ).value
            == expected
        )

    host_runtime = expect_instance(
        expect_binding(final.scope, "hostRuntime").value,
        FunctionCall,
    )
    arguments = expect_instance(host_runtime.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))

    env_commands = command_texts(build_shell, "/usr/bin/env")
    assert env_commands == [
        '/usr/bin/env -i "$outputHostRipgrep" --version',
        '/usr/bin/env -i "$outputHostRipgrep" --no-config --color=never '
        "--no-heading --no-filename --no-line-number -P "
        "'^traycer-(?=pcre2$)pcre2$'",
    ]
    assert all("DYLD_" not in command for command in env_commands)
    assert not any(
        re.search(r"/usr/bin/env -i [A-Za-z_][A-Za-z0-9_]*=", command)
        for command in env_commands
    )

    assert (
        "read -r hostRipgrepName hostRipgrepVersionNumber hostRipgrepRevision "
        '<<< "$hostRipgrepVersionLine"' in command_texts(build_shell, "read")
    )

    test_commands = command_texts(build_shell, "test")
    for expected_test in (
        'test -x "$outputHostRipgrep"',
        'test "$hostRipgrepName" = ripgrep',
        'test "$hostRipgrepVersionNumber" = __NIX_INTERP__',
        'test "$hostRipgrepFeatures" = __NIX_INTERP__',
        'test "$hostRipgrepPcre2Result" = traycer-pcre2',
    ):
        assert expected_test in test_commands

    commands = command_texts(build_shell)
    requirement_index = next(
        index
        for index, command in enumerate(commands)
        if "/usr/bin/codesign -d -r-" in command
    )
    smoke_index = next(
        index
        for index, command in enumerate(commands)
        if command.startswith('/usr/bin/env -i "$outputHostRipgrep"')
    )
    record_index = next(
        index
        for index, command in enumerate(commands)
        if '"$out/install.json"' in command
    )
    assert requirement_index < smoke_index < record_index
    assert command_texts(build_shell, "/usr/bin/install_name_tool") == []
    assert not any(
        "--sign" in command or "--force" in command
        for command in command_texts(build_shell, "/usr/bin/codesign")
    )


def test_traycer_host_install_record_uses_the_production_runtime_root() -> None:
    """Production must read one root record pointing at the shared Host path."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    for binding_name, expected in {
        "expectedHostInstallId": _HOST_INSTALL_ID,
        "expectedHostInstallSentinelTimestamp": _HOST_INSTALL_SENTINEL_TIMESTAMP,
    }.items():
        assert (
            expect_instance(
                expect_binding(final.scope, binding_name).value,
                StringPrimitive,
            ).value
            == expected
        )

    host_runtime = expect_instance(
        expect_binding(final.scope, "hostRuntime").value,
        FunctionCall,
    )
    arguments = expect_instance(host_runtime.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))
    interpolated_commands = command_texts(build_shell, "__NIX_INTERP__")
    record_commands = [
        command for command in interpolated_commands if '"$out/install.json"' in command
    ]
    assert len(record_commands) == 1
    assert '"$out/install.json" \\\n        "$out/__NIX_INTERP__"' in record_commands[0]
    subtree_identity_command = next(
        command
        for command in interpolated_commands
        if '"$hostRoot"' in command and '"$out/host-runtime"' in command
    )
    assert interpolated_commands.index(
        subtree_identity_command
    ) < interpolated_commands.index(record_commands[0])
    assert not any(
        "host-runtime/install.json" in command
        or '"$out/install/install.json"' in command
        for command in command_texts(build_shell)
    )


def test_traycer_host_install_record_is_exact_immutable_and_fail_closed(
    tmp_path: Path,
) -> None:
    """The production record must be deterministic, valid, and layout-bound."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    host_runtime = expect_instance(
        expect_binding(final.scope, "hostRuntime").value,
        FunctionCall,
    )
    arguments = expect_instance(host_runtime.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))
    raw_script = next(
        node_text(node, build_shell.sanitized)
        for node in iter_nodes(build_shell.tree.root_node, "heredoc_body")
        if "install_record =" in node_text(node, build_shell.sanitized)
    )
    indented_line = next(line for line in raw_script.splitlines()[1:] if line)
    indent = len(indented_line) - len(indented_line.lstrip())
    record_script = textwrap.dedent(f"{' ' * indent}{raw_script}")

    runtime = tmp_path / "runtime"
    executable = runtime / _HOST_RUNTIME_RELATIVE_EXECUTABLE
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"authenticated Host bytes")
    executable.chmod(0o755)
    before_bytes = executable.read_bytes()
    before_mode = executable.stat().st_mode
    record_path = runtime / "install.json"

    def run_writer(
        output_record: Path,
        output_executable: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-",
                str(output_record),
                str(output_executable),
                _HOST_INSTALL_ID,
                _VERSION,
                _HOST_ARCHIVE_HEX_SHA256,
                _HOST_MINISIGN_KEY_ID,
                str(76_162_681),
                _HOST_INSTALL_SENTINEL_TIMESTAMP,
            ],
            input=record_script,
            text=True,
            capture_output=True,
            check=False,
        )

    assert run_writer(record_path, executable).returncode == 0
    expected_record = {
        "installId": _HOST_INSTALL_ID,
        "version": _VERSION,
        "runtimeVersion": _VERSION,
        "platform": "darwin",
        "arch": "arm64",
        "installedAt": _HOST_INSTALL_SENTINEL_TIMESTAMP,
        "source": {"kind": "registry", "value": _VERSION},
        "archiveSha256": _HOST_ARCHIVE_HEX_SHA256,
        "signatureVerifiedAt": _HOST_INSTALL_SENTINEL_TIMESTAMP,
        "signatureKeyId": _HOST_MINISIGN_KEY_ID,
        "sizeBytes": 76_162_681,
        "executablePath": str(executable),
    }
    raw_record = record_path.read_text(encoding="utf-8")
    parsed_record = json.loads(raw_record)
    assert list(parsed_record) == list(expected_record)
    assert parsed_record == expected_record
    assert raw_record == json.dumps(expected_record, indent=2) + "\n"
    assert record_path.stat().st_mode & 0o777 == 0o444
    assert executable.read_bytes() == before_bytes
    assert executable.stat().st_mode == before_mode

    unsafe_runtime = tmp_path / "unsafe-runtime"
    unsafe_runtime.mkdir()
    unsafe_result = run_writer(
        unsafe_runtime / "install.json",
        tmp_path / "outside" / "traycer-host",
    )
    assert unsafe_result.returncode != 0
    assert "production layout mismatch" in unsafe_result.stderr
    assert not (unsafe_runtime / "install.json").exists()


def test_traycer_host_minisign_identity_uses_raw_packet_key_id(
    tmp_path: Path,
) -> None:
    """The precheck must honor minisign packet algorithms and raw key-ID order."""
    public_key_packet = base64.b64decode(
        _HOST_MINISIGN_PUBLIC_KEY,
        validate=True,
    )
    assert len(public_key_packet) == 42
    assert public_key_packet[:2] == b"Ed"
    raw_key_id = public_key_packet[2:10]
    assert raw_key_id.hex() == _HOST_MINISIGN_KEY_ID

    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    host_runtime = expect_instance(
        expect_binding(final.scope, "hostRuntime").value,
        FunctionCall,
    )
    arguments = expect_instance(host_runtime.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))
    heredocs = list(iter_nodes(build_shell.tree.root_node, "heredoc_body"))
    raw_precheck = next(
        node_text(node, build_shell.sanitized)
        for node in heredocs
        if "import base64" in node_text(node, build_shell.sanitized)
    )
    assert [
        command
        for command in command_texts(build_shell, "__NIX_INTERP__")
        if "-Vm" in command
    ] == [
        "__NIX_INTERP__ \\\n"
        '        -Vm "$archive" \\\n'
        '        -x "$signature" \\\n'
        "        -P __NIX_INTERP__"
    ]
    indented_line = next(line for line in raw_precheck.splitlines()[1:] if line)
    indent = len(indented_line) - len(indented_line.lstrip())
    identity_precheck = textwrap.dedent(f"{' ' * indent}{raw_precheck}")

    archive = tmp_path / "host.tar.gz"
    signature = tmp_path / "host.tar.gz.minisig"
    archive.write_bytes(b"synthetic host archive")

    def run_precheck(signature_key_id: bytes) -> subprocess.CompletedProcess[str]:
        signature_packet = b"ED" + signature_key_id + bytes(64)
        global_packet = b"ED" + signature_key_id + bytes(64)
        signature.write_text(
            "\n".join([
                "untrusted comment: synthetic minisign signature",
                base64.b64encode(signature_packet).decode("ascii"),
                f"trusted comment: {_HOST_MINISIGN_TRUSTED_COMMENT}",
                base64.b64encode(global_packet).decode("ascii"),
            ]),
            encoding="utf-8",
        )
        archive_payload = archive.read_bytes()
        signature_payload = signature.read_bytes()
        return subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-",
                str(len(archive_payload)),
                hashlib.sha256(archive_payload).hexdigest(),
                str(len(signature_payload)),
                hashlib.sha256(signature_payload).hexdigest(),
                _HOST_MINISIGN_KEY_ID,
                _HOST_MINISIGN_PUBLIC_KEY,
                _HOST_MINISIGN_TRUSTED_COMMENT,
                str(archive),
                str(signature),
            ],
            input=identity_precheck,
            text=True,
            capture_output=True,
            check=False,
        )

    assert run_precheck(raw_key_id).returncode == 0
    reversed_result = run_precheck(raw_key_id[::-1])
    assert reversed_result.returncode != 0
    assert "signer key ID mismatch" in reversed_result.stderr


def test_traycer_host_main_executable_is_bound_to_the_authenticated_digest(
    tmp_path: Path,
) -> None:
    """Host identity must include both its version and independently known digest."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    host_runtime = expect_instance(
        expect_binding(final.scope, "hostRuntime").value,
        FunctionCall,
    )
    arguments = expect_instance(host_runtime.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))
    raw_scripts = [
        node_text(node, build_shell.sanitized)
        for node in iter_nodes(build_shell.tree.root_node, "heredoc_body")
    ]
    raw_identity_check = next(
        script for script in raw_scripts if "import json" in script
    )
    indented_line = next(line for line in raw_identity_check.splitlines()[1:] if line)
    indent = len(indented_line) - len(indented_line.lstrip())
    identity_check = textwrap.dedent(f"{' ' * indent}{raw_identity_check}")

    version_file = tmp_path / "version.json"
    version_file.write_text(json.dumps({"version": _VERSION}), encoding="utf-8")
    executable = tmp_path / "traycer-host"
    executable.write_bytes(b"authenticated host executable")
    expected_digest = hashlib.sha256(executable.read_bytes()).hexdigest()

    def run_check(digest: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-",
                str(version_file),
                _VERSION,
                str(executable),
                digest,
            ],
            input=identity_check,
            text=True,
            capture_output=True,
            check=False,
        )

    assert run_check(expected_digest).returncode == 0
    mismatched = run_check("0" * 64)
    assert mismatched.returncode != 0
    assert "executable digest mismatch" in mismatched.stderr


def test_traycer_host_archive_accepts_only_the_authenticated_regular_inventory(
    tmp_path: Path,
) -> None:
    """Archive acceptance must bind exact counts and reject links and specials."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    host_runtime = expect_instance(
        expect_binding(final.scope, "hostRuntime").value,
        FunctionCall,
    )
    arguments = expect_instance(host_runtime.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))
    raw_scripts = [
        node_text(node, build_shell.sanitized)
        for node in iter_nodes(build_shell.tree.root_node, "heredoc_body")
    ]
    raw_inventory = next(script for script in raw_scripts if "import tarfile" in script)
    indented_line = next(line for line in raw_inventory.splitlines()[1:] if line)
    indent = len(indented_line) - len(indented_line.lstrip())
    inventory_precheck = textwrap.dedent(f"{' ' * indent}{raw_inventory}")

    def write_archive(path: Path, rejected_type: bytes | None = None) -> int:
        with tarfile.open(path, "w:gz") as archive:
            directory = tarfile.TarInfo("traycer-host")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            archive.addfile(directory)

            payload = b"authenticated payload"
            regular = tarfile.TarInfo("traycer-host/payload")
            regular.size = len(payload)
            regular.mode = 0o644
            archive.addfile(regular, io.BytesIO(payload))

            if rejected_type is not None:
                rejected = tarfile.TarInfo("traycer-host/rejected")
                rejected.type = rejected_type
                rejected.linkname = "payload"
                archive.addfile(rejected)
        return 2 + (rejected_type is not None)

    def run_precheck(path: Path, member_count: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [sys.executable, "-", str(path), str(member_count), "1"],
            input=inventory_precheck,
            text=True,
            capture_output=True,
            check=False,
        )

    valid_archive = tmp_path / "valid.tar.gz"
    assert run_precheck(valid_archive, write_archive(valid_archive)).returncode == 0

    for rejected_type in (tarfile.SYMTYPE, tarfile.FIFOTYPE):
        unsafe_archive = tmp_path / f"unsafe-{rejected_type.hex()}.tar.gz"
        result = run_precheck(
            unsafe_archive,
            write_archive(unsafe_archive, rejected_type),
        )
        assert result.returncode != 0
        assert "links or special files" in result.stderr


def test_traycer_host_copy_preserves_every_file_byte_and_rejects_nonfiles(
    tmp_path: Path,
) -> None:
    """The copied store tree must exactly match the authenticated extracted tree."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    host_runtime = expect_instance(
        expect_binding(final.scope, "hostRuntime").value,
        FunctionCall,
    )
    arguments = expect_instance(host_runtime.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))
    raw_scripts = [
        node_text(node, build_shell.sanitized)
        for node in iter_nodes(build_shell.tree.root_node, "heredoc_body")
    ]
    raw_identity_check = next(
        script for script in raw_scripts if "import stat" in script
    )
    indented_line = next(line for line in raw_identity_check.splitlines()[1:] if line)
    indent = len(indented_line) - len(indented_line.lstrip())
    identity_check = textwrap.dedent(f"{' ' * indent}{raw_identity_check}")

    source = tmp_path / "source"
    source.mkdir()
    payload = source / "payload"
    payload.write_bytes(b"authenticated bytes")
    payload.chmod(0o755)
    output = tmp_path / "output"
    shutil.copytree(source, output)

    def run_check() -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [sys.executable, "-", str(source), str(output), "1"],
            input=identity_check,
            text=True,
            capture_output=True,
            check=False,
        )

    assert run_check().returncode == 0

    (output / "payload").write_bytes(b"mutated bytes")
    mismatched = run_check()
    assert mismatched.returncode != 0
    assert "output tree differs" in mismatched.stderr

    shutil.rmtree(output)
    shutil.copytree(source, output)
    (output / "link").symlink_to("payload")
    linked = run_check()
    assert linked.returncode != 0
    assert "links or special files" in linked.stderr

    (output / "link").unlink()
    os.mkfifo(output / "fifo")
    special = run_check()
    assert special.returncode != 0
    assert "links or special files" in special.stderr


def test_traycer_nix_files_are_structurally_parseable() -> None:
    """Package-local Nix must parse without invoking Nix or realizing a closure."""
    for path in sorted(_PACKAGE_DIR.glob("*.nix")):
        assert parse_nix_expr(path.read_text(encoding="utf-8")) is not None, path


def test_traycer_declares_exact_platform_artifacts_and_build_validation() -> None:
    """The updater lane must remain Darwin-only and validate its final package."""
    updater = _load_updater_module().TraycerUpdater()

    assert updater.supported_platforms == ("aarch64-darwin",)
    assert updater.generated_artifact_files == ("bun.lock", "bun.nix")
    assert updater.derivation_validations == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            mode="build",
        ),
    )


_PINNED_INSTALLATION_SOURCE = """const HOST_INSTALL_DIRNAME = "install";
const HOST_STAGED_DIRNAME = "staged";

export function hostInstallDir(environment: Environment): string {
  return join(hostInstallHomeDir(environment), HOST_INSTALL_DIRNAME);
}

export function hostInstallRecordPath(environment: Environment): string {
  return join(hostInstallDir(environment), HOST_INSTALL_RECORD_FILENAME);
}
"""

_PINNED_RUNNER_SOURCE = """function withRunner(
  cmd: CommanderCommand,
  build: (
    opts: Record<string, unknown>,
    args: ReadonlyArray<string | undefined>,
  ) => CommandFn,
): CommanderCommand {
  return addRunnerFlags(cmd).action(async (...actionArgs: unknown[]) => {
    const command = actionArgs[actionArgs.length - 1] as CommanderCommand;
    const positionals = extractActionPositionals(actionArgs);
    const optsBag = command.optsWithGlobals() as Record<string, unknown>;
    const fn = build(optsBag, positionals);
    await runCommand(fn, extractRunnerFlags(optsBag));
  });
}

async function main(): Promise<void> {
  const supervisedStart = argvSelectsSupervisedHostStart(process.argv);
  const runEntry = async (): Promise<void> => {
    try {
      const replacedRunningBinary =
        await refreshCliSlotBeforeCommand(supervisedStart);
      if (replacedRunningBinary) {
        return;
      }
    } catch {
      return;
    }
  };
  await runEntry();
}
"""

_PINNED_ENSURE_SOURCE = """import { config } from "../config";
import { currentInstallPlatform, type InstallSourceArg } from "../installer";

export async function ensureHost(
  opts: EnsureHostOptions,
): Promise<HostEnsureResult> {
  if (opts.noServiceRegister && currentInstallPlatform() === "win32") {
    throw cliError({
      code: CLI_ERROR_CODES.INVALID_ARGUMENT,
      message: "host ensure: --no-service-register is not supported on Windows",
      details: { environment: opts.runtime.environment },
      exitCode: 1,
    });
  }
  opts.runtime.logger.info("Host ensure started", {
    environment: opts.runtime.environment,
    hasExplicitVersion: opts.versionRequest !== null,
    hasFromPath: opts.fromPath !== null,
    enableLinger: opts.enableLinger,
    allowSelfInvocation: opts.allowSelfInvocation,
    noServiceRegister: opts.noServiceRegister,
    force: opts.force,
  });
  return {} as HostEnsureResult;
}
"""

_PINNED_UPDATER_SOURCE = """const CURRENT_VERSION = app.getVersion();

export async function installAutoUpdater(
  isDev: boolean,
  deps: AppUpdaterDeps,
): Promise<void> {
  if (installed) {
    return;
  }
  installed = true;
  try {
    await configureAutoUpdater(deps);
  } catch (err) {
    markUpdaterInitialized("failed");
  }
}

async function canCheckForUpdates(isDev: boolean): Promise<boolean> {
  // `isDev` is the dev deploy slot (the development build) - it never has a
  // real update feed, so skip the updater entirely.
  if (isDev) return false;
  return true;
}

export async function checkForUpdatesNow(
  isDev: boolean,
  intent: DesktopAppUpdateCheckIntent,
): Promise<DesktopAppUpdateSnapshot> {
  await updaterInitialized;
  if (!(await canCheckForUpdates(isDev))) {
    log.debug("[updater] check skipped outside a shipped build");
    if (intent === "manual") {
      emitSnapshot({
        status: "unavailable",
        errorMessage: "Updates are not available for this build.",
        lastCheckedAt: new Date().toISOString(),
        lastCheckIntent: intent,
      });
    }
    return currentSnapshot;
  }
  await autoUpdater.checkForUpdates();
  return currentSnapshot;
}

async function performChannelChange(
  allowPrerelease: boolean,
): Promise<DesktopAppUpdateChannelChange> {
  await updaterInitialized;
  // Idempotent set (channel unchanged): change nothing.
  if (prereleaseUpdatesEnabled() === allowPrerelease) {
    return {
      outcome: "unchanged",
      snapshot: emitSnapshot({ allowPrerelease }),
    };
  }
  await persistPrereleaseUpdatesEnabled(allowPrerelease);
  autoUpdater.allowPrerelease = allowPrerelease;
  if (!allowPrerelease) {
    configureStableGitHubUpdateFeed();
  }
  return {
    outcome: "changed",
    snapshot: emitSnapshot({ allowPrerelease }),
  };
}
"""

_PINNED_CLI_RECONCILE_SOURCE = """export type CliReconcileOutcome =
  | {
      readonly kind: "skipped-dev-desktop";
    }
  | {
      readonly kind: "none";
    };

export async function runLaunchTimeCliReconciliation(args: {
  readonly isDevDesktop: boolean;
  readonly deps: ReconcileCliDeps;
}): Promise<CliReconcileOutcome> {
  if (args.isDevDesktop) {
    args.deps.logger.info(
      "[cli-reconcile] dev desktop detected - skipping launch-time reconciliation against production ~/.traycer/cli (dev CLI wrapper is staged by make dev-desktop)",
    );
    return { kind: "skipped-dev-desktop" };
  }
  return reconcileCli(args.deps);
}
"""

_PINNED_HOST_CONTROLLER_SOURCE = """class HostController {
  private stageLatestInFlight: Promise<void> | null = null;

  stageLatest(): Promise<void> {
    if (this.stageLatestInFlight !== null) {
      return this.stageLatestInFlight;
    }
    const job = this.runStageLatest().finally(() => {
      if (this.stageLatestInFlight === job) {
        this.stageLatestInFlight = null;
      }
    });
    this.stageLatestInFlight = job;
    return job;
  }
}
"""

_PINNED_AUTO_BOOTSTRAP_SOURCE = """import { installSourceLogFields } from "./install-source-log-fields";

export type AutoBootstrapReason =
  | "explicit-no-bootstrap"
  | "noninteractive-cannot-prompt"
  | "already-installed"
  | "installed"
  | "service-registered"
  | "install-failed"
  | "service-registration-failed"
  | "service-registration-warning";

export async function maybeAutoBootstrap(
  opts: AutoBootstrapOptions,
): Promise<AutoBootstrapDecision> {
  const decision = await evaluateAutoBootstrap(opts);
  if (
    decision.status !== "service-registered" &&
    decision.status !== "installed"
  ) {
    return decision;
  }
  const result = await provisionHost({ registerService: true });
  return projectProvisionResult(result);
}
"""

_PINNED_HOST_STATUS_SOURCE = """function renderBootstrapLine(
  decision: AutoBootstrapDecision,
  c: Colorizer,
): string | null {
  switch (decision.status) {
    case "ready":
      return null;
    case "installed":
      return c.green("bootstrap: installed");
    case "service-registered":
      return c.green("bootstrap: registered");
    case "skipped":
      if (decision.reason === "explicit-no-bootstrap") {
        return c.dim("bootstrap: skipped (--no-bootstrap)");
      }
      return c.dim(
        "bootstrap: skipped (non-interactive - CI=1 or TRAYCER_NONINTERACTIVE=1)",
      );
    case "failed":
      return c.red("bootstrap: failed");
  }
}
"""

_PINNED_HOST_RESTART_SOURCE = """// `traycer host restart` - kicks the OS service so the supervisor
// re-spawns the host.

export async function restartWithPendingCliUpgradeFinalize(
  args: RestartFinalizeArgs,
): Promise<RestartFinalizeResult> {
  // 1. Apply any marker from a prior helper attempt. This may clear
  //    pendingUpgrade if the helper succeeded on the last cycle.
  const markerReconcile = await reconcilePostFinalizeMarker({
    environment: args.environment,
  });
  const stop = await args.controller.stopForRestart(args.label, {
    force: args.force,
  });
  const finalize = await finalizePendingCliUpgrade({
    environment: args.environment,
  });
  await args.controller.relaunchAfterRestart(args.label, stop);
  return {
    finalize,
    helper: null,
    markerReconcile,
    helperOwnsServiceStart: false,
  };
}
"""

_PINNED_FINALIZE_HELPER_SOURCE = """export type ReconcileOutcome =
  | { readonly status: "no-marker" }
  | { readonly status: "marker-invalid"; readonly errorMessage: string };

export async function reconcilePostFinalizeMarker(opts: {
  readonly environment: Environment;
}): Promise<ReconcileOutcome> {
  const markerPath = cliPostFinalizeMarkerPath(opts.environment);
  const raw = await readFile(markerPath, "utf8");
  await safeUnlink(markerPath);
  return JSON.parse(raw) as ReconcileOutcome;
}
"""

_PINNED_SEA_TOOLCHAIN_SOURCE = """function bundleCjs({ entry, outfile, cwd }) {
  const buildOptions = {
    entryPoints: [entry],
    bundle: true,
    platform: "node",
    target: "node24",
    format: "cjs",
    outfile,
    absWorkingDir: cwd || REPO_ROOT,
    // Sentry's proxy module emits this warning for entry points with no
    // default export (e.g. main-sea.ts). The proxy is never imported for its
    // default, so the warning is noise.
    logOverride: { "import-is-undefined": "silent" },
  };
  return esbuild.build(buildOptions);
}

function macosRemoveSignature(target) {
  if (process.platform !== "darwin") return;
  const res = spawnSync("codesign", ["--remove-signature", target], {
    stdio: "inherit",
  });
  if (res.status !== 0) {
    console.warn(`remove failed: ${res.status}`);
  }
}

function macosSignAdHoc(target) {
  if (process.platform !== "darwin") return;
  const identity = process.env.TRAYCER_MACOS_SIGN_IDENTITY || "-";
  const args = ["--sign", identity];
  args.push(target);
  const res = spawnSync("codesign", args, {
    stdio: "inherit",
  });
  if (res.status !== 0) {
    throw new Error(`sign failed: ${res.status}`);
  }
}

function buildSingleSeaExecutable({
  workDir,
  bundleFile,
  configFile,
  blobFile,
  outputBinary,
  assets,
}) {
  const hostNode = resolveSeaHostNode();
  writeSeaConfig({
    mainBundle: bundleFile,
    outputBlob: blobFile,
    assets: assets || null,
    configPath: configFile,
  });
  generateSeaBlob({ hostNode, configPath: configFile, cwd: workDir });
  copyHostNodeBinary({ hostNode, destination: outputBinary });
  injectSeaBlob({ binary: outputBinary, blob: blobFile });
}
"""

_PINNED_DESKTOP_PACKAGE_JSON = """{
  "name": "@traycer-clients/desktop",
  "build": {
    "asar": true,
    "directories": {
      "buildResources": "resources/bundle",
      "output": "release"
    },
    "afterPack": "scripts/prepack/inject-host-launch-agent.cjs",
    "files": [
      "dist/**/*",
      "package.json",
      "!**/*.map",
      "node_modules/font-list/**/*"
    ],
    "asarUnpack": [
      "node_modules/font-list/**/*"
    ],
    "extraResources": [
      {
        "from": "resources/app",
        "to": "app",
        "filter": [
          "**/*.png"
        ]
      },
      {
        "from": "dist/renderer",
        "to": "renderer",
        "filter": [
          "**/*",
          "!**/*.map"
        ]
      },
      {
        "from": "resources/host",
        "to": "host",
        "filter": [
          "README.md",
          ".gitkeep"
        ]
      },
      {
        "from": "resources/cli",
        "to": "cli",
        "filter": [
          "**/*",
          "!README.md"
        ]
      },
      {
        "from": "resources/tray",
        "to": "tray",
        "filter": [
          "**/*.png"
        ]
      }
    ],
    "mac": {
      "minimumSystemVersion": "12.0",
      "entitlements": "resources/bundle/entitlements.mac.plist"
    }
  }
}
"""

_PINNED_BUILD_MAIN_BUNDLE_SOURCE = """const sharedConfig = {
  bundle: true,
  platform: "node",
  target: "node20",
  format: "cjs",
  tsconfig: tsconfigPath,
  external: ["electron", "*.node", "font-list"],
  // Source maps are useful when the packaged app surfaces a stack trace
  // through electron-log; "external" keeps them out of app.asar.
  sourcemap: "external",
  legalComments: "none",
  // Sentry's proxy module emits this warning for entry points with no
  // default export. The proxy is never imported for its default.
  logOverride: { "import-is-undefined": "silent" },
  define: envDefines,
};
"""


def _write_traycer_patch_fixture(root: Path) -> None:
    fixtures = {
        "protocol/src/config/installation.ts": _PINNED_INSTALLATION_SOURCE,
        "clients/traycer-cli/src/index.ts": _PINNED_RUNNER_SOURCE,
        "clients/traycer-cli/src/host/ensure.ts": _PINNED_ENSURE_SOURCE,
        "clients/desktop/src/electron-main/app/updater.ts": _PINNED_UPDATER_SOURCE,
        "clients/desktop/src/electron-main/cli/cli-reconcile.ts": (
            _PINNED_CLI_RECONCILE_SOURCE
        ),
        "clients/desktop/src/electron-main/host/host-controller.ts": (
            _PINNED_HOST_CONTROLLER_SOURCE
        ),
        "clients/traycer-cli/src/host/auto-bootstrap.ts": (
            _PINNED_AUTO_BOOTSTRAP_SOURCE
        ),
        "clients/traycer-cli/src/commands/host-status.ts": (_PINNED_HOST_STATUS_SOURCE),
        "clients/traycer-cli/src/commands/host-restart.ts": (
            _PINNED_HOST_RESTART_SOURCE
        ),
        "clients/traycer-cli/src/upgrade/finalize-helper.ts": (
            _PINNED_FINALIZE_HELPER_SOURCE
        ),
        "scripts/native-packaging/sea-toolchain.cjs": _PINNED_SEA_TOOLCHAIN_SOURCE,
        "clients/desktop/scripts/build-main-bundle.cjs": (
            _PINNED_BUILD_MAIN_BUNDLE_SOURCE
        ),
        "clients/desktop/package.json": _PINNED_DESKTOP_PACKAGE_JSON,
    }
    for relative, source in fixtures.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _typescript_facts(sources: dict[str, str]) -> dict[str, object]:
    bun = shutil.which("bun")
    assert bun is not None
    script = r"""
import ts from "typescript";

const sources = JSON.parse(await Bun.stdin.text());
const output = {};
for (const [name, text] of Object.entries(sources)) {
  const scriptKind = name.endsWith(".cjs") ? ts.ScriptKind.JS : ts.ScriptKind.TS;
  const source = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, scriptKind);
  const facts = { callArguments: [], calls: [], functions: {}, objectProperties: {}, strings: [], variables: {} };
  const visit = (node) => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      facts.variables[node.name.text] = node.initializer.getText(source);
      if (ts.isObjectLiteralExpression(node.initializer)) {
        facts.objectProperties[node.name.text] = node.initializer.properties.map(
          (property) => [
            property.name?.getText(source) ?? property.getText(source),
            ts.isPropertyAssignment(property)
              ? property.initializer.getText(source)
              : null,
          ],
        );
      }
    }
    if (ts.isFunctionDeclaration(node) && node.name && node.body) {
      facts.functions[node.name.text] = node.body.statements.map((statement) => statement.getText(source));
    }
    if (ts.isMethodDeclaration(node) && node.name && node.body) {
      facts.functions[node.name.getText(source)] = node.body.statements.map((statement) => statement.getText(source));
    }
    if (ts.isStringLiteral(node)) facts.strings.push(node.text);
    if (ts.isCallExpression(node)) {
      facts.calls.push(node.expression.getText(source));
      facts.callArguments.push({
        expression: node.expression.getText(source),
        arguments: node.arguments.map((argument) => argument.getText(source)),
      });
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  output[name] = {
    diagnostics: source.parseDiagnostics.map((diagnostic) =>
      ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n"),
    ),
    ...facts,
  };
}
console.log(JSON.stringify(output));
"""
    result = subprocess.run(  # noqa: S603
        [bun, "-e", script],
        input=json.dumps(sources),
        text=True,
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def _patched_policy_facts(tmp_path: Path) -> tuple[dict[str, object], str]:
    module = _load_patch_module()
    source_root = tmp_path / "traycer"
    host_runtime = "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-traycer-host-1.2.0"
    _write_traycer_patch_fixture(source_root)
    assert module.apply_patches(source_root, host_runtime) == 32
    assert module.apply_patches(source_root, host_runtime) == 0
    sources = {
        str(path.relative_to(source_root)): path.read_text(encoding="utf-8")
        for path in source_root.rglob("*")
        if path.suffix in {".cjs", ".ts"}
    }
    facts = _typescript_facts(sources)
    assert all(item["diagnostics"] == [] for item in facts.values())
    facts["clients/desktop/package.json"] = json.loads(
        (source_root / "clients/desktop/package.json").read_text(encoding="utf-8")
    )
    return facts, host_runtime


def _assert_nix_managed_status_rendering(facts: dict[str, object]) -> None:
    status = facts["clients/traycer-cli/src/commands/host-status.ts"]
    render_status = "\n".join(status["functions"]["renderBootstrapLine"])
    nix_reason = 'decision.reason === "nix-managed"'
    assert nix_reason in render_status
    assert "bootstrap: skipped (managed by Nix)" in status["strings"]
    assert render_status.index(nix_reason) < render_status.index("non-interactive")


def _assert_nix_managed_updater_policy(facts: dict[str, object]) -> None:
    updater = facts["clients/desktop/src/electron-main/app/updater.ts"]
    assert updater["variables"]["NIX_MANAGED_DESKTOP_UPDATES"] == "true"
    assert "Updates are managed by Nix." in updater["strings"]
    assert updater["functions"]["canCheckForUpdates"][0] == (
        "if (NIX_MANAGED_DESKTOP_UPDATES) return false;"
    )
    check_statements = updater["functions"]["checkForUpdatesNow"]
    managed_check = next(
        statement
        for statement in check_statements
        if "NIX_MANAGED_DESKTOP_UPDATES" in statement
    )
    assert "NIX_MANAGED_UPDATE_MESSAGE" in managed_check
    assert 'status: "unavailable"' in managed_check
    assert check_statements.index(managed_check) < next(
        index
        for index, statement in enumerate(check_statements)
        if "canCheckForUpdates" in statement
    )

    channel_statements = updater["functions"]["performChannelChange"]
    managed_channel = next(
        statement
        for statement in channel_statements
        if "NIX_MANAGED_DESKTOP_UPDATES" in statement
    )
    assert 'outcome: "unchanged"' in managed_channel
    assert "NIX_MANAGED_UPDATE_MESSAGE" in managed_channel
    assert "persistPrereleaseUpdatesEnabled" not in managed_channel
    assert "configureStableGitHubUpdateFeed" not in managed_channel
    assert channel_statements.index(managed_channel) < next(
        index
        for index, statement in enumerate(channel_statements)
        if "persistPrereleaseUpdatesEnabled" in statement
    )

    reconciliation = facts["clients/desktop/src/electron-main/cli/cli-reconcile.ts"]
    assert reconciliation["variables"]["NIX_MANAGED_CLI_RECONCILIATION"] == "true"
    reconciliation_statements = reconciliation["functions"][
        "runLaunchTimeCliReconciliation"
    ]
    managed_reconciliation = reconciliation_statements[0]
    assert "NIX_MANAGED_CLI_RECONCILIATION" in managed_reconciliation
    assert 'kind: "skipped-nix-managed"' in managed_reconciliation
    assert "reconcileCli" not in managed_reconciliation
    assert reconciliation_statements[-1] == "return reconcileCli(args.deps);"


def _assert_nix_managed_lifecycle_policy(facts: dict[str, object]) -> None:
    controller = facts["clients/desktop/src/electron-main/host/host-controller.ts"]
    assert controller["functions"]["stageLatest"] == ["return Promise.resolve();"]

    auto_bootstrap = facts["clients/traycer-cli/src/host/auto-bootstrap.ts"]
    assert auto_bootstrap["variables"]["NIX_MANAGED_HOST_LIFECYCLE"] == "true"
    auto_statements = auto_bootstrap["functions"]["maybeAutoBootstrap"]
    assert "NIX_MANAGED_HOST_LIFECYCLE" in auto_statements[1]
    assert 'reason: "nix-managed"' in auto_statements[1]
    assert "provisionHost" not in auto_statements[1]

    _assert_nix_managed_status_rendering(facts)

    restart = facts["clients/traycer-cli/src/commands/host-restart.ts"]
    assert restart["variables"]["NIX_MANAGED_CLI_UPDATES"] == "true"
    restart_guard = restart["functions"]["restartWithPendingCliUpgradeFinalize"][0]
    assert "NIX_MANAGED_CLI_UPDATES" in restart_guard
    assert "stopForRestart" in restart_guard
    assert "relaunchAfterRestart" in restart_guard
    assert 'status: "no-pending"' in restart_guard
    assert "finalizePendingCliUpgrade" not in restart_guard
    assert "reconcilePostFinalizeMarker" not in restart_guard

    finalizer = facts["clients/traycer-cli/src/upgrade/finalize-helper.ts"]
    assert finalizer["variables"]["NIX_MANAGED_CLI_UPDATES"] == "true"
    reconcile_guard = finalizer["functions"]["reconcilePostFinalizeMarker"][0]
    assert "NIX_MANAGED_CLI_UPDATES" in reconcile_guard
    assert 'status: "no-marker"' in reconcile_guard
    assert "readFile" not in reconcile_guard
    assert "safeUnlink" not in reconcile_guard

    sea_toolchain = facts["scripts/native-packaging/sea-toolchain.cjs"]
    codesign_calls = [
        call
        for call in sea_toolchain["callArguments"]
        if call["expression"] == "spawnSync"
        and call["arguments"][0] == '"/usr/bin/codesign"'
    ]
    assert [call["arguments"][1] for call in codesign_calls] == [
        '["--remove-signature", target]',
        "args",
    ]
    sea_config_calls = [
        call
        for call in sea_toolchain["callArguments"]
        if call["expression"] == "writeSeaConfig"
    ]
    assert len(sea_config_calls) == 1
    assert sea_config_calls[0]["arguments"] == [
        "{\n    mainBundle: path.relative(seaConfigDirectory, bundleFile),\n"
        "    outputBlob: path.relative(seaConfigDirectory, blobFile),\n"
        "    assets: assets || null,\n    configPath: configFile,\n  }"
    ]
    generate_calls = [
        call
        for call in sea_toolchain["callArguments"]
        if call["expression"] == "generateSeaBlob"
    ]
    assert len(generate_calls) == 1
    assert generate_calls[0]["arguments"] == [
        "{\n    hostNode,\n    configPath: configFile,\n"
        "    cwd: seaConfigDirectory,\n  }"
    ]
    assert sea_toolchain["objectProperties"]["buildOptions"] == [
        ["entryPoints", "[entry]"],
        ["bundle", "true"],
        ["platform", '"node"'],
        ["target", '"node24"'],
        ["format", '"cjs"'],
        ["outfile", None],
        ["absWorkingDir", "cwd || REPO_ROOT"],
        ["minifyWhitespace", "true"],
        ["minifyIdentifiers", "true"],
        ["keepNames", "true"],
        ["logOverride", '{ "import-is-undefined": "silent" }'],
    ]


def test_traycer_policy_patch_makes_store_host_and_updates_fail_closed(
    tmp_path: Path,
) -> None:
    """The source patch must preserve runtime control but prohibit byte mutation."""
    facts, host_runtime = _patched_policy_facts(tmp_path)

    installation = facts["protocol/src/config/installation.ts"]
    assert installation["variables"]["NIX_MANAGED_HOST_INSTALL_DIR"] == json.dumps(
        host_runtime
    )
    assert installation["functions"]["hostInstallDir"] == [
        'return environment === "production"\n    ? NIX_MANAGED_HOST_INSTALL_DIR\n    : join(hostInstallHomeDir(environment), HOST_INSTALL_DIRNAME);'
    ]
    assert installation["functions"]["hostInstallRecordPath"] == [
        "return join(hostInstallDir(environment), HOST_INSTALL_RECORD_FILENAME);"
    ]

    runner = facts["clients/traycer-cli/src/index.ts"]
    managed_paths = {
        "traycer host install",
        "traycer host apply",
        "traycer host purge-stage",
        "traycer host stamp-runtime",
        "traycer host update",
        "traycer host download",
        "traycer host uninstall",
        "traycer host service install",
        "traycer host service uninstall",
        "traycer cli upgrade",
        "traycer cli mark-source",
        "traycer cli finalize-upgrade",
        "traycer cli re-anchor",
    }
    assert managed_paths <= set(runner["strings"])
    assert runner["variables"]["NIX_MANAGED_CLI_UPDATES"] == "true"
    assert "nixManagedCommand" in runner["calls"]
    assert "commandPath" in runner["calls"]
    assert "build" in runner["calls"]
    assert runner["variables"]["replacedRunningBinary"] == (
        "NIX_MANAGED_CLI_UPDATES\n"
        "        ? false\n"
        "        : await refreshCliSlotBeforeCommand(supervisedStart)"
    )

    ensure = facts["clients/traycer-cli/src/host/ensure.ts"]
    ensure_body = "\n".join(ensure["functions"]["ensureHost"])
    assert "readHostInstallRecord" in ensure["calls"]
    assert f"{host_runtime}/{_HOST_RUNTIME_RELATIVE_EXECUTABLE}" in ensure["strings"]
    assert "opts.versionRequest !== null" in ensure_body
    assert "opts.fromPath !== null" in ensure_body
    assert "!opts.noServiceRegister" in ensure_body
    assert "installed.executablePath !== expectedExecutable" in ensure_body

    _assert_nix_managed_updater_policy(facts)
    _assert_nix_managed_lifecycle_policy(facts)
    desktop_manifest = facts["clients/desktop/package.json"]
    assert isinstance(desktop_manifest, dict)
    assert desktop_manifest["build"]["asar"] == {"smartUnpack": False}
    assert desktop_manifest["build"]["afterPack"] is None
    assert desktop_manifest["build"]["mac"]["minimumSystemVersion"] == "14.0"
    assert desktop_manifest["build"]["files"] == [
        "dist/**/*",
        "package.json",
        "node_modules/font-list/**/*",
        "!**/*.map",
        "!node_modules/font-list/LICENSE",
        "!node_modules/font-list/index.d.cts",
        "!node_modules/font-list/index.d.mts",
        "!node_modules/font-list/index.mjs",
        "!node_modules/font-list/libs/darwin/fontlist.m",
        "!node_modules/font-list/libs/linux/**/*",
        "!node_modules/font-list/libs/win32/**/*",
    ]
    assert desktop_manifest["build"]["asarUnpack"] == [
        "node_modules/font-list/libs/darwin/fontlist"
    ]
    assert desktop_manifest["build"]["extraResources"] == [
        {
            "from": "resources/app",
            "to": "app",
            "filter": ["**/*.png"],
        },
        {
            "from": "dist/renderer",
            "to": "renderer",
            "filter": ["**/*", "!**/*.map"],
        },
        {
            "from": "resources/cli",
            "to": "cli",
            "filter": ["**/*", "!README.md"],
        },
        {
            "from": "resources/tray",
            "to": "tray",
            "filter": [
                "tray.png",
                "tray@2x.png",
                "trayTemplate.png",
                "trayTemplate@2x.png",
            ],
        },
    ]

    main_bundle = facts["clients/desktop/scripts/build-main-bundle.cjs"]
    assert main_bundle["objectProperties"]["sharedConfig"] == [
        ["bundle", "true"],
        ["platform", '"node"'],
        ["target", '"node20"'],
        ["format", '"cjs"'],
        ["tsconfig", "tsconfigPath"],
        ["external", '["electron", "*.node", "font-list"]'],
        ["sourcemap", '"external"'],
        ["legalComments", '"none"'],
        ["minifyWhitespace", "true"],
        ["minifyIdentifiers", "true"],
        ["keepNames", "true"],
        ["logOverride", '{ "import-is-undefined": "silent" }'],
        ["define", "envDefines"],
    ]


@pytest.mark.parametrize(
    "host_runtime",
    [
        "relative/host",
        "/tmp/traycer-host",
        "/nix/store/../unsafe",
        '/nix/store/abc-host"; throw new Error("oops")',
    ],
)
def test_traycer_policy_patch_rejects_unsafe_host_store_paths(
    tmp_path: Path,
    host_runtime: str,
) -> None:
    """A runtime substitution must be one literal immutable Nix store path."""
    _write_traycer_patch_fixture(tmp_path)

    with pytest.raises(ValueError, match="Nix store path"):
        _load_patch_module().apply_patches(tmp_path, host_runtime)


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_traycer_policy_patch_rejects_source_anchor_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Upstream source drift must stop the build instead of weakening policy."""
    module = _load_patch_module()
    _write_traycer_patch_fixture(tmp_path)
    path = tmp_path / "protocol/src/config/installation.ts"
    source = path.read_text(encoding="utf-8")
    if mutation == "missing":
        source = source.replace('const HOST_INSTALL_DIRNAME = "install";', "")
    else:
        source += """
const HOST_INSTALL_DIRNAME = "install";
const HOST_STAGED_DIRNAME = "staged";
"""
    path.write_text(source, encoding="utf-8")

    with pytest.raises(RuntimeError, match="source anchor"):
        module.apply_patches(
            tmp_path,
            "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-traycer-host-1.2.0",
        )


@pytest.mark.parametrize("patch_index", range(32))
def test_traycer_policy_patch_checks_every_anchor_before_writing(
    tmp_path: Path,
    patch_index: int,
) -> None:
    """Each policy anchor is mandatory and any drift leaves every file untouched."""
    module = _load_patch_module()
    host_runtime = "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-traycer-host-1.2.0"
    _write_traycer_patch_fixture(tmp_path)
    patch = module._patches(host_runtime)[patch_index]
    path = tmp_path / patch.path
    source = path.read_text(encoding="utf-8")
    drifted = source.replace(patch.old, f"{patch.old[:-1]} /* drifted */", 1)
    assert drifted != source
    path.write_text(drifted, encoding="utf-8")
    before = {
        candidate: candidate.read_bytes()
        for candidate in tmp_path.rglob("*")
        if candidate.is_file()
    }

    with pytest.raises(RuntimeError, match="source anchor"):
        module.apply_patches(tmp_path, host_runtime)

    assert {candidate: candidate.read_bytes() for candidate in before} == before


def test_traycer_policy_patch_rejects_missing_source_root(tmp_path: Path) -> None:
    """A misspelled source root must fail before any patching is attempted."""
    with pytest.raises(ValueError, match="source root is not a directory"):
        _load_patch_module().apply_patches(
            tmp_path / "missing",
            "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-traycer-host-1.2.0",
        )


def test_traycer_policy_patch_cli_requires_exact_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build helper must reject ambiguous invocation shapes."""
    module = _load_patch_module()
    _write_traycer_patch_fixture(tmp_path)

    assert module.main(["patch_nix_managed.py"]) == 2
    assert module.main(["patch_nix_managed.py", str(tmp_path)]) == 2
    assert (
        module.main([
            "patch_nix_managed.py",
            "--unknown",
            str(tmp_path),
            "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-traycer-host-1.2.0",
        ])
        == 2
    )
    monkeypatch.setattr(module.sys, "argv", ["patch_nix_managed.py"])
    assert module.main() == 2


def test_traycer_policy_patch_cli_checks_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Check mode must validate the exact pinned anchors without changing source."""
    module = _load_patch_module()
    _write_traycer_patch_fixture(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    host_runtime = "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-traycer-host-1.2.0"

    assert (
        module.main([
            "patch_nix_managed.py",
            "--check",
            str(tmp_path),
            host_runtime,
        ])
        == 0
    )
    assert {path: path.read_bytes() for path in before} == before
    assert capsys.readouterr().out == "validated 32 Traycer Nix policy patches\n"


def test_traycer_policy_patch_cli_applies_and_reports_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command-line adapter must expose both application and failure outcomes."""
    module = _load_patch_module()
    _write_traycer_patch_fixture(tmp_path)
    host_runtime = "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-traycer-host-1.2.0"

    assert module.main(["patch_nix_managed.py", str(tmp_path), host_runtime]) == 0
    assert capsys.readouterr().out == "applied 32 Traycer Nix policy patches\n"
    assert module.main(["patch_nix_managed.py", str(tmp_path), "/tmp/host"]) == 1
    assert "must be one literal Nix store path" in capsys.readouterr().err
