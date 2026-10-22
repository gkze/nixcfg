"""Tests for newer updater modules added to the registry."""

import asyncio
from pathlib import Path
from types import ModuleType

import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.tests._assertions import expect_instance
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module_for_test as _load_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import (
    EventSink,
    StatusInfo,
    StatusKind,
    StatusPayload,
    UpdateEvent,
    UpdateEventKind,
    ignore_event,
)
from lib.update.updaters import VersionInfo
from lib.update.updaters import materialization as materialization_mod
from lib.update.updaters.core import UpdateContext
from lib.update.updaters.flake_backed import FlakeInputMetadataUpdater
from lib.update.updaters.metadata import NO_METADATA, FlakeInputMetadata


@pytest.fixture(scope="module")
def commander_module() -> ModuleType:
    """Load the commander updater module."""
    return _load_module("packages/commander/updater.py", prefix="commander")


@pytest.fixture(scope="module")
def chatgpt_module() -> ModuleType:
    """Load the chatgpt updater module."""
    return _load_module("packages/chatgpt/updater.py", prefix="chatgpt")


@pytest.fixture(scope="module")
def codex_module() -> ModuleType:
    """Load the codex updater module."""
    return _load_module("packages/codex/updater.py", prefix="codex")


@pytest.fixture(scope="module")
def goose_cli_module() -> ModuleType:
    """Load the goose-cli updater module."""
    return _load_module("overlays/goose-cli/updater.py", prefix="goose_cli")


@pytest.fixture(scope="module")
def crush_module() -> ModuleType:
    """Load the crush updater module."""
    return _load_module("overlays/crush/updater.py", prefix="crush")


@pytest.fixture(scope="module")
def mux_module() -> ModuleType:
    """Load the mux updater module."""
    return _load_module("packages/mux/updater.py", prefix="mux")


def test_mux_uses_platform_specific_node_modules_hashes(
    mux_module: ModuleType,
) -> None:
    """Mux node_modules hashes should be tracked separately per platform."""
    updater_cls = mux_module.MuxUpdater
    assert updater_cls.platform_specific is True
    assert updater_cls.hash_type == "nodeModulesHash"
    assert updater_cls.compatibility_pins is None


