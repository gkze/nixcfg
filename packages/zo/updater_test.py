"""Behavioral, package, and update-ownership tests for Zo desktop."""

import hashlib
import json
import plistlib
import shutil
import struct
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.asar_integrity import (
    asar_header_hash,
    check_info_plist_hash,
    read_packed_file,
)
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

_PACKAGE_DIR = REPO_ROOT / "packages/zo"
_VERSION = "1.5.13"
_ARTIFACT_NAME = f"Zo-{_VERSION}-universal-mac.zip"
_ARTIFACT_URL = (
    f"https://github.com/zocomputer/Zo/releases/download/v{_VERSION}/{_ARTIFACT_NAME}"
)
_HASH = "sha256-iAcRN3Nwo9VaFjGnDHS+3DJXczFyuz8RWGKnhpSAEiE="
_PLATFORMS = {
    "aarch64-darwin": "universal",
    "x86_64-darwin": "universal",
}
_BLOCK_SIZE = 64


def _load_updater_module() -> ModuleType:
    return load_repo_module("packages/zo/updater.py", "zo_updater_test")


def _load_patch_module() -> ModuleType:
    return load_repo_module("packages/zo/patch_updater.py", "zo_patch_test")


def _main_payload(module: ModuleType) -> bytes:
    return b"before;\n" + module._UPDATER_FUNCTION + b"\n;after"


def _sentry_payload(module: ModuleType) -> bytes:
    return (
        b"const index = {sentryMinidumpIntegration: () => "
        b"({name: 'SentryMinidump'})};\n"
        b"const integrations = [\n"
        + module._SENTRY_MINIDUMP_DEFAULT
        + b",\n        {name: 'Other'},\n];\n"
        b"console.log(JSON.stringify(integrations.map(({name}) => name)));\n"
    )


def _file_integrity(payload: bytes) -> dict[str, object]:
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(payload).hexdigest(),
        "blockSize": _BLOCK_SIZE,
        "blocks": [
            hashlib.sha256(payload[offset : offset + _BLOCK_SIZE]).hexdigest()
            for offset in range(0, len(payload), _BLOCK_SIZE)
        ],
    }


