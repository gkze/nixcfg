"""Behavioral and package-shape tests for Energy desktop."""

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
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.derivation_validation import DerivationValidation
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

_VERSION = "0.7.95"
_ARTIFACT_URL = "https://static.getenergy.com/desktop/beta/arm64/0.7.95/arm64.dmg"
_HASH = "sha256-/byt1Ac1LS/7Y/ZwGkWk2Vv7aOT7mui85JLmPkJauPc="


def _load_module() -> ModuleType:
    return load_repo_module("packages/energy/updater.py", "energy_updater_test")


def _load_policy_module() -> ModuleType:
    return load_repo_module(
        "packages/energy/patch_updater.py",
        "energy_update_policy_test",
    )


def _write_policy_bundle(
    tmp_path: Path,
    payload: bytes,
) -> tuple[Path, Path]:
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
                "out": {
                    "files": {
                        "main": {
                            "files": {
                                "index.js": {
                                    "size": len(payload),
                                    "offset": "0",
                                    "integrity": integrity,
                                }
                            }
                        }
                    }
                }
            }
        },
        separators=(",", ":"),
    ).encode()
    prefix = struct.pack("<IIII", 4, 8 + len(header), 4 + len(header), len(header))
    asar_path = tmp_path / "app.asar"
    asar_path.write_bytes(prefix + header + payload)
    plist_path = tmp_path / "Info.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump({}, handle)
    return asar_path, plist_path


def test_energy_policy_disables_packaged_checks_and_install_on_quit() -> None:
    """Every app-update entry point must see a fail-closed packaged gate."""
    packaged_gate = b"!this.app.isPackaged"
    install_on_quit = b"kn.autoUpdater.autoInstallOnAppQuit=!0"
    payload = b"|".join([packaged_gate, packaged_gate, packaged_gate, install_on_quit])

    patched = _load_policy_module().disable_updates(payload)

    assert len(patched) == len(payload)
    assert packaged_gate not in patched
    assert patched.count(b"!!1/*nix-managed*/  ") == 3
    assert install_on_quit not in patched
    assert b"kn.autoUpdater.autoInstallOnAppQuit=!1" in patched


def test_energy_policy_ignores_the_minified_auto_updater_binding_name() -> None:
    """Vendor symbol renaming must not reactivate the install-on-quit path."""
    packaged_gate = b"!this.app.isPackaged"
    install_on_quit = b"Cn.autoUpdater.autoInstallOnAppQuit=!0"
    payload = b"|".join([packaged_gate, packaged_gate, packaged_gate, install_on_quit])

    patched = _load_policy_module().disable_updates(payload)

    assert install_on_quit not in patched
    assert b"Cn.autoUpdater.autoInstallOnAppQuit=!1" in patched


@pytest.mark.parametrize(
    ("packaged_gate_count", "install_on_quit_count"),
    [(2, 1), (4, 1), (3, 0), (3, 2)],
)
def test_energy_policy_rejects_drifted_vendor_contracts(
    packaged_gate_count: int,
    install_on_quit_count: int,
) -> None:
    """Every missing or duplicate policy anchor must fail closed."""
    module = _load_policy_module()
    payload = b"|".join(
        [module._PACKAGED_GATE] * packaged_gate_count
        + [module._INSTALL_ON_QUIT] * install_on_quit_count
    )

    with pytest.raises(module.PatchError, match="Energy updater policy anchors"):
        module.disable_updates(payload)


def test_energy_policy_allows_release_drift_outside_owned_anchors() -> None:
    """Unrelated bundle bytes must not become a second release-version pin."""
    module = _load_policy_module()
    release_specific_prefix = b"release-specific-vendor-code|"
    payload = release_specific_prefix + b"|".join([
        module._PACKAGED_GATE,
        module._PACKAGED_GATE,
        module._PACKAGED_GATE,
        module._INSTALL_ON_QUIT,
    ])

    patched = module.disable_updates(payload)

    assert patched.startswith(release_specific_prefix)
    assert module._PACKAGED_GATE not in patched
    assert module._INSTALL_ON_QUIT not in patched


