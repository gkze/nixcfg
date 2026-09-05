"""Focused tests for the overlay-only updater lane."""

import asyncio
import json
from dataclasses import dataclass

import pytest

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import install_fixed_hash_stream
from lib.tests._updater_helpers import load_repo_module as _load_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import (
    JsonObject,
    PlatformAPIMetadata,
    ReleasePayloadMetadata,
)

HASH_A = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
HASH_B = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="

_CHROME_APP_ID = "com.google.Chrome"
_CHROME_MAC_VERSION = "152.0.7977.83"
_CHROME_LINUX_VERSION = "152.0.7977.82"
_CHROME_DMG_CODEBASE = (
    "https://dl.google.com/release2/chrome/g62gliie746ywu62ed7go3adam_152.0.7977.83/"
)
_CHROME_DMG_URL = f"{_CHROME_DMG_CODEBASE}GoogleChrome-{_CHROME_MAC_VERSION}.dmg"
_CHROME_DEB_FILENAME = (
    "pool/main/g/google-chrome-stable/"
    f"google-chrome-stable_{_CHROME_LINUX_VERSION}-1_amd64.deb"
)
_CHROME_DEB_URL = f"https://dl.google.com/linux/chrome/deb/{_CHROME_DEB_FILENAME}"
_CHROME_DMG_HEX_HASH = (
    "51cd7a59e04f86efebef307f504f72b7e72091ba5162444cdf1b4434596daa9b"
)
_CHROME_DEB_HEX_HASH = (
    "4d25e4a028c78a7ae910683551c2f234792cc5595e7e3e34939f599342ada446"
)
_CHROME_DMG_SRI_HASH = "sha256-Uc16WeBPhu/r7zB/UE9yt+cgkbpRYkRM3xtENFltqps="
_CHROME_DEB_SRI_HASH = "sha256-TSXkoCjHinrpEGg1UcLyNHksxVlefj40k59Zk0KtpEY="
_CHROME_OMAHA_PREFIX = b")]}'\n"


def _chrome_omaha_json(payload: object) -> bytes:
    return _CHROME_OMAHA_PREFIX + json.dumps(payload).encode()


@dataclass(slots=True)
class _ChromeHTTPResponse:
    payload: bytes
    status: int = 200
    reason: str = "OK"

    async def __aenter__(self) -> _ChromeHTTPResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def read(self) -> bytes:
        return self.payload


