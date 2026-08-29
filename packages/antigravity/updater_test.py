"""Focused tests for the Google Antigravity desktop package."""

import hashlib
import json
import plistlib
import struct
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.asar_integrity import check_info_plist_hash, read_packed_file
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.derivation_validation import DerivationValidation
from lib.update.paths import REPO_ROOT
from lib.update.sources import load_source_entry
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import DownloadUrlMetadata

_PRODUCT_VERSION = "2.10.0"
_VERSION = f"{_PRODUCT_VERSION}-4996573600546816"
_ZIP_URL = (
    "https://storage.googleapis.com/antigravity-public/antigravity-hub/"
    f"{_VERSION}/darwin-arm/Antigravity.zip"
)
_DMG_URL = _ZIP_URL.removesuffix(".zip") + ".dmg"
_HASH = "sha256-Ig51l2OYm4+GxaBkVGeLSwoOpSin+4lg+NRffHrIkJs="


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/antigravity/updater.py",
        "antigravity_updater_test",
    )


def _load_policy_module() -> ModuleType:
    return load_repo_module(
        "packages/antigravity/patch_updater.py",
        "antigravity_update_policy_test",
    )


def _load_signing_module() -> ModuleType:
    return load_repo_module(
        "packages/antigravity/resign_bundle.py",
        "antigravity_signing_test",
    )


def _write_policy_bundle(tmp_path: Path, payload: bytes) -> Path:
    block_size = 64
    integrity = {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(payload).hexdigest(),
        "blockSize": block_size,
        "blocks": [
            hashlib.sha256(payload[offset : offset + block_size]).hexdigest()
            for offset in range(0, len(payload), block_size)
        ],
    }
    header = json.dumps(
        {
            "files": {
                "dist": {
                    "files": {
                        "updater.js": {
                            "size": len(payload),
                            "offset": "0",
                            "integrity": integrity,
                        }
                    }
                }
            }
        },
        separators=(",", ":"),
    ).encode()
    prefix = struct.pack("<IIII", 4, 8 + len(header), 4 + len(header), len(header))
    bundle = tmp_path / "Antigravity.app"
    resources = bundle / "Contents/Resources"
    resources.mkdir(parents=True)
    (resources / "app.asar").write_bytes(prefix + header + payload)
    with (bundle / "Contents/Info.plist").open("wb") as handle:
        plistlib.dump({}, handle)
    (resources / "app-update.yml").write_text(
        "provider: generic\nurl: https://vendor.invalid/manifest/\n",
        encoding="utf-8",
    )
    return bundle


_POLICY_FIXTURE = b"\n".join([
    b'MenuUpdateStep["CheckForUpdates"] = "Check for Updates";',
    b'MenuUpdateStep["RestartToUpdate"] = "Restart to Update";',
    b"    [MenuUpdateStep.CheckForUpdates]: () => checkForUpdates(true),",
    b"    [MenuUpdateStep.RestartToUpdate]: () => quitAndInstall(),",
    b"function setAutoUpdateChecking(enabled) {",
    b"    if (!updaterInitialized) {\n        return;\n    }",
    b"}",
    b"function initAutoUpdater(isHeadless, settingsService) {",
    b"}",
    b"function checkForUpdates(isManual = false) {",
    b"}",
    b"function quitAndInstall() {\n    electron_updater_1.autoUpdater.quitAndInstall();\n}",
    b"function applyHostUpdate() {\n    const state = getLastState();",
    b"}",
])

