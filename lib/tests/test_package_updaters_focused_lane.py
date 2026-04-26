"""Focused tests for a small non-overlapping package-updater lane."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import install_fixed_hash_stream
from lib.tests._updater_helpers import load_repo_module as _load_updater
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEventKind
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.metadata import GranolaFeedMetadata


def test_granola_fetch_latest_and_download_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse the Electron feed and build the versioned download URL."""
    module = _load_updater("packages/granola/updater.py", "granola_updater_test")
    updater = module.GranolaUpdater()

    async def _fetch_url(_session: object, url: str, **kwargs: object) -> bytes:
        assert url == updater.FEED_URL
        assert kwargs["request_timeout"] == updater.config.default_timeout
        assert kwargs["config"] == updater.config
        return b"version: 2.3.4\npath: Granola-mac.zip\nsha512: deadbeef\n"

    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    latest = _run(updater.fetch_latest(object()))

    assert latest == VersionInfo(
        version="2.3.4",
        metadata=GranolaFeedMetadata(path="Granola-mac.zip", sha512="deadbeef"),
    )
    assert (
        updater.get_download_url("aarch64-darwin", latest)
        == "https://dr2v7l5emb758.cloudfront.net/2.3.4/Granola-mac.zip"
    )


def test_granola_rejects_non_mapping_feed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail cleanly when the YAML feed is not an object."""
    module = _load_updater(
        "packages/granola/updater.py", "granola_updater_test_bad_payload"
    )
    updater = module.GranolaUpdater()
    monkeypatch.setattr(
        module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=b"- not\n- an\n- object\n"),
    )

    with pytest.raises(TypeError, match="Expected JSON object"):
        _run(updater.fetch_latest(object()))


def test_raycast_platform_urls_and_commit_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use Raycast's per-arch feed shape and preserve commit metadata."""
    module = _load_updater("packages/raycast/updater.py", "raycast_updater_test")
    updater = module.RaycastUpdater()

    payloads = {
        "arm": {"version": "1.99.0", "targetCommitish": "abc123"},
        "x86_64": {"version": "1.99.0", "targetCommitish": "abc123"},
    }

    async def _fetch_json(_session: object, url: str, **_kwargs: object) -> object:
        return payloads[url.rsplit("=", maxsplit=1)[-1]]

    monkeypatch.setattr("lib.update.updaters.platform_api.fetch_json", _fetch_json)

    latest = _run(updater.fetch_latest(object()))

    assert latest.version == "1.99.0"
    assert latest.metadata.commit == "abc123"
    assert (
        updater._api_url("arm")
        == "https://releases.raycast.com/releases/latest?build=arm"
    )
    assert (
        updater._download_url("x86_64", latest)
        == "https://releases.raycast.com/releases/1.99.0/download?build=x86_64"
    )


def test_wispr_flow_platform_urls_and_required_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use Wispr Flow's Darwin JSON feed and versioned DMG URLs."""
    module = _load_updater("packages/wispr-flow/updater.py", "wispr_flow_updater_test")
    updater = module.WisprFlowUpdater()

    payloads = {
        "arm64": {"currentRelease": "5.4.3"},
        "x64": {"currentRelease": "5.4.3"},
    }

    async def _fetch_json(_session: object, url: str, **_kwargs: object) -> object:
        return payloads[url.split("/")[-2]]

    monkeypatch.setattr("lib.update.updaters.platform_api.fetch_json", _fetch_json)

    latest = _run(updater.fetch_latest(object()))

    assert latest.version == "5.4.3"
    assert updater.required_tools == ("nix", "nix-prefetch-url")
    assert (
        updater._api_url("arm64")
        == "https://dl.wisprflow.com/wispr-flow/darwin/arm64/RELEASES.json"
    )
    assert (
        updater._download_url("x64", latest)
        == "https://dl.wisprflow.com/wispr-flow/darwin/x64/dmgs/Flow-v5.4.3.dmg"
    )


