"""Release discovery must respect already deployed vendor release cohorts."""

from types import ModuleType

import pytest

from lib.nix.models.sources import SourceEntry
from lib.tests._updater_helpers import load_repo_module_for_test, run_async
from lib.update.updaters import UpdateContext


def test_figma_requests_current_release_cohort(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_repo_module_for_test("packages/figma/updater.py", prefix="floor")
    requested = []

    async def fetch(_session: object, url: str, **_kwargs: object) -> dict[str, str]:
        requested.append(url)
        return {"version": "126.10.11", "url": "https://example.test/Figma.zip"}

    monkeypatch.setattr(module, "fetch_json", fetch)
    current = SourceEntry.model_validate({"version": "126.10.11", "hashes": {}})
    info = run_async(
        module.FigmaUpdater().fetch_latest(
            object(), context=UpdateContext(current=current)
        )
    )
    assert info.version == "126.10.11"
    assert len(requested) == 2
    assert all(url.endswith("?localVersion=126.10.11") for url in requested)


@pytest.mark.parametrize("current_version", ["3.26.16", "3.8.9"])
def test_cursor_resolves_newer_of_page_and_current_series(
    monkeypatch: pytest.MonkeyPatch, current_version: str
) -> None:
    module: ModuleType = load_repo_module_for_test(
        "overlays/code-cursor/updater.py", prefix="floor"
    )
    updater = module.CodeCursorUpdater()
    requested = []
    expected_series = "3.26" if current_version == "3.26.16" else "3.24"
    expected_version = expected_series + ".16"
    commit = "a" * 40
    page = " ".join(
        f'"{updater._api_url(p, "3.24")}"' for p in updater.PLATFORMS.values()
    )

    async def fetch(_session: object, _url: str, **_kwargs: object) -> bytes:
        return page.encode()

    async def resolve(_session: object, url: str) -> str:
        requested.append(url)
        platform = url.split("/golden/", 1)[1].split("/", 1)[0]
        file = (
            f"Cursor-{expected_version}-x86_64.AppImage"
            if "linux" in platform
            else "Cursor.dmg"
        )
        return f"https://downloads.cursor.com/production/{commit}/{platform}/{file}"

    monkeypatch.setattr(module, "fetch_url", fetch)
    monkeypatch.setattr(updater, "_resolve_download_url", resolve)
    current = SourceEntry.model_validate({"version": current_version, "hashes": {}})
    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=current))
    )
    assert info.version == expected_version
    assert info.commit == commit
    assert len(requested) == 4
    assert all(url.endswith("/cursor/" + expected_series) for url in requested)


def test_cursor_accepts_overlong_production_commit_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 41-character production path must still name the newer build.

    Cursor publishes 3.27.6 under ``production/<41 hex>/``. Requiring exactly
    40 characters made the commit unresolvable, so the updater treated the
    release as unstaged and kept the older pin forever.
    """
    module: ModuleType = load_repo_module_for_test(
        "overlays/code-cursor/updater.py", prefix="floor"
    )
    updater = module.CodeCursorUpdater()
    commit = "f7119520544accb8dcf6d27119b9f1963d8ee5ae"
    page = " ".join(
        f'"{updater._api_url(p, "3.24")}"' for p in updater.PLATFORMS.values()
    )

    async def fetch(_session: object, _url: str, **_kwargs: object) -> bytes:
        return page.encode()

    async def resolve(_session: object, url: str) -> str:
        platform = url.split("/golden/", 1)[1].split("/", 1)[0]
        file = "Cursor-3.27.6-x86_64.AppImage" if "linux" in platform else "Cursor.dmg"
        return f"https://downloads.cursor.com/production/{commit}/{platform}/{file}"

    monkeypatch.setattr(module, "fetch_url", fetch)
    monkeypatch.setattr(updater, "_resolve_download_url", resolve)
    current = SourceEntry.model_validate({"version": "3.26.16", "hashes": {}})
    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=current))
    )
    assert info.version == "3.27.6"
    assert info.commit == commit[:40]


def test_cursor_keeps_pin_when_channel_offers_older_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully resolved but older build must not rewrite the pin downwards."""
    module: ModuleType = load_repo_module_for_test(
        "overlays/code-cursor/updater.py", prefix="floor"
    )
    updater = module.CodeCursorUpdater()
    pinned_commit = "a" * 40
    pinned_urls = {
        platform: f"https://downloads.cursor.com/production/{pinned_commit}/{platform}/Cursor.dmg"
        for platform in updater.PLATFORMS
    }
    older_commit = "8fcb8ace5c73fa31f2e2707fd6783160e4ed3e49"
    page = " ".join(
        f'"{updater._api_url(p, "3.24")}"' for p in updater.PLATFORMS.values()
    )

    async def fetch(_session: object, _url: str, **_kwargs: object) -> bytes:
        return page.encode()

    async def resolve(_session: object, url: str) -> str:
        platform = url.split("/golden/", 1)[1].split("/", 1)[0]
        file = "Cursor-3.26.16-x86_64.AppImage" if "linux" in platform else "Cursor.dmg"
        return (
            f"https://downloads.cursor.com/production/{older_commit}/{platform}/{file}"
        )

    monkeypatch.setattr(module, "fetch_url", fetch)
    monkeypatch.setattr(updater, "_resolve_download_url", resolve)
    current = SourceEntry.model_validate({
        "version": "3.27.6",
        "commit": pinned_commit,
        "urls": pinned_urls,
        "hashes": {},
    })
    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=current))
    )
    assert info.version == "3.27.6"
    assert info.commit == pinned_commit
    platform_info = info.metadata.platform_info
    assert platform_info == {
        platform: {"downloadUrl": url} for platform, url in pinned_urls.items()
    }