def test_codex_updater_refreshes_crate2nix_artifacts(
    codex_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex should emit checked-in crate2nix artifacts during refreshes."""
    updater = codex_module.CodexUpdater()
    assert updater.materialize_when_current is True
    assert updater.shows_materialize_artifacts_phase is True

    async def _stream(name: str, *, emit: EventSink = ignore_event) -> object:
        await emit(
            UpdateEvent.status(
                name,
                "Refreshing crate2nix artifacts...",
                operation="materialize_artifacts",
            )
        )
        await emit(
            UpdateEvent.artifact(
                name,
                GeneratedArtifact.text(
                    "packages/codex/Cargo.nix", "{ codex = true; }\n"
                ),
            )
        )
        await emit(
            UpdateEvent.status(
                name,
                "Prepared crate2nix artifacts",
                operation="materialize_artifacts",
                status=StatusInfo(kind=StatusKind.UPDATED, value="crate2nix artifacts"),
            )
        )

    monkeypatch.setattr(
        codex_module.CodexUpdater,
        "stream_materialized_artifacts",
        lambda _self, *, emit, **_kwargs: _stream("codex", emit=emit),
    )

    events = _run(
        _collect(
            lambda emit: updater.fetch_hashes(
                VersionInfo("main", {}),
                object(),
                context=UpdateContext(current=None),
                emit=emit,
            )
        )
    )
    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.ARTIFACT,
        UpdateEventKind.STATUS,
    ]
    assert events.result == []


def test_goose_cli_updater_materializes_crate2nix_from_locked_input(
    goose_cli_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Goose should refresh crate2nix artifacts from its locked flake input."""
    updater = goose_cli_module.GooseCliUpdater()
    assert updater.bulk_update_hold is not None
    assert "macos-15" in updater.bulk_update_hold
    assert updater.materialize_when_current is True
    assert updater.shows_materialize_artifacts_phase is True
    assert updater.input_name == "goose"

    async def _stream(name: str, *, emit: EventSink = ignore_event) -> object:
        await emit(
            UpdateEvent.artifact(
                name,
                GeneratedArtifact.text(
                    "overlays/goose-cli/Cargo.nix",
                    "{ goose = true; }\n",
                ),
            )
        )

    def _materialize(_self: object, *, emit, **_kwargs: object):
        return _stream("goose-cli", emit=emit)

    monkeypatch.setattr(
        goose_cli_module.GooseCliUpdater,
        "stream_materialized_artifacts",
        _materialize,
    )

    events = _run(
        _collect(
            lambda emit: updater.fetch_hashes(
                VersionInfo("1.0.0", {}),
                object(),
                context=UpdateContext(current=None),
                emit=emit,
            )
        )
    )
    artifact_index = next(
        index
        for index, event in enumerate(events)
        if event.kind == UpdateEventKind.ARTIFACT
    )
    assert artifact_index >= 0
    assert events.result == []


def test_goose_cli_updater_reads_version_and_commit_from_release_input(
    goose_cli_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The package metadata should come from the locked Goose release source."""
    node = FlakeLockNode.model_validate({
        "original": {
            "type": "github",
            "owner": "aaif-goose",
            "repo": "goose",
            "ref": "v1.46.0",
        },
        "locked": {
            "type": "github",
            "owner": "aaif-goose",
            "repo": "goose",
            "rev": "a" * 40,
            "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        },
    })
    updater = goose_cli_module.GooseCliUpdater()
    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)

    info = _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))

    assert info.version == "1.46.0"
    assert info.commit == "a" * 40
    assert info.metadata == FlakeInputMetadata(node=node, commit="a" * 40)


def test_crate2nix_artifacts_mixin_streams_shared_materialization_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared crate2nix materialization mixin should proxy the standard stream."""

    async def _stream(
        name: str, *, operation: str, emit: EventSink = ignore_event
    ) -> object:
        await emit(UpdateEvent.status(name, "refreshing", operation=operation))
        await emit(
            UpdateEvent.artifact(
                name,
                GeneratedArtifact.text("packages/demo/Cargo.nix", "{ demo = true; }\n"),
            )
        )

    monkeypatch.setattr(
        materialization_mod,
        "stream_crate2nix_artifact_updates",
        _stream,
    )

    class _Updater(
        materialization_mod.Crate2NixArtifactsMixin, FlakeInputMetadataUpdater
    ):
        name = "demo"

    updater = _Updater()
    events = _run(
        _collect(lambda emit: updater.stream_materialized_artifacts(emit=emit))
    )

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.ARTIFACT,
    ]
    assert events[0].payload == StatusPayload(operation="materialize_artifacts")
    artifact_payload = expect_instance(events[1].payload, list)
    assert len(artifact_payload) == 1
    assert artifact_payload[0].path == Path("packages/demo/Cargo.nix")


def test_commander_tracks_the_installed_app_signed_release_feed(
    commander_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The release appcast advances even when the website changelog stays stale."""
    from lib.update.updaters.vendor_feeds import SparkleAppcastItem

    updater = commander_module.CommanderUpdater()
    url = "https://softwareupdate.thecommander.app/release/Commander-0.7.1055.zip"
    requested: list[str] = []

    async def fetch_items(_session: object, feed_url: str, **_kwargs: object):
        requested.append(feed_url)
        return (SparkleAppcastItem("0.7.1055", "0.7.1055", url),)

    monkeypatch.setattr(
        "lib.update.updaters.strategies.fetch_sparkle_appcast_items", fetch_items
    )
    latest = _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))

    assert requested == [
        "https://softwareupdate.thecommander.app/appcast/commanderai-appcast.xml"
    ]
    assert latest.version == "0.7.1055"
    for platform in ("aarch64-darwin", "x86_64-darwin"):
        assert updater.get_download_url(platform, latest) == url


def test_commander_recomputes_hashes_for_mutable_release_artifacts(
    commander_module: ModuleType,
) -> None:
    """A version string alone must not freeze mutable release archives."""
    updater = commander_module.CommanderUpdater()
    latest = VersionInfo(version="0.7.1055")

    assert _run(updater._is_latest(UpdateContext(current=None), latest)) is False


def test_chatgpt_reads_platform_appcasts_and_linux_deb_indexes(
    chatgpt_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve the newest common release plus Darwin ZIP and Linux deb URLs."""
    updater = chatgpt_module.ChatGPTUpdater()
    newer_arm_url = "https://example.invalid/ChatGPT-darwin-arm64-26.429.61741.zip"
    arm_url = "https://example.invalid/ChatGPT-darwin-arm64-26.429.20946.zip"
    x64_url = "https://example.invalid/ChatGPT-darwin-x64-26.429.20946.zip"
    deb_version = "26.429.20946"
    arm_deb_name = f"pool/main/c/chatgpt/chatgpt_{deb_version}_arm64.deb"
    x64_deb_name = f"pool/main/c/chatgpt/chatgpt_{deb_version}_amd64.deb"

    def _item(version: str, build: str, download_url: str) -> str:
        return f"""
            <item>
              <sparkle:version>{build}</sparkle:version>
              <sparkle:shortVersionString>{version}</sparkle:shortVersionString>
              <enclosure url="{download_url}" />
            </item>
        """

    def _appcast(download_url: str, *, include_newer_arm: bool = False) -> bytes:
        items = ""
        if include_newer_arm:
            items += _item("26.429.61741", "2429", newer_arm_url)
        items += _item("26.429.20946", "2312", download_url)
        return f"""
            <rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
              <channel>{items}</channel>
            </rss>
        """.encode()

    def _deb_index(filename: str) -> bytes:
        # Real ``Packages`` stanzas are unindented; continuation lines start
        # with whitespace and must not be mistaken for fields.
        return (
            "Package: chatgpt\n"
            f"Version: {deb_version}\n"
            "Architecture: arm64\n"
            "Depends: libgtk-3-0,\n libnotify4\n"
            f"Filename: {filename}\n"
            f"SHA256: {'0' * 64}\n"
            "\n"
        ).encode()

    async def _fetch_url(_session: object, url: str, **kwargs: object) -> bytes:
        assert kwargs["request_timeout"] == updater.config.default_timeout
        assert kwargs["config"] == updater.config
        if url in updater.APPCASTS.values():
            assert kwargs["user_agent"] == "Sparkle/2.0"
            if url == updater.APPCASTS["aarch64-darwin"]:
                return _appcast(arm_url, include_newer_arm=True)
            return _appcast(x64_url)
        if url == updater.DEB_INDEXES["aarch64-linux"]:
            # The deb index is a plain repository file, not a Sparkle request.
            assert "user_agent" not in kwargs
            return _deb_index(arm_deb_name)
        if url == updater.DEB_INDEXES["x86_64-linux"]:
            assert "user_agent" not in kwargs
            return _deb_index(x64_deb_name)
        raise AssertionError(url)

    monkeypatch.setattr(chatgpt_module, "fetch_url", _fetch_url)

    latest = _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))

    assert latest.version == "26.429.20946-2312"
    assert updater.get_download_url("aarch64-darwin", latest) == arm_url
    assert updater.get_download_url("x86_64-darwin", latest) == x64_url
    assert (
        updater.get_download_url("aarch64-linux", latest)
        == f"{updater.DEB_BASE_URL}/{arm_deb_name}"
    )
    assert (
        updater.get_download_url("x86_64-linux", latest)
        == f"{updater.DEB_BASE_URL}/{x64_deb_name}"
    )
    assert set(updater._platform_urls(latest)) == set(updater.PLATFORMS)