_EXPECTED_MACHOS = (
    "Contents/Frameworks/Antigravity Helper (GPU).app/Contents/MacOS/Antigravity Helper (GPU)",
    "Contents/Frameworks/Antigravity Helper (Plugin).app/Contents/MacOS/Antigravity Helper (Plugin)",
    "Contents/Frameworks/Antigravity Helper (Renderer).app/Contents/MacOS/Antigravity Helper (Renderer)",
    "Contents/Frameworks/Antigravity Helper.app/Contents/MacOS/Antigravity Helper",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers/chrome_crashpad_handler",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libEGL.dylib",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libGLESv2.dylib",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libffmpeg.dylib",
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libvk_swiftshader.dylib",
    "Contents/Frameworks/Mantle.framework/Versions/A/Mantle",
    "Contents/Frameworks/ReactiveObjC.framework/Versions/A/ReactiveObjC",
    "Contents/Frameworks/Squirrel.framework/Versions/A/Resources/ShipIt",
    "Contents/Frameworks/Squirrel.framework/Versions/A/Squirrel",
    "Contents/MacOS/Antigravity",
    "Contents/Resources/bin/language_server",
    "Contents/Resources/bin/webm_encoder",
)
_EXPECTED_NESTED_BUNDLES = (
    "Contents/Frameworks/Antigravity Helper (GPU).app",
    "Contents/Frameworks/Antigravity Helper (Plugin).app",
    "Contents/Frameworks/Antigravity Helper (Renderer).app",
    "Contents/Frameworks/Antigravity Helper.app",
    "Contents/Frameworks/Electron Framework.framework",
    "Contents/Frameworks/Mantle.framework",
    "Contents/Frameworks/ReactiveObjC.framework",
    "Contents/Frameworks/Squirrel.framework",
)
_SOURCE_MAIN_ENTITLEMENTS = {
    "com.apple.security.automation.apple-events": True,
    "com.apple.security.cs.allow-jit": True,
    "com.apple.security.device.audio-input": True,
    "com.apple.security.device.camera": True,
}
_MAIN_ENTITLEMENTS = {
    **_SOURCE_MAIN_ENTITLEMENTS,
    "com.apple.security.cs.disable-library-validation": True,
}
_JIT_ENTITLEMENTS = {"com.apple.security.cs.allow-jit": True}
_HELPER_ENTITLEMENTS = {
    **_JIT_ENTITLEMENTS,
    "com.apple.security.cs.disable-library-validation": True,
}
_PLUGIN_ENTITLEMENTS = {
    **_JIT_ENTITLEMENTS,
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    "com.apple.security.cs.disable-library-validation": True,
}
_EXPECTED_ENTITLEMENTS = {
    ".": _MAIN_ENTITLEMENTS,
    "Contents/MacOS/Antigravity": _MAIN_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (GPU).app": _HELPER_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (GPU).app/Contents/MacOS/Antigravity Helper (GPU)": _HELPER_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (Plugin).app": _PLUGIN_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (Plugin).app/Contents/MacOS/Antigravity Helper (Plugin)": _PLUGIN_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (Renderer).app": _HELPER_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (Renderer).app/Contents/MacOS/Antigravity Helper (Renderer)": _HELPER_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper.app": _HELPER_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper.app/Contents/MacOS/Antigravity Helper": _HELPER_ENTITLEMENTS,
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers/chrome_crashpad_handler": _JIT_ENTITLEMENTS,
    "Contents/Frameworks/Squirrel.framework/Versions/A/Resources/ShipIt": _JIT_ENTITLEMENTS,
    "Contents/Resources/bin/language_server": _JIT_ENTITLEMENTS,
    "Contents/Resources/bin/webm_encoder": _JIT_ENTITLEMENTS,
}
_SOURCE_EXPECTED_ENTITLEMENTS = {
    **_EXPECTED_ENTITLEMENTS,
    ".": _SOURCE_MAIN_ENTITLEMENTS,
    "Contents/MacOS/Antigravity": _SOURCE_MAIN_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (GPU).app": _JIT_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (GPU).app/Contents/MacOS/Antigravity Helper (GPU)": _JIT_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (Renderer).app": _JIT_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper (Renderer).app/Contents/MacOS/Antigravity Helper (Renderer)": _JIT_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper.app": _JIT_ENTITLEMENTS,
    "Contents/Frameworks/Antigravity Helper.app/Contents/MacOS/Antigravity Helper": _JIT_ENTITLEMENTS,
}


def _write_signing_bundle(tmp_path: Path) -> Path:
    app = tmp_path / "Antigravity.app"
    for relative_path in _EXPECTED_MACHOS:
        candidate = app / relative_path
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(bytes.fromhex("cffaedfe") + b"test Mach-O")
    for relative_path in _EXPECTED_NESTED_BUNDLES:
        (app / relative_path).mkdir(parents=True, exist_ok=True)
    duplicate = app / "Contents/Resources/bin/language_server-link"
    duplicate.symlink_to("language_server")
    return app