def test_codex_desktop_version_derivation_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefer Content-MD5, then ETag, then Last-Modified."""
    module = _load_updater(
        "packages/codex-desktop/updater.py", "codex_desktop_updater_test"
    )
    updater = module.CodexDesktopUpdater()

    monkeypatch.setattr(
        module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={"Content-MD5": "dSDx9z9xMK/8IITfW12Edg=="},
        ),
    )
    assert _run(updater.fetch_latest(object())).version == (
        "md5.7520f1f73f7130affc2084df5b5d8476"
    )

    monkeypatch.setattr(
        module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result={"ETag": '"0xABCDEF"'}),
    )
    assert _run(updater.fetch_latest(object())).version == "etag.abcdef"

    monkeypatch.setattr(
        module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={"Last-Modified": "Wed, 04 Mar 2026 00:25:01 GMT"},
        ),
    )
    assert _run(updater.fetch_latest(object())).version == "modified.20260304002501"


@pytest.mark.parametrize(
    ("headers", "match"),
    [
        ({"Content-MD5": "not-base64"}, "Invalid Content-MD5"),
        ({"Last-Modified": "not-a-date"}, "Could not parse Last-Modified"),
        ({}, "Missing Content-MD5/ETag/Last-Modified headers"),
    ],
)
def test_codex_desktop_rejects_invalid_header_inputs(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    match: str,
) -> None:
    """Raise clear errors for malformed or missing artifact metadata."""
    module = _load_updater(
        "packages/codex-desktop/updater.py", "codex_desktop_updater_test_errors"
    )
    updater = module.CodexDesktopUpdater()
    monkeypatch.setattr(
        module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result=headers),
    )

    with pytest.raises(RuntimeError, match=match):
        _run(updater.fetch_latest(object()))


def test_netnewswire_fetch_latest_and_download_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse the Sparkle appcast and return the enclosure URL."""
    module = _load_updater(
        "packages/netnewswire/updater.py", "netnewswire_updater_test"
    )
    updater = module.NetNewsWireUpdater()
    xml = (
        '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
        "<channel><item><enclosure "
        'url="https://example.invalid/NetNewsWire.zip" '
        'sparkle:shortVersionString="6.2.1"/>'
        "</item></channel></rss>"
    )

    async def _fetch_url(_session: object, url: str, **kwargs: object) -> bytes:
        assert url == updater.APPCAST_URL
        assert kwargs["user_agent"] == "Sparkle/2.0"
        assert kwargs["timeout"] == updater.config.default_timeout
        return xml.encode()

    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    latest = _run(updater.fetch_latest(object()))

    assert latest.version == "6.2.1"
    assert latest.metadata.url == "https://example.invalid/NetNewsWire.zip"
    assert (
        updater.get_download_url("x86_64-darwin", latest)
        == "https://example.invalid/NetNewsWire.zip"
    )


@pytest.mark.parametrize(
    ("xml", "match"),
    [
        ("<", "Invalid appcast XML"),
        ("<rss><channel /></rss>", "No items found in appcast"),
        ("<rss><channel><item /></channel></rss>", "No enclosure found in appcast"),
        (
            '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><enclosure url="https://example.invalid"/></item></channel></rss>',
            "No version found in appcast enclosure",
        ),
        (
            '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><enclosure sparkle:shortVersionString="6.2.1"/></item></channel></rss>',
            "No URL found in enclosure",
        ),
    ],
)
def test_netnewswire_rejects_invalid_appcast_shapes(
    monkeypatch: pytest.MonkeyPatch,
    xml: str,
    match: str,
) -> None:
    """Surface targeted appcast parsing errors."""
    module = _load_updater(
        "packages/netnewswire/updater.py", "netnewswire_updater_test_errors"
    )
    updater = module.NetNewsWireUpdater()
    monkeypatch.setattr(
        module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=xml.encode()),
    )

    with pytest.raises(RuntimeError, match=match):
        _run(updater.fetch_latest(object()))


def test_netnewswire_requires_download_url_metadata() -> None:
    """Reject metadata payloads that lack the stored download URL."""
    module = _load_updater(
        "packages/netnewswire/updater.py", "netnewswire_updater_test_missing_url"
    )
    updater = module.NetNewsWireUpdater()

    with pytest.raises(RuntimeError, match="Missing NetNewsWire download URL"):
        updater.get_download_url("aarch64-darwin", VersionInfo(version="6.2.1"))


