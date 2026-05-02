"""Tests for newer updater modules added to the registry."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import ModuleType

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._assertions import expect_instance
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module as _load_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import EventStream, UpdateEvent, UpdateEventKind
from lib.update.updaters import github_release as github_release_module
from lib.update.updaters import materialization as materialization_mod
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.flake_backed import FlakeInputMetadataUpdater


@pytest.fixture(scope="module")
def commander_module() -> ModuleType:
    """Load the commander updater module."""
    return _load_module("packages/commander/updater.py", "commander_updater_test")


@pytest.fixture(scope="module")
def codex_desktop_module() -> ModuleType:
    """Load the codex-desktop updater module."""
    return _load_module(
        "packages/codex-desktop/updater.py", "codex_desktop_updater_test"
    )


@pytest.fixture(scope="module")
def codex_module() -> ModuleType:
    """Load the codex updater module."""
    return _load_module("packages/codex/updater.py", "codex_updater_test")


@pytest.fixture(scope="module")
def goose_cli_module() -> ModuleType:
    """Load the goose-cli updater module."""
    return _load_module("overlays/goose-cli/updater.py", "goose_cli_updater_test")


@pytest.fixture(scope="module")
def element_desktop_module() -> ModuleType:
    """Load the element-desktop updater module."""
    return _load_module(
        "overlays/element-desktop/updater.py", "element_desktop_updater_test"
    )


@pytest.fixture(scope="module")
def superset_module() -> ModuleType:
    """Load the superset updater module."""
    return _load_module("packages/superset/updater.py", "superset_updater_test")


@pytest.fixture(scope="module")
def crush_module() -> ModuleType:
    """Load the crush updater module."""
    return _load_module("overlays/crush/updater.py", "crush_updater_test")


@pytest.fixture(scope="module")
def mux_module() -> ModuleType:
    """Load the mux updater module."""
    return _load_module("packages/mux/updater.py", "mux_updater_test")


def test_mux_uses_platform_specific_node_modules_hashes(
    mux_module: ModuleType,
) -> None:
    """Mux node_modules hashes should be tracked separately per platform."""
    updater_cls = mux_module.MuxUpdater
    assert updater_cls.platform_specific is True
    assert updater_cls.hash_type == "nodeModulesHash"


def test_codex_updater_refreshes_crate2nix_artifacts(
    codex_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex should emit checked-in crate2nix artifacts during refreshes."""
    updater = codex_module.CodexUpdater()
    assert updater.materialize_when_current is True
    assert updater.shows_materialize_artifacts_phase is True

    async def _stream(name: str) -> EventStream:
        yield UpdateEvent.status(
            name,
            "Refreshing crate2nix artifacts...",
            operation="materialize_artifacts",
        )
        yield UpdateEvent.artifact(
            name,
            GeneratedArtifact.text("packages/codex/Cargo.nix", "{ codex = true; }\n"),
        )
        yield UpdateEvent.status(
            name,
            "Prepared crate2nix artifacts",
            operation="materialize_artifacts",
            status="updated",
            detail="crate2nix artifacts",
        )

    monkeypatch.setattr(
        codex_module.CodexUpdater,
        "stream_materialized_artifacts",
        lambda _self: _stream("codex"),
    )

    events = _run(_collect(updater.fetch_hashes(VersionInfo("main", {}), object())))
    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.ARTIFACT,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert events[-1].payload == []


