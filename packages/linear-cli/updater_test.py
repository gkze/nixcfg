"""Linear CLI's native release selection preserves platform coverage."""

from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.planner import held_bulk_update_input_names
from lib.update.updaters import UpdateContext


def _module() -> ModuleType:
    return load_repo_module("packages/linear-cli/updater.py", "linear_native_test")


def _release() -> dict[str, object]:
    triples = (
        "aarch64-apple-darwin",
        "aarch64-unknown-linux-gnu",
        "x86_64-apple-darwin",
        "x86_64-unknown-linux-gnu",
    )
    return {
        "tag_name": "v4.5.6",
        "assets": [
            {
                "name": f"linear-{triple}.tar.xz",
                "browser_download_url": f"https://example.test/v4.5.6/linear-{triple}.tar.xz",
            }
            for triple in triples
        ],
    }


def test_latest_release_selects_native_archives_for_all_existing_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _module().LinearCliUpdater()

    async def payload(_session: object):
        return _release()

    monkeypatch.setattr(updater, "_fetch_latest_release_payload", payload)
    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )
    assert info.version == "4.5.6"
    assert {
        platform: updater.get_download_url(platform, info)
        for platform in updater.PLATFORMS
    } == {
        "aarch64-darwin": "https://example.test/v4.5.6/linear-aarch64-apple-darwin.tar.xz",
        "aarch64-linux": "https://example.test/v4.5.6/linear-aarch64-unknown-linux-gnu.tar.xz",
        "x86_64-darwin": "https://example.test/v4.5.6/linear-x86_64-apple-darwin.tar.xz",
        "x86_64-linux": "https://example.test/v4.5.6/linear-x86_64-unknown-linux-gnu.tar.xz",
    }
    assert held_bulk_update_input_names({"linear-cli": type(updater)}) == set()


def test_missing_platform_asset_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    updater = _module().LinearCliUpdater()

    async def payload(_session: object):
        release = _release()
        release["assets"] = []
        return release

    monkeypatch.setattr(updater, "_fetch_latest_release_payload", payload)
    with pytest.raises(RuntimeError, match="linear-aarch64-apple-darwin.tar.xz"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))