def test_sculptor_fetch_latest_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Derive the date version from Last-Modified and fall back when needed."""
    module = _load_updater("packages/sculptor/updater.py", "sculptor_updater_test")
    updater = module.SculptorUpdater()

    monkeypatch.setattr(
        module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={"Last-Modified": "Tue, 20 Feb 2024 12:34:56 GMT"},
        ),
    )
    assert _run(updater.fetch_latest(object())).version == "2024-02-20"

    monkeypatch.setattr(
        module,
        "parsedate_to_datetime",
        lambda _value: (_ for _ in ()).throw(ValueError("bad date")),
    )
    monkeypatch.setattr(
        module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result={"Last-Modified": "invalid-date"}),
    )
    assert _run(updater.fetch_latest(object())).version == "invalid-da"


def test_sculptor_accepts_naive_last_modified_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat naive parsed dates as UTC before normalizing."""
    module = _load_updater(
        "packages/sculptor/updater.py", "sculptor_updater_test_naive_timestamp"
    )
    updater = module.SculptorUpdater()
    monkeypatch.setattr(
        module,
        "parsedate_to_datetime",
        lambda _value: datetime(2024, 2, 20, 12, 34, 56, tzinfo=UTC).replace(
            tzinfo=None
        ),
    )
    monkeypatch.setattr(
        module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={"Last-Modified": "Tue, 20 Feb 2024 12:34:56"},
        ),
    )

    assert _run(updater.fetch_latest(object())).version == "2024-02-20"


def test_sculptor_rejects_missing_last_modified_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require the object-store metadata header used for versioning."""
    module = _load_updater(
        "packages/sculptor/updater.py", "sculptor_updater_test_missing_header"
    )
    updater = module.SculptorUpdater()
    monkeypatch.setattr(
        module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result={"Last-Modified": ""}),
    )

    with pytest.raises(RuntimeError, match="No Last-Modified header"):
        _run(updater.fetch_latest(object()))


def test_zen_twilight_reads_pinned_channel_and_recomputes_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep Twilight pinned to its channel artifact while forcing hash refreshes."""
    module = _load_updater(
        "packages/zen-twilight/updater.py", "zen_twilight_updater_test"
    )
    updater = module.ZenTwilightUpdater()
    pinned_version = "1.2.3"
    monkeypatch.setattr(
        module,
        "read_pinned_source_version",
        lambda name: pinned_version if name == "zen-twilight" else "",
    )

    latest = _run(updater.fetch_latest(object()))

    assert latest == VersionInfo(version=pinned_version)
    assert updater.PLATFORMS == {
        "aarch64-darwin": updater.TWILIGHT_DMG_URL,
        "x86_64-darwin": updater.TWILIGHT_DMG_URL,
    }
    assert _run(updater._is_latest(None, latest)) is False


def test_scratch_expr_builders_fetch_hashes_and_build_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build flake-based expressions and emit both npm and cargo hash entries."""
    module = _load_updater("packages/scratch/updater.py", "scratch_updater_test")
    updater = module.ScratchUpdater()
    latest = VersionInfo(version="9.9.9", metadata={"commit": "f" * 40})

    wrapped_expr = updater._wrap_expr_with_flake_and_pkgs(
        module.identifier_attr_path("pkgs", "hello")
    )
    assert "builtins.getFlake" in wrapped_expr
    assert f"git+file://{module.REPO_ROOT}?dirty=1" in wrapped_expr

    npm_expr = updater._expr_for_npm_deps()
    cargo_expr = updater._expr_for_cargo_vendor()
    assert "fetchNpmDeps" in npm_expr
    assert 'name = "scratch-npm-deps"' in npm_expr
    assert "fetchCargoVendor" in cargo_expr
    assert '"/src-tauri"' in cargo_expr

    calls = install_fixed_hash_stream(
        monkeypatch,
        (("hashing npm deps", "sha256-npm"), ("hashing cargo vendor", "sha256-cargo")),
    )

    events = _run(_collect_events(updater.fetch_hashes(latest, object())))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert [event.message for event in events[:-1]] == [
        "hashing npm deps",
        "hashing cargo vendor",
    ]
    assert calls == [
        {
            "name": "scratch",
            "expr": npm_expr,
            "env": None,
            "config": updater.config,
        },
        {
            "name": "scratch",
            "expr": cargo_expr,
            "env": None,
            "config": updater.config,
        },
    ]
    assert events[-1].payload == [
        HashEntry.create("npmDepsHash", "sha256-npm"),
        HashEntry.create("cargoHash", "sha256-cargo"),
    ]

    result = updater.build_result(latest, events[-1].payload)
    assert result.version == "9.9.9"
    assert result.input == "scratch"
    assert result.commit == "f" * 40