def test_goose_cli_updater_emits_crate2nix_artifacts_before_src_hash(
    goose_cli_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Goose should refresh crate2nix artifacts before computing srcHash."""
    updater = goose_cli_module.GooseCliUpdater()
    assert updater.materialize_when_current is True
    assert updater.shows_materialize_artifacts_phase is True

    async def _stream(name: str) -> EventStream:
        yield UpdateEvent.artifact(
            name,
            GeneratedArtifact.text(
                "overlays/goose-cli/Cargo.nix",
                "{ goose = true; }\n",
            ),
        )

    async def _fixed_hash(_name: str, _expr: str, **_kwargs: object) -> EventStream:
        yield UpdateEvent.value(
            "goose-cli", "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        )

    monkeypatch.setattr(
        goose_cli_module.GooseCliUpdater,
        "stream_materialized_artifacts",
        lambda _self: _stream("goose-cli"),
    )
    monkeypatch.setattr(goose_cli_module, "compute_fixed_output_hash", _fixed_hash)

    events = _run(_collect(updater.fetch_hashes(VersionInfo("1.0.0", {}), object())))
    artifact_index = next(
        index
        for index, event in enumerate(events)
        if event.kind == UpdateEventKind.ARTIFACT
    )
    value_index = max(
        index
        for index, event in enumerate(events)
        if event.kind == UpdateEventKind.VALUE
    )
    assert artifact_index < value_index
    payload = expect_instance(events[value_index].payload, list)
    hash_entry = expect_instance(payload[0], HashEntry)
    assert hash_entry.hash_type == "srcHash"


def test_crate2nix_artifacts_mixin_streams_shared_materialization_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared crate2nix materialization mixin should proxy the standard stream."""

    async def _stream(name: str, *, operation: str) -> EventStream:
        yield UpdateEvent.status(name, "refreshing", operation=operation)
        yield UpdateEvent.artifact(
            name,
            GeneratedArtifact.text("packages/demo/Cargo.nix", "{ demo = true; }\n"),
        )

    monkeypatch.setattr(
        materialization_mod,
        "stream_crate2nix_artifact_updates",
        _stream,
    )

    class _Updater(materialization_mod.Crate2NixArtifactsMixin):
        name = "demo"

    updater = _Updater()
    events = _run(_collect(updater.stream_materialized_artifacts()))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.ARTIFACT,
    ]
    assert events[0].payload == {"operation": "materialize_artifacts"}
    artifact_payload = expect_instance(events[1].payload, list)
    assert len(artifact_payload) == 1
    assert artifact_payload[0].path == Path("packages/demo/Cargo.nix")


def test_commander_fetches_latest_version_from_changelog(
    commander_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse the latest version from a markdown changelog heading."""
    updater = commander_module.CommanderUpdater()
    monkeypatch.setattr(
        commander_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=b"# Changelog\n\n## 0.7.875 - 2026-03-16\n\n- Fix stuff\n",
        ),
    )
    monkeypatch.setattr(
        commander_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result={"ETag": '"abc"'}),
    )
    latest = _run(updater.fetch_latest(object()))
    assert latest.version == "0.7.875"


def test_commander_fetches_latest_version_from_html_changelog(
    commander_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse the latest version from the live HTML changelog heading."""
    updater = commander_module.CommanderUpdater()
    monkeypatch.setattr(
        commander_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=(
                b"<!DOCTYPE html><html><body><h1>Changelog</h1>"
                b"<h2>0.7.875 - 2026-03-16</h2>"
                b"<h3>Fix</h3></body></html>"
            ),
        ),
    )
    monkeypatch.setattr(
        commander_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result={"ETag": '"abc"'}),
    )
    latest = _run(updater.fetch_latest(object()))
    assert latest.version == "0.7.875"


def test_commander_uses_versioned_download_urls(
    commander_module: ModuleType,
) -> None:
    """Commander should pin each release to a versioned DMG URL when available."""
    updater = commander_module.CommanderUpdater()
    latest = VersionInfo(version="0.7.890")

    assert (
        updater.get_download_url("aarch64-darwin", latest)
        == "https://download.thecommander.app/release/Commander-0.7.890.dmg"
    )
    assert (
        updater.get_download_url("x86_64-darwin", latest)
        == "https://download.thecommander.app/release/Commander-0.7.890.dmg"
    )