def _signature_runner(
    app: Path,
    *,
    details_overrides: dict[str, str] | None = None,
    entitlements_overrides: dict[str, object] | None = None,
    fail_on: tuple[str, ...] | None = None,
    source_before_sign: bool = False,
) -> tuple[list[list[str]], list[tuple[str, dict[str, object]]], object]:
    calls: list[list[str]] = []
    explicit_entitlements: list[tuple[str, dict[str, object]]] = []
    details_overrides = details_overrides or {}
    entitlements_overrides = entitlements_overrides or {}
    signed = False

    def relative(arguments: list[str]) -> str:
        path = Path(arguments[-1])
        return "." if path == app else path.relative_to(app).as_posix()

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
        nonlocal signed
        calls.append(arguments)
        if fail_on is not None and tuple(arguments[1:-1]) == fail_on:
            return subprocess.CompletedProcess(arguments, 1, b"", b"simulated failure")
        label = relative(arguments)
        if "--sign" in arguments:
            if "--entitlements" in arguments:
                entitlement_path = Path(
                    arguments[arguments.index("--entitlements") + 1]
                )
                explicit_entitlements.append((
                    label,
                    plistlib.loads(entitlement_path.read_bytes()),
                ))
            signed = True
            return subprocess.CompletedProcess(arguments, 0, b"", b"")
        if "--entitlements" in arguments:
            entitlements = entitlements_overrides.get(
                label,
                (
                    _SOURCE_EXPECTED_ENTITLEMENTS
                    if source_before_sign and not signed
                    else _EXPECTED_ENTITLEMENTS
                ).get(label, {}),
            )
            stdout = (
                entitlements
                if isinstance(entitlements, bytes)
                else plistlib.dumps(entitlements)
                if entitlements
                else b""
            )
            return subprocess.CompletedProcess(arguments, 0, stdout, b"")
        if "-d" in arguments:
            details = details_overrides.get(
                label,
                "CodeDirectory v=20500 size=1 flags=0x10002(adhoc,runtime) "
                "hashes=1+0 location=embedded\n"
                "Signature=adhoc\n"
                "TeamIdentifier=not set\n"
                "Runtime Version=26.2.0\n",
            )
            return subprocess.CompletedProcess(arguments, 0, b"", details.encode())
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    return calls, explicit_entitlements, runner


def test_google_manifest_resolves_the_immutable_arm64_dmg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The vendor's current feed should produce its matching versioned DMG."""
    module = _load_updater_module()
    updater = module.AntigravityUpdater()

    async def _fetch_feed(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> tuple[str, tuple[str, ...]]:
        assert url == updater.FEED_URL
        assert config is updater.config
        return _PRODUCT_VERSION, (_ZIP_URL,)

    monkeypatch.setattr(module, "fetch_electron_builder_feed", _fetch_feed)

    info = _run(updater.fetch_latest(object()))
    result = updater.build_result(info, {"aarch64-darwin": _HASH})

    assert info == VersionInfo(
        version=_VERSION,
        metadata=DownloadUrlMetadata(url=_DMG_URL),
    )
    assert result.version == _VERSION
    assert result.urls == {"aarch64-darwin": _DMG_URL}
    assert result.hashes.to_json() == {"aarch64-darwin": _HASH}
    assert updater.PLATFORMS == {"aarch64-darwin": "darwin-arm"}
    assert updater.get_derivation_validations() == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )


@pytest.mark.parametrize(
    "artifact_url",
    [
        _ZIP_URL.replace("https://storage.googleapis.com", "https://example.test"),
        _ZIP_URL.replace(_VERSION, "2.8.1-4871453687021568"),
        _ZIP_URL.replace(_VERSION, f"{_PRODUCT_VERSION}-canary"),
        f"{_ZIP_URL}?signature=temporary",
        _ZIP_URL.replace("Antigravity.zip", "Antigravity.dmg"),
    ],
)
def test_google_manifest_rejects_untrusted_or_mismatched_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    artifact_url: str,
) -> None:
    """Only Google's exact immutable ZIP peer may select the packaged DMG."""
    module = _load_updater_module()

    async def _fetch_feed(
        *_args: object,
        **_kwargs: object,
    ) -> tuple[str, tuple[str, ...]]:
        return _PRODUCT_VERSION, (artifact_url,)

    monkeypatch.setattr(module, "fetch_electron_builder_feed", _fetch_feed)

    with pytest.raises(RuntimeError, match="No matching Google Antigravity artifact"):
        _run(module.AntigravityUpdater().fetch_latest(object()))


