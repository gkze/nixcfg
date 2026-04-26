"""Dedicated tests for the Zoom updater's redirect and URL handling."""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.updaters.base import VersionInfo


def _load_module() -> ModuleType:
    return load_repo_module("overlays/zoom-us/updater.py", "zoom_us_updater_test")


@dataclass(slots=True)
class _FakeResponse:
    url: str
    status: int = 200
    reason: str = "OK"

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSession:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        return self._responses[url]


def test_extract_version_accepts_zoom_cdn_paths_and_rejects_unknown_urls() -> None:
    """Zoom version extraction should accept known CDN paths and reject drift."""
    module = _load_module()

    assert (
        module.ZoomUsUpdater._extract_version(
            "https://cdn.zoom.us/prod/9.9.9.99999/zoomusInstallerFull.pkg"
        )
        == "9.9.9.99999"
    )
    assert (
        module.ZoomUsUpdater._extract_version(
            "https://cdn.zoom.us/prod/9.9.9.99999/arm64/zoomusInstallerFull.pkg"
        )
        == "9.9.9.99999"
    )

    with pytest.raises(RuntimeError, match="Could not extract Zoom version"):
        module.ZoomUsUpdater._extract_version("https://cdn.zoom.us/latest/zoom.pkg")


def test_extract_version_rejects_empty_match_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject regex matches that do not actually yield a version token."""
    module = _load_module()

    class _EmptyMatch:
        @staticmethod
        def group(_name: str) -> str:
            return ""

    monkeypatch.setattr(
        module,
        "_VERSION_PATTERN",
        type("_Pattern", (), {"search": staticmethod(lambda _path: _EmptyMatch())})(),
    )

    with pytest.raises(RuntimeError, match="did not include a version"):
        module.ZoomUsUpdater._extract_version(
            "https://cdn.zoom.us/prod/ignored/zoom.pkg"
        )


def test_fetch_latest_resolves_shared_version_from_latest_redirects() -> None:
    """Zoom should resolve both macOS latest URLs via HEAD and require one version."""
    module = _load_module()
    updater = module.ZoomUsUpdater()
    session = _FakeSession({
        updater._LATEST_URLS["aarch64-darwin"]: _FakeResponse(
            url="https://cdn.zoom.us/prod/9.9.9.99999/arm64/zoomusInstallerFull.pkg"
        ),
        updater._LATEST_URLS["x86_64-darwin"]: _FakeResponse(
            url="https://cdn.zoom.us/prod/9.9.9.99999/zoomusInstallerFull.pkg"
        ),
    })

    info = _run(updater.fetch_latest(session))

    assert info == VersionInfo(version="9.9.9.99999", metadata=module.NO_METADATA)
    assert [(method, url) for method, url, _kwargs in session.calls] == [
        ("HEAD", updater._LATEST_URLS["aarch64-darwin"]),
        ("HEAD", updater._LATEST_URLS["x86_64-darwin"]),
    ]
    for _method, _url, kwargs in session.calls:
        assert kwargs["allow_redirects"] is True
        assert kwargs["timeout"].total == updater.config.default_timeout


def test_fetch_latest_rejects_failed_or_mismatched_redirects() -> None:
    """Zoom should fail on HTTP errors or platform version skew."""
    module = _load_module()
    updater = module.ZoomUsUpdater()

    failing_session = _FakeSession({
        updater._LATEST_URLS["aarch64-darwin"]: _FakeResponse(
            url=updater._LATEST_URLS["aarch64-darwin"],
            status=503,
            reason="Service Unavailable",
        )
    })
    with pytest.raises(RuntimeError, match="Failed to resolve Zoom latest URL"):
        _run(
            updater._resolve_latest_url(
                failing_session,
                updater._LATEST_URLS["aarch64-darwin"],
            )
        )

    mismatched_session = _FakeSession({
        updater._LATEST_URLS["aarch64-darwin"]: _FakeResponse(
            url="https://cdn.zoom.us/prod/9.9.9.99999/arm64/zoomusInstallerFull.pkg"
        ),
        updater._LATEST_URLS["x86_64-darwin"]: _FakeResponse(
            url="https://cdn.zoom.us/prod/9.9.8.99999/zoomusInstallerFull.pkg"
        ),
    })
    with pytest.raises(RuntimeError, match="mismatched versions"):
        _run(updater.fetch_latest(mismatched_session))


def test_get_download_url_uses_pinned_zoom_paths_and_rejects_unknown_platform() -> None:
    """Zoom download URLs should stay in the versioned zoom.us path format."""
    module = _load_module()
    updater = module.ZoomUsUpdater()
    info = VersionInfo(version="9.9.9.99999")

    assert updater.get_download_url("aarch64-darwin", info) == (
        "https://zoom.us/client/9.9.9.99999/zoomusInstallerFull.pkg?archType=arm64"
    )
    assert updater.get_download_url("x86_64-darwin", info) == (
        "https://zoom.us/client/9.9.9.99999/zoomusInstallerFull.pkg"
    )

    with pytest.raises(RuntimeError, match="Unsupported platform for zoom-us updater"):
        updater.get_download_url("x86_64-linux", info)
