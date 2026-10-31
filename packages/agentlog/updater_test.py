"""Update contract for the Agentlog package."""

from types import ModuleType
from typing import TYPE_CHECKING

from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext, VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pytest

_VERSION = "0.4.2"
_ASSET_NAME = "Agentlog_0.4.2_aarch64.dmg"
_URL = (
    "https://github.com/jordienr/agentlog-releases/releases/download/"
    f"v{_VERSION}/{_ASSET_NAME}"
)
_HASH = "sha256-X12FJkdkstnJBfixcohsEiEsoUK1l4QUkiWwEYBMcJI="


def _load_updater() -> ModuleType:
    return load_repo_module(
        "packages/agentlog/updater.py",
        "agentlog_updater_contract_test",
    )


def test_updater_pins_the_official_arm64_release_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Updates should retain the immutable, architecture-specific DMG."""
    module = _load_updater()
    updater = module.AgentlogUpdater()

    async def _fetch_release(
        _session: object,
        endpoint: str,
        *,
        config: object,
    ) -> Mapping[str, object]:
        assert endpoint == "repos/jordienr/agentlog-releases/releases/latest"
        assert config is updater.config
        return {
            "tag_name": f"v{_VERSION}",
            "assets": [{"name": _ASSET_NAME, "browser_download_url": _URL}],
        }

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _fetch_release,
    )

    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )
    result = updater.build_result(info, {"aarch64-darwin": _HASH})

    assert updater.PLATFORMS == {"aarch64-darwin": "aarch64"}
    assert info == VersionInfo(
        version=_VERSION,
        metadata=AssetURLsMetadata({"aarch64-darwin": _URL}),
    )
    assert result.urls == {"aarch64-darwin": _URL}
    assert result.hashes.to_json() == {"aarch64-darwin": _HASH}