def test_google_manifest_ignores_unrelated_assets_before_the_dmg_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrelated manifest entry must not hide the matching arm64 release."""
    module = _load_updater_module()

    async def _fetch_feed(
        *_args: object,
        **_kwargs: object,
    ) -> tuple[str, tuple[str, ...]]:
        return _PRODUCT_VERSION, ("https://example.test/unrelated.zip", _ZIP_URL)

    monkeypatch.setattr(module, "fetch_electron_builder_feed", _fetch_feed)

    assert _run(module.AntigravityUpdater().fetch_latest(object())) == VersionInfo(
        version=_VERSION,
        metadata=DownloadUrlMetadata(url=_DMG_URL),
    )


def test_sources_pin_the_updater_generated_google_release() -> None:
    """The checked-in source must remain a coherent updater-generated release."""
    updater = _load_updater_module().AntigravityUpdater()
    source = load_source_entry(REPO_ROOT / "packages/antigravity/sources.json")

    assert source.version is not None
    assert source.urls is not None
    assert source.hashes.mapping is not None
    assert set(source.urls) == {"aarch64-darwin"}
    assert set(source.hashes.mapping) == {"aarch64-darwin"}

    product_version, separator, build_id = source.version.rpartition("-")
    assert separator == "-"
    assert build_id.isdigit()
    dmg_url = source.urls["aarch64-darwin"]
    version, parsed_dmg_url = updater._parse_artifact_url(
        dmg_url.removesuffix(".dmg") + ".zip",
        product_version,
    )
    result = updater.build_result(
        VersionInfo(version, DownloadUrlMetadata(url=parsed_dmg_url)),
        source.hashes.mapping,
    )

    assert result.equivalent_to(source)


def test_package_policy_disables_every_update_entry_point(tmp_path: Path) -> None:
    """The package hook should remove acquisition, menu, and install paths."""
    module = _load_policy_module()
    bundle = _write_policy_bundle(tmp_path, _POLICY_FIXTURE)
    asar_path = bundle / "Contents/Resources/app.asar"
    original_size = asar_path.stat().st_size

    module.patch_bundle(
        bundle,
        expected_sha256=hashlib.sha256(_POLICY_FIXTURE).hexdigest(),
    )

    patched = read_packed_file(asar_path, "dist/updater.js")
    assert asar_path.stat().st_size == original_size
    assert b"Check for Updates" not in patched
    assert b"Restart to Update" not in patched
    assert b"Managed by Nix" in patched
    assert b"function initAutoUpdater(_,__) { return;" in patched
    assert b"function checkForUpdates(_) { return;" in patched
    assert b"function quitAndInstall() {\n    return;" in patched
    assert b"function applyHostUpdate() {\n    return false;" in patched
    assert not (bundle / "Contents/Resources/app-update.yml").exists()
    check_info_plist_hash(bundle / "Contents/Info.plist", asar_path)
    module.validate_bundle(bundle)


def test_package_policy_rejects_unreviewed_or_incomplete_payloads() -> None:
    """Vendor drift must stop before any unreviewed archive can be promoted."""
    module = _load_policy_module()

    with pytest.raises(module.PatchError, match="is not reviewed"):
        module.disable_updates(_POLICY_FIXTURE)
    with pytest.raises(module.PatchError, match="SHA-256 drifted"):
        module.disable_updates(_POLICY_FIXTURE, expected_sha256="0" * 64)

    _label, vendor, _disabled, _count = module.PATCHES[0]
    incomplete = _POLICY_FIXTURE.replace(vendor, b"X" * len(vendor), 1)
    with pytest.raises(module.PatchError, match="inventory drifted"):
        module.disable_updates(
            incomplete,
            expected_sha256=hashlib.sha256(incomplete).hexdigest(),
        )

    assert (
        frozenset({"3a9ccfaef9bc9a299f0e761a171997a21887dc0c7bda38bf178647ed59a60c71"})
        == module.REVIEWED_UPDATER_SHA256
    )


def test_package_policy_validator_rejects_vendor_and_drifted_disabled_anchors() -> None:
    """Post-sign validation should catch both reactivation and partial patching."""
    module = _load_policy_module()
    with pytest.raises(module.PatchError, match="still contains"):
        module.validate_disabled_payload(_POLICY_FIXTURE)

    patched = module.disable_updates(
        _POLICY_FIXTURE,
        expected_sha256=hashlib.sha256(_POLICY_FIXTURE).hexdigest(),
    )
    _label, _vendor, disabled, _count = module.PATCHES[0]
    with pytest.raises(module.PatchError, match="disabled .* inventory drifted"):
        module.validate_disabled_payload(
            patched.replace(disabled, b"X" * len(disabled))
        )


def test_package_policy_requires_and_removes_the_vendor_feed(tmp_path: Path) -> None:
    """Both patching and validation should fail closed around feed ownership."""
    module = _load_policy_module()
    bundle = _write_policy_bundle(tmp_path, _POLICY_FIXTURE)

    with pytest.raises(module.PatchError, match="vendor updater config remains"):
        module.validate_bundle(bundle)

    update_config = bundle / "Contents/Resources/app-update.yml"
    update_config.unlink()
    with pytest.raises(module.PatchError, match="updater config is missing"):
        module.patch_bundle(
            bundle,
            expected_sha256=hashlib.sha256(_POLICY_FIXTURE).hexdigest(),
        )


def test_package_policy_cli_patches_checks_and_reports_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The derivation-facing CLI should read back success and surface failures."""
    module = _load_policy_module()
    bundle = _write_policy_bundle(tmp_path, _POLICY_FIXTURE)
    reviewed = hashlib.sha256(_POLICY_FIXTURE).hexdigest()

    original = module.REVIEWED_UPDATER_SHA256
    module.REVIEWED_UPDATER_SHA256 = frozenset({reviewed})
    try:
        assert module.main([str(bundle)]) == 0
        assert module.main(["--check", str(bundle)]) == 0
    finally:
        module.REVIEWED_UPDATER_SHA256 = original
    output = capsys.readouterr().out
    assert "disabled Antigravity updates" in output
    assert "verified Antigravity updates" in output

    assert module.main([str(tmp_path / "missing.app")]) == 1
    assert "updater config is missing" in capsys.readouterr().err