def test_chatgpt_selects_newest_deb_stanza(
    chatgpt_module: ModuleType,
) -> None:
    """Pick the highest deb version when the index lists several."""
    updater = chatgpt_module.ChatGPTUpdater()
    payload = (
        "Package: chatgpt\n"
        "Version: 26.429.20946\n"
        "Filename: pool/main/c/chatgpt/chatgpt_26.429.20946_arm64.deb\n"
        "\n"
        "Package: chatgpt\n"
        "Version: 26.1007.21434\n"
        "Filename: pool/main/c/chatgpt/chatgpt_26.1007.21434_arm64.deb\n"
        "\n"
        "Package: unrelated\n"
        "Version: 99.99.99\n"
        "Filename: pool/main/u/unrelated/unrelated_99.99.99_arm64.deb\n"
    )

    stanza = updater._select_deb_stanza(
        payload,
        index_url=updater.DEB_INDEXES["aarch64-linux"],
    )

    assert stanza["Version"] == "26.1007.21434"


def test_chatgpt_rejects_deb_index_without_package(
    chatgpt_module: ModuleType,
) -> None:
    """Fail closed when the deb index has no ChatGPT stanza."""
    updater = chatgpt_module.ChatGPTUpdater()
    with pytest.raises(RuntimeError, match="No chatgpt package found in ChatGPT"):
        updater._select_deb_stanza(
            "Package: something-else\nVersion: 1.0\n",
            index_url=updater.DEB_INDEXES["aarch64-linux"],
        )