def test_energy_patch_cli_updates_the_real_integrity_layers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The package CLI must patch policy, refresh integrity, and fail on drift."""
    module = _load_policy_module()
    payload = b"|".join([
        module._PACKAGED_GATE,
        module._PACKAGED_GATE,
        module._PACKAGED_GATE,
        module._INSTALL_ON_QUIT,
    ])
    asar_path, plist_path = _write_policy_bundle(tmp_path, payload)
    original_size = asar_path.stat().st_size

    assert module.main([str(asar_path), str(plist_path)]) == 0

    patched = read_packed_file(asar_path, module.MAIN_PATH)
    digest = check_info_plist_hash(plist_path, asar_path)
    assert asar_path.stat().st_size == original_size
    assert module._PACKAGED_GATE not in patched
    assert module._INSTALL_ON_QUIT not in patched
    assert f"ASAR header SHA256 {digest}" in capsys.readouterr().out

    assert module.main([str(asar_path), str(plist_path)]) == 1
    assert "found 0" in capsys.readouterr().err


def test_energy_resolves_the_immutable_arm64_vendor_dmg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The official feed should pin its versioned DMG and only arm64."""
    module = _load_module()
    updater = module.EnergyUpdater()

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


def test_energy_selector_and_fallback_reject_non_dmg_feed_assets() -> None:
    """ZIPs and stale-version paths must not replace the signed DMG source."""
    updater = _load_module().EnergyUpdater()
    selector = updater.SELECTORS["aarch64-darwin"]

    assert selector(_VERSION, _ARTIFACT_URL)
    assert not selector(_VERSION, _ARTIFACT_URL.replace(".dmg", ".zip"))
    assert not selector("0.7.87", _ARTIFACT_URL)
    assert (
        updater.get_download_url("aarch64-darwin", VersionInfo(_VERSION))
        == _ARTIFACT_URL
    )


def test_energy_updater_build_validates_the_materialized_darwin_package() -> None:
    """Promotion must build the exact ASAR policy against the new artifact."""
    updater = _load_module().EnergyUpdater()

    assert updater.get_derivation_validations() == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )


def test_energy_package_patches_updates_and_resigns_the_extracted_bundle() -> None:
    """The final app must patch policy with an ad-hoc-safe runtime signature."""
    source = Path(REPO_ROOT / "packages/energy/default.nix").read_text(encoding="utf-8")
    package = expect_instance(parse_nix_expr(source), FunctionDefinition)
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    bindings = binding_map(arguments.values)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkSimpleDarwinApp"))
    assert_nix_ast_equal(
        expect_binding(arguments.values, "builder").value,
        Identifier(name="mkDmgApp"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "appName").value,
        StringPrimitive(value="Energy"),
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
        "      --preserve-metadata=identifier,flags,runtime \\\n"
        "      --entitlements __NIX_INTERP__ \\\n"
        '      "$app_bundle"',
        '/usr/bin/codesign --verify --deep --strict "$app_bundle"',
    ]
    assert_nix_ast_equal(
        expect_binding(arguments.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )


def test_energy_ad_hoc_entitlements_exclude_vendor_identity_claims() -> None:
    """An ad-hoc signature cannot retain team-scoped restricted entitlements."""
    entitlements_path = REPO_ROOT / "packages/energy/Entitlements.plist"
    with entitlements_path.open("rb") as handle:
        entitlements = plistlib.load(handle)

    assert entitlements == {
        "com.apple.security.cs.allow-jit": True,
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
        "com.apple.security.cs.disable-library-validation": True,
        "com.apple.security.device.audio-input": True,
        "com.apple.security.device.camera": True,
        "com.apple.security.network.client": True,
    }
    assert "com.apple.application-identifier" not in entitlements
    assert "com.apple.developer.team-identifier" not in entitlements
    assert "com.apple.developer.web-browser.public-key-credential" not in entitlements