class _ChromeHTTPSession:
    def __init__(self, response: _ChromeHTTPResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> _ChromeHTTPResponse:
        self.calls.append((method, url, kwargs))
        return self.response


def _chrome_omaha_response(
    *,
    version: str = _CHROME_MAC_VERSION,
    app_status: str = "ok",
    update_status: str = "ok",
    appids: tuple[str, ...] = (_CHROME_APP_ID,),
    package_names: tuple[str, ...] | None = None,
    package_hash: object = _CHROME_DMG_HEX_HASH,
    codebases: tuple[object, ...] | None = None,
) -> bytes:
    resolved_packages = (
        (f"GoogleChrome-{version}.dmg",) if package_names is None else package_names
    )
    resolved_codebases = (_CHROME_DMG_CODEBASE,) if codebases is None else codebases
    payload = {
        "response": {
            "app": [
                {
                    "appid": appid,
                    "status": app_status,
                    "updatecheck": {
                        "status": update_status,
                        "urls": {
                            "url": [
                                ({"codebase": codebase} if codebase is not None else {})
                                for codebase in resolved_codebases
                            ]
                        },
                        "manifest": {
                            "version": version,
                            "packages": {
                                "package": [
                                    {
                                        "name": package_name,
                                        "hash_sha256": package_hash,
                                    }
                                    for package_name in resolved_packages
                                ]
                            },
                        },
                    },
                }
                for appid in appids
            ]
        }
    }
    return _chrome_omaha_json(payload)


def _chrome_apt_packages(
    *,
    version: str = f"{_CHROME_LINUX_VERSION}-1",
    filename: str = _CHROME_DEB_FILENAME,
    sha256: str = _CHROME_DEB_HEX_HASH,
) -> bytes:
    return f"""Package: google-chrome-stable
Version: {version}
Architecture: amd64
Filename: {filename}
SHA256: {sha256}
Description: Google Chrome
 continuation
""".encode()


def test_code_cursor_fetch_checksums_and_download_url_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor should hash resolved artifact URLs and validate typed per-platform payloads."""
    module = _load_module("overlays/code-cursor/updater.py", "code_cursor_lane_test")
    updater = module.CodeCursorUpdater()
    assert (
        updater._api_url("darwin-arm64")
        == "https://api2.cursor.sh/updates/download/golden/darwin-arm64/cursor/3.9"
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

    async def _compute_url_hashes(name: str, urls, *, config: object) -> object:
        assert config is updater.config
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

    monkeypatch.setattr("lib.update.process.compute_url_hashes", _compute_url_hashes)

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


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ([], "Unexpected Chrome VersionHistory payload"),
        ({}, "Missing Chrome VersionHistory releases"),
        ({"releases": ["bad"]}, "Unexpected Chrome VersionHistory release"),
        (
            {"releases": [{"fraction": 1, "serving": {}}]},
            "Invalid Chrome version",
        ),
        (
            {"releases": [{"version": "152.latest", "fraction": 1, "serving": {}}]},
            "Invalid Chrome version",
        ),
        (
            {"releases": [{"version": "152.0.0.1", "fraction": True, "serving": {}}]},
            "Invalid Chrome rollout fraction",
        ),
        (
            {"releases": [{"version": "152.0.0.1", "fraction": 1}]},
            "Invalid Chrome serving interval",
        ),
        (
            {
                "releases": [
                    {
                        "version": "152.0.0.1",
                        "fraction": 1,
                        "serving": {"endTime": 42},
                    }
                ]
            },
            "Invalid Chrome rollout end time",
        ),
    ],
)
def test_google_chrome_rejects_malformed_version_history_results(
    payload: object,
    match: str,
) -> None:
    """Chrome should fail clearly when VersionHistory violates its schema."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_malformed_releases",
    )

    with pytest.raises(TypeError, match=match):
        module._full_rollout_version(payload, platform="mac")


def test_google_chrome_requires_one_active_full_rollout() -> None:
    """Chrome should reject missing or ambiguous fully rolled-out baselines."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_release_errors",
    )
    no_baseline = {
        "releases": [
            {"version": "153.0.0.1", "fraction": 0.005, "serving": {}},
            {
                "version": "152.0.0.1",
                "fraction": 1,
                "serving": {"endTime": "2026-09-02T00:00:00Z"},
            },
        ]
    }
    with pytest.raises(RuntimeError, match="No active fully rolled-out"):
        module._full_rollout_version(no_baseline, platform="mac")

    ambiguous = {
        "releases": [
            {"version": "152.0.0.2", "fraction": 1, "serving": {}},
            {"version": "152.0.0.1", "fraction": 1.0, "serving": {}},
        ]
    }
    with pytest.raises(RuntimeError, match="Ambiguous active fully rolled-out"):
        module._full_rollout_version(ambiguous, platform="mac")

    with pytest.raises(RuntimeError, match="divergent fully rolled-out Darwin"):
        module._shared_darwin_version({
            "mac_arm64": "152.0.7977.83",
            "mac": "152.0.7977.82",
            "linux": "152.0.7977.82",
        })


def test_google_chrome_omaha_request_separates_os_and_browser_versions() -> None:
    """Omaha OS identity must never be populated with the target Chrome version."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_omaha_request",
    )

    request = module._omaha_request(
        target_version=_CHROME_MAC_VERSION,
        os_version="26.6.2",
        os_arch="arm64",
    )["request"]

    assert request["protocol"] == "3.1"
    assert request["testsource"] == "prober"
    assert request["os"] == {
        "platform": "mac",
        "version": "26.6.2",
        "arch": "arm64",
    }
    assert request["os"]["version"] != _CHROME_MAC_VERSION
    app = request["app"][0]
    assert app["version"] == "0"
    assert app["installsource"] == "ondemand"
    assert app["updatecheck"] == {"targetversionprefix": f"{_CHROME_MAC_VERSION}$"}