def test_signer_resigns_every_macho_and_bundle_inside_out(tmp_path: Path) -> None:
    """Every mapped binary must cross the same explicit ad hoc signing boundary."""
    module = _load_signing_module()
    app = _write_signing_bundle(tmp_path)
    calls, explicit_entitlements, runner = _signature_runner(
        app,
        source_before_sign=True,
    )

    module.resign_bundle(app, runner=runner)

    assert module.EXPECTED_MACHOS == _EXPECTED_MACHOS
    assert module.EXPECTED_NESTED_BUNDLES == _EXPECTED_NESTED_BUNDLES
    sign_calls = [arguments for arguments in calls if "--sign" in arguments]
    sign_labels = [
        Path(arguments[-1]).relative_to(app).as_posix()
        if Path(arguments[-1]) != app
        else "."
        for arguments in sign_calls
    ]
    assert sign_labels == [*_EXPECTED_MACHOS, *_EXPECTED_NESTED_BUNDLES, "."]
    expected_explicit_entitlements = [
        (
            "Contents/Frameworks/Antigravity Helper (GPU).app/Contents/MacOS/Antigravity Helper (GPU)",
            _HELPER_ENTITLEMENTS,
        ),
        (
            "Contents/Frameworks/Antigravity Helper (Renderer).app/Contents/MacOS/Antigravity Helper (Renderer)",
            _HELPER_ENTITLEMENTS,
        ),
        (
            "Contents/Frameworks/Antigravity Helper.app/Contents/MacOS/Antigravity Helper",
            _HELPER_ENTITLEMENTS,
        ),
        ("Contents/MacOS/Antigravity", _MAIN_ENTITLEMENTS),
        (
            "Contents/Frameworks/Antigravity Helper (GPU).app",
            _HELPER_ENTITLEMENTS,
        ),
        (
            "Contents/Frameworks/Antigravity Helper (Renderer).app",
            _HELPER_ENTITLEMENTS,
        ),
        ("Contents/Frameworks/Antigravity Helper.app", _HELPER_ENTITLEMENTS),
        (".", _MAIN_ENTITLEMENTS),
    ]
    explicit_labels = {label for label, _entitlements in expected_explicit_entitlements}
    ordinary_sign_calls = [
        arguments
        for arguments, label in zip(sign_calls, sign_labels, strict=True)
        if label not in explicit_labels
    ]
    assert all(
        arguments[:-1]
        == [
            "/usr/bin/codesign",
            "--force",
            "--timestamp=none",
            "--sign",
            "-",
            "--preserve-metadata=identifier,entitlements,flags,runtime",
        ]
        for arguments in ordinary_sign_calls
    )
    entitled_sign_calls = [
        arguments
        for arguments, label in zip(sign_calls, sign_labels, strict=True)
        if label in explicit_labels
    ]
    assert all(
        arguments[:7]
        == [
            "/usr/bin/codesign",
            "--force",
            "--timestamp=none",
            "--sign",
            "-",
            "--preserve-metadata=identifier,flags,runtime",
            "--entitlements",
        ]
        and (
            "."
            if Path(arguments[-1]) == app
            else Path(arguments[-1]).relative_to(app).as_posix()
        )
        in explicit_labels
        for arguments in entitled_sign_calls
    )
    assert explicit_entitlements == expected_explicit_entitlements
    assert [
        "/usr/bin/codesign",
        "--verify",
        "--deep",
        "--strict",
        str(app),
    ] in calls