def test_chatgpt_rejects_incomplete_deb_stanza(
    chatgpt_module: ModuleType,
) -> None:
    """Fail closed when a deb stanza omits its version or pool path."""
    updater = chatgpt_module.ChatGPTUpdater()
    index_url = updater.DEB_INDEXES["aarch64-linux"]
    with pytest.raises(RuntimeError, match="Incomplete chatgpt stanza"):
        updater._deb_artifact_url({"Package": "chatgpt"}, index_url=index_url)
    with pytest.raises(RuntimeError, match="Incomplete chatgpt stanza"):
        updater._deb_artifact_url(
            {"Version": "26.1007.21434", "Filename": ""},
            index_url=index_url,
        )


def test_chatgpt_rejects_unpinned_deb_url(
    chatgpt_module: ModuleType,
) -> None:
    """Fail closed when the deb pool path omits the release version."""
    updater = chatgpt_module.ChatGPTUpdater()
    with pytest.raises(RuntimeError, match="ChatGPT deb URL is not version-pinned"):
        updater._deb_artifact_url(
            {
                "Version": "26.1007.21434",
                "Filename": "pool/main/c/chatgpt/chatgpt_latest_arm64.deb",
            },
            index_url=updater.DEB_INDEXES["aarch64-linux"],
        )


def test_chatgpt_ignores_malformed_deb_index_lines(
    chatgpt_module: ModuleType,
) -> None:
    """Ignore continuation lines and lines that are not ``Key: value`` fields."""
    updater = chatgpt_module.ChatGPTUpdater()

    stanza = updater._parse_deb_stanza(
        "Package: chatgpt\n"
        "Depends: libgtk-3-0,\n libnotify4\n"
        "not a field\n"
        "Version: 26.1007.21434\n"
    )

    assert stanza == {
        "Package": "chatgpt",
        "Depends": "libgtk-3-0,",
        "Version": "26.1007.21434",
    }


def test_chatgpt_requires_metadata_for_linux_fallback(
    chatgpt_module: ModuleType,
) -> None:
    """A Linux platform cannot be reconstructed from the Darwin ZIP pattern."""
    updater = chatgpt_module.ChatGPTUpdater()
    info = VersionInfo(version="26.1007.21159-13536", metadata=NO_METADATA)

    assert (
        updater.get_download_url("aarch64-darwin", info)
        == f"{updater.ARCHIVE_BASE_URL}/ChatGPT-darwin-arm64-26.1007.21159.zip"
    )
    with pytest.raises(RuntimeError, match="URL metadata is required for platform"):
        updater.get_download_url("x86_64-linux", info)