@pytest.mark.parametrize(
    ("mac_version", "machine", "match"),
    [
        ("", "arm64", "Cannot determine the Darwin host OS version"),
        ("Sonoma", "arm64", "Cannot determine the Darwin host OS version"),
        ("26.6.2", "aarch64", "Unsupported Darwin host architecture"),
    ],
)
def test_google_chrome_requires_truthful_supported_darwin_identity(
    monkeypatch: pytest.MonkeyPatch,
    mac_version: str,
    machine: str,
    match: str,
) -> None:
    """Non-Darwin or malformed host identity must fail before querying Omaha."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_darwin_identity_errors",
    )
    monkeypatch.setattr(
        module.host_platform,
        "mac_ver",
        lambda: (mac_version, ("", "", ""), ""),
    )
    monkeypatch.setattr(module.host_platform, "machine", lambda: machine)

    with pytest.raises(RuntimeError, match=match):
        module._darwin_host_identity()


def test_google_chrome_fetch_latest_uses_platform_full_rollout_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chrome should pin full rollouts to immutable, platform-versioned artifacts."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_success",
    )
    updater = module.GoogleChromeUpdater()
    session = _ChromeHTTPSession(_ChromeHTTPResponse(_chrome_omaha_response()))
    requested_urls: list[str] = []
    apt_calls: list[tuple[object, str, dict[str, object]]] = []

    async def _fetch_json(_session: object, url: str, *, config) -> object:
        assert config == updater.config
        requested_urls.append(url)
        if "/platforms/mac_arm64/" in url or "/platforms/mac/" in url:
            return {
                "releases": [
                    {"version": "153.0.8010.12", "fraction": 0.005, "serving": {}},
                    {"version": _CHROME_MAC_VERSION, "fraction": 1, "serving": {}},
                    {
                        "version": "154.0.9000.1",
                        "fraction": 1,
                        "serving": {"endTime": "2026-08-01T00:00:00Z"},
                    },
                ]
            }
        assert "/platforms/linux/" in url
        return {
            "releases": [
                {"version": _CHROME_LINUX_VERSION, "fraction": 1, "serving": {}}
            ]
        }

    async def _fetch_url(
        passed_session: object,
        url: str,
        **kwargs: object,
    ) -> bytes:
        apt_calls.append((passed_session, url, kwargs))
        return _chrome_apt_packages()

    monkeypatch.setattr(module, "fetch_json", _fetch_json)
    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(
        module.host_platform,
        "mac_ver",
        lambda: ("26.6.2", ("", "", ""), ""),
    )
    monkeypatch.setattr(module.host_platform, "machine", lambda: "arm64")

    latest = _run(updater.fetch_latest(session))
    assert isinstance(latest.metadata, module._ChromeReleaseMetadata)
    events = _run(_collect_events(updater.fetch_hashes(latest, session)))
    result = updater.build_result(latest, latest.metadata.artifact_hashes)

    assert latest.version == _CHROME_MAC_VERSION
    assert latest.metadata.platform_versions == {
        "aarch64-darwin": _CHROME_MAC_VERSION,
        "x86_64-darwin": _CHROME_MAC_VERSION,
        "x86_64-linux": _CHROME_LINUX_VERSION,
    }
    assert latest.metadata.asset_urls == {
        "aarch64-darwin": _CHROME_DMG_URL,
        "x86_64-darwin": _CHROME_DMG_URL,
        "x86_64-linux": _CHROME_DEB_URL,
    }
    assert latest.metadata.artifact_hashes == {
        "aarch64-darwin": _CHROME_DMG_SRI_HASH,
        "x86_64-darwin": _CHROME_DMG_SRI_HASH,
        "x86_64-linux": _CHROME_DEB_SRI_HASH,
    }
    assert result.pins == latest.metadata.platform_versions
    assert result.urls == latest.metadata.asset_urls
    assert result.hashes.to_json() == latest.metadata.artifact_hashes
    assert events == [
        UpdateEvent.value("google-chrome", latest.metadata.artifact_hashes)
    ]
    assert len(requested_urls) == 3
    assert all("channels/stable" in url for url in requested_urls)
    assert all("filter=endtime%3Dnone%2Cfraction%3D1" in url for url in requested_urls)
    assert apt_calls == [
        (
            session,
            module._LINUX_PACKAGES_URL,
            {
                "request_timeout": updater.config.default_timeout,
                "config": updater.config,
            },
        )
    ]
    assert len(session.calls) == 1
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == module._OMAHA_URL
    assert kwargs["headers"] == {
        "User-Agent": updater.config.default_user_agent,
        "Content-Type": "application/json",
        "X-Goog-Update-Interactivity": "fg",
        "X-Goog-Update-AppId": _CHROME_APP_ID,
        "X-Goog-Update-Updater": "nixcfg-0",
    }
    assert kwargs["json"] == module._omaha_request(
        target_version=_CHROME_MAC_VERSION,
        os_version="26.6.2",
        os_arch="arm64",
    )
    assert kwargs["allow_redirects"] is True
    assert kwargs["timeout"].total == updater.config.default_timeout
    assert updater.materialize_when_current is False
    assert updater.supported_platforms == ("aarch64-darwin", "x86_64-darwin")
    assert updater.PLATFORMS == {
        "aarch64-darwin": "mac_arm64",
        "x86_64-darwin": "mac",
        "x86_64-linux": "linux",
    }


@pytest.mark.parametrize(
    ("payload", "exception", "match"),
    [
        (b"not json", RuntimeError, "omitted its anti-XSSI prefix"),
        (
            _CHROME_OMAHA_PREFIX + b"not-json",
            RuntimeError,
            "was not valid JSON",
        ),
        (
            _CHROME_OMAHA_PREFIX + b"\xff",
            RuntimeError,
            "was not valid JSON",
        ),
        (
            _chrome_omaha_json([]),
            TypeError,
            "Expected JSON object for Google Chrome Omaha response",
        ),
        (
            _chrome_omaha_json({}),
            TypeError,
            "Google Chrome Omaha response.response",
        ),
        (
            _chrome_omaha_json({"response": {"app": "bad"}}),
            TypeError,
            "Expected JSON array",
        ),
        (
            _chrome_omaha_json({"response": {"app": ["bad"]}}),
            TypeError,
            "Expected JSON object",
        ),
        (
            _chrome_omaha_response(appids=()),
            RuntimeError,
            "contained 0 matching apps",
        ),
        (
            _chrome_omaha_response(appids=(_CHROME_APP_ID, _CHROME_APP_ID)),
            RuntimeError,
            "contained 2 matching apps",
        ),
        (
            _chrome_omaha_response(app_status="error"),
            RuntimeError,
            "app returned status",
        ),
        (
            _chrome_omaha_json({
                "response": {"app": [{"appid": _CHROME_APP_ID, "status": "ok"}]}
            }),
            TypeError,
            "Google Chrome Omaha updatecheck",
        ),
        (
            _chrome_omaha_response(update_status="noupdate"),
            RuntimeError,
            "updatecheck returned status 'noupdate'",
        ),
        (
            _chrome_omaha_json({
                "response": {
                    "app": [
                        {
                            "appid": _CHROME_APP_ID,
                            "status": "ok",
                            "updatecheck": {"status": "ok"},
                        }
                    ]
                }
            }),
            TypeError,
            "Google Chrome Omaha manifest",
        ),
        (
            _chrome_omaha_response(version="152.0.7977.82"),
            RuntimeError,
            "observed '152.0.7977.82'",
        ),
        (
            _chrome_omaha_response(package_names=()),
            RuntimeError,
            "contained 0 matching DMG packages",
        ),
        (
            _chrome_omaha_response(
                package_names=(
                    f"GoogleChrome-{_CHROME_MAC_VERSION}.dmg",
                    f"GoogleChrome-{_CHROME_MAC_VERSION}.dmg",
                )
            ),
            RuntimeError,
            "contained 2 matching DMG packages",
        ),
        (
            _chrome_omaha_response(package_hash=None),
            TypeError,
            "string field 'hash_sha256'",
        ),
        (
            _chrome_omaha_response(package_hash="not-a-hash"),
            RuntimeError,
            "Invalid SHA-256",
        ),
        (
            _chrome_omaha_response(codebases=()),
            RuntimeError,
            "contained 0 immutable Google DMG URLs",
        ),
        (
            _chrome_omaha_response(codebases=(None,)),
            TypeError,
            "string field 'codebase'",
        ),
        (
            _chrome_omaha_response(
                codebases=(
                    "http://dl.google.com/release2/chrome/"
                    "g62gliie746ywu62ed7go3adam_152.0.7977.83/",
                    "https://example.com/release2/chrome/"
                    "g62gliie746ywu62ed7go3adam_152.0.7977.83/",
                    "https://dl.google.com/release2/chrome/not-versioned/",
                )
            ),
            RuntimeError,
            "contained 0 immutable Google DMG URLs",
        ),
        (
            _chrome_omaha_response(
                codebases=(
                    _CHROME_DMG_CODEBASE,
                    "https://dl.google.com/release2/chrome/another_152.0.7977.83/",
                )
            ),
            RuntimeError,
            "contained 2 immutable Google DMG URLs",
        ),
    ],
)
def test_google_chrome_rejects_malformed_or_ambiguous_omaha_artifacts(
    payload: bytes,
    exception: type[Exception],
    match: str,
) -> None:
    """Only one first-party immutable DMG matching VersionHistory may be pinned."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_omaha_errors",
    )

    with pytest.raises(exception, match=match):
        module._parse_omaha_artifact(payload, expected_version=_CHROME_MAC_VERSION)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (b"\xff", "Packages metadata is not UTF-8"),
        (b"", "contained 0 stable amd64 packages"),
        (
            b"Package: google-chrome-beta\nArchitecture: amd64\n",
            "contained 0 stable amd64 packages",
        ),
        (
            _chrome_apt_packages() + b"Malformed line\n",
            "Malformed Google Chrome apt metadata line",
        ),
        (
            _chrome_apt_packages() + b"\n" + _chrome_apt_packages(),
            "contained 2 stable amd64 packages",
        ),
        (
            b"Package: google-chrome-stable\nArchitecture: amd64\n",
            "invalid 'Version' field",
        ),
        (
            _chrome_apt_packages().replace(
                b"Version: ",
                b"Version: duplicate\nVersion: ",
                1,
            ),
            "invalid 'Version' field",
        ),
        (
            _chrome_apt_packages(version=_CHROME_LINUX_VERSION),
            "does not match the fully rolled-out Linux version",
        ),
        (
            _chrome_apt_packages(version="152.0.7977.81-1"),
            "observed '152.0.7977.81-1'",
        ),
        (
            _chrome_apt_packages(version=f"{_CHROME_LINUX_VERSION}-?"),
            "does not match the fully rolled-out Linux version",
        ),
        (
            _chrome_apt_packages(filename=f"/{_CHROME_DEB_FILENAME}"),
            "invalid Filename",
        ),
        (
            _chrome_apt_packages(
                filename=f"direct/google-chrome-stable_{_CHROME_LINUX_VERSION}-1_amd64.deb"
            ),
            "invalid Filename",
        ),
        (
            _chrome_apt_packages(filename="pool/main/g/google-chrome-stable/wrong.deb"),
            "invalid Filename",
        ),
        (
            _chrome_apt_packages(filename=_CHROME_DEB_FILENAME.replace("/g/", "//")),
            "invalid Filename",
        ),
        (
            _chrome_apt_packages(filename=f"pool/../{_CHROME_DEB_FILENAME}"),
            "invalid Filename",
        ),
        (
            _chrome_apt_packages().replace(
                f"SHA256: {_CHROME_DEB_HEX_HASH}\n".encode(),
                b"",
            ),
            "invalid 'SHA256' field",
        ),
        (
            _chrome_apt_packages(sha256="not-a-hash"),
            "Invalid SHA-256",
        ),
    ],
)
def test_google_chrome_rejects_malformed_or_ambiguous_apt_artifacts(
    payload: bytes,
    match: str,
) -> None:
    """The apt index must identify one safe immutable DEB for the baseline."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_apt_errors",
    )

    with pytest.raises(RuntimeError, match=match):
        module._parse_linux_artifact(payload, expected_version=_CHROME_LINUX_VERSION)


def test_google_chrome_reports_omaha_http_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP failures must stop before a Chrome artifact can be persisted."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_omaha_http_error",
    )
    updater = module.GoogleChromeUpdater()
    session = _ChromeHTTPSession(
        _ChromeHTTPResponse(b"unavailable", status=503, reason="Service Unavailable")
    )
    monkeypatch.setattr(
        module.host_platform,
        "mac_ver",
        lambda: ("26.6.2", ("", "", ""), ""),
    )
    monkeypatch.setattr(module.host_platform, "machine", lambda: "arm64")

    with pytest.raises(
        RuntimeError,
        match="Omaha request failed with HTTP 503 Service Unavailable",
    ):
        _run(
            updater._fetch_darwin_artifact(
                session,
                expected_version=_CHROME_MAC_VERSION,
            )
        )


def test_google_chrome_requires_complete_artifact_metadata() -> None:
    """Hashing and package versions should fail closed on partial metadata."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_metadata_errors",
    )
    updater = module.GoogleChromeUpdater()
    missing = VersionInfo(version=_CHROME_MAC_VERSION)
    incomplete = VersionInfo(
        version=_CHROME_MAC_VERSION,
        metadata=module._ChromeReleaseMetadata(
            asset_urls={},
            artifact_hashes={},
            platform_versions={"aarch64-darwin": _CHROME_MAC_VERSION},
        ),
    )

    with pytest.raises(TypeError, match="Missing Google Chrome artifact metadata"):
        updater.source_pins_for(missing)
    with pytest.raises(RuntimeError, match="Incomplete Google Chrome platform"):
        updater.source_pins_for(incomplete)