def test_signer_rejects_the_mixed_team_identity_that_crashed_dyld(
    tmp_path: Path,
) -> None:
    """Strict/deep validity is insufficient when one mapped dylib keeps Team ID."""
    module = _load_signing_module()
    app = _write_signing_bundle(tmp_path)
    calls, _explicit_entitlements, runner = _signature_runner(
        app,
        details_overrides={
            "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libEGL.dylib": (
                "CodeDirectory v=20500 size=1 flags=0x10000(runtime) "
                "hashes=1+0 location=embedded\n"
                "Signature size=9012\n"
                "TeamIdentifier=EQHXZ8M8AV\n"
                "Runtime Version=26.2.0\n"
            )
        },
    )

    with pytest.raises(module.SigningError, match="Team ID"):
        module.validate_bundle(app, runner=runner)

    assert [
        "/usr/bin/codesign",
        "--verify",
        "--strict",
        str(
            app
            / "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libEGL.dylib"
        ),
    ] in calls


@pytest.mark.parametrize(
    ("label", "entitlements"),
    [
        ("Contents/MacOS/Antigravity", _JIT_ENTITLEMENTS),
        (
            "Contents/Frameworks/Antigravity Helper (GPU).app",
            _PLUGIN_ENTITLEMENTS,
        ),
        (
            "Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries/libffmpeg.dylib",
            _JIT_ENTITLEMENTS,
        ),
    ],
)
def test_signer_rejects_entitlement_drift(
    tmp_path: Path,
    label: str,
    entitlements: dict[str, bool],
) -> None:
    """Required and unentitled code must retain its exact reviewed policy."""
    module = _load_signing_module()
    app = _write_signing_bundle(tmp_path)
    _calls, _explicit_entitlements, runner = _signature_runner(
        app,
        entitlements_overrides={label: entitlements},
    )

    with pytest.raises(module.SigningError, match="entitlements"):
        module.validate_bundle(app, runner=runner)


def test_signer_rejects_malformed_entitlements(tmp_path: Path) -> None:
    """Unreadable codesign entitlement output must fail closed."""
    module = _load_signing_module()
    app = _write_signing_bundle(tmp_path)
    _calls, _explicit_entitlements, runner = _signature_runner(
        app,
        entitlements_overrides={
            "Contents/Resources/bin/language_server": b"not a plist"
        },
    )

    with pytest.raises(module.SigningError, match="invalid .* entitlements"):
        module.validate_bundle(app, runner=runner)

    _calls, _explicit_entitlements, runner = _signature_runner(
        app,
        entitlements_overrides={
            "Contents/Resources/bin/language_server": plistlib.dumps(["not a dict"])
        },
    )
    with pytest.raises(module.SigningError, match="invalid .* entitlements"):
        module.validate_bundle(app, runner=runner)


