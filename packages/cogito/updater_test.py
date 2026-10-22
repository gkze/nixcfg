"""Cogito must discover future releases even while today's pin is current."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext
from lib.update.updaters.vendor_feeds import SparkleAppcastItem


def test_cogito_tracks_installed_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_repo_module("packages/cogito/updater.py", "cogito_latest_test")
    updater = module.CogitoUpdater()
    url = "https://downloads.cogito.md/releases/0.13.0/1/Cogito-0.13.0-1-mac.zip"

    async def fetch(_session: object, endpoint: str, **_kwargs: object):
        assert endpoint == "https://cogito.md/updates/appcast.xml"
        return (SparkleAppcastItem("13000001", "0.13.0", url),)

    monkeypatch.setattr(
        "lib.update.updaters.strategies.fetch_sparkle_appcast_items", fetch
    )
    latest = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )
    assert latest.version == "0.13.0"
    assert updater.get_download_url("aarch64-darwin", latest) == url
