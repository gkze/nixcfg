"""Release discovery must respect already deployed vendor release cohorts."""

import json
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


class _TrackResponse:
    """Minimal transfer response; the channel client reads only status and body."""

    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self.body = body


def _cursor_urls(version: str, commit: str) -> dict[str, str]:
    base = f"https://downloads.cursor.com/production/{commit}"
    return {
        "aarch64-darwin": f"{base}/darwin/arm64/Cursor-darwin-arm64.dmg",
        "x86_64-darwin": f"{base}/darwin/x64/Cursor-darwin-x64.dmg",
        "aarch64-linux": f"{base}/linux/arm64/Cursor-{version}-aarch64.AppImage",
        "x86_64-linux": f"{base}/linux/x64/Cursor-{version}-x86_64.AppImage",
    }


def _channel_payload(api_platform: str, version: str, artifact: str) -> bytes:
    """Render the channel entry that publishes ``artifact`` for one platform."""
    if api_platform.startswith("darwin"):
        archive = artifact.removesuffix(".dmg") + ".zip"
        return json.dumps({"name": version, "url": archive}).encode()
    zsync = artifact + ".zsync"
    return json.dumps({
        "version": version,
        "productVersion": version,
        "url": zsync,
    }).encode()


def _nix_platform(updater: DownloadingPlatformAPIUpdater, api_platform: str) -> str:
    return next(p for p, a in updater.PLATFORMS.items() if a == api_platform)


def _cursor_feed(
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    *,
    urls: dict[str, str] | None = None,
    payloads: dict[str, bytes] | None = None,
    statuses: dict[str, int] | None = None,
) -> tuple[DownloadingPlatformAPIUpdater, list[str]]:
    """Serve one release track, with overrides keyed by nix platform."""
    module = load_repo_module_for_test(
        "overlays/code-cursor/updater.py", prefix="floor"
    )
    updater = module.CodeCursorUpdater()
    artifacts = _cursor_urls(version, "b" * 40) if urls is None else urls
    requested: list[str] = []

    async def fetch(_session: object, url: str, **_kwargs: object) -> _TrackResponse:
        api_platform = url.split("/update/", 1)[1].split("/", 1)[0]
        requested.append(url)
        platform = _nix_platform(updater, api_platform)
        if statuses is not None and platform in statuses:
            return _TrackResponse(statuses[platform])
        if payloads is not None and platform in payloads:
            return _TrackResponse(200, payloads[platform])
        return _TrackResponse(
            200, _channel_payload(api_platform, version, artifacts[platform])
        )

    monkeypatch.setattr(module, "request", fetch)
    return updater, requested


def test_cursor_discovers_newest_build_from_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater, requested = _cursor_feed(monkeypatch, "3.27.18")
    info = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )
    assert info.version == "3.27.18"
    assert info.commit == "b" * 40
    assert info.metadata["commitSha"] == "b" * 40
    assert len(requested) == 4
    # The channel answers 204 for a current version, so discovery asks about a floor.
    assert all("/cursor/0.0.0/" in url for url in requested)
    assert all(url.endswith("/dev") for url in requested)
    assert {
        platform: data["downloadUrl"]
        for platform, data in updater._metadata(info).platform_info.items()
    } == _cursor_urls("3.27.18", "b" * 40)


def test_cursor_accepts_complete_40_character_production_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recorded 3.27.6 artifact segment is 40 characters, not 41."""
    commit = "f7119520544accb8dcf6d27119b9f1963d8ee5ae"
    assert len(commit) == 40
    urls = _cursor_urls("3.27.6", commit)
    updater, _requested = _cursor_feed(monkeypatch, "3.27.6", urls=urls)
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
    updater, _requested = _cursor_feed(monkeypatch, "3.27.6", urls=urls)
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
    updater, _requested = _cursor_feed(monkeypatch, feed_version)
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
    updater, _requested = _cursor_feed(monkeypatch, "3.26.16")
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
    updater, _requested = _cursor_feed(monkeypatch, "3.26.16")
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


def test_cursor_retains_when_platform_versions_disagree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ahead = _channel_payload(
        "darwin-arm64",
        "3.27.19",
        _cursor_urls("3.27.19", "b" * 40)["aarch64-darwin"],
    )
    updater, _requested = _cursor_feed(
        monkeypatch, "3.27.18", payloads={"aarch64-darwin": ahead}
    )
    with pytest.raises(RuntimeError, match="Unable to resolve one Cursor"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))


def test_cursor_reports_channel_with_no_newer_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater, _requested = _cursor_feed(
        monkeypatch, "3.27.18", statuses={"aarch64-darwin": 204}
    )
    with pytest.raises(RuntimeError, match="reported no build newer"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))


def test_cursor_rejects_non_object_channel_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater, _requested = _cursor_feed(
        monkeypatch, "3.27.18", payloads={"aarch64-darwin": b"[]"}
    )
    with pytest.raises(TypeError, match="Expected a JSON object"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))


def test_cursor_rejects_entry_without_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({"name": "3.27.18"}).encode()
    updater, _requested = _cursor_feed(
        monkeypatch, "3.27.18", payloads={"aarch64-darwin": payload}
    )
    with pytest.raises(TypeError, match="named no artifact"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))


def test_cursor_rejects_entry_without_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "b" * 40
    payload = json.dumps({
        "url": (
            f"https://downloads.cursor.com/production/{commit}/darwin/arm64/"
            "Cursor-darwin-arm64.zip"
        )
    }).encode()
    updater, _requested = _cursor_feed(
        monkeypatch, "3.27.18", payloads={"aarch64-darwin": payload}
    )
    with pytest.raises(TypeError, match="named no version"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))


@pytest.mark.parametrize(
    ("platform", "artifact"),
    [
        ("aarch64-darwin", "darwin/arm64/Cursor-darwin-arm64.dmg"),
        ("aarch64-linux", "linux/arm64/Cursor-3.27.18-aarch64.AppImage"),
    ],
)
def test_cursor_rejects_unexpected_artifact_shape(
    monkeypatch: pytest.MonkeyPatch, platform: str, artifact: str
) -> None:
    commit = "b" * 40
    payload = json.dumps({
        "name": "3.27.18",
        "version": "3.27.18",
        "url": f"https://downloads.cursor.com/production/{commit}/{artifact}",
    }).encode()
    updater, _requested = _cursor_feed(
        monkeypatch, "3.27.18", payloads={platform: payload}
    )
    with pytest.raises(RuntimeError, match="Unexpected Cursor"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))


def test_cursor_rejects_staged_linux_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "b" * 40
    staged = json.dumps({
        "version": "3.27.18",
        "productVersion": "3.27.18",
        "url": (
            f"https://downloads.cursor.com/production/{commit}/linux/arm64/"
            "Cursor-3.27.17-aarch64.AppImage.zsync"
        ),
    }).encode()
    updater, _requested = _cursor_feed(
        monkeypatch, "3.27.18", payloads={"aarch64-linux": staged}
    )
    with pytest.raises(RuntimeError, match="channel named 3.27.18 but published"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))
