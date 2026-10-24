"""The Bun overlay updater follows oven-sh/bun release binaries."""

from types import ModuleType
from typing import TYPE_CHECKING

from lib.system_policy import bun_artifact_names, supported_systems
from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import UpdateContext, VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

if TYPE_CHECKING:
    import pytest

_VERSION = "1.4.4"
_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _load_updater_module() -> ModuleType:
    return load_repo_module("overlays/bun/updater.py", "bun_updater_test")


def _asset_url(asset: str) -> str:
    return f"https://github.com/oven-sh/bun/releases/download/bun-v{_VERSION}/{asset}"


def test_bun_updater_tracks_official_release_binaries_for_exported_systems(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The latest oven-sh/bun tag supplies one binary hash per exported system."""
    module = _load_updater_module()
    updater = module.BunUpdater()
    expected_platforms = {
        system: bun_artifact_names()[system] for system in supported_systems()
    }
    assets = [
        {
            "name": asset,
            "browser_download_url": _asset_url(asset),
        }
        for asset in expected_platforms.values()
    ]
    assets.append({
        "name": "bun-linux-x64.zip",
        "browser_download_url": _asset_url("bun-linux-x64.zip"),
    })

    async def _fetch_github_api(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> dict[str, object]:
        assert path == "repos/oven-sh/bun/releases/latest"
        assert config == updater.config
        return {"tag_name": f"bun-v{_VERSION}", "assets": assets}

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _fetch_github_api,
    )

    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )
    hashes = dict.fromkeys(expected_platforms, _HASH)
    result = updater.build_result(info, hashes)

    assert expected_platforms == updater.PLATFORMS
    assert "x86_64-darwin" not in updater.PLATFORMS
    assert updater._asset_name(_VERSION, "bun-linux-x64-baseline.zip") == (
        "bun-linux-x64-baseline.zip"
    )
    assert info == VersionInfo(
        version=_VERSION,
        metadata=AssetURLsMetadata({
            system: _asset_url(asset) for system, asset in expected_platforms.items()
        }),
    )
    assert result.version == _VERSION
    assert result.urls == {
        system: _asset_url(asset) for system, asset in expected_platforms.items()
    }
    assert result.hashes.to_json() == hashes
    assert updater.derivation_validations == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=supported_systems(),
            mode="build",
        ),
    )
