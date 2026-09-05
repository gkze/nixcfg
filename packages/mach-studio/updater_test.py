"""Behavioral and package-shape tests for Mach Studio."""

import hashlib
import json
import plistlib
import struct
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.asar_integrity import check_info_plist_hash, read_packed_file
from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._source_metadata import (
    assert_https_url,
    assert_platform_source_entry,
    assert_release_version,
    assert_url_contains_version,
)
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.derivation_validation import DerivationValidation
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

_VERSION = "0.1.142"
_ARTIFACT_URL = (
    "https://api.maniac.ai/storage/v1/object/public/desktop-releases/"
    "stable/mac-arm64/Mach-Studio-0.1.142-arm64.dmg"
)
_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_RENDERER_NAME = "index-NextRelease42.js"


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/mach-studio/updater.py",
        "mach_studio_updater_dedicated_test",
    )


def _load_policy_module() -> ModuleType:
    return load_repo_module(
        "packages/mach-studio/patch_updater.py",
        "mach_studio_update_policy_test",
    )


def _write_policy_bundle(
    tmp_path: Path,
    main_payload: bytes,
    renderer_payload: bytes,
    *,
    renderer_name: str = _RENDERER_NAME,
    additional_assets: dict[str, bytes] | None = None,
) -> tuple[Path, Path]:
    block_size = 64

    def _entry(payload: bytes, *, offset: int) -> dict[str, object]:
        return {
            "size": len(payload),
            "offset": str(offset),
            "integrity": {
                "algorithm": "SHA256",
                "hash": hashlib.sha256(payload).hexdigest(),
                "blockSize": block_size,
                "blocks": [
                    hashlib.sha256(payload[start : start + block_size]).hexdigest()
                    for start in range(0, len(payload), block_size)
                ],
            },
        }

    archive_payload = bytearray(main_payload)
    asset_entries: dict[str, object] = {}
    for name, payload in (
        (renderer_name, renderer_payload),
        *((additional_assets or {}).items()),
    ):
        asset_entries[name] = _entry(payload, offset=len(archive_payload))
        archive_payload.extend(payload)

    header = json.dumps(
        {
            "files": {
                "dist-electron": {
                    "files": {
                        "main.js": _entry(main_payload, offset=0),
                    }
                },
                "dist": {
                    "files": {
                        "assets": {
                            "files": asset_entries,
                        }
                    }
                },
            }
        },
        separators=(",", ":"),
    ).encode()
    prefix = struct.pack("<IIII", 4, 8 + len(header), 4 + len(header), len(header))
    asar_path = tmp_path / "app.asar"
    asar_path.write_bytes(prefix + header + archive_payload)
    plist_path = tmp_path / "Info.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump({}, handle)
    return asar_path, plist_path


def test_mach_studio_policy_forces_the_existing_service_gate_disabled() -> None:
    """The packaged app-update service must initialize in its disabled state."""
    module = _load_policy_module()
    payload = (
        b"before " + module._ENABLED_GATE + module._FAIL_OPEN_ENGINE_INSTALL + b" after"
    )

    patched = module.patch_main(payload)

    assert len(patched) == len(payload)
    assert module._ENABLED_GATE not in patched
    assert module._DISABLED_GATE in patched


def test_mach_studio_policy_fails_closed_and_validates_the_engine_source() -> None:
    """Provisioning must propagate failures and import Mach from this bundle."""
    module = _load_policy_module()
    payload = (
        b"before " + module._ENABLED_GATE + module._FAIL_OPEN_ENGINE_INSTALL + b" after"
    )

    patched = module.patch_main(payload)

    assert len(patched) == len(payload)
    assert module._FAIL_OPEN_ENGINE_INSTALL not in patched
    assert module._FAIL_CLOSED_ENGINE_INSTALL.rstrip() in patched
    assert b"catch(e){if(t8(e))throw e" not in patched
    assert (
        b"import mach,pathlib,sys;sys.exit(not pathlib.Path(mach.__file__)"
        b".resolve().is_relative_to(pathlib.Path(sys.argv[1]).resolve()))" in patched
    )


def test_mach_studio_policy_reports_the_packaged_engine_source() -> None:
    """Engine maintenance must describe this release's bundled source."""
    module = _load_policy_module()
    payload = (
        b"before "
        + module._WHEEL_REINSTALL_DESCRIPTION
        + module._WHEEL_MISSING_DESCRIPTION
        + module._WHEEL_TITLE * 3
        + b" after"
    )

    patched = module.patch_renderer(payload)

    assert len(patched) == len(payload)
    assert module._WHEEL_REINSTALL_DESCRIPTION not in patched
    assert module._SOURCE_REINSTALL_DESCRIPTION in patched
    assert module._WHEEL_MISSING_DESCRIPTION not in patched
    assert module._SOURCE_READY_DESCRIPTION in patched
    assert module._WHEEL_TITLE not in patched
    assert patched.count(module._SOURCE_TITLE) == 3