def test_commander_falls_back_to_latest_download_url_when_versioned_asset_is_missing(
    commander_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the stable latest DMG URL when versioned release assets 404."""
    updater = commander_module.CommanderUpdater()
    changelog_version = "7.8.9"
    versioned_url = (
        f"https://download.thecommander.app/release/Commander-{changelog_version}.dmg"
    )
    monkeypatch.setattr(
        commander_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=(
                f"# Changelog\n\n## {changelog_version} - 2026-03-30\n\n- Fix stuff\n"
            ).encode(),
        ),
    )

    calls: list[str] = []

    async def _fetch_headers(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> dict[str, str]:
        calls.append(url)
        if url == versioned_url:
            raise RuntimeError(
                "Request to versioned asset failed after 3 attempts: HTTP error 404"
            )
        return {"ETag": '"fallback"'}

    monkeypatch.setattr(commander_module, "fetch_headers", _fetch_headers)

    latest = _run(updater.fetch_latest(object()))
    assert latest.version == changelog_version
    assert (
        latest.metadata["url"]
        == "https://download.thecommander.app/release/Commander.dmg"
    )
    assert (
        updater.get_download_url("aarch64-darwin", latest)
        == "https://download.thecommander.app/release/Commander.dmg"
    )
    assert calls == [
        versioned_url,
        "https://download.thecommander.app/release/Commander.dmg",
    ]


def test_commander_propagates_non_404_probe_failures(
    commander_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not silently mask transient or unexpected probe errors."""
    updater = commander_module.CommanderUpdater()
    changelog_version = "7.8.9"
    monkeypatch.setattr(
        commander_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=(
                f"# Changelog\n\n## {changelog_version} - 2026-03-30\n\n- Fix stuff\n"
            ).encode(),
        ),
    )

    async def _fetch_headers_fail(
        _session: object,
        _url: str,
        **_kwargs: object,
    ) -> dict[str, str]:
        raise RuntimeError("network timeout")

    monkeypatch.setattr(commander_module, "fetch_headers", _fetch_headers_fail)

    with pytest.raises(RuntimeError, match="network timeout"):
        _run(updater.fetch_latest(object()))


def test_commander_rejects_changelog_without_release_heading(
    commander_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail when the changelog page lacks a release heading."""
    updater = commander_module.CommanderUpdater()
    monkeypatch.setattr(
        commander_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=b"# Changelog\n\nNo releases yet\n"),
    )
    with pytest.raises(RuntimeError, match="Could not parse latest Commander version"):
        _run(updater.fetch_latest(object()))


def test_codex_desktop_reads_platform_appcasts(
    codex_desktop_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve Codex desktop version and immutable ZIP URLs from appcasts."""
    updater = codex_desktop_module.CodexDesktopUpdater()
    arm_url = "https://example.invalid/Codex-darwin-arm64-26.429.20946.zip"
    x64_url = "https://example.invalid/Codex-darwin-x64-26.429.20946.zip"

    def _appcast(download_url: str) -> bytes:
        return f"""
            <rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
              <channel><item>
                <sparkle:version>2312</sparkle:version>
                <sparkle:shortVersionString>26.429.20946</sparkle:shortVersionString>
                <enclosure url="{download_url}" />
              </item></channel>
            </rss>
        """.encode()

    async def _fetch_url(_session: object, url: str, **kwargs: object) -> bytes:
        assert kwargs["user_agent"] == "Sparkle/2.0"
        assert kwargs["request_timeout"] == updater.config.default_timeout
        assert kwargs["config"] == updater.config
        if url == updater.APPCASTS["aarch64-darwin"]:
            return _appcast(arm_url)
        if url == updater.APPCASTS["x86_64-darwin"]:
            return _appcast(x64_url)
        raise AssertionError(url)

    monkeypatch.setattr(codex_desktop_module, "fetch_url", _fetch_url)

    latest = _run(updater.fetch_latest(object()))

    assert latest.version == "26.429.20946-2312"
    assert updater.get_download_url("aarch64-darwin", latest) == arm_url
    assert updater.get_download_url("x86_64-darwin", latest) == x64_url


def test_codex_desktop_rejects_mismatched_appcast_versions(
    codex_desktop_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail if the architecture appcasts do not agree on one release."""
    updater = codex_desktop_module.CodexDesktopUpdater()

    async def _fetch_url(_session: object, url: str, **_kwargs: object) -> bytes:
        version = "26.429.20946" if url.endswith("appcast.xml") else "26.430.1"
        return f"""
            <rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
              <channel><item>
                <sparkle:version>2312</sparkle:version>
                <sparkle:shortVersionString>{version}</sparkle:shortVersionString>
                <enclosure url="https://example.invalid/Codex.zip" />
              </item></channel>
            </rss>
        """.encode()

    monkeypatch.setattr(codex_desktop_module, "fetch_url", _fetch_url)

    with pytest.raises(RuntimeError, match="codex-desktop version mismatch"):
        _run(updater.fetch_latest(object()))


def test_codex_desktop_rejects_invalid_appcast_shape(
    codex_desktop_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface appcast parsing errors with Codex-specific messages."""
    updater = codex_desktop_module.CodexDesktopUpdater()
    monkeypatch.setattr(
        codex_desktop_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=b"<rss><channel /></rss>"),
    )

    with pytest.raises(RuntimeError, match="No items found in Codex appcast"):
        _run(updater.fetch_latest(object()))


@pytest.mark.parametrize(
    ("item_xml", "message"),
    [
        (
            """
            <sparkle:version>2312</sparkle:version>
            <sparkle:shortVersionString />
            <enclosure url="https://example.invalid/Codex.zip" />
            """,
            "Blank short version found in Codex appcast",
        ),
        (
            """
            <sparkle:version>2312</sparkle:version>
            <sparkle:shortVersionString>26.429.20946</sparkle:shortVersionString>
            <enclosure />
            """,
            "No URL found in Codex appcast enclosure",
        ),
    ],
)
def test_codex_desktop_rejects_blank_appcast_fields(
    codex_desktop_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    item_xml: str,
    message: str,
) -> None:
    """Reject appcast items whose required fields are present but blank."""
    updater = codex_desktop_module.CodexDesktopUpdater()
    payload = f"""
        <rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
          <channel><item>{item_xml}</item></channel>
        </rss>
    """.encode()
    monkeypatch.setattr(
        codex_desktop_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=payload),
    )

    with pytest.raises(RuntimeError, match=message):
        _run(updater.fetch_latest(object()))


