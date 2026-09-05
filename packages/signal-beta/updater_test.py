"""Behavioral tests for Signal Beta's vendor-feed update contract."""

from types import ModuleType

import pytest

from lib.nix.models.sources import SourceEntry
from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext, VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

_VERSION = "8.27.0-beta.3"
_FEED_URL = "https://updates.signal.org/desktop/beta-mac.yml"
_ARM64_URL = (
    "https://updates.signal.org/desktop/signal-desktop-beta-mac-arm64-8.27.0-beta.3.zip"
)
_X64_URL = (
    "https://updates.signal.org/desktop/signal-desktop-beta-mac-x64-8.27.0-beta.3.zip"
)
_HASHES = {
    "aarch64-darwin": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    "x86_64-darwin": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
}


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/signal-beta/updater.py",
        "signal_beta_updater_test",
    )


def _install_feed(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []

    async def fetch_url(
        _session: object,
        url: str,
        *,
        request_timeout: float,
        config: object,
    ) -> bytes:
        calls.append((url, config))
        assert request_timeout == config.default_timeout
        return payload

    monkeypatch.setattr("lib.update.updaters.vendor_feeds.fetch_url", fetch_url)
    return calls


def test_signal_beta_uses_the_published_macos_feed_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The feed version and URLs, not a newer source tag, own availability."""
    module = _load_module()
    updater = module.SignalBetaUpdater()
    calls = _install_feed(
        monkeypatch,
        f"""
version: {_VERSION}
files:
  - url: signal-desktop-beta-mac-x64-{_VERSION}.zip
    sha512: ignored
  - url: signal-desktop-beta-mac-arm64-{_VERSION}.zip
    sha512: ignored
  - url: signal-desktop-beta-mac-universal-{_VERSION}.dmg
    sha512: ignored
""".encode(),
    )

    info = run_async(updater.fetch_latest(object()))
    result = updater.build_result(info, _HASHES)

    assert calls == [(_FEED_URL, updater.config)]
    assert info == VersionInfo(
        version=_VERSION,
        metadata=AssetURLsMetadata({
            "aarch64-darwin": _ARM64_URL,
            "x86_64-darwin": _X64_URL,
        }),
    )
    assert result.version == _VERSION
    assert result.urls == {
        "aarch64-darwin": _ARM64_URL,
        "x86_64-darwin": _X64_URL,
    }
    assert result.hashes.to_json() == _HASHES


def test_signal_beta_preserves_metadata_urls_across_filename_layout_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifact discovery tolerates directory, prefix, and architecture-alias changes."""
    version = "8.28.0-beta.1"
    arm64_url = (
        f"https://updates.signal.org/releases/{version}/macos/SignalBeta-aarch64.zip"
        "?immutable=release"
    )
    x64_url = f"https://updates.signal.org/Signal-Beta-{version}-darwin-x86_64.zip"
    updater = _load_module().SignalBetaUpdater()
    _install_feed(
        monkeypatch,
        f"""
version: {version}
files:
  - url: {arm64_url}
  - url: {x64_url}
""".encode(),
    )

    info = run_async(updater.fetch_latest(object()))

    assert info == VersionInfo(
        version=version,
        metadata=AssetURLsMetadata({
            "aarch64-darwin": arm64_url,
            "x86_64-darwin": x64_url,
        }),
    )


def test_signal_beta_rejects_multiple_matching_platform_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _load_module().SignalBetaUpdater()
    _install_feed(
        monkeypatch,
        f"""
version: {_VERSION}
files:
  - url: signal-desktop-beta-mac-arm64-{_VERSION}.zip
  - url: mirror/signal-desktop-beta-mac-aarch64-{_VERSION}.zip
  - url: signal-desktop-beta-mac-x64-{_VERSION}.zip
""".encode(),
    )

    with pytest.raises(
        RuntimeError,
        match="Expected exactly one Signal beta URL.*aarch64-darwin.*found 2",
    ):
        run_async(updater.fetch_latest(object()))


@pytest.mark.parametrize(
    ("version", "url"),
    [
        (_VERSION, _ARM64_URL.replace("https://", "http://")),
        (_VERSION, "https:///desktop/signal-arm64-8.27.0-beta.3.zip"),
        (_VERSION, _ARM64_URL.replace("updates.signal.org", "example.test")),
        (_VERSION, _ARM64_URL.replace(".zip", ".dmg")),
        ("8.27.0-beta.2", _ARM64_URL),
        (_VERSION, _ARM64_URL.replace("beta.3.zip", "beta.30.zip")),
        (_VERSION, _ARM64_URL.replace("arm64", "universal")),
        (_VERSION, _ARM64_URL.replace("arm64", "arm64-x64")),
        (_VERSION, _X64_URL),
    ],
)
def test_signal_beta_arm64_selector_rejects_unsafe_or_wrong_artifacts(
    version: str,
    url: str,
) -> None:
    selector = _load_module().SignalBetaUpdater.SELECTORS["aarch64-darwin"]

    assert not selector(version, url)


def test_signal_beta_selectors_support_architecture_aliases() -> None:
    module = _load_module()
    selectors = module.SignalBetaUpdater.SELECTORS

    assert selectors["aarch64-darwin"](
        _VERSION,
        _ARM64_URL.replace("arm64", "aarch64"),
    )
    assert selectors["x86_64-darwin"](
        _VERSION,
        _X64_URL.replace("x64", "amd64"),
    )
    assert not selectors["x86_64-darwin"](_VERSION, _ARM64_URL)


@pytest.mark.parametrize("version", ["8.27.0", "v8.27.0-beta.3", "beta"])
def test_signal_beta_rejects_non_beta_feed_versions(version: str) -> None:
    updater = _load_module().SignalBetaUpdater()

    with pytest.raises(RuntimeError, match="non-beta version"):
        updater._validate_beta_version(version)


def test_signal_beta_detects_same_version_url_drift() -> None:
    updater = _load_module().SignalBetaUpdater()
    info = VersionInfo(
        version=_VERSION,
        metadata=AssetURLsMetadata({
            "aarch64-darwin": _ARM64_URL,
            "x86_64-darwin": _X64_URL,
        }),
    )
    matching = SourceEntry(
        version=_VERSION, hashes=_HASHES, urls=info.metadata.asset_urls
    )
    stale = matching.model_copy(
        update={
            "urls": {
                **info.metadata.asset_urls,
                "aarch64-darwin": _ARM64_URL.replace(
                    "updates.signal.org", "old.example"
                ),
            }
        }
    )

    assert run_async(updater._is_latest(matching, info)) is True
    assert run_async(updater._is_latest(UpdateContext(current=stale), info)) is False
    assert (
        run_async(
            updater._is_latest(
                matching.model_copy(update={"version": "8.26.0-beta.1"}),
                info,
            )
        )
        is False
    )
