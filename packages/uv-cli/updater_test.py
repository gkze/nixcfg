"""UV release selection keeps the platform matrix on the same stable version."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext


def test_future_release_selects_all_native_archives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = load_repo_module(
        "packages/uv-cli/updater.py", "uv_cli_test"
    ).UvCliUpdater()

    async def payload(_session: object):
        return {
            "tag_name": "0.99.0",
            "assets": [
                {
                    "name": f"uv-{triple}.tar.gz",
                    "browser_download_url": f"https://example.test/uv-{triple}.tar.gz",
                }
                for triple in updater.PLATFORMS.values()
            ],
        }

    monkeypatch.setattr(updater, "_fetch_latest_release_payload", payload)
    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )
    assert info.version == "0.99.0"
    assert set(updater.PLATFORMS) == {
        "aarch64-darwin",
        "x86_64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    }
    for platform, triple in updater.PLATFORMS.items():
        assert (
            updater.get_download_url(platform, info)
            == f"https://example.test/uv-{triple}.tar.gz"
        )
