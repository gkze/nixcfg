"""Focused tests for the overlay-only updater lane."""

from __future__ import annotations

import asyncio

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import install_fixed_hash_stream
from lib.tests._updater_helpers import load_repo_module as _load_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.metadata import (
    DownloadUrlMetadata,
    PlatformAPIMetadata,
    ReleasePayloadMetadata,
)

HASH_A = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
HASH_B = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


def test_chatgpt_fetch_latest_passes_expected_request_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChatGPT should fetch the Sparkle appcast with the expected client settings."""
    module = _load_module("overlays/chatgpt/updater.py", "chatgpt_lane_test")
    updater = module.ChatGPTUpdater()
    captured: dict[str, object] = {}

    async def _fetch_url(
        session, url: str, *, user_agent: str, timeout: object, config
    ):
        captured.update({
            "session": session,
            "url": url,
            "user_agent": user_agent,
            "timeout": timeout,
            "config": config,
        })
        return (
            b'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
            b"<channel><item>"
            b"<sparkle:shortVersionString>1.2.3</sparkle:shortVersionString>"
            b'<enclosure url="https://example.com/chatgpt.dmg" />'
            b"</item></channel></rss>"
        )

    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    session = object()
    latest = _run(updater.fetch_latest(session))

    assert latest.version == "1.2.3"
    assert latest.metadata == DownloadUrlMetadata(url="https://example.com/chatgpt.dmg")
    assert captured == {
        "session": session,
        "url": updater.APPCAST_URL,
        "user_agent": "Sparkle/2.0",
        "timeout": updater.config.default_timeout,
        "config": updater.config,
    }


def test_chatgpt_get_download_url_requires_typed_metadata() -> None:
    """ChatGPT should fail clearly when download metadata is missing."""
    module = _load_module("overlays/chatgpt/updater.py", "chatgpt_lane_bad_metadata")
    updater = module.ChatGPTUpdater()

    assert (
        updater.get_download_url(
            "aarch64-darwin",
            VersionInfo(
                version="1.2.3",
                metadata=DownloadUrlMetadata(url="https://example.com/chatgpt.dmg"),
            ),
        )
        == "https://example.com/chatgpt.dmg"
    )

    with pytest.raises(RuntimeError, match="Missing ChatGPT download URL"):
        updater.get_download_url(
            "aarch64-darwin",
            VersionInfo(
                version="1.2.3", metadata={"url": "https://example.com/chatgpt.dmg"}
            ),
        )


def test_chatgpt_parser_helpers_reject_missing_appcast_fields() -> None:
    """ChatGPT helper methods should reject malformed appcast structures."""
    module = _load_module("overlays/chatgpt/updater.py", "chatgpt_lane_helpers")
    updater = module.ChatGPTUpdater()

    with pytest.raises(RuntimeError, match="Invalid appcast XML"):
        updater._parse_appcast("<")

    with pytest.raises(RuntimeError, match="No items found in appcast"):
        updater._extract_item(updater._parse_appcast("<rss><channel /></rss>"))

    item_without_version = updater._parse_appcast(
        "<rss><channel><item><enclosure url='https://example.com/app.dmg' /></item></channel></rss>"
    ).find("./channel/item")
    assert item_without_version is not None
    with pytest.raises(RuntimeError, match="No version found in appcast"):
        updater._extract_version(item_without_version)

    item_without_enclosure = updater._parse_appcast(
        "<rss xmlns:sparkle='http://www.andymatuschak.org/xml-namespaces/sparkle'><channel><item><sparkle:shortVersionString>1.2.3</sparkle:shortVersionString></item></channel></rss>"
    ).find("./channel/item")
    assert item_without_enclosure is not None
    with pytest.raises(RuntimeError, match="No enclosure found in appcast"):
        updater._extract_download_url(item_without_enclosure)

    item_without_url = updater._parse_appcast(
        "<rss xmlns:sparkle='http://www.andymatuschak.org/xml-namespaces/sparkle'><channel><item><sparkle:shortVersionString>1.2.3</sparkle:shortVersionString><enclosure /></item></channel></rss>"
    ).find("./channel/item")
    assert item_without_url is not None
    with pytest.raises(RuntimeError, match="No URL found in enclosure"):
        updater._extract_download_url(item_without_url)


def test_code_cursor_fetch_checksums_and_download_url_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor should hash resolved artifact URLs and validate typed per-platform payloads."""
    module = _load_module("overlays/code-cursor/updater.py", "code_cursor_lane_test")
    updater = module.CodeCursorUpdater()
    assert updater._api_url("darwin-arm64").endswith(
        "platform=darwin-arm64&releaseTrack=stable"
    )
    info = VersionInfo(
        version="1.99.0",
        metadata=PlatformAPIMetadata(
            platform_info={
                platform: {"downloadUrl": f"https://example.com/{api_platform}.zip"}
                for platform, api_platform in updater.PLATFORMS.items()
            },
            equality_fields={"commitSha": "deadbeef"},
        ),
    )

    async def _compute_url_hashes(name: str, urls) -> object:
        url_list = list(urls)
        assert name == updater.name
        assert url_list == [
            f"https://example.com/{api_platform}.zip"
            for api_platform in updater.PLATFORMS.values()
        ]
        yield UpdateEvent.value(
            name,
            {
                url: f"sha256-{index:0<43}="
                for index, url in enumerate(url_list, start=1)
            },
        )

    monkeypatch.setattr(
        "lib.update.updaters.base.compute_url_hashes", _compute_url_hashes
    )

    checksums = _run(updater.fetch_checksums(info, object()))

    assert (
        checksums["aarch64-darwin"]
        == "sha256-1000000000000000000000000000000000000000000="
    )
    assert (
        checksums["x86_64-linux"]
        == "sha256-4000000000000000000000000000000000000000000="
    )
    assert (
        updater._download_url("darwin-arm64", info)
        == "https://example.com/darwin-arm64.zip"
    )

    with pytest.raises(TypeError, match="Expected platform payload"):
        updater._download_url(
            "darwin-arm64",
            VersionInfo(
                version="1.99.0",
                metadata=PlatformAPIMetadata(
                    platform_info={"aarch64-darwin": "bad"},
                    equality_fields={},
                ),
            ),
        )

    with pytest.raises(TypeError, match="Expected downloadUrl string"):
        updater._download_url(
            "darwin-arm64",
            VersionInfo(
                version="1.99.0",
                metadata=PlatformAPIMetadata(
                    platform_info={"aarch64-darwin": {"downloadUrl": None}},
                    equality_fields={},
                ),
            ),
        )


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (["bad"], "Unexpected DataGrip payload type: list"),
        ({"DG": {}}, "No DataGrip releases found in response"),
        ({"DG": ["bad"]}, "Unexpected DataGrip release payload"),
    ],
)
def test_datagrip_fetch_latest_rejects_bad_payload_shapes(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    match: str,
) -> None:
    """DataGrip should reject malformed JetBrains API payloads early."""
    module = _load_module("overlays/datagrip/updater.py", "datagrip_lane_fetch_latest")
    updater = module.DataGripUpdater()

    async def _fetch_json(_session: object, _url: str, *, config) -> object:
        assert config == updater.config
        return payload

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    with pytest.raises((TypeError, RuntimeError), match=match):
        _run(updater.fetch_latest(object()))