def _valid_header(
    main_payload: bytes,
    sentry_payload: bytes | None = None,
) -> dict[str, object]:
    header: dict[str, object] = {
        "files": {
            "out": {
                "files": {
                    "main": {
                        "files": {
                            "index.js": {
                                "size": len(main_payload),
                                "offset": "0",
                                "integrity": _file_integrity(main_payload),
                            }
                        }
                    }
                }
            }
        }
    }
    if sentry_payload is not None:
        header["files"]["node_modules"] = {  # type: ignore[index]
            "files": {
                "@sentry": {
                    "files": {
                        "electron": {
                            "files": {
                                "main": {
                                    "files": {
                                        "sdk.js": {
                                            "size": len(sentry_payload),
                                            "offset": str(len(main_payload)),
                                            "integrity": _file_integrity(
                                                sentry_payload
                                            ),
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    return header


def _write_asar(
    path: Path,
    main_payload: bytes,
    *,
    sentry_payload: bytes = b"",
    header: dict[str, object] | list[object] | None = None,
) -> None:
    archive_header = (
        _valid_header(main_payload, sentry_payload) if header is None else header
    )
    header_bytes = json.dumps(archive_header, separators=(",", ":")).encode()
    data_padding = b"\0\0\0"
    prefix = struct.pack(
        "<4I",
        4,
        len(header_bytes) + 8 + len(data_padding),
        len(header_bytes) + 4,
        len(header_bytes),
    )
    path.write_bytes(
        prefix + header_bytes + data_padding + main_payload + sentry_payload
    )


def _write_plist(path: Path, value: object | None = None) -> None:
    payload = (
        {
            "CFBundleIdentifier": "computer.zo.desktop",
            "ElectronAsarIntegrity": {
                "Resources/app.asar": {
                    "algorithm": "SHA256",
                    "hash": "0" * 64,
                }
            },
        }
        if value is None
        else value
    )
    with path.open("wb") as handle:
        plistlib.dump(payload, handle)


def _fixture_paths(
    tmp_path: Path,
    module: ModuleType,
    *,
    name: str = "fixture",
    payload: bytes | None = None,
    header: dict[str, object] | list[object] | None = None,
    plist: object | None = None,
) -> tuple[Path, Path, bytes]:
    main_payload = _main_payload(module) if payload is None else payload
    sentry_payload = _sentry_payload(module)
    asar_path = tmp_path / f"{name}.asar"
    plist_path = tmp_path / f"{name}.plist"
    _write_asar(
        asar_path,
        main_payload,
        sentry_payload=sentry_payload,
        header=header,
    )
    _write_plist(plist_path, plist)
    return asar_path, plist_path, main_payload


def _archive_payload(
    module: ModuleType,
    asar_path: Path,
) -> bytes:
    return read_packed_file(asar_path, module.MAIN_PATH)


def _archive_sentry_payload(
    module: ModuleType,
    asar_path: Path,
) -> bytes:
    return read_packed_file(asar_path, module.SENTRY_SDK_PATH)


def _release_payload(*, url: str = _ARTIFACT_URL) -> dict[str, object]:
    return {
        "tag_name": f"v{_VERSION}",
        "assets": [
            {
                "name": _ARTIFACT_NAME,
                "browser_download_url": url,
            }
        ],
    }


def test_zo_resolves_one_canonical_immutable_universal_zip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The GitHub release API must resolve the exact versioned universal ZIP."""
    module = _load_updater_module()
    updater = module.ZoUpdater()
    calls: list[tuple[str, object]] = []

    async def _fetch_github_api(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> object:
        calls.append((path, config))
        return _release_payload()

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _fetch_github_api,
    )

    info = _run(updater.fetch_latest(object()))
    result = updater.build_result(info, dict.fromkeys(_PLATFORMS, _HASH))

    assert updater.PLATFORMS == _PLATFORMS
    assert updater.supported_platforms == tuple(_PLATFORMS)
    assert updater._asset_name(_VERSION, "universal") == _ARTIFACT_NAME
    assert info == VersionInfo(
        version=_VERSION,
        metadata=AssetURLsMetadata(dict.fromkeys(_PLATFORMS, _ARTIFACT_URL)),
    )
    assert result.urls == dict.fromkeys(_PLATFORMS, _ARTIFACT_URL)
    assert result.hashes.to_json() == dict.fromkeys(_PLATFORMS, _HASH)
    assert calls == [("repos/zocomputer/Zo/releases/latest", updater.config)]


def test_zo_rejects_missing_or_noncanonical_release_assets() -> None:
    """Asset names and URLs must stay under the immutable official tag path."""
    updater = _load_updater_module().ZoUpdater()

    with pytest.raises(RuntimeError, match="Could not find zo release asset"):
        updater._asset_urls_from_payload(
            {
                "tag_name": f"v{_VERSION}",
                "assets": [
                    {"name": "Zo-latest.zip", "browser_download_url": _ARTIFACT_URL}
                ],
            },
            version=_VERSION,
            tag_name=f"v{_VERSION}",
        )

    with pytest.raises(RuntimeError, match="canonical immutable GitHub asset URL"):
        updater._asset_urls_from_payload(
            _release_payload(url="https://example.test/Zo.zip"),
            version=_VERSION,
            tag_name=f"v{_VERSION}",
        )

    fallback = VersionInfo(version=_VERSION)
    for platform in _PLATFORMS:
        assert updater.get_download_url(platform, fallback) == _ARTIFACT_URL


def test_zo_package_owns_required_runtime_entitlements() -> None:
    """Ad-hoc Electron processes must opt out of Team-ID library validation."""
    with (_PACKAGE_DIR / "Entitlements.plist").open("rb") as handle:
        entitlements = plistlib.load(handle)

    assert entitlements == {
        "com.apple.security.cs.allow-dyld-environment-variables": True,
        "com.apple.security.cs.allow-jit": True,
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
        "com.apple.security.cs.disable-library-validation": True,
        "com.apple.security.device.audio-input": True,
    }


def test_zo_package_patches_integrity_then_deep_resigns() -> None:
    """Package construction must patch updates and re-sign every process."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkSimpleDarwinApp"))
    assert_nix_ast_equal(
        expect_binding(arguments.values, "builder").value,
        Identifier(name="mkZipApp"),
    )
    for name, value in {"pname": "zo", "appName": "Zo"}.items():
        assert_nix_ast_equal(
            expect_binding(arguments.values, name).value,
            StringPrimitive(value=value),
        )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "info").value,
        Identifier(name="selfSource"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "dontFixup").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "platforms").value,
        '[ "aarch64-darwin" "x86_64-darwin" ]',
    )

    post_install = expect_instance(
        expect_binding(arguments.values, "postInstallApp").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(post_install.rebuild()))
    patch_commands = command_texts(shell, "__NIX_INTERP__")
    sign_commands = command_texts(shell, "/usr/bin/codesign")

    assert patch_commands == [
        "PYTHONPATH=__NIX_INTERP__ __NIX_INTERP__ __NIX_INTERP__ \\\n"
        '      "$app_bundle/Contents/Resources/app.asar" \\\n'
        '      "$app_bundle/Contents/Info.plist"'
    ]
    assert sign_commands == [
        "/usr/bin/codesign \\\n"
        "      --force \\\n"
        "      --deep \\\n"
        "      --sign - \\\n"
        "      --preserve-metadata=identifier,flags,runtime \\\n"
        "      --entitlements __NIX_INTERP__ \\\n"
        '      "$app_bundle"'
    ]


def test_zo_sources_pin_the_official_latest_release_zip() -> None:
    """Checked-in metadata must be ready only for the routed hash discovery."""
    sources = json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))

    assert sources == {
        "hashes": dict.fromkeys(_PLATFORMS, _HASH),
        "urls": dict.fromkeys(_PLATFORMS, _ARTIFACT_URL),
        "version": _VERSION,
    }


def test_zo_patch_updates_payload_file_hashes_and_plist(tmp_path: Path) -> None:
    """Updater suppression must update every ASAR and plist integrity layer."""
    module = _load_patch_module()
    asar_path, plist_path, original = _fixture_paths(tmp_path, module)
    original_size = asar_path.stat().st_size

    digest = module.patch_bundle(asar_path, plist_path)
    patched = _archive_payload(module, asar_path)

    assert asar_path.stat().st_size == original_size
    assert len(patched) == len(original)
    assert module._UPDATER_FUNCTION not in patched
    assert module._DISABLED_UPDATER_FUNCTION in patched
    sentry = _archive_sentry_payload(module, asar_path)
    assert module._SENTRY_MINIDUMP_DEFAULT not in sentry
    assert module._SENTRY_WITHOUT_MINIDUMPS in sentry
    assert digest == asar_header_hash(asar_path)
    assert check_info_plist_hash(plist_path, asar_path) == digest

    with pytest.raises(module.PatchError, match="updater anchor.*found 0"):
        module.patch_bundle(asar_path, plist_path)


def test_zo_patch_semantically_disconnects_every_auto_updater_call(
    tmp_path: Path,
) -> None:
    """The patched accessor must return no-op methods, never the vendor updater."""
    module = _load_patch_module()
    program = (
        b"const calls = [];\n"
        b"const electronUpdater = {autoUpdater: new Proxy({}, {get(_target, name) {"
        b"return () => {calls.push(String(name)); return Promise.resolve();};}})};\n"
        + module._UPDATER_FUNCTION
        + b"\n(async () => {\n"
        b"const updater = getAutoUpdater();\n"
        b"updater.once('update-available', () => {});\n"
        b"await updater.checkForUpdates();\n"
        b"await updater.checkForUpdatesAndNotify();\n"
        b"updater.quitAndInstall();\n"
        b"console.log(JSON.stringify(calls));\n"
        b"})().catch((error) => { console.error(error); process.exitCode = 1; });\n"
    )
    asar_path, plist_path, _payload = _fixture_paths(
        tmp_path,
        module,
        payload=program,
    )
    node = shutil.which("node")
    assert node is not None

    original = subprocess.run(  # noqa: S603
        [node, "-e", program.decode()],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(original.stdout) == [
        "once",
        "checkForUpdates",
        "checkForUpdatesAndNotify",
        "quitAndInstall",
    ]

    module.patch_bundle(asar_path, plist_path)
    patched = _archive_payload(module, asar_path)
    disabled = subprocess.run(  # noqa: S603
        [node, "-e", patched.decode()],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(disabled.stdout) == []


def test_zo_patch_disables_only_the_native_crashpad_integration(
    tmp_path: Path,
) -> None:
    """Normal Sentry telemetry must remain after removing its Crashpad process."""
    module = _load_patch_module()
    asar_path, plist_path, _payload = _fixture_paths(tmp_path, module)
    original = _archive_sentry_payload(module, asar_path)
    node = shutil.which("node")
    assert node is not None

    enabled = subprocess.run(  # noqa: S603
        [node, "-e", original.decode()],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(enabled.stdout) == ["SentryMinidump", "Other"]

    module.patch_bundle(asar_path, plist_path)
    patched = _archive_sentry_payload(module, asar_path)
    crashpad_free = subprocess.run(  # noqa: S603
        [node, "-e", patched.decode()],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(crashpad_free.stdout) == ["Other"]


@pytest.mark.parametrize(
    ("old", "new", "payload", "message"),
    [
        (b"a", b"longer", b"a", "replacement is longer"),
        (b"anchor", b"short", b"no match", "found 0"),
        (b"anchor", b"short", b"anchor anchor", "found 2"),
    ],
)
def test_zo_patch_rejects_unstable_replacement_contracts(
    old: bytes,
    new: bytes,
    payload: bytes,
    message: str,
) -> None:
    """A changed or ambiguous vendor anchor must fail before mutation."""
    module = _load_patch_module()

    with pytest.raises(module.PatchError, match=message):
        module._replace_padded_once(payload, old, new, name="test")


def test_zo_patch_requires_the_packed_main_entry(tmp_path: Path) -> None:
    """Missing ASAR paths must fail without mutating the archive."""
    module = _load_patch_module()
    asar_path, plist_path, _payload = _fixture_paths(
        tmp_path,
        module,
        header={"files": {}},
    )
    original = asar_path.read_bytes()

    with pytest.raises(module.PatchError, match="missing packed file"):
        module.patch_bundle(asar_path, plist_path)

    assert asar_path.read_bytes() == original


def test_zo_patch_preflights_the_sentry_entry_before_mutation(tmp_path: Path) -> None:
    """A missing Sentry SDK path must leave the otherwise valid archive untouched."""
    module = _load_patch_module()
    main_payload = _main_payload(module)
    asar_path, plist_path, _payload = _fixture_paths(
        tmp_path,
        module,
        header=_valid_header(main_payload),
    )
    original = asar_path.read_bytes()

    with pytest.raises(module.PatchError, match="missing packed file"):
        module.patch_bundle(asar_path, plist_path)

    assert asar_path.read_bytes() == original


@pytest.mark.parametrize(
    ("plist", "message"),
    [
        (["not", "a", "dictionary"], "Expected a plist dictionary"),
        (
            {"ElectronAsarIntegrity": "invalid"},
            "Expected ElectronAsarIntegrity dictionary",
        ),
    ],
)
def test_zo_patch_rejects_invalid_plist_integrity_shape(
    tmp_path: Path,
    plist: object,
    message: str,
) -> None:
    """The top-level Electron integrity contract must stay dictionary-shaped."""
    module = _load_patch_module()
    asar_path, plist_path, _payload = _fixture_paths(
        tmp_path,
        module,
        plist=plist,
    )
    original_asar = asar_path.read_bytes()
    original_plist = plist_path.read_bytes()

    with pytest.raises(module.PatchError, match=message):
        module.patch_bundle(asar_path, plist_path)

    assert asar_path.read_bytes() == original_asar
    assert plist_path.read_bytes() == original_plist


def test_zo_patch_rolls_back_if_plist_publication_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed second atomic replacement must restore the original bundle."""
    module = _load_patch_module()
    asar_path, plist_path, _payload = _fixture_paths(tmp_path, module)
    original_asar = asar_path.read_bytes()
    original_plist = plist_path.read_bytes()
    replace = Path.replace

    def fail_plist_publication(path: Path, target: Path) -> Path:
        if target == plist_path:
            msg = "simulated plist publication failure"
            raise OSError(msg)
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_plist_publication)

    with pytest.raises(OSError, match="simulated plist publication failure"):
        module.patch_bundle(asar_path, plist_path)

    assert asar_path.read_bytes() == original_asar
    assert plist_path.read_bytes() == original_plist


def test_zo_patch_cli_reports_success_and_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The package hook should expose concise success and fail-closed diagnostics."""
    module = _load_patch_module()
    asar_path, plist_path, _payload = _fixture_paths(tmp_path, module)

    assert module.main([str(asar_path), str(plist_path)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("disabled Zo updates; ASAR header SHA256 ")

    missing = tmp_path / "missing.asar"
    assert module.main([str(missing), str(plist_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No such file or directory" in captured.err
