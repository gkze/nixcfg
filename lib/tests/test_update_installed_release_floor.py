"""Release discovery must respect already deployed vendor release cohorts."""

from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from lib.nix.models.sources import SourceEntry
from lib.tests._updater_helpers import load_repo_module_for_test, run_async
from lib.update.updaters import UpdateContext

if TYPE_CHECKING:
    from lib.update.updaters.platform_api import DownloadingPlatformAPIUpdater


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


def _cursor_urls(version: str, commit: str) -> dict[str, str]:
    base = f"https://downloads.cursor.com/production/{commit}"
    return {
        "aarch64-darwin": f"{base}/darwin/arm64/Cursor-darwin-arm64.dmg",
        "x86_64-darwin": f"{base}/darwin/x64/Cursor-darwin-x64.dmg",
        "aarch64-linux": f"{base}/linux/arm64/Cursor-{version}-aarch64.AppImage",
        "x86_64-linux": f"{base}/linux/x64/Cursor-{version}-x86_64.AppImage",
    }


def _cursor_feed(
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    *,
    urls: dict[str, str] | None = None,
) -> DownloadingPlatformAPIUpdater:
    module = load_repo_module_for_test(
        "overlays/code-cursor/updater.py", prefix="floor"
    )
    updater = module.CodeCursorUpdater()
    urls = _cursor_urls(version, "b" * 40) if urls is None else urls

    async def fetch(_session: object, _url: str, **_kwargs: object) -> bytes:
        return " ".join(
            f'"{updater._api_url(p)}"' for p in updater.PLATFORMS.values()
        ).encode()

    async def resolve(_session: object, url: str) -> str:
        api_platform = url.split("/golden/", 1)[1].split("/", 1)[0]
        platform = next(
            p for p, api in updater.PLATFORMS.items() if api == api_platform
        )
        return urls[platform]

    monkeypatch.setattr(module, "fetch_url", fetch)
    monkeypatch.setattr(updater, "_resolve_download_url", resolve)
    return updater


def test_cursor_accepts_complete_40_character_production_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recorded 3.27.6 artifact segment is 40 characters, not 41."""
    commit = "f7119520544accb8dcf6d27119b9f1963d8ee5ae"
    assert len(commit) == 40
    urls = _cursor_urls("3.27.6", commit)
    updater = _cursor_feed(monkeypatch, "3.27.6", urls=urls)
    current = SourceEntry.model_validate({"version": "3.26.16", "hashes": {}})
    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=current))
    )
    assert info.version == "3.27.6"
    assert info.commit == commit
    assert {
        platform: data["downloadUrl"]
        for platform, data in updater._metadata(info).platform_info.items()
    } == urls


@pytest.mark.parametrize(
    "malformed_platform", ["all", "aarch64-darwin", "aarch64-linux"]
)
def test_cursor_rejects_true_41_character_production_commit(
    monkeypatch: pytest.MonkeyPatch, malformed_platform: str
) -> None:
    commit = "f7119520544accb8dcf6d27119b9f1963d8ee5ae"
    malformed = commit + "0"
    assert len(malformed) == 41
    urls = _cursor_urls("3.27.6", commit)
    invalid = _cursor_urls("3.27.6", malformed)
    if malformed_platform == "all":
        urls = invalid
    else:
        urls[malformed_platform] = invalid[malformed_platform]
    updater = _cursor_feed(monkeypatch, "3.27.6", urls=urls)
    current = SourceEntry.model_validate({"version": "3.26.16", "hashes": {}})
    with pytest.raises(RuntimeError, match="Unable to resolve one Cursor"):
        run_async(
            updater.fetch_latest(object(), context=UpdateContext(current=current))
        )
    pinned_urls = _cursor_urls("3.26.16", "a" * 40)
    current = SourceEntry.model_validate({
        "version": "3.26.16",
        "commit": "a" * 40,
        "urls": pinned_urls,
        "hashes": {},
    })
    retained = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=current))
    )
    assert retained.version == current.version
    assert retained.commit == current.commit
    assert {
        platform: data["downloadUrl"]
        for platform, data in updater._metadata(retained).platform_info.items()
    } == pinned_urls


@pytest.mark.parametrize("feed_version", ["3.26.16", "3.27.7"])
def test_cursor_lagging_feed_retains_verified_cohort(
    monkeypatch: pytest.MonkeyPatch, feed_version: str
) -> None:
    updater = _cursor_feed(monkeypatch, feed_version)
    pinned_urls = _cursor_urls("3.27.6", "a" * 40)
    current = SourceEntry.model_validate({
        "version": "3.27.6",
        "commit": "a" * 40,
        "urls": pinned_urls,
        "hashes": {},
    })
    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=current))
    )
    retained = feed_version == "3.26.16"
    assert info.version == ("3.27.6" if retained else feed_version)
    assert info.commit == ("a" * 40 if retained else "b" * 40)
    urls = {
        p: data["downloadUrl"]
        for p, data in updater._metadata(info).platform_info.items()
    }
    assert urls == (pinned_urls if retained else _cursor_urls(feed_version, "b" * 40))


@pytest.mark.parametrize(
    "fault", ["missing_platform", "wrong_commit", "wrong_version", "overlong_commit"]
)
def test_cursor_cannot_retain_incoherent_source_pin(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    updater = _cursor_feed(monkeypatch, "3.26.16")
    pinned_urls = _cursor_urls("3.27.6", "a" * 40)
    if fault == "missing_platform":
        del pinned_urls["x86_64-darwin"]
    elif fault == "wrong_commit":
        pinned_urls["aarch64-darwin"] = _cursor_urls("3.27.6", "c" * 40)[
            "aarch64-darwin"
        ]
    elif fault == "overlong_commit":
        pinned_urls["aarch64-darwin"] = _cursor_urls("3.27.6", "a" * 41)[
            "aarch64-darwin"
        ]
    else:
        pinned_urls["aarch64-linux"] = _cursor_urls("3.26.16", "a" * 40)[
            "aarch64-linux"
        ]
    current = SourceEntry.model_validate({
        "version": "3.27.6",
        "commit": "a" * 40,
        "urls": pinned_urls,
        "hashes": {},
    })
    with pytest.raises(RuntimeError, match="Unable to resolve one Cursor"):
        run_async(
            updater.fetch_latest(object(), context=UpdateContext(current=current))
        )


def test_cursor_retained_artifact_download_failure_is_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _cursor_feed(monkeypatch, "3.26.16")
    pinned_urls = _cursor_urls("3.27.6", "a" * 40)
    current = SourceEntry.model_validate({
        "version": "3.27.6",
        "commit": "a" * 40,
        "urls": pinned_urls,
        "hashes": {},
    })
    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=current))
    )

    async def fail(_name: str, urls: object, **_kwargs: object) -> dict[str, str]:
        assert urls == pinned_urls
        msg = "publisher artifact unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        "lib.update.updaters.platform_api.stream_url_hash_mapping", fail
    )
    with pytest.raises(RuntimeError, match="publisher artifact unavailable"):
        run_async(updater.fetch_checksums(info, object()))