def test_datagrip_helpers_fetch_checksums_and_build_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DataGrip should reuse release metadata for checksum and URL selection."""
    module = _load_module("overlays/datagrip/updater.py", "datagrip_lane_helpers")
    updater = module.DataGripUpdater()
    release = {
        "version": "2025.1",
        "downloads": {
            "macM1": {
                "checksumLink": "https://checksums.invalid/macM1",
                "link": "https://downloads.invalid/macM1",
            },
            "mac": {
                "checksumLink": "https://checksums.invalid/mac",
                "link": "https://downloads.invalid/mac",
            },
            "linuxARM64": {
                "checksumLink": "https://checksums.invalid/linuxARM64",
                "link": "https://downloads.invalid/linuxARM64",
            },
            "linux": {
                "checksumLink": "https://checksums.invalid/linux",
                "link": "https://downloads.invalid/linux",
            },
        },
    }
    info = VersionInfo(
        version="2025.1",
        metadata=ReleasePayloadMetadata(release=release),
    )
    seen_urls: dict[str, str] = {}

    async def _fetch_checksums_from_urls(
        _session: object, urls: dict[str, str], *, parser
    ):
        seen_urls.update(urls)
        return {
            platform: parser(f"{platform}-hash  file".encode(), url)
            for platform, url in urls.items()
        }

    monkeypatch.setattr(
        updater, "_fetch_checksums_from_urls", _fetch_checksums_from_urls
    )

    checksums = _run(updater.fetch_checksums(info, object()))
    result = updater.build_result(
        info,
        dict.fromkeys(updater.PLATFORMS, HASH_A),
    )

    assert checksums["x86_64-darwin"] == "x86_64-darwin-hash"
    assert seen_urls["aarch64-linux"] == "https://checksums.invalid/linuxARM64"
    assert result.urls == {
        "aarch64-darwin": "https://downloads.invalid/macM1",
        "x86_64-darwin": "https://downloads.invalid/mac",
        "aarch64-linux": "https://downloads.invalid/linuxARM64",
        "x86_64-linux": "https://downloads.invalid/linux",
    }

    with pytest.raises(
        RuntimeError, match="Missing or invalid DataGrip release metadata"
    ):
        module.DataGripUpdater._release_payload(VersionInfo(version="1", metadata={}))
    with pytest.raises(
        RuntimeError, match="Missing or invalid DataGrip downloads metadata"
    ):
        module.DataGripUpdater._release_downloads({"downloads": []})
    with pytest.raises(TypeError, match="Missing DataGrip platform payload"):
        module.DataGripUpdater._release_download_field({}, "mac", "link")
    with pytest.raises(RuntimeError, match="Missing DataGrip download field 'link'"):
        module.DataGripUpdater._release_download_field({"mac": {}}, "mac", "link")


def test_datagrip_fetch_latest_rejects_empty_and_versionless_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DataGrip should reject empty release lists and releases without versions."""
    module = _load_module(
        "overlays/datagrip/updater.py", "datagrip_lane_release_errors"
    )
    updater = module.DataGripUpdater()

    monkeypatch.setattr(
        module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result={"DG": []}),
    )
    with pytest.raises(RuntimeError, match="No DataGrip releases found"):
        _run(updater.fetch_latest(object()))

    monkeypatch.setattr(
        module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result={"DG": [{"downloads": {}}]}),
    )
    with pytest.raises(
        RuntimeError, match="Missing DataGrip version in release payload"
    ):
        _run(updater.fetch_latest(object()))