@pytest.mark.parametrize(
    ("metadata", "expected_error", "message"),
    [
        (
            {"asset_urls": "https://example.invalid/Codex.zip"},
            TypeError,
            "Invalid Codex desktop asset URLs in metadata",
        ),
        (
            {"asset_urls": {}},
            RuntimeError,
            "Missing Codex desktop URL for platform 'aarch64-darwin'",
        ),
        (
            {"asset_urls": {"aarch64-darwin": " "}},
            RuntimeError,
            "Missing Codex desktop URL for platform 'aarch64-darwin'",
        ),
    ],
)
def test_codex_desktop_rejects_invalid_asset_url_metadata(
    codex_desktop_module: ModuleType,
    metadata: object,
    expected_error: type[Exception],
    message: str,
) -> None:
    """Reject malformed appcast URL metadata instead of fabricating a bad URL."""
    updater = codex_desktop_module.CodexDesktopUpdater()

    with pytest.raises(expected_error, match=message):
        updater.get_download_url(
            "aarch64-darwin", VersionInfo("26.429.20946", metadata)
        )


def test_element_desktop_reads_pinned_version_from_sources(
    element_desktop_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load pinned version from per-package sources file."""
    updater = element_desktop_module.ElementDesktopUpdater()
    pinned_version = "fixture-version"
    monkeypatch.setattr(
        element_desktop_module,
        "read_pinned_source_version",
        lambda _n: pinned_version,
    )
    latest = _run(updater.fetch_latest(object()))
    assert latest.version == pinned_version

    is_latest = _run(updater._is_latest(None, latest))
    assert is_latest is False


def test_flake_input_metadata_updater_emits_empty_hash_entries() -> None:
    """Metadata-only flake inputs should still emit a typed empty value event."""

    class _DemoUpdater(FlakeInputMetadataUpdater):
        name = "demo"
        input_name = "demo"

    updater = _DemoUpdater()
    events = _run(_collect(updater.fetch_hashes(VersionInfo("main"), object())))

    assert [event.kind for event in events] == [UpdateEventKind.VALUE]
    assert events[0].source == "demo"
    assert events[0].payload == []


def test_superset_fetches_desktop_release_assets(
    superset_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve desktop version and release asset URL from GitHub releases."""
    updater = superset_module.SupersetUpdater()
    monkeypatch.setattr(
        github_release_module,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "desktop-v1.2.3",
                "assets": [
                    {
                        "name": "superset-1.2.3-x86_64.AppImage",
                        "browser_download_url": "https://example.test/superset-1.2.3-x86_64.AppImage",
                    }
                ],
            },
        ),
    )

    latest = _run(updater.fetch_latest(object()))
    assert latest.version == "1.2.3"
    assert (
        updater.get_download_url("x86_64-linux", latest)
        == "https://example.test/superset-1.2.3-x86_64.AppImage"
    )