def test_mach_studio_policy_rejects_incomplete_anchor_inventories() -> None:
    """Release drift must fail before either partial transformation can run."""
    module = _load_policy_module()

    with pytest.raises(module.PatchError, match="updater policy anchor.*found 0"):
        module.patch_main(b"unrelated main code")
    with pytest.raises(
        module.PatchError,
        match="engine reinstall description anchor.*found 0",
    ):
        module.patch_renderer(b"unrelated renderer code")


def test_mach_studio_resolves_the_renderer_by_semantic_inventory(
    tmp_path: Path,
) -> None:
    """A release fingerprint may change without changing the patch contract."""
    module = _load_policy_module()
    main_payload = module._ENABLED_GATE + module._FAIL_OPEN_ENGINE_INSTALL
    renderer_payload = (
        module._WHEEL_REINSTALL_DESCRIPTION
        + module._WHEEL_MISSING_DESCRIPTION
        + module._WHEEL_TITLE * 3
    )
    asar_path, _plist_path = _write_policy_bundle(
        tmp_path,
        main_payload,
        renderer_payload,
        renderer_name="index-New.Fingerprint+99.js",
        additional_assets={"index-UnrelatedChunk.js": b"unrelated renderer code"},
    )

    assert (
        module.resolve_renderer_path(asar_path)
        == "dist/assets/index-New.Fingerprint+99.js"
    )


@pytest.mark.parametrize("matching_assets", [0, 2])
def test_mach_studio_renderer_resolution_fails_closed_on_ambiguity(
    tmp_path: Path,
    matching_assets: int,
) -> None:
    """The patch must reject a missing or ambiguous renderer policy owner."""
    module = _load_policy_module()
    main_payload = module._ENABLED_GATE + module._FAIL_OPEN_ENGINE_INSTALL
    renderer_payload = (
        module._WHEEL_REINSTALL_DESCRIPTION
        + module._WHEEL_MISSING_DESCRIPTION
        + module._WHEEL_TITLE * 3
    )
    primary_payload = renderer_payload if matching_assets else b"unrelated"
    additional_assets = (
        {"index-SecondMatch.js": renderer_payload} if matching_assets == 2 else None
    )
    asar_path, _plist_path = _write_policy_bundle(
        tmp_path,
        main_payload,
        primary_payload,
        additional_assets=additional_assets,
    )

    with pytest.raises(module.PatchError, match=rf"found {matching_assets}:"):
        module.resolve_renderer_path(asar_path)