def test_datagrip_fetch_latest_returns_typed_release_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DataGrip should keep the validated release payload in typed metadata."""
    module = _load_module("overlays/datagrip/updater.py", "datagrip_lane_success")
    updater = module.DataGripUpdater()
    release = {
        "version": "2025.1",
        "downloads": {"mac": {"checksumLink": "https://checksums.invalid/mac"}},
    }

    monkeypatch.setattr(
        module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result={"DG": [release]}),
    )

    latest = _run(updater.fetch_latest(object()))

    assert latest.version == "2025.1"
    assert latest.metadata == ReleasePayloadMetadata(release=release)


def test_google_chrome_fetch_latest_rejects_non_list_and_non_mapping_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chrome should fail clearly on malformed Chromium Dash responses."""
    module = _load_module(
        "overlays/google-chrome/updater.py", "google_chrome_lane_test"
    )
    updater = module.GoogleChromeUpdater()

    async def _fetch_json(_session: object, _url: str, *, config) -> object:
        assert config == updater.config
        return {"version": "133.0.6943.54"}

    monkeypatch.setattr(module, "fetch_json", _fetch_json)
    with pytest.raises(TypeError, match="Unexpected chromiumdash payload type: dict"):
        _run(updater.fetch_latest(object()))

    monkeypatch.setattr(
        module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=["bad"]),
    )
    with pytest.raises(TypeError, match="Unexpected chromiumdash release payload"):
        _run(updater.fetch_latest(object()))