def test_chatgpt_declares_the_linux_launch_validation(
    chatgpt_module: ModuleType,
) -> None:
    """The Linux repack is validated by launching it, not only by building it."""
    updater = chatgpt_module.ChatGPTUpdater()

    assert [
        (validation.installable, validation.systems, validation.mode)
        for validation in updater.derivation_validations
    ] == [
        (
            "path:.#checks.{system}.chatgpt-linux-launch",
            ("aarch64-linux", "x86_64-linux"),
            "build",
        )
    ]


def test_chatgpt_rejects_blank_short_version_in_url_guard(
    chatgpt_module: ModuleType,
) -> None:
    """The URL pin check cannot treat an empty short version as present."""
    updater = chatgpt_module.ChatGPTUpdater()
    with pytest.raises(RuntimeError, match="not version-pinned"):
        updater._require_version_pinned_url(
            url="https://example.invalid/ChatGPT-darwin-arm64-26.930.61225.zip",
            short_version="",
        )


def test_chatgpt_rejects_latest_only_archive_url(
    chatgpt_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed when the Sparkle enclosure omits the short version."""
    updater = chatgpt_module.ChatGPTUpdater()
    payload = b"""
        <rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
          <channel><item>
            <sparkle:version>13520</sparkle:version>
            <sparkle:shortVersionString>26.930.61225</sparkle:shortVersionString>
            <enclosure url="https://example.invalid/ChatGPT-darwin-arm64.zip" />
          </item></channel>
        </rss>
    """
    monkeypatch.setattr(
        chatgpt_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=payload),
    )

    with pytest.raises(RuntimeError, match="not version-pinned"):
        _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))


def test_chatgpt_rejects_appcasts_without_common_release(
    chatgpt_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail if the architecture appcasts have no release in common."""
    updater = chatgpt_module.ChatGPTUpdater()

    async def _fetch_url(_session: object, url: str, **_kwargs: object) -> bytes:
        version = "26.429.20946" if url.endswith("appcast.xml") else "26.430.1"
        return f"""
            <rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
              <channel><item>
                <sparkle:version>2312</sparkle:version>
                <sparkle:shortVersionString>{version}</sparkle:shortVersionString>
                <enclosure url="https://example.invalid/ChatGPT-darwin-arm64-{version}.zip" />
              </item></channel>
            </rss>
        """.encode()

    monkeypatch.setattr(chatgpt_module, "fetch_url", _fetch_url)

    with pytest.raises(RuntimeError, match="No common ChatGPT desktop release"):
        _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))