def test_google_chrome_latest_check_compares_complete_published_identity() -> None:
    """Same-version URL or checksum changes must still produce an update."""
    module = _load_module(
        "overlays/google-chrome/updater.py",
        "google_chrome_lane_complete_identity",
    )
    updater = module.GoogleChromeUpdater()
    platform_versions = {
        "aarch64-darwin": _CHROME_MAC_VERSION,
        "x86_64-darwin": _CHROME_MAC_VERSION,
        "x86_64-linux": _CHROME_LINUX_VERSION,
    }
    asset_urls = {
        "aarch64-darwin": _CHROME_DMG_URL,
        "x86_64-darwin": _CHROME_DMG_URL,
        "x86_64-linux": _CHROME_DEB_URL,
    }
    artifact_hashes = {
        "aarch64-darwin": _CHROME_DMG_SRI_HASH,
        "x86_64-darwin": _CHROME_DMG_SRI_HASH,
        "x86_64-linux": _CHROME_DEB_SRI_HASH,
    }
    info = VersionInfo(
        version=_CHROME_MAC_VERSION,
        metadata=module._ChromeReleaseMetadata(
            asset_urls=asset_urls,
            artifact_hashes=artifact_hashes,
            platform_versions=platform_versions,
        ),
    )
    matching = SourceEntry(
        version=_CHROME_MAC_VERSION,
        hashes=artifact_hashes,
        urls=asset_urls,
        pins=platform_versions,
    )
    changed_url = matching.model_copy(
        update={"urls": {**asset_urls, "x86_64-linux": "https://example.com/old.deb"}}
    )
    changed_hash = matching.model_copy(
        update={
            "hashes": matching.hashes.model_copy(
                update={
                    "mapping": {
                        **artifact_hashes,
                        "x86_64-linux": HASH_A,
                    }
                }
            )
        }
    )

    assert _run(updater._is_latest(None, info)) is False
    assert _run(updater._is_latest(matching, info)) is True
    assert (
        _run(updater._is_latest(module.UpdateContext(current=matching), info)) is True
    )
    assert _run(updater._is_latest(changed_url, info)) is False
    assert _run(updater._is_latest(changed_hash, info)) is False