def test_google_chrome_fetch_latest_rejects_empty_and_versionless_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chrome should reject empty release lists and releases without versions."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_release_errors",
    )
    updater = module.GoogleChromeUpdater()

    monkeypatch.setattr(
        module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=[]),
    )
    with pytest.raises(
        RuntimeError, match="No Chrome releases returned from chromiumdash"
    ):
        _run(updater.fetch_latest(object()))

    monkeypatch.setattr(
        module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=[{}]),
    )
    with pytest.raises(RuntimeError, match="Missing version in chromiumdash response"):
        _run(updater.fetch_latest(object()))


def test_google_chrome_fetch_latest_returns_version_without_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chrome should return the latest stable version with no extra metadata."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_success",
    )
    updater = module.GoogleChromeUpdater()

    monkeypatch.setattr(
        module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=[{"version": "133.0.6943.54"}]),
    )

    latest = _run(updater.fetch_latest(object()))

    assert latest.version == "133.0.6943.54"
    assert latest.metadata is module.NO_METADATA


def test_sentry_cli_fetch_hashes_handles_event_flow_and_type_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sentry should forward build events and validate captured hash payload types."""
    module = _load_module("overlays/sentry-cli/updater.py", "sentry_cli_lane_test")
    updater = module.SentryCliUpdater()
    info = VersionInfo(version="2.40.0")

    monkeypatch.setattr(module, "_build_nix_expr", lambda expr: expr)

    calls = install_fixed_hash_stream(
        monkeypatch,
        (("building src", HASH_A), ("building cargo", HASH_B)),
    )

    events = _run(_collect_events(updater.fetch_hashes(info, object())))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert [event.message for event in events[:-1]] == [
        "building src",
        "building cargo",
    ]
    assert calls == [
        {
            "name": updater.name,
            "expr": updater._src_nix_expr(info.version),
            "env": None,
            "config": updater.config,
        },
        {
            "name": updater.name,
            "expr": updater._cargo_nix_expr(info.version, HASH_A),
            "env": None,
            "config": updater.config,
        },
    ]
    assert events[-1].payload == [
        HashEntry.create("srcHash", HASH_A),
        HashEntry.create("cargoHash", HASH_B),
    ]


def test_vscode_insiders_fetch_latest_checksums_and_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VS Code Insiders should normalize version, commit, checksums, and URLs."""
    module = _load_module(
        "overlays/vscode-insiders/updater.py",
        "vscode_insiders_lane_test",
    )
    updater = module.VSCodeInsidersUpdater()

    async def _fetch_json(_session: object, url: str, *, config) -> object:
        assert config == updater.config
        api_platform = url.rsplit("/", maxsplit=3)[-3]
        return {
            "productVersion": "1.100.0-insider",
            "sha256hash": f"sha256-{api_platform}",
            "version": "67c59a1440590a328f6fd0f15c37383c7576a236",
        }

    monkeypatch.setattr("lib.update.updaters.platform_api.fetch_json", _fetch_json)

    latest = _run(updater.fetch_latest(object()))
    checksums = _run(updater.fetch_checksums(latest, object()))
    result = updater.build_result(latest, dict.fromkeys(updater.PLATFORMS, HASH_A))

    assert latest.version == "1.100.0-insider"
    assert latest.commit == "67c59a1440590a328f6fd0f15c37383c7576a236"
    assert checksums["aarch64-linux"] == "sha256-linux-arm64"
    assert result.commit == "67c59a1440590a328f6fd0f15c37383c7576a236"
    assert result.urls == {
        platform: f"https://update.code.visualstudio.com/1.100.0-insider/{api_platform}/insider"
        for platform, api_platform in updater.PLATFORMS.items()
    }
