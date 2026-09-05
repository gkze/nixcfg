"""Tests for the Zed nightly updater."""

import asyncio

import pytest
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import (
    StatusInfo,
    StatusKind,
    UpdateEvent,
    UpdateEventKind,
    expect_artifact_updates,
)
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import FlakeInputMetadata

_PACKAGE_DIR = REPO_ROOT / "packages/zed-editor-nightly"


def _load_module(module_name: str):
    return load_repo_module("packages/zed-editor-nightly/updater.py", module_name)


def test_zed_editor_nightly_updater_refreshes_root_rust_overlay() -> None:
    """Refresh the root Rust overlay consumed by the Zed derivation."""
    module = _load_module("zed_editor_nightly_updater_input_dependencies_test")

    assert module.ZedEditorNightlyUpdater.additional_input_names == ("rust-overlay",)


def test_zed_source_preparations_follow_apple_shader_owner() -> None:
    """Give gpui_apple stable access across its supported source contracts."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    preparations = expect_instance(
        expect_binding(package.output.scope, "crateSourcePreparations").value,
        AttributeSet,
    )

    preparation_bindings = binding_map(preparations.values)

    assert "gpui_macos" not in preparation_bindings
    assert_nix_ast_equal(
        preparation_bindings["gpui_apple"].value,
        r"""''
          cp -r ${src}/crates/gpui "$crateRoot/workspace-gpui"
          if grep -Fq 'gpui::GPUI_MANIFEST_DIR.into()' "$crateRoot/build.rs"; then
            substituteInPlace "$crateRoot/build.rs" \
              --replace-fail 'gpui::GPUI_MANIFEST_DIR.into()' \
              'PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("workspace-gpui")'
          elif grep -Fq '.join("../gpui")' "$crateRoot/build.rs"; then
            substituteInPlace "$crateRoot/build.rs" \
              --replace-fail '.join("../gpui")' '.join("workspace-gpui")'
          else
            echo "unsupported Zed gpui_apple source-location contract" >&2
            exit 1
          fi
        ''""",
    )


def test_tree_sitter_patch_runs_from_selected_cargo_manifest() -> None:
    """Patch only after buildRustCrate enters tree-sitter's manifest directory."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    override = expect_instance(
        expect_binding(package.output.scope, "treeSitterOverride").value,
        FunctionDefinition,
    )
    override_output = expect_instance(override.output, AttributeSet)
    override_bindings = binding_map(override_output.values)

    assert "postPatch" not in override_bindings
    assert_nix_ast_equal(
        override_bindings["preConfigure"].value,
        r"""(attrs.preConfigure or "") + ''
          export DEP_WASMTIME_C_API_INCLUDE="${wasmtimeCApiIncludeDirs}"
          if [ -z "$DEP_WASMTIME_C_API_INCLUDE" ]; then
            echo "missing wasmtime-c-api-impl include path for tree-sitter" >&2
            exit 1
          fi
          PYTHONPATH=${import ../../lib/codemods-pythonpath.nix { inherit lib; }} ${lib.getExe python3} \
            ${./patch_tree_sitter_build_rs.py} \
            ${lib.escapeShellArg attrs.build}
        ''""",
    )


def test_zed_editor_nightly_updater_tracks_manifest_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve the app version from the locked upstream Cargo manifest."""
    module = _load_module("zed_editor_nightly_updater_test")
    updater = module.ZedEditorNightlyUpdater()

    node = type(
        "Node",
        (),
        {
            "locked": type(
                "Locked",
                (),
                {
                    "owner": "zed-industries",
                    "repo": "zed",
                    "rev": "a" * 40,
                },
            )(),
        },
    )()
    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    monkeypatch.setattr(
        module,
        "fetch_url",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=b'[package]\nversion = "0.999.0"\n',
        ),
    )

    async def _empty_stream(_name: str):
        if False:
            yield None

    monkeypatch.setattr(
        module.ZedEditorNightlyUpdater,
        "stream_materialized_artifacts",
        lambda _self, **_kwargs: _empty_stream("zed-editor-nightly"),
    )

    info = _run(updater.fetch_latest(object()))
    assert info.version == "0.999.0"
    assert info.commit == "a" * 40
    assert info.metadata == FlakeInputMetadata(node=node, commit="a" * 40)

    events = _run(_collect_events(updater.fetch_hashes(info, object())))
    assert len(events) == 1
    assert events[0].payload == []

    result = updater.build_result(info, [])
    assert result == SourceEntry(
        version="0.999.0",
        hashes=[],
        input="zed",
        commit="a" * 40,
    )


def test_zed_editor_nightly_updater_rejects_missing_manifest_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail cleanly when the upstream manifest shape changes."""
    module = _load_module("zed_editor_nightly_updater_missing_version_test")
    updater = module.ZedEditorNightlyUpdater()

    node = type(
        "Node",
        (),
        {
            "locked": type(
                "Locked",
                (),
                {
                    "owner": "zed-industries",
                    "repo": "zed",
                    "rev": "b" * 40,
                },
            )(),
        },
    )()
    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    monkeypatch.setattr(
        module,
        "fetch_url",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=b'[package]\nname = "zed"\n'),
    )

    with pytest.raises(RuntimeError, match="package.version"):
        _run(updater.fetch_latest(object()))


def test_zed_editor_nightly_updater_rejects_missing_locked_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail before fetching when the flake lock lacks owner/repo/rev fields."""
    module = _load_module("zed_editor_nightly_updater_missing_locked_metadata_test")
    updater = module.ZedEditorNightlyUpdater()

    node = type(
        "Node",
        (),
        {
            "locked": type(
                "Locked",
                (),
                {
                    "owner": "zed-industries",
                    "repo": "zed",
                    "rev": "",
                },
            )(),
        },
    )()
    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)

    with pytest.raises(RuntimeError, match="missing owner/repo/rev metadata"):
        _run(updater.fetch_latest(object()))


def test_zed_editor_nightly_updater_refreshes_crate2nix_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit checked-in crate2nix artifacts during the hash/materialization phase."""
    module = _load_module("zed_editor_nightly_updater_crate2nix_test")
    updater = module.ZedEditorNightlyUpdater()
    assert updater.materialize_when_current is True
    assert updater.shows_materialize_artifacts_phase is True

    async def _fake_stream(name: str):
        yield UpdateEvent.status(
            name,
            "Refreshing crate2nix artifacts...",
            operation="materialize_artifacts",
            status=StatusInfo(
                kind=StatusKind.COMPUTING_HASH,
                value="crate2nix artifacts",
            ),
        )
        yield UpdateEvent.artifact(
            name,
            GeneratedArtifact.text(
                "packages/zed-editor-nightly/Cargo.nix",
                "{ zed = true; }\n",
            ),
        )
        yield UpdateEvent.status(
            name,
            "Prepared crate2nix artifacts",
            operation="materialize_artifacts",
            status=StatusInfo(kind=StatusKind.UPDATED, value="crate2nix artifacts"),
        )

    monkeypatch.setattr(
        module.ZedEditorNightlyUpdater,
        "stream_materialized_artifacts",
        lambda _self, **_kwargs: _fake_stream("zed-editor-nightly"),
    )

    info = VersionInfo(
        version="0.999.0",
        metadata=FlakeInputMetadata(node=object(), commit="c" * 40),
    )
    events = _run(_collect_events(updater.fetch_hashes(info, object())))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.ARTIFACT,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    artifact_paths = tuple(
        str(artifact.path) for artifact in expect_artifact_updates(events[1].payload)
    )
    assert artifact_paths == ("packages/zed-editor-nightly/Cargo.nix",)
    assert events[-1].payload == []
