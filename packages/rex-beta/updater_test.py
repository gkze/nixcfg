"""Rex Beta must advance its version token for every snapshot build."""

import pytest

from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext
from lib.update.updaters.vendor_feeds import SparkleAppcastItem

_APPCAST = "https://blob.superlogical.com/public/rex-app/snapshot-arm64/appcast.xml"


def _updater():
    module = load_repo_module("packages/rex-beta/updater.py", "rex_beta_latest_test")
    return module.RexBetaUpdater()


def test_rex_beta_combines_short_version_and_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The snapshot build keeps the token unique while 0.1.0 stays the marketing version."""
    updater = _updater()
    url = (
        "https://blob.superlogical.com/public/rex-app/snapshot-arm64/Rex-0.1.0-1700.dmg"
    )

    async def fetch(_session: object, endpoint: str, **_kwargs: object):
        assert endpoint == _APPCAST
        return (SparkleAppcastItem("1700", "0.1.0", url),)

    monkeypatch.setattr(
        "lib.update.updaters.strategies.fetch_sparkle_appcast_items", fetch
    )
    latest = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )

    assert latest.version == "0.1.0-1700"
    assert updater.get_download_url("aarch64-darwin", latest) == url


def test_rex_beta_rejects_an_item_without_short_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing marketing version must fail instead of emitting a bare build number."""
    updater = _updater()

    async def fetch(_session: object, endpoint: str, **_kwargs: object):
        assert endpoint == _APPCAST
        return (SparkleAppcastItem("1700", None, "https://example.invalid/Rex.dmg"),)

    monkeypatch.setattr(
        "lib.update.updaters.strategies.fetch_sparkle_appcast_items", fetch
    )

    with pytest.raises(RuntimeError, match="Missing version"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))
