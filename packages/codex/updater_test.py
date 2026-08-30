"""Updater ownership contracts for Codex's prebuilt WebRTC closure."""

import json
from types import ModuleType
from typing import TYPE_CHECKING

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._updater_helpers import collect_events, load_repo_module, run_async
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

if TYPE_CHECKING:
    import pytest

_PACKAGE_DIR = REPO_ROOT / "packages/codex"
_VERSION = "rust-v0.150.1"
_COMMIT = "90854393966b21e9ebfd21b122334eb09a20c93d"
_CLANG_RESOURCE_VERSION = "23"
_WEBRTC_URLS = {
    "aarch64-darwin": "https://github.com/livekit/rust-sdks/releases/download/webrtc-24f6822-2/webrtc-mac-arm64-release.zip",
    "x86_64-linux": "https://github.com/livekit/rust-sdks/releases/download/webrtc-24f6822-2/webrtc-linux-x64-release.zip",
}
_WEBRTC_HASHES = {
    "aarch64-darwin": "sha256-4IwJM6EzTFgQd2AdX+Hj9NWzmyqXrSioRax2L6GKL1U=",
    "x86_64-linux": "sha256-aR76GGfK2UJheN5nI10e2f8CZPgxMxqlEPxyWc95AQ0=",
}


def _load_updater_module() -> ModuleType:
    return load_repo_module("packages/codex/updater.py", "codex_updater_test")


def test_codex_updater_owns_platform_webrtc_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex refreshes crate metadata and hashes both sandboxed WebRTC inputs."""
    module = _load_updater_module()
    updater = module.CodexUpdater()

    async def materialize(
        *,
        source_overrides: dict[str, SourceEntry] | None = None,
    ):
        assert source_overrides == {
            "codex": updater.build_result(
                VersionInfo(_VERSION, {"commit": _COMMIT}), []
            )
        }
        yield UpdateEvent.artifact(
            "codex",
            GeneratedArtifact.text(
                _PACKAGE_DIR / "Cargo.nix", "{ generated = true; }\n"
            ),
        )

    async def compute_url_hashes(
        name: str,
        urls: object,
        *,
        config: object,
    ):
        assert name == "codex"
        assert config is updater.config
        assert list(urls) == [_WEBRTC_URLS[key] for key in sorted(_WEBRTC_URLS)]
        yield UpdateEvent.status(name, "hashing WebRTC artifacts")
        yield UpdateEvent.value(
            name,
            {url: _WEBRTC_HASHES[platform] for platform, url in _WEBRTC_URLS.items()},
        )

    monkeypatch.setattr(updater, "stream_materialized_artifacts", materialize)
    monkeypatch.setattr("lib.update.process.compute_url_hashes", compute_url_hashes)
    info = VersionInfo(_VERSION, {"commit": _COMMIT})

    events = run_async(collect_events(updater.fetch_hashes(info, object())))
    expected_hashes = [
        HashEntry.create(
            "sha256",
            _WEBRTC_HASHES[platform],
            platform=platform,
            url=_WEBRTC_URLS[platform],
        )
        for platform in sorted(_WEBRTC_URLS)
    ]

    assert [event.kind for event in events] == [
        UpdateEventKind.ARTIFACT,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert events[-1].payload == expected_hashes
    assert updater.build_result(info, expected_hashes) == SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        input="codex",
        hashes=expected_hashes,
        pins={"clangResourceVersion": _CLANG_RESOURCE_VERSION},
    )


def test_codex_checked_in_candidate_is_immediately_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persisted WebRTC hashes must not make the metadata candidate perpetually stale."""
    updater = _load_updater_module().CodexUpdater()
    current = SourceEntry.model_validate(
        json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    )
    info = VersionInfo(
        current.version or "",
        {"commit": current.commit},
    )
    captured: dict[str, object] = {}

    async def fetch_latest(_session: object) -> VersionInfo:
        return info

    async def materialize(
        *,
        source_overrides: dict[str, SourceEntry] | None = None,
    ):
        captured["source_overrides"] = source_overrides
        if False:
            yield UpdateEvent.status("codex", "unreachable")

    async def compute_url_hashes(
        _name: str,
        _urls: object,
        *,
        config: object,
    ):
        assert config is updater.config
        entries = current.hashes.entries or []
        yield UpdateEvent.value(
            "codex",
            {entry.url: entry.hash for entry in entries if entry.url is not None},
        )

    monkeypatch.setattr(updater, "fetch_latest", fetch_latest)
    monkeypatch.setattr(updater, "stream_materialized_artifacts", materialize)
    monkeypatch.setattr("lib.update.process.compute_url_hashes", compute_url_hashes)

    events = run_async(collect_events(updater.update_stream(current, object())))

    assert any(
        event.message == "Version up to date; refreshing generated artifacts..."
        for event in events
    )
    assert captured["source_overrides"] == {"codex": current}
    assert [
        event.payload for event in events if event.kind is UpdateEventKind.RESULT
    ] == [None]


def test_codex_webrtc_url_change_invalidates_current_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed updater-owned WebRTC release must force hash recomputation."""
    updater = _load_updater_module().CodexUpdater()
    current = SourceEntry.model_validate(
        json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    )
    info = VersionInfo(
        current.version or "",
        {"commit": current.commit},
    )
    monkeypatch.setattr(type(updater), "WEBRTC_RELEASE", "webrtc-new-release")

    assert (
        run_async(
            updater._is_latest(
                current.model_copy(update={"version": "old"}),
                info,
            )
        )
        is False
    )
    assert (
        run_async(
            updater._is_latest(
                current.model_copy(update={"hashes": HashCollection(mapping={})}),
                info,
            )
        )
        is False
    )
    assert run_async(updater._is_latest(current, info)) is False


def test_codex_v8_clang_resource_version_is_updater_owned() -> None:
    """The concrete V8 build must consume the updater-produced Clang pin."""
    updater = _load_updater_module().CodexUpdater()

    assert updater.source_pins == {
        "clangResourceVersion": _CLANG_RESOURCE_VERSION,
    }
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    v8_build = expect_instance(
        expect_binding(package.output.scope, "v8Build").value,
        FunctionCall,
    )
    v8_arguments = expect_instance(v8_build.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(v8_arguments.values, "clangResourceVersion").value,
        "slib.sources.codex.pins.clangResourceVersion",
    )


def test_codex_derivation_consumes_platform_webrtc_metadata() -> None:
    """The WebRTC override must fetch only updater-produced URL/hash metadata."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    scope = package.output.scope

    assert_nix_ast_equal(
        expect_binding(scope, "webrtcSource").value,
        """slib.sourceHashEntryForPlatform
          "codex" "sha256" pkgs.stdenv.hostPlatform.system""",
    )
    prebuilt = expect_instance(
        expect_binding(scope, "webrtcPrebuilt").value, FunctionCall
    )
    assert_nix_ast_equal(
        prebuilt,
        """pkgs.fetchzip {
          inherit (webrtcSource) hash url;
        }""",
    )
