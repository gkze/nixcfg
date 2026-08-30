"""Tests for the goose-cli updater."""

import json
from pathlib import Path

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent
from lib.update.updaters import VersionInfo

_PACKAGE_DIR = Path(__file__).parent
_BITCOIN_INTERNALS_RUST_VERSIONS = {
    "0.5.0": "1.74.0",
    "0.6.0": "1.74.0",
}
_CLANG_RESOURCE_VERSION = "22"
_SOURCE_PINS = {
    **{
        f"bitcoinInternals.{version}": rust_version
        for version, rust_version in _BITCOIN_INTERNALS_RUST_VERSIONS.items()
    },
    "clangResourceVersion": _CLANG_RESOURCE_VERSION,
}


def _load_module(module_name: str):
    return load_repo_module("overlays/goose-cli/updater.py", module_name)


def test_goose_cli_updater_forwards_materialized_artifacts_without_source_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The locked input replaces the former duplicate fixed-output source hash."""
    module = _load_module("goose_cli_updater_artifact_test")
    updater = module.GooseCliUpdater()

    async def _artifacts(
        *,
        source_overrides: dict[str, SourceEntry] | None = None,
    ):
        assert source_overrides == {
            "goose-cli": updater.build_result(VersionInfo("1.2.3", {}), [])
        }
        yield UpdateEvent.status("goose-cli", "materialized cargo artifacts")

    monkeypatch.setattr(updater, "stream_materialized_artifacts", _artifacts)

    events = _run(_collect(updater.fetch_hashes(VersionInfo("1.2.3", {}), object())))

    assert [event.kind.value for event in events] == ["status", "value"]
    assert events[0].message == "materialized cargo artifacts"
    assert events[-1].payload == []


def test_goose_cli_bitcoin_compatibility_map_is_updater_owned() -> None:
    """Exact crate compatibility metadata must come from the updater sidecar."""
    updater = _load_module("goose_cli_compatibility_test").GooseCliUpdater()

    assert updater.source_pins == _SOURCE_PINS
    result = updater.build_result(
        VersionInfo("1.48.0", {"commit": "a" * 40}),
        [],
    )
    assert result.pins == _SOURCE_PINS
    checked_in = SourceEntry.model_validate(
        json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    )
    assert updater.build_result(
        VersionInfo(checked_in.version or "", {"commit": checked_in.commit}),
        [],
    ).equivalent_to(checked_in)

    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    scope = package.output.scope
    assert_nix_ast_equal(
        expect_binding(scope, "bitcoinInternalsRustVersionPinPrefix").value,
        '"bitcoinInternals."',
    )
    assert_nix_ast_equal(
        expect_binding(scope, "bitcoinInternalsRustVersions").value,
        """prev.lib.mapAttrs'
          (pinName: rustVersion:
            prev.lib.nameValuePair
              (prev.lib.removePrefix bitcoinInternalsRustVersionPinPrefix pinName)
              rustVersion)
          (prev.lib.filterAttrs
            (pinName: _value:
              prev.lib.hasPrefix bitcoinInternalsRustVersionPinPrefix pinName)
            selfSource.pins)""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "bitcoinInternalsOverride").value,
        """attrs: {
          "rust-version" = bitcoinInternalsRustVersions.${attrs.version}
            or (throw "review bitcoin-internals ${attrs.version} rust-version metadata");
        }""",
    )

    v8_build = expect_instance(expect_binding(scope, "v8Build").value, FunctionCall)
    v8_arguments = expect_instance(v8_build.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(v8_arguments.values, "clangResourceVersion").value,
        "selfSource.pins.clangResourceVersion",
    )


def test_goose_checked_in_candidate_materializes_then_is_immediately_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One persisted candidate should feed materialization and produce no rewrite."""
    updater = _load_module("goose_cli_candidate_noop_test").GooseCliUpdater()
    current = SourceEntry.model_validate(
        json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    )
    info = VersionInfo(current.version or "", {"commit": current.commit})
    captured: dict[str, object] = {}

    async def fetch_latest(_session: object) -> VersionInfo:
        return info

    async def materialize(
        *,
        source_overrides: dict[str, SourceEntry] | None = None,
    ):
        captured["source_overrides"] = source_overrides
        if False:
            yield UpdateEvent.status("goose-cli", "unreachable")

    monkeypatch.setattr(updater, "fetch_latest", fetch_latest)
    monkeypatch.setattr(updater, "stream_materialized_artifacts", materialize)

    events = _run(_collect(updater.update_stream(current, object())))

    assert captured["source_overrides"] == {"goose-cli": current}
    assert [event.payload for event in events if event.kind.value == "result"] == [None]


@pytest.mark.parametrize("ref", [None, "", "main", "v"])
def test_goose_cli_updater_requires_a_versioned_release_ref(
    ref: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Goose versions must be derived from an explicit v-prefixed input ref."""
    module = _load_module(f"goose_cli_updater_ref_test_{ref!r}")
    updater = module.GooseCliUpdater()
    node = FlakeLockNode.model_validate({
        "original": {
            "type": "github",
            "owner": "aaif-goose",
            "repo": "goose",
            **({"ref": ref} if ref is not None else {}),
        },
        "locked": {
            "type": "github",
            "owner": "aaif-goose",
            "repo": "goose",
            "rev": "a" * 40,
            "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        },
    })
    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)

    with pytest.raises(RuntimeError, match="v<version> ref"):
        _run(updater.fetch_latest(object()))