def test_chatgpt_rejects_invalid_appcast_shape(
    chatgpt_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface appcast parsing errors with ChatGPT-specific messages."""
    updater = chatgpt_module.ChatGPTUpdater()
    monkeypatch.setattr(
        chatgpt_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=b"<rss><channel /></rss>"),
    )

    with pytest.raises(RuntimeError, match="No items found in ChatGPT appcast"):
        _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))


@pytest.mark.parametrize(
    ("item_xml", "message"),
    [
        (
            """
            <sparkle:version>2312</sparkle:version>
            <sparkle:shortVersionString />
            <enclosure url="https://example.invalid/Codex.zip" />
            """,
            "Blank short version found in ChatGPT appcast",
        ),
        (
            """
            <sparkle:version>2312</sparkle:version>
            <sparkle:shortVersionString>26.429.20946</sparkle:shortVersionString>
            <enclosure />
            """,
            "No URL found in ChatGPT appcast enclosure",
        ),
    ],
)
def test_chatgpt_rejects_blank_appcast_fields(
    chatgpt_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    item_xml: str,
    message: str,
) -> None:
    """Reject appcast items whose required fields are present but blank."""
    updater = chatgpt_module.ChatGPTUpdater()
    payload = f"""
        <rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
          <channel><item>{item_xml}</item></channel>
        </rss>
    """.encode()
    monkeypatch.setattr(
        chatgpt_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=payload),
    )

    with pytest.raises(RuntimeError, match=message):
        _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))


@pytest.mark.parametrize(
    ("metadata", "expected_error", "message"),
    [
        (
            {"asset_urls": "https://example.invalid/Codex.zip"},
            TypeError,
            "Invalid ChatGPT desktop asset URLs in metadata",
        ),
        (
            {"asset_urls": {}},
            RuntimeError,
            "Missing ChatGPT desktop URL for platform 'aarch64-darwin'",
        ),
        (
            {"asset_urls": {"aarch64-darwin": " "}},
            RuntimeError,
            "Missing ChatGPT desktop URL for platform 'aarch64-darwin'",
        ),
    ],
)
def test_chatgpt_rejects_invalid_asset_url_metadata(
    chatgpt_module: ModuleType,
    metadata: object,
    expected_error: type[Exception],
    message: str,
) -> None:
    """Reject malformed appcast URL metadata instead of fabricating a bad URL."""
    updater = chatgpt_module.ChatGPTUpdater()

    with pytest.raises(expected_error, match=message):
        updater.get_download_url(
            "aarch64-darwin", VersionInfo("26.429.20946", metadata)
        )


def test_flake_input_metadata_updater_emits_empty_hash_entries() -> None:
    """Metadata-only flake inputs should still emit a typed empty value event."""

    class _DemoUpdater(FlakeInputMetadataUpdater):
        name = "demo"
        input_name = "demo"

    updater = _DemoUpdater()
    events = _run(
        _collect(
            lambda emit: updater.fetch_hashes(
                VersionInfo("main"),
                object(),
                context=UpdateContext(current=None),
                emit=emit,
            )
        )
    )

    assert [event.kind for event in events] == []
    assert events.result == []


def test_crush_prefers_newest_release_compatible_with_repo_go_floor(
    crush_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scan far enough back to find the newest release the current Go can build."""
    updater = crush_module.CrushUpdater()
    compatible_tag = "v1.2.3"
    compatible_version = compatible_tag.removeprefix("v")
    compatible_commit = "a" * 40
    incompatible_commit = "b" * 40
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
        updater,
        "_resolve_release_tag_commit",
        lambda _session, tag: asyncio.sleep(
            0,
            result=(
                compatible_commit if tag == compatible_tag else incompatible_commit
            ),
        ),
    )
    monkeypatch.setattr(
        crush_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=(
                b"module github.com/charmbracelet/crush\n\ngo 1.26.1\n"
                if compatible_commit in _a[1]
                else b"module github.com/charmbracelet/crush\n\ngo 1.27.0\n"
            ),
        ),
    )

    latest = _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))
    assert latest.version == compatible_version
    assert latest.metadata["tag"] == compatible_tag
    assert latest.metadata["commit"] == compatible_commit
    assert captured_kwargs["per_page"] == 100
    assert "item_limit" not in captured_kwargs


def test_crush_falls_back_to_current_pin_when_no_release_is_compatible(
    crush_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve the current crush pin until nixpkgs can build newer releases."""
    updater = crush_module.CrushUpdater()
    pinned_version = "fixture-version"
    pinned_commit = "c" * 40
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
    monkeypatch.setattr(
        updater,
        "_resolve_release_tag_commit",
        lambda _session, tag: asyncio.sleep(
            0,
            result=pinned_commit if tag == f"v{pinned_version}" else "b" * 40,
        ),
    )

    latest = _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))
    assert latest.version == pinned_version
    assert latest.metadata["tag"] == f"v{pinned_version}"
    assert latest.metadata["commit"] == pinned_commit
