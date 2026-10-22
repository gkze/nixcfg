"""Contracts for Agentastic.dev release discovery."""

import asyncio
from typing import TYPE_CHECKING

from lib.tests._updater_helpers import load_repo_module
from lib.update.updaters import UpdateContext
from lib.update.updaters.vendor_feeds import SparkleAppcastItem

if TYPE_CHECKING:
    import pytest


def test_agentastic_discovers_releases_from_the_installed_app_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale fixed pin must not hide a newer signed appcast release."""
    module = load_repo_module(
        "packages/agentastic-dev/updater.py", "agentastic_updater_test"
    )
    updater = module.AgentasticDevUpdater()
    url = "https://releases.agentastic.ai/agentasticdev/Agentastic.dev-0.14.dmg"
    requested = []

    async def fetch_items(_session: object, feed_url: str, **_kwargs: object):
        requested.append(feed_url)
        return (SparkleAppcastItem("14", "0.14", url),)

    monkeypatch.setattr(
        "lib.update.updaters.strategies.fetch_sparkle_appcast_items", fetch_items
    )
    latest = asyncio.run(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )

    assert requested == ["https://releases.agentastic.ai/agentasticdev/appcast.xml"]
    assert latest.version == "0.14"
    assert updater.get_download_url("aarch64-darwin", latest) == url
