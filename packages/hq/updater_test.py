"""Behavioral tests for the HQ release updater."""

from types import ModuleType
from typing import TYPE_CHECKING

from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

if TYPE_CHECKING:
    import pytest

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

    info = run_async(updater.fetch_latest(object()))
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