def test_sentry_cli_fetch_hashes_handles_event_flow_and_type_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sentry should forward build events and validate captured hash payload types."""
    module = _load_module("overlays/sentry-cli/updater.py", "sentry_cli_lane_test")
    updater = module.SentryCliUpdater()
    commit = "d" * 40
    info = VersionInfo(version="2.40.0", metadata={"commit": commit})

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
            "expr": updater._src_nix_expr(commit),
            "env": None,
            "config": updater.config,
        },
        {
            "name": updater.name,
            "expr": updater._cargo_nix_expr(commit, HASH_A),
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
            "url": (
                "https://vscode.download.example/insider/"
                f"67c59a1440590a328f6fd0f15c37383c7576a236/{api_platform}"
            ),
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
        "aarch64-darwin": (
            "https://vscode.download.example/insider/"
            "67c59a1440590a328f6fd0f15c37383c7576a236/darwin-arm64"
        ),
        "x86_64-darwin": (
            "https://vscode.download.example/insider/"
            "67c59a1440590a328f6fd0f15c37383c7576a236/darwin"
        ),
        "aarch64-linux": (
            "https://vscode.download.example/insider/"
            "67c59a1440590a328f6fd0f15c37383c7576a236/linux-arm64"
        ),
        "x86_64-linux": (
            "https://vscode.download.example/insider/"
            "67c59a1440590a328f6fd0f15c37383c7576a236/linux-x64"
        ),
    }


@pytest.mark.parametrize(
    "platform_payload",
    [{}, {"url": 42}],
    ids=["missing", "non-string"],
)
def test_vscode_insiders_rejects_invalid_download_url(
    platform_payload: JsonObject,
) -> None:
    """VS Code Insiders should reject absent or non-string artifact URLs."""
    module = _load_module(
        "overlays/vscode-insiders/updater.py",
        "vscode_insiders_lane_invalid_url_test",
    )
    updater = module.VSCodeInsidersUpdater()
    platform_info: dict[str, JsonObject] = {
        platform: {"url": f"https://vscode.download.example/{platform}"}
        for platform in updater.PLATFORMS
    }
    platform_info["aarch64-darwin"] = platform_payload
    info = VersionInfo(
        version="1.100.0-insider",
        metadata=PlatformAPIMetadata(
            platform_info=platform_info,
            equality_fields={},
            commit="67c59a1440590a328f6fd0f15c37383c7576a236",
        ),
    )

    with pytest.raises(TypeError, match="Expected string field 'url'"):
        updater.build_result(info, dict.fromkeys(updater.PLATFORMS, HASH_A))
