"""Updater and package contracts for Codex's current source closure."""

from types import ModuleType
from typing import TYPE_CHECKING

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import binding_map, parse_nix_expr
from lib.tests._updater_helpers import collect_events, load_repo_module, run_async
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

if TYPE_CHECKING:
    import pytest

_PACKAGE_DIR = REPO_ROOT / "packages/codex"
_INFO = VersionInfo("rust-v9.9.9", {"commit": "a" * 40})
_CLEAN_SOURCE = SourceEntry(
    version=_INFO.version,
    commit=_INFO.commit,
    input="codex",
    hashes=[],
)
_LEGACY_SOURCE = SourceEntry(
    version=_INFO.version,
    commit=_INFO.commit,
    input="codex",
    hashes=[
        HashEntry.create(
            "sha256",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            platform="aarch64-darwin",
            url="https://example.invalid/obsolete-webrtc.zip",
        )
    ],
    pins={"clangResourceVersion": "99"},
)


def _load_updater_module() -> ModuleType:
    return load_repo_module("packages/codex/updater.py", "codex_updater_test")


def test_codex_updater_discards_obsolete_closure_metadata() -> None:
    """Legacy WebRTC hashes and Clang pins must not survive a refresh."""
    updater = _load_updater_module().CodexUpdater()

    assert updater.materialization_source_overrides(
        _INFO,
        context=_LEGACY_SOURCE,
    ) == {"codex": _CLEAN_SOURCE}
    assert run_async(updater._is_latest(_LEGACY_SOURCE, _INFO)) is False
    assert run_async(updater._is_latest(_CLEAN_SOURCE, _INFO)) is True


def test_codex_updater_materializes_from_the_clean_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crate regeneration must see the same closure metadata that is persisted."""
    updater = _load_updater_module().CodexUpdater()

    async def materialize(
        *,
        source_overrides: dict[str, SourceEntry] | None = None,
    ):
        assert source_overrides == {"codex": _CLEAN_SOURCE}
        yield UpdateEvent.artifact(
            "codex",
            GeneratedArtifact.text(
                _PACKAGE_DIR / "Cargo.nix",
                "{ generated = true; }\n",
            ),
        )

    monkeypatch.setattr(updater, "stream_materialized_artifacts", materialize)

    events = run_async(
        collect_events(
            updater.fetch_hashes(
                _INFO,
                object(),
                context=_LEGACY_SOURCE,
            )
        )
    )

    assert [event.kind for event in events] == [
        UpdateEventKind.ARTIFACT,
        UpdateEventKind.VALUE,
    ]
    assert events[-1].payload == []
    assert updater.build_result(_INFO, events[-1].payload) == _CLEAN_SOURCE


def test_codex_package_uses_only_graph_owned_v8_inputs() -> None:
    """V8 derives Clang metadata from its source and Codex carries no dead WebRTC input."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    scope = binding_map(package.output.scope)

    assert "webrtcSource" not in scope
    assert "webrtcPrebuilt" not in scope
    assert "webrtcSysOverride" not in scope

    v8_build = expect_instance(scope["v8Build"].value, FunctionCall)
    v8_arguments = expect_instance(v8_build.argument, AttributeSet)
    assert "clangResourceVersion" not in binding_map(v8_arguments.values)