def test_mach_studio_patch_cli_updates_integrity_and_fails_on_drift(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The package CLI must patch policy, refresh integrity, and reject reuse."""
    module = _load_policy_module()
    main_payload = (
        b"before " + module._ENABLED_GATE + module._FAIL_OPEN_ENGINE_INSTALL + b" after"
    )
    renderer_payload = (
        b"before "
        + module._WHEEL_REINSTALL_DESCRIPTION
        + module._WHEEL_MISSING_DESCRIPTION
        + module._WHEEL_TITLE * 3
        + b" after"
    )
    asar_path, plist_path = _write_policy_bundle(
        tmp_path,
        main_payload,
        renderer_payload,
    )
    (tmp_path / "vendor/local_moe_engine").mkdir(parents=True)
    original_size = asar_path.stat().st_size

    assert module.main([str(asar_path), str(plist_path)]) == 0

    patched_main = read_packed_file(asar_path, module.MAIN_PATH)
    patched_renderer = read_packed_file(
        asar_path,
        f"dist/assets/{_RENDERER_NAME}",
    )
    digest = check_info_plist_hash(plist_path, asar_path)
    assert asar_path.stat().st_size == original_size
    assert module._ENABLED_GATE not in patched_main
    assert module._DISABLED_GATE in patched_main
    assert module._FAIL_OPEN_ENGINE_INSTALL not in patched_main
    assert module._FAIL_CLOSED_ENGINE_INSTALL in patched_main
    assert module._WHEEL_MISSING_DESCRIPTION not in patched_renderer
    assert module._SOURCE_READY_DESCRIPTION in patched_renderer
    assert f"ASAR header SHA256 {digest}" in capsys.readouterr().out

    assert module.main([str(asar_path), str(plist_path)]) == 1
    assert "found 0" in capsys.readouterr().err


def test_mach_studio_patch_rejects_a_missing_packaged_engine_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Source-aware diagnostics require the claimed packaged source to exist."""
    module = _load_policy_module()
    main_payload = module._ENABLED_GATE + module._FAIL_OPEN_ENGINE_INSTALL
    renderer_payload = (
        module._WHEEL_REINSTALL_DESCRIPTION
        + module._WHEEL_MISSING_DESCRIPTION
        + module._WHEEL_TITLE * 3
    )
    asar_path, plist_path = _write_policy_bundle(
        tmp_path,
        main_payload,
        renderer_payload,
    )
    original_archive = asar_path.read_bytes()

    assert module.main([str(asar_path), str(plist_path)]) == 1

    assert "packaged local_moe_engine source is missing" in capsys.readouterr().err
    assert asar_path.read_bytes() == original_archive
    with plist_path.open("rb") as handle:
        assert plistlib.load(handle) == {}


def test_mach_studio_resolves_the_immutable_arm64_vendor_dmg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The official feed should pin its versioned DMG and only arm64."""
    module = _load_module()
    updater = module.MachStudioUpdater()

    async def _fetch_asset_urls(
        _session: object,
        url: str,
        selectors: object,
        *,
        config: object,
    ) -> tuple[str, dict[str, str]]:
        assert url == updater.FEED_URL
        assert selectors is updater.SELECTORS
        assert config == updater.config
        return _VERSION, {"aarch64-darwin": _ARTIFACT_URL}

    monkeypatch.setattr(
        "lib.update.updaters.strategies.fetch_electron_builder_asset_urls",
        _fetch_asset_urls,
    )

    info = _run(updater.fetch_latest(object()))
    result = updater.build_result(info, {"aarch64-darwin": _HASH})

    assert updater.PLATFORMS == {"aarch64-darwin": "arm64"}
    assert info == VersionInfo(
        version=_VERSION,
        metadata=AssetURLsMetadata({"aarch64-darwin": _ARTIFACT_URL}),
    )
    assert result.urls == {"aarch64-darwin": _ARTIFACT_URL}
    assert result.hashes.to_json() == {"aarch64-darwin": _HASH}


def test_mach_studio_selector_and_fallback_reject_wrong_assets() -> None:
    """ZIPs, stale versions, and non-arm64 DMGs must not enter sources.json."""
    updater = _load_module().MachStudioUpdater()
    selector = updater.SELECTORS["aarch64-darwin"]

    assert selector(_VERSION, _ARTIFACT_URL)
    assert not selector(_VERSION, _ARTIFACT_URL.replace(".dmg", ".zip"))
    assert not selector("0.1.139", _ARTIFACT_URL)
    assert not selector(_VERSION, _ARTIFACT_URL.replace("arm64", "x64"))
    assert (
        updater.get_download_url("aarch64-darwin", VersionInfo(_VERSION))
        == _ARTIFACT_URL
    )


def test_mach_studio_updater_build_validates_the_materialized_package() -> None:
    """Promotion must build the exact ASAR policy against the new artifact."""
    updater = _load_module().MachStudioUpdater()

    assert updater.get_derivation_validations() == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )


def test_mach_studio_package_patches_updates_and_resigns_the_bundle() -> None:
    """The final app must patch policy without discarding runtime entitlements."""
    source = SourceEntry.model_validate_json(
        (REPO_ROOT / "packages/mach-studio/sources.json").read_text(encoding="utf-8")
    )
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/mach-studio/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    bindings = binding_map(arguments.values)

    version = assert_release_version(source.version)
    _hashes, urls = assert_platform_source_entry(
        source,
        platforms={"aarch64-darwin"},
    )
    url = urls["aarch64-darwin"]
    assert_https_url(url, host="api.maniac.ai")
    assert_url_contains_version(url, version)
    assert "/stable/mac-arm64/" in url
    assert url.endswith("-arm64.dmg")
    assert_nix_ast_equal(derivation.name, Identifier(name="mkSimpleDarwinApp"))
    assert_nix_ast_equal(
        expect_binding(arguments.values, "builder").value,
        Identifier(name="mkDmgApp"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "pname").value,
        StringPrimitive(value="mach-studio"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "appName").value,
        StringPrimitive(value="Mach Studio"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "sourceName").value,
        '"Mach-Studio-${selfSource.version}-arm64.dmg"',
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )
    assert "codesignApp" not in bindings
    post_install = expect_instance(
        expect_binding(arguments.values, "postInstallApp").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(post_install.rebuild()))
    assert command_texts(shell, "__NIX_INTERP__") == [
        "PYTHONPATH=__NIX_INTERP__ __NIX_INTERP__ __NIX_INTERP__ \\\n"
        '      "$app_bundle/Contents/Resources/app.asar" \\\n'
        '      "$app_bundle/Contents/Info.plist"'
    ]
    assert command_texts(shell, "/usr/bin/codesign") == [
        "/usr/bin/codesign \\\n"
        "      --force \\\n"
        "      --deep \\\n"
        "      --sign - \\\n"
        "      --preserve-metadata=identifier,entitlements,flags,runtime \\\n"
        '      "$app_bundle"',
        '/usr/bin/codesign --verify --deep --strict "$app_bundle"',
    ]