def test_superset_rejects_release_without_expected_asset(
    superset_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail when latest desktop release misses the expected Linux AppImage."""
    updater = superset_module.SupersetUpdater()
    monkeypatch.setattr(
        github_release_module,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "desktop-v1.2.3",
                "assets": [
                    {
                        "name": "superset-1.2.3-arm64.AppImage",
                        "browser_download_url": "https://example.test/other.AppImage",
                    }
                ],
            },
        ),
    )

    with pytest.raises(
        RuntimeError, match="Could not find Superset desktop release asset"
    ):
        _run(updater.fetch_latest(object()))


def test_superset_rejects_unexpected_release_tag(
    superset_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject release payloads that are not desktop-tagged."""
    updater = superset_module.SupersetUpdater()
    monkeypatch.setattr(
        github_release_module,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "v1.2.3",
                "assets": [],
            },
        ),
    )

    with pytest.raises(RuntimeError, match="Unexpected Superset release tag format"):
        _run(updater.fetch_latest(object()))


def test_superset_release_url_format(superset_module: ModuleType) -> None:
    """Generate fallback release URL and reject unsupported platforms."""
    updater = superset_module.SupersetUpdater()
    url = updater.get_download_url("x86_64-linux", VersionInfo("1.2.3", {}))
    assert (
        url
        == "https://github.com/superset-sh/superset/releases/download/desktop-v1.2.3/superset-1.2.3-x86_64.AppImage"
    )
    with pytest.raises(RuntimeError, match="Unsupported platform"):
        updater.get_download_url("aarch64-linux", VersionInfo("1.2.3", {}))


def test_crush_prefers_newest_release_compatible_with_repo_go_floor(
    crush_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scan far enough back to find the newest release the current Go can build."""
    updater = crush_module.CrushUpdater()
    compatible_tag = "v1.2.3"
    compatible_version = compatible_tag.removeprefix("v")
    monkeypatch.setattr(
        updater,
        "_resolve_supported_go_version",
        lambda: asyncio.sleep(0, result=(1, 26, 1)),
    )

    captured_kwargs: dict[str, object] = {}

    async def _fake_fetch_releases(
        *_a: object, **kwargs: object
    ) -> list[dict[str, object]]:
        captured_kwargs.update(kwargs)
        return [
            *[
                {"tag_name": f"v0.{minor}.0", "draft": False, "prerelease": False}
                for minor in range(67, 56, -1)
            ],
            {"tag_name": compatible_tag, "draft": False, "prerelease": False},
        ]

    monkeypatch.setattr(
        crush_module, "fetch_github_api_paginated", _fake_fetch_releases
    )
    monkeypatch.setattr(
        crush_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=(
                b"module github.com/charmbracelet/crush\n\ngo 1.26.1\n"
                if compatible_tag in _a[1]
                else b"module github.com/charmbracelet/crush\n\ngo 1.27.0\n"
            ),
        ),
    )

    latest = _run(updater.fetch_latest(object()))
    assert latest.version == compatible_version
    assert latest.metadata.tag == compatible_tag
    assert captured_kwargs["per_page"] == 100
    assert "item_limit" not in captured_kwargs


def test_crush_falls_back_to_current_pin_when_no_release_is_compatible(
    crush_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve the current crush pin until nixpkgs can build newer releases."""
    updater = crush_module.CrushUpdater()
    pinned_version = "fixture-version"
    monkeypatch.setattr(
        updater,
        "_resolve_supported_go_version",
        lambda: asyncio.sleep(0, result=(1, 26, 1)),
    )
    monkeypatch.setattr(
        crush_module,
        "fetch_github_api_paginated",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=[
                {"tag_name": "v0.57.0", "draft": False, "prerelease": False},
            ],
        ),
    )
    monkeypatch.setattr(
        crush_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=b"module github.com/charmbracelet/crush\n\ngo 1.26.2\n",
        ),
    )
    monkeypatch.setattr(
        crush_module,
        "read_pinned_source_version",
        lambda _n: pinned_version,
    )

    latest = _run(updater.fetch_latest(object()))
    assert latest.version == pinned_version
    assert latest.metadata.tag == f"v{pinned_version}"