@pytest.mark.parametrize(
    ("details", "message"),
    [
        (
            "CodeDirectory v=20500 size=1 flags=0x10002(adhoc,runtime)\n"
            "TeamIdentifier=not set\n",
            "not exactly ad hoc",
        ),
        (
            "CodeDirectory v=20500 size=1 flags=0x2(adhoc)\n"
            "Signature=adhoc\nTeamIdentifier=not set\n",
            "lacks required flags",
        ),
        (
            "CodeDirectory v=20500 size=1 flags=0x10002(adhoc,runtime)\n"
            "Signature=adhoc\n",
            "Team ID",
        ),
        (
            "Signature=adhoc\nTeamIdentifier=not set\n",
            "CodeDirectory lines",
        ),
        (
            "CodeDirectory malformed\nSignature=adhoc\nTeamIdentifier=not set\n",
            "lacks required flags",
        ),
    ],
)
def test_signer_rejects_incomplete_signature_evidence(
    tmp_path: Path,
    details: str,
    message: str,
) -> None:
    """Every final code object needs one ad hoc hardened-runtime identity."""
    module = _load_signing_module()
    app = _write_signing_bundle(tmp_path)
    _calls, _explicit_entitlements, runner = _signature_runner(
        app,
        details_overrides={_EXPECTED_MACHOS[0]: details},
    )

    with pytest.raises(module.SigningError, match=message):
        module.validate_bundle(app, runner=runner)


def test_signer_rejects_inventory_and_codesign_failures(tmp_path: Path) -> None:
    """Artifact layout drift and failed strict verification must stop promotion."""
    module = _load_signing_module()
    app = _write_signing_bundle(tmp_path)
    unexpected = app / "Contents/Frameworks/unreviewed.dylib"
    unexpected.write_bytes(bytes.fromhex("cffaedfe") + b"unexpected")

    with pytest.raises(module.SigningError, match="unexpected .* Mach-O inventory"):
        module.discover_inventory(app)

    unexpected.unlink()
    unexpected_bundle = app / "Contents/Frameworks/unreviewed.xpc"
    unexpected_bundle.mkdir()
    with pytest.raises(
        module.SigningError, match="unexpected .* nested-bundle inventory"
    ):
        module.discover_inventory(app)

    unexpected_bundle.rmdir()
    _calls, _explicit_entitlements, runner = _signature_runner(
        app,
        fail_on=("--verify", "--strict"),
    )
    with pytest.raises(module.SigningError, match="command failed.*simulated failure"):
        module.validate_bundle(app, runner=runner)

    with pytest.raises(module.SigningError, match="is not a directory"):
        module.discover_inventory(tmp_path / "missing.app")


def test_signer_cli_checks_and_reports_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build-facing CLI should expose both signing and validation modes."""
    module = _load_signing_module()
    app = _write_signing_bundle(tmp_path)
    _calls, _explicit_entitlements, runner = _signature_runner(
        app,
        source_before_sign=True,
    )

    assert module.main([str(app)], runner=runner) == 0
    assert module.main(["--check", str(app)], runner=runner) == 0
    output = capsys.readouterr().out
    assert "re-signed Antigravity" in output
    assert "verified Antigravity signatures" in output

    assert module.main([str(tmp_path / "missing.app")], runner=runner) == 1
    assert "is not a directory" in capsys.readouterr().err

    expected = subprocess.CompletedProcess(["codesign"], 0, b"out", b"error")
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: expected)
    assert module._default_runner(["codesign"]) is expected


def test_derivation_patches_then_resigns_the_managed_bundle() -> None:
    """The final bundle should enforce Nix ownership before signature checks."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/antigravity/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkDmgApp7zz"))
    post_install = expect_instance(
        expect_binding(arguments.values, "postInstallApp").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(post_install.rebuild()))

    assert command_texts(shell, "chmod") == ['chmod -R u+w "$app_bundle"']
    assert command_texts(shell, "__NIX_INTERP__") == [
        'PYTHONPATH=__NIX_INTERP__ __NIX_INTERP__ __NIX_INTERP__ "$app_bundle"',
        '__NIX_INTERP__ __NIX_INTERP__ "$app_bundle"',
        'PYTHONPATH=__NIX_INTERP__ __NIX_INTERP__ __NIX_INTERP__ --check "$app_bundle"',
        '__NIX_INTERP__ __NIX_INTERP__ --check "$app_bundle"',
    ]
    assert command_texts(shell, "/usr/bin/codesign") == []
