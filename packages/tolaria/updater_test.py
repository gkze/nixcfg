"""Tolaria release discovery follows the publisher's dated asset schema."""

from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext


def _module() -> ModuleType:
    return load_repo_module("packages/tolaria/updater.py", "tolaria_latest_test")


def test_latest_release_uses_matching_native_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _module().TolariaUpdater()
    url = "https://github.com/refactoringhq/tolaria/releases/download/v2026-10-13/Tolaria_2026.10.13_macOS_Silicon.app.tar.gz"

    async def payload(_session: object):
        return {
            "tag_name": "v2026-10-13",
            "assets": [
                {
                    "name": "Tolaria_2026.10.13_macOS_Silicon.app.tar.gz",
                    "browser_download_url": url,
                }
            ],
        }

    monkeypatch.setattr(updater, "_fetch_latest_release_payload", payload)
    latest = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )
    assert latest.version == "2026.10.13"
    assert updater.get_download_url("aarch64-darwin", latest) == url


@pytest.mark.parametrize("tag", ["bad", "v2026-02-31", "v2026.10.13"])
def test_invalid_release_dates_fail_closed(tag: str) -> None:
    with pytest.raises((ValueError, RuntimeError)):
        _module().TolariaUpdater()._normalize_release_version(tag)
