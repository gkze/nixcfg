"""Behavioral tests for the HQ release updater."""

import plistlib
from pathlib import Path
from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module, run_async
from lib.tests.test_hq_package import (
    _CURRENT_AUTOMATIC_MUTATION_PATHS,
    _CURRENT_DISABLED_MUTATION_PATHS,
    _load_hq_module,
    _mutation_fixture,
)
from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import UpdateContext, VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

_VERSION = "0.10.155"
_ARTIFACT_NAME = f"HQ_{_VERSION}_universal.app.tar.gz"
_ARTIFACT_URL = (
    "https://github.com/indigoai-us/hq-desktop-app/releases/download/"
    f"v{_VERSION}/{_ARTIFACT_NAME}"
)
_HASH = "sha256-eKmJjRUNIpMrTQEyve04szcRvzE9GVoRkF9NezD19uU="


def _load_updater_module() -> ModuleType:
    return load_repo_module("packages/hq/updater.py", "hq_updater_test")


def test_hq_updater_tracks_the_exact_universal_release_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the immutable versioned official archive may enter sources.json."""
    module = _load_updater_module()
    updater = module.HQUpdater()

    async def _fetch_github_api(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> dict[str, object]:
        assert path == "repos/indigoai-us/hq-desktop-app/releases/latest"
        assert config == updater.config
        return {
            "tag_name": f"v{_VERSION}",
            "assets": [
                {
                    "name": _ARTIFACT_NAME,
                    "browser_download_url": _ARTIFACT_URL,
                }
            ],
        }

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _fetch_github_api,
    )

    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )
    result = updater.build_result(info, {"aarch64-darwin": _HASH})

    assert updater.PLATFORMS == {"aarch64-darwin": "universal"}
    assert updater._asset_name(_VERSION, "universal") == _ARTIFACT_NAME
    assert info == VersionInfo(
        version=_VERSION,
        metadata=AssetURLsMetadata({"aarch64-darwin": _ARTIFACT_URL}),
    )
    assert result.urls == {"aarch64-darwin": _ARTIFACT_URL}
    assert result.hashes.to_json() == {"aarch64-darwin": _HASH}


def test_hq_updater_build_validates_the_materialized_darwin_package() -> None:
    """Promotion must build the exact HQ package after persisting new metadata."""
    updater = _load_updater_module().HQUpdater()

    assert updater.get_derivation_validations() == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )


@pytest.mark.parametrize("copies", [0, 1, 2])
def test_hq_0339_paired_load_guard_is_unique_and_preserves_its_target(
    tmp_path: Path, copies: int
) -> None:
    """The audited paired loads must not loosen the one-guard policy inventory."""
    module = _load_hq_module("patch_updater.py", "hq_0339_patch_test")
    validator = _load_hq_module("validate_artifact.py", "hq_0339_validator_test")
    # HQ 0.10.339 arm64 at file offset 0x35b9a3c, including its report arguments.
    guard = bytes.fromhex(
        "d8 b3 07 94 60 86 53 a9 48 37 d8 97 00 09 00 36 "
        "61 8a 53 a9 43 5b 00 f0 63 c8 0f 91 e0 03 13 aa 64 02 80 52"
    )
    paths = (
        *_CURRENT_AUTOMATIC_MUTATION_PATHS[:3],
        *([guard] * copies),
        *_CURRENT_AUTOMATIC_MUTATION_PATHS[4:],
    )
    payload = _mutation_fixture(module, paths)
    if copies != 1:
        with pytest.raises(ValueError, match="arm64 hq-core install guard inventory"):
            module.patch_payload(payload)
        return

    patched = module.patch_payload(payload)
    offset = payload.index(guard)
    assert len(patched) == len(payload)
    assert patched[offset : offset + 12] == guard[:12]
    assert patched[offset + 16 : offset + len(guard)] == guard[16:]
    branch = int.from_bytes(patched[offset + 12 : offset + 16], "little")
    assert branch >> 26 == 0b000101
    assert 12 + ((branch & 0x03FFFFFF) << 2) == 0x12C

    executable = tmp_path / "hq-sync-menubar"
    executable.write_bytes(patched)
    info = tmp_path / "Info.plist"
    info.write_bytes(
        plistlib.dumps({
            "CFBundleExecutable": "hq-sync-menubar",
            "CFBundleIdentifier": "ai.indigo.hq-sync-menubar",
            "CFBundleShortVersionString": "0.10.339",
            "CFBundleVersion": "0.10.339",
            "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True,
        })
    )
    validator.validate_artifact(
        info_plist=info,
        main_executable=executable,
        expected_version="0.10.339",
    )
    mixed = _mutation_fixture(module, (*paths, _CURRENT_AUTOMATIC_MUTATION_PATHS[3]))
    with pytest.raises(ValueError, match="inventory drifted: expected 1, got 2"):
        module.patch_payload(mixed)
    executable.write_bytes(payload)
    with pytest.raises(ValueError, match="app-owned update URL"):
        validator.validate_artifact(
            info_plist=info,
            main_executable=executable,
            expected_version="0.10.339",
        )


_GENERIC_ARM64_RETURN_FALSE_STUB = bytes.fromhex(
    "00 00 80 52 c0 03 5f d6 1f 20 03 d5 "
    "1f 20 03 d5 1f 20 03 d5 1f 20 03 d5 "
    "1f 20 03 d5"
)


def test_hq_arm64_auto_update_gate_disabled_signature_is_unique_against_compiler_stubs() -> (
    None
):
    """0.10.373+ compiler return-false stubs must not collide with the brand."""
    module = _load_hq_module("patch_updater.py", "hq_0373_gate_unique_test")
    payload = _mutation_fixture(
        module,
        (
            *_CURRENT_AUTOMATIC_MUTATION_PATHS,
            _GENERIC_ARM64_RETURN_FALSE_STUB,
            _GENERIC_ARM64_RETURN_FALSE_STUB,
            _GENERIC_ARM64_RETURN_FALSE_STUB,
        ),
    )

    patched = module.patch_payload(payload)

    assert patched.count(_GENERIC_ARM64_RETURN_FALSE_STUB) == 3
    assert patched.count(_CURRENT_DISABLED_MUTATION_PATHS[1]) == 1


@pytest.mark.parametrize("copies", [0, 1, 2])
def test_hq_0375_staging_guard_is_unique_and_preserves_its_target(
    tmp_path: Path, copies: int
) -> None:
    """0.10.375's mov x19 state-base reload must stay a single staging guard."""
    module = _load_hq_module("patch_updater.py", "hq_0375_staging_patch_test")
    validator = _load_hq_module("validate_artifact.py", "hq_0375_staging_validator_test")
    # HQ 0.10.375 arm64 staging at file offset 0x37d32bc.
    guard = bytes.fromhex(
        "50 c8 07 94 60 86 54 a9 d4 13 d6 97 60 41 00 36 "
        "61 8a 54 a9 63 92 51 a9 e0 03 13 aa e3 7c 07 94 "
        "e8 83 0f 91"
    )
    paths = (
        *_CURRENT_AUTOMATIC_MUTATION_PATHS[:5],
        *([guard] * copies),
        *_CURRENT_AUTOMATIC_MUTATION_PATHS[6:],
    )
    payload = _mutation_fixture(module, paths)
    if copies != 1:
        with pytest.raises(
            ValueError, match="arm64 staging hq-core install guard inventory"
        ):
            module.patch_payload(payload)
        return

    patched = module.patch_payload(payload)
    offset = payload.index(guard)
    assert len(patched) == len(payload)
    assert patched[offset : offset + 12] == guard[:12]
    assert patched[offset + 16 : offset + len(guard)] == guard[16:]
    branch = int.from_bytes(patched[offset + 12 : offset + 16], "little")
    assert branch >> 26 == 0b000101
    assert 12 + ((branch & 0x03FFFFFF) << 2) == 0x838

    executable = tmp_path / "hq-sync-menubar"
    executable.write_bytes(patched)
    info = tmp_path / "Info.plist"
    info.write_bytes(
        plistlib.dumps({
            "CFBundleExecutable": "hq-sync-menubar",
            "CFBundleIdentifier": "ai.indigo.hq-sync-menubar",
            "CFBundleShortVersionString": "0.10.375",
            "CFBundleVersion": "0.10.375",
            "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True,
        })
    )
    validator.validate_artifact(
        info_plist=info,
        main_executable=executable,
        expected_version="0.10.375",
    )
    mixed = _mutation_fixture(module, (*paths, _CURRENT_AUTOMATIC_MUTATION_PATHS[5]))
    with pytest.raises(ValueError, match="inventory drifted: expected 1, got 2"):
        module.patch_payload(mixed)
