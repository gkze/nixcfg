"""Tests for the crate2nix CI helper command."""

import asyncio
import errno
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from functools import cache
from pathlib import Path

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.import_expression import Import
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import Primitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update import crate2nix
from lib.update.events import (
    StatusInfo,
    StatusKind,
    StatusPayload,
    UpdateEvent,
    UpdateEventKind,
    expect_artifact_updates,
)


def _process_exists(pid: int) -> bool:
    """Return whether a process remains visible to the current user."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_process_exit(pid: int, timeout: float = 2.0) -> bool:
    """Wait briefly for a terminated descendant to be reaped by its parent or init."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return True
        time.sleep(0.01)
    return not _process_exists(pid)


def _wait_for_path(path: Path, timeout: float = 2.0) -> bool:
    """Wait for a subprocess to publish one coordination file."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.01)
    return path.exists()


@cache
def _registry_expr() -> AttributeSet:
    """Return the package registry attrset, including its let-scope bindings."""
    root = expect_instance(
        parse_nix_expr(
            (crate2nix.REPO_ROOT / "packages/registry.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, AttributeSet)


def _constraint_value(value: Primitive | NixList) -> str | list[str] | None:
    """Decode one simple registry constraint literal."""
    if isinstance(value, NixList):
        decoded = [expect_instance(item, Primitive).value for item in value.value]
        assert all(isinstance(item, str) for item in decoded)
        return decoded
    decoded = expect_instance(value, Primitive).value
    assert decoded is None or isinstance(decoded, str)
    return decoded


def _string_list(value: NixList) -> list[str]:
    """Decode a simple Nix list of strings."""
    decoded = [expect_instance(item, Primitive).value for item in value.value]
    assert all(isinstance(item, str) for item in decoded)
    return decoded


def _registry_override_groups(
    overrides_expr: BinaryExpression,
) -> dict[str, dict[str, object]]:
    """Decode grouped registry override metadata from the let-bound lists."""
    groups = {
        "helperPackages": {"helper": True},
        "darwinPackages": {"constraint": "darwin"},
        "aarch64DarwinPackages": {"constraint": ["aarch64-darwin"]},
        "darwinLinuxPackages": {"constraint": ["aarch64-darwin", "x86_64-linux"]},
        "nonX86DarwinLinuxPackages": {
            "constraint": ["aarch64-darwin", "aarch64-linux", "x86_64-linux"]
        },
        "allLocalSystemsPackages": {
            "constraint": [
                "aarch64-darwin",
                "x86_64-darwin",
                "aarch64-linux",
                "x86_64-linux",
            ]
        },
    }
    decoded: dict[str, dict[str, object]] = {}
    for group_name, metadata in groups.items():
        group = expect_instance(
            expect_scope_binding(overrides_expr, group_name).value,
            NixList,
        )
        decoded.update({name: dict(metadata) for name in _string_list(group)})
    decoded["sculptor"] = {
        "constraint": ["aarch64-darwin", "x86_64-darwin", "x86_64-linux"]
    }
    return decoded


def _registry_overrides() -> dict[str, dict[str, object]]:
    """Decode the literal override metadata table used by the registry."""
    overrides_expr = expect_scope_binding(
        _registry_expr(), "packageMetadataOverrides"
    ).value
    if isinstance(overrides_expr, BinaryExpression):
        return _registry_override_groups(overrides_expr)
    overrides = expect_instance(overrides_expr, AttributeSet)
    decoded: dict[str, dict[str, object]] = {}
    for binding in overrides.values:
        entry = expect_instance(binding, Binding)
        entry_value = expect_instance(entry.value, AttributeSet)
        metadata: dict[str, object] = {}
        for meta in entry_value.values:
            field = expect_instance(meta, Binding)
            if field.name == "helper":
                metadata[field.name] = expect_instance(field.value, Primitive).value
                continue
            assert field.name == "constraint"
            metadata[field.name] = _constraint_value(field.value)
        decoded[entry.name.strip('"')] = metadata
    return decoded


def _supports_system(constraint: object, system: str) -> bool:
    """Mirror the tiny system-constraint contract exported by ``packages/registry.nix``."""
    if constraint is None:
        return True
    if isinstance(constraint, list):
        return system in constraint
    assert constraint == "darwin"
    return system.endswith("-darwin")


def test_normalize_json_text_sorts_keys_and_adds_newline() -> None:
    """Canonical JSON rendering should be stable for crate-hashes.json."""
    rendered = crate2nix._normalize_json_text('{"b":1,"a":2}')
    assert rendered == '{\n  "a": 2,\n  "b": 1\n}\n'


def test_normalize_trailing_newline_collapses_extra_newlines() -> None:
    """Generated artifacts should always end with exactly one newline."""
    assert crate2nix._normalize_trailing_newline("demo") == "demo\n"
    assert crate2nix._normalize_trailing_newline("demo\n") == "demo\n"
    assert crate2nix._normalize_trailing_newline("demo\n\n") == "demo\n"


def test_xdg_cache_home_defaults_to_home_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset XDG cache home should fall back to the user's home cache directory."""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert crate2nix._xdg_cache_home() == tmp_path / ".cache"


def test_stabilize_generated_command_comment_rewrites_dynamic_paths() -> None:
    """Generated command comments should use stable repo-relative output paths."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
    )
    refreshed = (
        "# This file was @generated by crate2nix 0.15.0 with the command:\n"
        '#   "generate" "-f" "./Cargo.toml" "-o" "/tmp/Cargo.nix" '
        '"-h" "/tmp/crate-hashes.json" "--default-features"\n'
        "# See https://github.com/kolloch/crate2nix for more info.\n"
        "{ }\n"
    )

    stabilized = crate2nix._stabilize_generated_command_comment(target, refreshed)

    assert (
        '#   "generate" "-f" "Cargo.toml" "-o" "demo/Cargo.nix" '
        '"-h" "demo/crate-hashes.json" "--default-features"\n' in stabilized
    )


def test_stabilize_generated_command_comment_preserves_nested_manifest_path() -> None:
    """Nested-manifest targets should keep their real manifest path in comments."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
        cargo_manifest_relpath=Path("packages/desktop/src-tauri/Cargo.toml"),
    )
    refreshed = (
        "# This file was @generated by crate2nix 0.15.0 with the command:\n"
        '#   "generate" "-f" "/tmp/Cargo.toml" "-o" "/tmp/Cargo.nix" '
        '"-h" "/tmp/crate-hashes.json" "--default-features"\n'
        "# See https://github.com/kolloch/crate2nix for more info.\n"
        "{ }\n"
    )

    stabilized = crate2nix._stabilize_generated_command_comment(target, refreshed)

    assert (
        '#   "generate" "-f" "packages/desktop/src-tauri/Cargo.toml" '
        '"-o" "demo/Cargo.nix" "-h" "demo/crate-hashes.json" '
        '"--default-features"\n' in stabilized
    )


def test_stabilize_generated_command_comment_leaves_unrelated_text_unchanged() -> None:
    """Non-generated comments should pass through unchanged."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
    )
    refreshed = (
        "# This file was @generated by crate2nix 0.15.0 with the command:\n"
        "# no command here\n"
        "# See https://github.com/kolloch/crate2nix for more info.\n"
        "{ }\n"
    )

    assert (
        crate2nix._stabilize_generated_command_comment(target, refreshed) == refreshed
    )


def test_stabilize_generated_root_src_paths_rewrites_relative_store_paths() -> None:
    """Store-relative crate source paths should normalize back to ``${rootSrc}``."""
    patched_src = Path("/nix/store/demo-src")
    generated_cargo = Path("/tmp/build/Cargo.nix")
    relative_src = os.path.relpath(
        patched_src / "crates/demo",
        generated_cargo.parent,
    ).replace(os.sep, "/")
    refreshed = (
        "        src = lib.cleanSourceWith { filter = sourceFilter;  "
        f"src = {relative_src}; }};\n"
    )

    stabilized = crate2nix._stabilize_generated_root_src_paths(
        refreshed,
        patched_src=patched_src,
        generated_cargo=generated_cargo,
    )

    assert (
        "src = lib.cleanSourceWith { filter = sourceFilter;  "
        'src = "${rootSrc}/crates/demo"; };\n' in stabilized
    )


def test_stabilize_generated_root_src_paths_handles_canonicalized_temp_paths(
    tmp_path: Path,
) -> None:
    """crate2nix may emit paths relative to a canonicalized temp directory."""
    patched_src = Path("/nix/store/demo-src")
    real_parent = tmp_path / "real" / "build"
    symlink_parent = tmp_path / "link" / "build"
    real_parent.mkdir(parents=True)
    symlink_parent.parent.symlink_to(real_parent.parent, target_is_directory=True)
    generated_cargo = symlink_parent / "Cargo.nix"
    relative_src = os.path.relpath(
        patched_src / "crates/demo",
        real_parent,
    ).replace(os.sep, "/")
    refreshed = (
        "        src = lib.cleanSourceWith { filter = sourceFilter;  "
        f"src = {relative_src}; }};\n"
    )

    stabilized = crate2nix._stabilize_generated_root_src_paths(
        refreshed,
        patched_src=patched_src,
        generated_cargo=generated_cargo,
    )

    assert (
        "src = lib.cleanSourceWith { filter = sourceFilter;  "
        'src = "${rootSrc}/crates/demo"; };\n' in stabilized
    )


def test_stabilize_generated_root_src_paths_handles_root_and_unrelated_paths() -> None:
    """Exact root paths normalize, while unrelated paths are left alone."""
    patched_src = Path("/nix/store/demo-src")
    generated_cargo = Path("/tmp/build/Cargo.nix")
    root_relative = os.path.relpath(patched_src, generated_cargo.parent).replace(
        os.sep,
        "/",
    )
    refreshed = (
        "        src = lib.cleanSourceWith { filter = sourceFilter;  "
        f"src = {root_relative}; }};\n"
        "        src = lib.cleanSourceWith { filter = sourceFilter;  "
        "src = ../vendor/demo; };\n"
    )

    stabilized = crate2nix._stabilize_generated_root_src_paths(
        refreshed,
        patched_src=patched_src,
        generated_cargo=generated_cargo,
    )

    assert 'src = "${rootSrc}"; };' in stabilized
    assert "src = ../vendor/demo; };" in stabilized


def test_stabilize_generated_root_src_paths_tolerates_spacing_drift() -> None:
    """Root source rewriting should not depend on one exact crate2nix layout."""
    patched_src = Path("/nix/store/demo-src")
    generated_cargo = Path("/tmp/build/Cargo.nix")
    relative_src = os.path.relpath(
        patched_src / "crates/demo",
        generated_cargo.parent,
    ).replace(os.sep, "/")
    refreshed = (
        f"src=lib.cleanSourceWith {{ filter = sourceFilter; src={relative_src}; }};\n"
    )

    stabilized = crate2nix._stabilize_generated_root_src_paths(
        refreshed,
        patched_src=patched_src,
        generated_cargo=generated_cargo,
    )

    assert 'src="${rootSrc}/crates/demo"; };' in stabilized


def test_generated_cargo_uses_injected_content_addressed_source_contract() -> None:
    """Local crates should delegate source materialization to the caller contract."""
    generated = """{ lib
, rootSrc ? ./.
}:
rec {
  demo = {
    src = lib.cleanSourceWith {
      filter = sourceFilter;
      src = "${rootSrc}/crates/demo";
    };
  };
}
"""

    normalized, source_paths = crate2nix._apply_crate_source_contract(generated)

    assert source_paths == ("crates/demo",)
    assert_nix_ast_equal(
        normalized,
        """{ lib
, rootSrc ? ./.
, crateSource ? relativePath: throw "Cargo.nix requires crateSource when a local crate source is evaluated"
}:
rec {
  demo = {
    src = crateSource sourceFilter "crates/demo";
  };
}
""",
    )


def test_generated_cargo_contract_rejects_unparseable_nix() -> None:
    """Fail closed if transformed Cargo.nix cannot be inspected structurally."""
    with pytest.raises(
        RuntimeError,
        match="Could not parse transformed Cargo.nix source contract",
    ):
        crate2nix._apply_crate_source_contract("{ invalid = ; }")


def test_crate_source_manifest_binds_slices_to_the_production_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Generated slice hashes must describe Cargo.nix's production rootSrc."""
    production_root = tmp_path / "codex-input" / "codex-rs"
    (production_root / "cli").mkdir(parents=True)
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
        source_input="codex",
        root_src_relpath=Path("codex-rs"),
        crate_sources=Path("demo/crate-sources.json"),
        externally_overridden_source_paths=("vendor/v8-goose-src",),
    )
    seen_roots: list[Path] = []
    monkeypatch.setattr(
        crate2nix,
        "_resolve_production_root",
        lambda _target: (production_root, "sha256-root-input="),
    )

    def _materialize(
        root: Path,
        source_paths: tuple[str, ...],
        cargo_nix: Path,
        *,
        root_source_name: str,
    ) -> dict[str, object]:
        seen_roots.append(root)
        assert source_paths == ("cli",)
        assert cargo_nix == tmp_path / "Cargo.nix"
        assert root_source_name == "codex-rs"
        return {
            "cli": {
                "hash": "sha256-cli-source=",
                "name": "cli",
            }
        }

    monkeypatch.setattr(crate2nix, "_materialize_source_slices", _materialize)
    cargo_nix = tmp_path / "Cargo.nix"
    cargo_nix.write_text("final generated Cargo.nix\n", encoding="utf-8")

    rendered = crate2nix._render_crate_source_manifest(
        target,
        ("cli", "vendor/v8-goose-src"),
        cargo_nix=cargo_nix,
    )

    assert seen_roots == [production_root]
    assert json.loads(rendered) == {
        "source": {
            "input": "codex",
            "narHash": "sha256-root-input=",
            "subdir": "codex-rs",
            "cargoNixSha256": hashlib.sha256(cargo_nix.read_bytes()).hexdigest(),
        },
        "slices": {
            "cli": {
                "hash": "sha256-cli-source=",
                "name": "cli",
            }
        },
    }


def test_source_slice_materialization_uses_the_generated_cargo_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The updater must pass Cargo.nix's generated filter to its Nix boundary."""
    source_root = tmp_path / "source"
    source_root.mkdir()
    cargo_nix = tmp_path / "Cargo.nix"
    cargo_nix.write_text(
        "{ lib ? { } }: { internal.sourceFilter = name: type: true; }\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []
    responses = iter((
        '{"crate": "/nix/store/demo-crate"}',
        '{"/nix/store/demo-crate": {"narHash": "sha256-demo="}}',
    ))

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=next(responses),
            stderr="",
        )

    monkeypatch.setattr(crate2nix, "_run", _run)

    slices = crate2nix._materialize_source_slices(
        source_root,
        ("crate",),
        cargo_nix,
        root_source_name="workspace",
    )

    assert slices == {"crate": {"hash": "sha256-demo=", "name": "crate"}}
    assert commands[0][:-1] == ["nix", "eval", "--impure", "--json", "--expr"]
    assert commands[1] == [
        "nix",
        "path-info",
        "--json",
        "--json-format",
        "1",
        "/nix/store/demo-crate",
    ]

    materialize = expect_instance(parse_nix_expr(commands[0][-1]), FunctionCall)
    helper_import = expect_instance(
        expect_scope_binding(materialize, "helper").value,
        Import,
    )
    assert expect_instance(helper_import.argument, NixPath).path == str(
        crate2nix.REPO_ROOT / "lib/crate2nix-source-slice.nix"
    )
    cargo_call = expect_instance(
        expect_scope_binding(materialize, "cargo").value,
        FunctionCall,
    )
    cargo_import = expect_instance(cargo_call.name, Import)
    assert expect_instance(cargo_import.argument, NixPath).path == str(cargo_nix)
    cargo_arguments = expect_instance(cargo_call.argument, AttributeSet)
    cargo_lib = expect_instance(
        expect_binding(cargo_arguments.values, "lib").value,
        Select,
    )
    assert expect_instance(cargo_lib.expression, Identifier).name == "helper"
    assert cargo_lib.attribute == "sourceFilterLib"

    materialize_name = expect_instance(materialize.name, Select)
    assert expect_instance(materialize_name.expression, Identifier).name == "helper"
    assert materialize_name.attribute == "materialize"
    arguments = expect_instance(materialize.argument, AttributeSet)
    root_src = expect_instance(
        expect_binding(arguments.values, "rootSrc").value, NixPath
    )
    assert root_src.path == str(source_root)
    source_filter = expect_instance(
        expect_binding(arguments.values, "sourceFilter").value,
        Select,
    )
    assert_nix_ast_equal(source_filter, "cargo.internal.sourceFilter")
    sources = expect_instance(
        expect_binding(arguments.values, "sources").value,
        FunctionCall,
    )
    sources_name = expect_instance(sources.name, Select)
    assert expect_instance(sources_name.expression, Identifier).name == "builtins"
    assert sources_name.attribute == "fromJSON"
    sources_json = json.loads(expect_instance(sources.argument, Primitive).rebuild())
    assert isinstance(sources_json, str)
    assert json.loads(sources_json) == {"crate": {"name": "crate"}}


@pytest.mark.parametrize("relative_path", ["", "../escape", "/absolute"])
def test_source_slice_materialization_rejects_unsafe_paths(
    relative_path: str,
    tmp_path: Path,
) -> None:
    """Generated source paths must remain inside the production root."""
    with pytest.raises(ValueError, match="Invalid local crate source path"):
        crate2nix._materialize_source_slices(
            tmp_path,
            (relative_path,),
            tmp_path / "Cargo.nix",
            root_source_name="workspace",
        )


def test_source_slice_materialization_rejects_missing_nar_hashes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Malformed Nix path metadata must fail instead of emitting a stale artifact."""
    responses = iter((
        '{"crate": "/nix/store/demo-crate"}',
        '{"/nix/store/demo-crate": {}}',
    ))
    monkeypatch.setattr(
        crate2nix,
        "_run",
        lambda _args: subprocess.CompletedProcess(
            _args,
            0,
            stdout=next(responses),
            stderr="",
        ),
    )

    with pytest.raises(TypeError, match="did not report a NAR hash"):
        crate2nix._materialize_source_slices(
            tmp_path,
            ("crate",),
            tmp_path / "Cargo.nix",
            root_source_name="workspace",
        )


def test_resolve_targets_skips_unsupported_platforms(monkeypatch) -> None:
    """Default target selection should skip platform-specific packages."""
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "x86_64-linux")

    runnable, skipped = crate2nix._resolve_targets(())

    assert [target.name for target in runnable] == [
        "codex",
        "goose-cli",
        "gitbutler",
        "zed-editor-nightly",
    ]
    assert skipped == []


def test_resolve_targets_skips_aarch64_linux_for_all_current_targets(
    monkeypatch,
) -> None:
    """aarch64-linux must skip every currently registered target (registry-gated)."""
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "aarch64-linux")

    runnable, skipped = crate2nix._resolve_targets(())

    assert runnable == []
    assert sorted(skipped) == [
        "codex",
        "gitbutler",
        "goose-cli",
        "zed-editor-nightly",
    ]


def test_stream_crate2nix_artifact_updates_emits_changed_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface crate2nix regen through normal updater status/artifact events."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("packages/demo/Cargo.nix"),
        crate_hashes=Path("packages/demo/crate-hashes.json"),
        crate_sources=Path("packages/demo/crate-sources.json"),
        normalizer_path=Path("packages/demo/normalize_cargo_nix.py"),
        supported_platforms=("linux",),
    )
    monkeypatch.setitem(crate2nix.TARGETS, "demo", target)
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "linux")
    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target, **_kwargs: crate2nix.RefreshResult(
            cargo_nix="{ demo = true; }\n",
            crate_hashes='{"demo": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}\n',
            crate_sources='{"source": {}, "slices": {}}\n',
        ),
    )

    async def _collect() -> list[UpdateEvent]:
        return [
            event async for event in crate2nix.stream_crate2nix_artifact_updates("demo")
        ]

    events = asyncio.run(_collect())

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.ARTIFACT,
        UpdateEventKind.STATUS,
    ]
    assert events[0].message == "Refreshing crate2nix artifacts..."
    assert events[2].message == "Prepared crate2nix artifacts"
    artifact_paths = tuple(
        str(artifact.path) for artifact in expect_artifact_updates(events[1].payload)
    )
    assert artifact_paths == (
        "packages/demo/Cargo.nix",
        "packages/demo/crate-hashes.json",
        "packages/demo/crate-sources.json",
    )


def test_crate2nix_artifact_updates_skip_unknown_or_unsupported_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip crate2nix refreshes for unknown names and unsupported platforms."""
    target = crate2nix.Crate2NixTarget(
        name="demo-skip",
        patched_src_installable="path:.#demo-skip-crate2nix-src",
        cargo_nix=Path("packages/demo/Cargo.nix"),
        crate_hashes=Path("packages/demo/crate-hashes.json"),
        normalizer_path=Path("packages/demo/normalize_cargo_nix.py"),
        supported_platforms=("linux",),
    )
    monkeypatch.setitem(crate2nix.TARGETS, "demo-skip", target)
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "darwin")
    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not refresh")
        ),
    )

    assert crate2nix.crate2nix_artifact_updates("missing-target") == ()
    assert crate2nix.crate2nix_artifact_updates("demo-skip") == ()


def test_stream_crate2nix_artifact_updates_skips_unknown_or_unsupported_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit explicit skip statuses when refresh cannot run here."""
    target = crate2nix.Crate2NixTarget(
        name="demo-stream-skip",
        patched_src_installable="path:.#demo-stream-skip-crate2nix-src",
        cargo_nix=Path("packages/demo/Cargo.nix"),
        crate_hashes=Path("packages/demo/crate-hashes.json"),
        normalizer_path=Path("packages/demo/normalize_cargo_nix.py"),
        supported_platforms=("linux",),
    )
    monkeypatch.setitem(crate2nix.TARGETS, "demo-stream-skip", target)
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "darwin")

    async def _collect(name: str) -> list[UpdateEvent]:
        return [
            event async for event in crate2nix.stream_crate2nix_artifact_updates(name)
        ]

    missing_events = asyncio.run(_collect("missing-target"))
    assert len(missing_events) == 1
    assert missing_events[0].payload == StatusPayload(
        operation="materialize_artifacts",
        info=StatusInfo(kind=StatusKind.SKIPPED, value="unknown_target"),
    )

    skipped_events = asyncio.run(_collect("demo-stream-skip"))
    assert len(skipped_events) == 1
    assert skipped_events[0].payload == StatusPayload(
        operation="materialize_artifacts",
        info=StatusInfo(kind=StatusKind.UNSUPPORTED_PLATFORM, value="darwin"),
    )


def test_stream_crate2nix_artifact_updates_reports_up_to_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit an up-to-date status when regenerated artifacts are unchanged."""
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "x86_64-linux")

    monkeypatch.setattr(
        crate2nix,
        "crate2nix_artifact_updates",
        lambda _name, **_kwargs: (),
    )

    async def _collect() -> list[UpdateEvent]:
        return [
            event
            async for event in crate2nix.stream_crate2nix_artifact_updates("codex")
        ]

    events = asyncio.run(_collect())

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
    ]
    assert events[1].message == "crate2nix artifacts up to date"
    assert events[1].payload == StatusPayload(
        operation="materialize_artifacts",
        info=StatusInfo(
            kind=StatusKind.UP_TO_DATE,
            scope="artifacts",
            value="crate2nix artifacts",
        ),
    )


def test_stream_crate2nix_artifact_updates_reports_bounded_live_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The async updater should surface sanitized progress while work is active."""
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "x86_64-linux")

    def _artifacts(
        _name: str,
        *,
        cancel_event: threading.Event,
        progress,
    ) -> tuple[()]:
        assert not cancel_event.is_set()
        progress("\x1b[31mPrefetching demo\x1b[0m" + "x" * 1000)
        time.sleep(0.05)
        return ()

    monkeypatch.setattr(crate2nix, "crate2nix_artifact_updates", _artifacts)

    async def _collect() -> list[UpdateEvent]:
        return [
            event
            async for event in crate2nix.stream_crate2nix_artifact_updates("codex")
        ]

    events = asyncio.run(_collect())
    progress_events = [event for event in events if event.kind == UpdateEventKind.LINE]
    assert len(progress_events) == 1
    assert progress_events[0].stream == "crate2nix"
    assert progress_events[0].message is not None
    assert progress_events[0].message.startswith("Prefetching demo")
    assert "\x1b" not in progress_events[0].message
    assert len(progress_events[0].message) <= crate2nix._CRATE2NIX_PROGRESS_LINE_LIMIT


def test_progress_handoff_drops_old_messages_before_allocating_more() -> None:
    """A chatty worker must have a hard cross-thread progress memory bound."""
    progress_queue: queue.Queue[str] = queue.Queue(maxsize=3)

    crate2nix._put_bounded_progress(progress_queue, "\x1b[31m\x1b[0m")
    for index in range(10):
        crate2nix._put_bounded_progress(progress_queue, f"progress {index}")

    assert progress_queue.qsize() == 3
    assert [progress_queue.get_nowait() for _ in range(3)] == [
        "progress 7",
        "progress 8",
        "progress 9",
    ]


def test_stream_crate2nix_artifact_updates_cancels_worker_promptly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling the event stream should stop its worker instead of awaiting 2400s."""
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "x86_64-linux")
    started = threading.Event()
    stopped = threading.Event()

    def _artifacts(
        _name: str,
        *,
        cancel_event: threading.Event,
        progress,
    ) -> tuple[()]:
        del progress
        started.set()
        assert cancel_event.wait(timeout=2.0)
        stopped.set()
        raise crate2nix.Crate2NixCommandCancelledError("cancelled")

    monkeypatch.setattr(crate2nix, "crate2nix_artifact_updates", _artifacts)

    async def _cancel() -> None:
        stream = crate2nix.stream_crate2nix_artifact_updates("codex")
        initial = await anext(stream)
        assert initial.message == "Refreshing crate2nix artifacts..."
        task = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(started.wait, 1.0)
        started_at = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - started_at < 1.0
        assert stopped.is_set()
        await stream.aclose()

    asyncio.run(_cancel())


def test_stream_crate2nix_artifact_updates_close_cancels_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing a partially consumed stream should not strand its executor worker."""
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "x86_64-linux")
    started = threading.Event()
    stopped = threading.Event()

    def _artifacts(
        _name: str,
        *,
        cancel_event: threading.Event,
        progress,
    ) -> tuple[()]:
        started.set()
        progress("working")
        assert cancel_event.wait(timeout=2.0)
        stopped.set()
        raise crate2nix.Crate2NixCommandCancelledError("cancelled")

    monkeypatch.setattr(crate2nix, "crate2nix_artifact_updates", _artifacts)

    async def _close() -> None:
        stream = crate2nix.stream_crate2nix_artifact_updates("codex")
        await anext(stream)
        progress_event = await anext(stream)
        assert progress_event.kind == UpdateEventKind.LINE
        assert started.is_set()
        started_at = time.monotonic()
        await stream.aclose()
        assert time.monotonic() - started_at < 1.0
        assert stopped.is_set()

    asyncio.run(_close())


def test_targets_use_dedicated_source_installables() -> None:
    """Built-in targets should avoid final-package passthru source paths."""
    assert {
        name: target.patched_src_installable
        for name, target in crate2nix.TARGETS.items()
    } == {
        "codex": "path:.#codex-crate2nix-src",
        "goose-cli": "path:.#goose-cli-crate2nix-src",
        "gitbutler": "path:.#gitbutler-crate2nix-src",
        "zed-editor-nightly": "path:.#zed-editor-nightly-crate2nix-src",
    }
    assert {
        name: target.externally_overridden_source_paths
        for name, target in crate2nix.TARGETS.items()
    } == {
        "codex": (),
        "goose-cli": ("vendor/v8-goose-src",),
        "gitbutler": (),
        "zed-editor-nightly": (),
    }


def test_crate2nix_companion_discovery_uses_package_registry() -> None:
    """Package registry contracts should discover crate2nix companions."""
    packages_root = (crate2nix.REPO_ROOT / "packages").resolve()
    companion_entries = {
        f"{path.parent.name}-crate2nix-src"
        for path in packages_root.glob("*/crate2nix-src.nix")
    }
    expected_companions = {
        "codex-crate2nix-src",
        "gitbutler-crate2nix-src",
        "goose-cli-crate2nix-src",
        "zed-editor-nightly-crate2nix-src",
    }

    assert expected_companions <= companion_entries

    assert_nix_ast_equal(
        expect_scope_binding(_registry_expr(), "companionPackages").value,
        """
        discovery.discoverCompanionEntries {
          root = pkgDir;
          directories = discoveredPackages.dirNames;
          fileName = "crate2nix-src.nix";
        }
        """,
    )


def test_package_registry_system_constraint_contract_is_structural() -> None:
    """System constraint helper shape should remain explicit in the registry."""
    assert_nix_ast_equal(
        expect_scope_binding(_registry_expr(), "supportsSystem").value,
        """
        constraint: system:
        if constraint == null then
          true
        else if builtins.isList constraint then
          builtins.elem system constraint
        else if constraint == "darwin" then
          builtins.match ".*-darwin" system != null
        else
          throw "packages/registry.nix: unsupported system constraint `${constraint}`"
        """,
    )
    assert_nix_ast_equal(
        expect_binding(_registry_expr().values, "forSystem").value,
        "system: packagePathsMatching (meta: supportsSystem meta.constraint system)",
    )


def test_package_registry_metadata_overrides_are_intentional() -> None:
    """Important package helper and platform overrides should stay visible."""
    overrides = _registry_overrides()
    assert {name for name, meta in overrides.items() if meta.get("helper") is True} == {
        "go-cli-wrapper",
        "registry",
        "t3code-workspace",
    }
    assert sorted(
        name for name, meta in overrides.items() if meta.get("constraint") == "darwin"
    ) == [
        "airfoil",
        "arc",
        "aside",
        "baseten-switch",
        "claude",
        "cleanshot",
        "clearly",
        "codeedit",
        "codex-desktop",
        "comet",
        "commander",
        "conductor",
        "factory",
        "figma",
        "framer",
        "granola",
        "grok-bot",
        "keepingyouawake",
        "linear",
        "loom",
        "macai",
        "mole-app",
        "netnewswire",
        "raycast",
        "screen-studio",
        "signal-beta",
        "tembo",
        "voiceos",
        "wispr-flow",
        "zen-twilight",
        "zo",
    ]
    assert overrides["executor"]["constraint"] == ["aarch64-darwin"]
    assert overrides["github-copilot-app"]["constraint"] == ["aarch64-darwin"]
    assert overrides["hermes-desktop"]["constraint"] == ["aarch64-darwin"]
    assert overrides["goose-desktop"]["constraint"] == ["aarch64-darwin"]
    assert overrides["openchamber"]["constraint"] == ["aarch64-darwin"]
    assert overrides["reflect-open"]["constraint"] == ["aarch64-darwin"]
    assert overrides["sculptor"]["constraint"] == [
        "aarch64-darwin",
        "x86_64-darwin",
        "x86_64-linux",
    ]


def test_crate2nix_source_files_exist_for_registered_targets() -> None:
    """Registered source installables should have checked-in companion files."""
    packages_root = (crate2nix.REPO_ROOT / "packages").resolve()
    selected_paths = {
        "codex": str(packages_root / "codex/crate2nix-src.nix"),
        "gitbutler": str(packages_root / "gitbutler/crate2nix-src.nix"),
        "goose": str(packages_root / "goose-cli/crate2nix-src.nix"),
        "zed": str(packages_root / "zed-editor-nightly/crate2nix-src.nix"),
    }
    assert all(Path(path).is_file() for path in selected_paths.values())


@pytest.mark.parametrize("target_name", sorted(crate2nix.TARGETS))
def test_registered_crate2nix_target_has_complete_current_artifacts(
    target_name: str,
) -> None:
    """Every production target must check in a manifest bound to its Cargo.nix."""
    target = crate2nix.TARGETS[target_name]
    assert target.crate_sources is not None
    cargo_path = crate2nix.REPO_ROOT / target.cargo_nix
    hashes_path = crate2nix.REPO_ROOT / target.crate_hashes
    manifest_path = crate2nix.REPO_ROOT / target.crate_sources

    assert cargo_path.is_file()
    assert hashes_path.is_file()
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"] == {
        "cargoNixSha256": hashlib.sha256(cargo_path.read_bytes()).hexdigest(),
        "input": target.source_input,
        "narHash": manifest["source"]["narHash"],
        "subdir": target.root_src_relpath.as_posix(),
    }
    assert isinstance(manifest["source"]["narHash"], str)
    assert manifest["source"]["narHash"]
    assert manifest["slices"]
    assert all(
        set(source) == {"hash", "name"}
        and isinstance(source["hash"], str)
        and source["hash"].startswith("sha256-")
        and isinstance(source["name"], str)
        and source["name"]
        for source in manifest["slices"].values()
    )


def test_goose_crate2nix_companion_declares_overlay_dependency() -> None:
    """Expose the Goose overlay source as an injectable package argument."""
    assert_nix_ast_equal(
        (crate2nix.REPO_ROOT / "packages/goose-cli/crate2nix-src.nix").read_text(
            encoding="utf-8"
        ),
        """
        { goose-cli-crate2nix-src, ... }:
        goose-cli-crate2nix-src
        """,
    )


def test_crate2nix_target_platforms_match_registry_constraints() -> None:
    """crate2nix runner platform gates should mirror registry constraints."""
    overrides = _registry_overrides()

    presence = {
        "gooseCli": {"linux": True, "darwin": True, "linuxAarch64": False},
        "gooseCliCrate2nixSrc": {
            "linux": True,
            "darwin": True,
            "linuxAarch64": False,
        },
        "gitbutler": {
            "linux": True,
            "darwin": True,
            "linuxAarch64": False,
        },
        "gitbutlerCrate2nixSrc": {
            "linux": True,
            "darwin": True,
            "linuxAarch64": False,
        },
        "codexCrate2nixSrc": {
            "linux": True,
            "darwin": True,
            "linuxAarch64": False,
        },
        "zedEditorNightly": {
            "linux": True,
            "darwin": True,
            "linuxAarch64": False,
        },
        "zedEditorNightlyCrate2nixSrc": {
            "linux": True,
            "darwin": True,
            "linuxAarch64": False,
        },
        "sculptor": {
            "linux": True,
            "darwin": True,
            "x86Darwin": True,
            "linuxAarch64": False,
        },
    }
    systems = {
        "linux": "x86_64-linux",
        "darwin": "aarch64-darwin",
        "x86Darwin": "x86_64-darwin",
        "linuxAarch64": "aarch64-linux",
    }
    package_constraints = {
        name: overrides.get(name, {}).get("constraint")
        for name in (
            "goose-cli",
            "goose-cli-crate2nix-src",
            "gitbutler",
            "gitbutler-crate2nix-src",
            "codex-crate2nix-src",
            "zed-editor-nightly",
            "zed-editor-nightly-crate2nix-src",
            "sculptor",
        )
    }
    assert {
        key: {
            surface: _supports_system(package_constraints[name], system)
            for surface, system in systems.items()
            if not (key != "sculptor" and surface == "x86Darwin")
        }
        for key, name in {
            "gooseCli": "goose-cli",
            "gooseCliCrate2nixSrc": "goose-cli-crate2nix-src",
            "gitbutler": "gitbutler",
            "gitbutlerCrate2nixSrc": "gitbutler-crate2nix-src",
            "codexCrate2nixSrc": "codex-crate2nix-src",
            "zedEditorNightly": "zed-editor-nightly",
            "zedEditorNightlyCrate2nixSrc": "zed-editor-nightly-crate2nix-src",
            "sculptor": "sculptor",
        }.items()
    } == presence

    systems_by_name = {
        "aarch64-darwin": "aarch64-darwin",
        "x86_64-linux": "x86_64-linux",
        "aarch64-linux": "aarch64-linux",
    }
    assert {
        name: tuple(
            system
            for system in systems_by_name
            if _supports_system(
                overrides.get(f"{name}-crate2nix-src", {}).get("constraint"),
                system,
            )
        )
        for name in crate2nix.TARGETS
    } == {
        name: target.supported_platforms for name, target in crate2nix.TARGETS.items()
    }


def test_run_writes_refreshed_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Write mode should persist refreshed Cargo.nix and crate-hashes.json."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
    )
    repo = tmp_path / "repo"
    init_update_workspace_repo(
        repo,
        tracked_files={
            "demo/Cargo.nix": "old cargo\n",
            "demo/crate-hashes.json": '{"old": 1}\n',
        },
    )
    demo_dir = repo / "demo"

    monkeypatch.setattr(crate2nix, "REPO_ROOT", repo)
    monkeypatch.setattr(crate2nix, "TARGETS", {"demo": target})
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "linux")
    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target, **_kwargs: crate2nix.RefreshResult(
            cargo_nix="new cargo\n",
            crate_hashes='{"new": 2}\n',
        ),
    )

    rc = crate2nix.run(packages=("demo",), write=True)

    assert rc == 0
    assert (demo_dir / "Cargo.nix").read_text(encoding="utf-8") == "new cargo\n"
    assert (demo_dir / "crate-hashes.json").read_text(
        encoding="utf-8"
    ) == '{"new": 2}\n'
    assert capsys.readouterr().err == (
        "Refreshing crate2nix artifacts for demo...\n"
        "UPDATED demo\n"
        "Wrote crate2nix drift for: demo\n"
    )


def test_write_target_rejects_incomplete_source_set_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject an incomplete three-artifact result without changing any artifact."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        crate_sources=Path("demo/crate-sources.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
    )
    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    cargo_path = demo_dir / "Cargo.nix"
    hashes_path = demo_dir / "crate-hashes.json"
    sources_path = demo_dir / "crate-sources.json"
    cargo_path.write_text("old cargo\n", encoding="utf-8")
    hashes_path.write_text('{"old": 1}\n', encoding="utf-8")
    sources_path.write_text('{"old": true}\n', encoding="utf-8")
    monkeypatch.setattr(crate2nix, "REPO_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="Incomplete crate source artifact metadata"):
        crate2nix._write_target(
            target,
            crate2nix.RefreshResult(
                cargo_nix="new cargo\n",
                crate_hashes='{"new": 2}\n',
            ),
        )

    assert cargo_path.read_text(encoding="utf-8") == "old cargo\n"
    assert hashes_path.read_text(encoding="utf-8") == '{"old": 1}\n'
    assert sources_path.read_text(encoding="utf-8") == '{"old": true}\n'


def test_run_write_failure_does_not_partially_promote_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed target write must leave the live three-artifact set unchanged."""
    repo = tmp_path / "repo"
    init_update_workspace_repo(
        repo,
        tracked_files={
            "demo/Cargo.nix": "old cargo\n",
            "demo/crate-hashes.json": '{"old": 1}\n',
        },
    )
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        crate_sources=Path("demo/crate-sources.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
    )
    monkeypatch.setattr(crate2nix, "REPO_ROOT", repo)
    monkeypatch.setattr(crate2nix, "TARGETS", {"demo": target})
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "linux")
    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target, **_kwargs: crate2nix.RefreshResult(
            cargo_nix="new cargo\n",
            crate_hashes='{"new": 2}\n',
            crate_sources='{"source": {}, "slices": {}}\n',
        ),
    )

    def _fail_third_write(
        failing_target: crate2nix.Crate2NixTarget,
        refreshed: crate2nix.RefreshResult,
    ) -> None:
        (crate2nix.REPO_ROOT / failing_target.cargo_nix).write_text(
            refreshed.cargo_nix,
            encoding="utf-8",
        )
        (crate2nix.REPO_ROOT / failing_target.crate_hashes).write_text(
            refreshed.crate_hashes,
            encoding="utf-8",
        )
        raise OSError("injected manifest write failure")

    monkeypatch.setattr(crate2nix, "_write_target", _fail_third_write)

    with pytest.raises(OSError, match="injected manifest write failure"):
        crate2nix.run(packages=("demo",), write=True)

    assert (repo / target.cargo_nix).read_text(encoding="utf-8") == "old cargo\n"
    assert (repo / target.crate_hashes).read_text(encoding="utf-8") == '{"old": 1}\n'
    assert not (repo / target.crate_sources).exists()


def test_run_write_validation_failure_discards_isolated_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A contained target failure must abort the isolated write transaction."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
    )
    repo = tmp_path / "repo"
    init_update_workspace_repo(
        repo,
        tracked_files={
            "demo/Cargo.nix": "old cargo\n",
            "demo/crate-hashes.json": '{"old": 1}\n',
        },
    )
    monkeypatch.setattr(crate2nix, "REPO_ROOT", repo)
    monkeypatch.setattr(crate2nix, "TARGETS", {"demo": target})
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "linux")

    def _invalid_refresh(
        _target: crate2nix.Crate2NixTarget,
        **_kwargs,
    ) -> crate2nix.RefreshResult:
        raise ValueError("invalid source metadata")

    monkeypatch.setattr(crate2nix, "_refresh_target", _invalid_refresh)

    assert crate2nix.run(packages=("demo",), write=True) == 1
    assert (repo / target.cargo_nix).read_text(encoding="utf-8") == "old cargo\n"
    assert capsys.readouterr().err == (
        "Refreshing crate2nix artifacts for demo...\n"
        "FAIL demo: invalid source metadata\n"
    )


def test_run_write_reports_workspace_setup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep isolated-workspace failures inside the write-mode CLI boundary."""
    from lib.update import persistence

    def _workspace_failure(_root: Path) -> None:
        raise RuntimeError("workspace snapshot failed")

    monkeypatch.setattr(persistence, "IsolatedUpdateWorkspace", _workspace_failure)
    monkeypatch.setattr(crate2nix, "REPO_ROOT", tmp_path)

    assert crate2nix.run(write=True) == 1
    assert capsys.readouterr().err == "Error: workspace snapshot failed\n"


def test_run_write_with_current_artifacts_has_nothing_to_promote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An up-to-date write run should complete without a promotion summary."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
    )
    repo = tmp_path / "repo"
    init_update_workspace_repo(
        repo,
        tracked_files={
            "demo/Cargo.nix": "current cargo\n",
            "demo/crate-hashes.json": "{}\n",
        },
    )
    monkeypatch.setattr(crate2nix, "REPO_ROOT", repo)
    monkeypatch.setattr(crate2nix, "TARGETS", {"demo": target})
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "linux")
    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target, **_kwargs: crate2nix.RefreshResult(
            cargo_nix="current cargo\n",
            crate_hashes="{}\n",
        ),
    )

    assert crate2nix.run(packages=("demo",), write=True) == 0
    assert capsys.readouterr().err == (
        "Refreshing crate2nix artifacts for demo...\n"
        "OK demo\n"
        "All checked-in crate2nix artifacts are up to date.\n"
    )


def test_run_fails_when_drift_is_detected(monkeypatch, tmp_path: Path) -> None:
    """Check mode should fail when refreshed artifacts differ."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
    )
    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    (demo_dir / "Cargo.nix").write_text("old cargo\n", encoding="utf-8")
    (demo_dir / "crate-hashes.json").write_text('{"old": 1}\n', encoding="utf-8")

    monkeypatch.setattr(crate2nix, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(crate2nix, "TARGETS", {"demo": target})
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "linux")
    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target, **_kwargs: crate2nix.RefreshResult(
            cargo_nix="new cargo\n",
            crate_hashes='{"new": 2}\n',
        ),
    )

    rc = crate2nix.run(packages=("demo",), write=False)

    assert rc == 1
    assert (demo_dir / "Cargo.nix").read_text(encoding="utf-8") == "old cargo\n"


def test_current_platform_delegates_to_nix_platform_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route crate2nix platform selection through the shared Nix helper."""
    monkeypatch.setattr(crate2nix, "get_current_nix_platform", lambda: "aarch64-darwin")
    assert crate2nix._current_platform() == "aarch64-darwin"
    monkeypatch.setattr(crate2nix, "get_current_nix_platform", lambda: "x86_64-linux")
    assert crate2nix._current_platform() == "x86_64-linux"
    monkeypatch.setattr(crate2nix, "get_current_nix_platform", lambda: "aarch64-linux")
    assert crate2nix._current_platform() == "aarch64-linux"


def test_load_normalizer_handles_success_and_type_errors(tmp_path: Path) -> None:
    """Load normalizer modules and validate their callable contract."""
    good = tmp_path / "good.py"
    good.write_text(
        "def normalize(text):\n    return text + '! ', 1, True\n",
        encoding="utf-8",
    )
    normalize = crate2nix.load_normalizer(good)
    assert normalize("demo") == ("demo! ", 1, True)

    missing = tmp_path / "missing.py"
    missing.write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(TypeError, match="does not expose normalize"):
        crate2nix.load_normalizer(missing)

    invalid = tmp_path / "invalid.py"
    invalid.write_text(
        "def normalize(text):\n    return text\n",
        encoding="utf-8",
    )
    with pytest.raises(TypeError, match="returned an invalid result"):
        crate2nix.load_normalizer(invalid)("demo")

    invalid_types = tmp_path / "invalid_types.py"
    invalid_types.write_text(
        "def normalize(text):\n    return text, '1', False\n",
        encoding="utf-8",
    )
    with pytest.raises(TypeError, match="returned an invalid result"):
        crate2nix.load_normalizer(invalid_types)("demo")

    invalid_bool_count = tmp_path / "invalid_bool_count.py"
    invalid_bool_count.write_text(
        "def normalize(text):\n    return text, True, False\n",
        encoding="utf-8",
    )
    with pytest.raises(TypeError, match="returned an invalid result"):
        crate2nix.load_normalizer(invalid_bool_count)("demo")


def test_load_normalizer_rejects_missing_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail clearly when the normalizer helper module cannot be loaded."""
    monkeypatch.setattr(
        crate2nix,
        "load_module_from_path",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="Could not load normalizer"):
        crate2nix.load_normalizer(Path("missing.py"))


def test_run_helper_and_build_patched_src_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface subprocess failures and empty nix build output."""
    completed = crate2nix._run(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ['CRATE2NIX_TEST_FLAG'])",
        ],
        env={"CRATE2NIX_TEST_FLAG": "ok"},
    )
    assert completed.stdout == "ok\n"

    with pytest.raises(RuntimeError, match="boom"):
        crate2nix._run([
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('boom'); sys.exit(2)",
        ])

    monkeypatch.setattr(
        crate2nix,
        "_run",
        lambda _args, cwd=crate2nix.REPO_ROOT: subprocess.CompletedProcess(
            ["nix"], 0, stdout="/tmp/demo\n", stderr=""
        ),
    )
    assert crate2nix._build_patched_src(
        crate2nix.Crate2NixTarget(
            name="demo",
            patched_src_installable="path:.#demo",
            cargo_nix=Path("Cargo.nix"),
            crate_hashes=Path("crate-hashes.json"),
            normalizer_path=Path("normalize.py"),
            supported_platforms=("linux",),
        )
    ) == Path("/tmp/demo")

    monkeypatch.setattr(
        crate2nix,
        "_run",
        lambda _args, cwd=crate2nix.REPO_ROOT: subprocess.CompletedProcess(
            ["nix"], 0, stdout="\n", stderr=""
        ),
    )
    with pytest.raises(RuntimeError, match="Expected one patchedSrc output path"):
        crate2nix._build_patched_src(
            crate2nix.Crate2NixTarget(
                name="demo",
                patched_src_installable="path:.#demo",
                cargo_nix=Path("Cargo.nix"),
                crate_hashes=Path("crate-hashes.json"),
                normalizer_path=Path("normalize.py"),
                supported_platforms=("linux",),
            )
        )
    monkeypatch.setattr(
        crate2nix,
        "_run",
        lambda _args, cwd=crate2nix.REPO_ROOT: subprocess.CompletedProcess(
            ["nix"], 0, stdout="/tmp/one\n/tmp/two\n", stderr=""
        ),
    )
    with pytest.raises(RuntimeError, match="Expected one patchedSrc output path"):
        crate2nix._build_patched_src(
            crate2nix.Crate2NixTarget(
                name="demo",
                patched_src_installable="path:.#demo",
                cargo_nix=Path("Cargo.nix"),
                crate_hashes=Path("crate-hashes.json"),
                normalizer_path=Path("normalize.py"),
                supported_platforms=("linux",),
            )
        )


def test_run_timeout_terminates_the_complete_descendant_group(tmp_path: Path) -> None:
    """A timed-out crate2nix command must not leave its prefetch child alive."""
    pid_file = tmp_path / "pids"
    script = (
        "import os, pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text("
        "f'{os.getpid()} {child.pid}', encoding='utf-8'); "
        "print('prefetch started', flush=True); time.sleep(60)"
    )

    started = time.monotonic()
    with pytest.raises(crate2nix.Crate2NixCommandTimeoutError, match="timed out"):
        crate2nix._run([sys.executable, "-c", script, str(pid_file)], timeout=0.2)
    elapsed = time.monotonic() - started

    parent_pid, child_pid = (int(value) for value in pid_file.read_text().split())
    assert elapsed < 2.0
    assert _wait_for_process_exit(parent_pid)
    assert _wait_for_process_exit(child_pid)


def test_run_cancellation_terminates_descendants_without_waiting_for_timeout(
    tmp_path: Path,
) -> None:
    """Cancellation should wake the runner and stop its process tree promptly."""
    pid_file = tmp_path / "pids"
    cancel_event = threading.Event()
    script = (
        "import os, pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text("
        "f'{os.getpid()} {child.pid}', encoding='utf-8'); time.sleep(60)"
    )

    async def _cancel() -> None:
        task = asyncio.create_task(
            asyncio.to_thread(
                crate2nix._run,
                [sys.executable, "-c", script, str(pid_file)],
                timeout=60,
                cancel_event=cancel_event,
            )
        )
        assert await asyncio.to_thread(_wait_for_path, pid_file)
        cancel_event.set()
        with pytest.raises(crate2nix.Crate2NixCommandCancelledError):
            await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(_cancel())

    parent_pid, child_pid = (int(value) for value in pid_file.read_text().split())
    assert _wait_for_process_exit(parent_pid)
    assert _wait_for_process_exit(child_pid)


def test_run_keyboard_interrupt_terminates_descendants(tmp_path: Path) -> None:
    """Ctrl-C-style interruption should clean up the same isolated process group."""
    pid_file = tmp_path / "pids"
    script = (
        "import os, pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text("
        "f'{os.getpid()} {child.pid}', encoding='utf-8'); "
        "print('ready', flush=True); time.sleep(60)"
    )

    def _interrupt(_message: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        crate2nix._run(
            [sys.executable, "-c", script, str(pid_file)],
            timeout=60,
            progress=_interrupt,
        )

    parent_pid, child_pid = (int(value) for value in pid_file.read_text().split())
    assert _wait_for_process_exit(parent_pid)
    assert _wait_for_process_exit(child_pid)


def test_run_reports_sanitized_bounded_progress_and_output() -> None:
    """Command output should be sanitized and retained within fixed bounds."""
    progress: list[str] = []
    completed = crate2nix._run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "print('\\x1b[31mprefetching demo\\x1b[0m', flush=True); "
                "sys.stdout.write('x' * 400000); sys.stdout.flush()"
            ),
        ],
        timeout=2,
        progress=progress.append,
    )

    assert any(message == "prefetching demo" for message in progress)
    assert all("\x1b" not in message for message in progress)
    assert all(
        len(message) <= crate2nix._CRATE2NIX_PROGRESS_LINE_LIMIT for message in progress
    )
    assert len(completed.stdout) <= crate2nix._CRATE2NIX_CAPTURE_LIMIT_CHARS
    assert "output truncated" in completed.stdout


def test_run_classifies_enospc_without_retrying() -> None:
    """Disk exhaustion is a distinct terminal failure, not a transient fetch flake."""
    with pytest.raises(crate2nix.Crate2NixResourceError, match="ENOSPC"):
        crate2nix._run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "sys.stderr.write('No space left on device\\n' + 'x' * 400000); "
                    "sys.exit(1)"
                ),
            ],
            timeout=2,
        )


def test_build_patched_src_rewrites_local_path_installable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build local crate2nix sources through Git's clean source view."""
    captured: list[str] = []
    monkeypatch.setattr(
        crate2nix, "local_flake_url", lambda: "git+file:///repo?dirty=1"
    )

    def _run(args: list[str], *, cwd=crate2nix.REPO_ROOT):
        del cwd
        captured.extend(args)
        return subprocess.CompletedProcess(args, 0, stdout="/tmp/demo\n", stderr="")

    monkeypatch.setattr(crate2nix, "_run", _run)

    assert crate2nix._build_patched_src(
        crate2nix.Crate2NixTarget(
            name="demo",
            patched_src_installable="path:.#demo",
            cargo_nix=Path("Cargo.nix"),
            crate_hashes=Path("crate-hashes.json"),
            normalizer_path=Path("normalize.py"),
            supported_platforms=("linux",),
        )
    ) == Path("/tmp/demo")
    assert captured[-1] == "git+file:///repo?dirty=1#demo"


def test_build_patched_src_uses_contextual_source_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generate crate graphs from the source selected by the current update wave."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("aarch64-darwin",),
    )
    source = SourceEntry(
        version="2.0.0",
        hashes=HashCollection.from_value([
            HashEntry.create(
                "srcHash",
                "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            )
        ]),
    )
    captured: dict[str, object] = {}

    def _expr(
        package: str,
        attr_path: str,
        *,
        source_overrides: dict[str, SourceEntry],
    ) -> str:
        captured.update({
            "package": package,
            "attr_path": attr_path,
            "source_overrides": source_overrides,
        })
        return "contextual-package-expression"

    monkeypatch.setattr(crate2nix, "_build_package_path_attr_expr", _expr)

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="/nix/store/demo\n",
            stderr="",
        )

    monkeypatch.setattr(crate2nix, "_run", _run)

    assert crate2nix._build_patched_src(
        target,
        source_overrides={"demo": source},
    ) == Path("/nix/store/demo")
    assert captured == {
        "package": "demo-crate2nix-src",
        "attr_path": "",
        "source_overrides": {"demo": source},
        "args": [
            "nix",
            "build",
            "--impure",
            "--no-link",
            "--print-out-paths",
            "--expr",
            "contextual-package-expression",
        ],
    }


def test_run_crate2nix_generate_retries_transient_prefetch_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Retry the known nix-prefetch-git cleanup race without hiding real drift."""
    generated_cargo = tmp_path / "Cargo.nix"
    generated_hashes = tmp_path / "crate-hashes.json"
    hash_seed = b'{"seed": "known-hash"}\n'
    sleeps: list[float] = []
    calls = 0

    def _run(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del timeout
        calls += 1
        assert args == ["nix", "run", "nixpkgs#crate2nix"]
        assert env == {"CARGO_HOME": "/tmp/cargo"}
        assert generated_hashes.read_bytes() == hash_seed
        if calls == 1:
            generated_cargo.write_text("partial\n", encoding="utf-8")
            generated_hashes.write_text("partial\n", encoding="utf-8")
            raise RuntimeError(
                "nix run nixpkgs#crate2nix\n"
                "rm: cannot remove '/tmp/git-checkout/clone/.git/objects': "
                "Directory not empty\n"
                "Error: while prefetching crates for calculating sha256: "
                "nix-prefetch-git"
            )
        assert not generated_cargo.exists()
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(crate2nix, "_run", _run)
    monkeypatch.setattr(crate2nix.time, "sleep", sleeps.append)

    result = crate2nix._run_crate2nix_generate(
        ["nix", "run", "nixpkgs#crate2nix"],
        env={"CARGO_HOME": "/tmp/cargo"},
        generated_outputs=(generated_cargo, generated_hashes),
        seeded_outputs={generated_hashes: hash_seed},
    )

    assert result.returncode == 0
    assert result.stdout == "ok\n"
    assert calls == 2
    assert sleeps == [2.0]


def test_run_crate2nix_generate_holds_shared_cargo_home_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """crate2nix refreshes share one Cargo cache, so generation is serialized."""

    def _run(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, timeout
        assert crate2nix._CRATE2NIX_GENERATE_LOCK.locked()
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(crate2nix, "_run", _run)

    result = crate2nix._run_crate2nix_generate(
        ["nix", "run", "nixpkgs#crate2nix"],
        env={},
        generated_outputs=(tmp_path / "Cargo.nix",),
    )

    assert result.stdout == "ok\n"


def test_run_crate2nix_generate_can_cancel_while_waiting_for_shared_lock(
    tmp_path: Path,
) -> None:
    """A queued refresh must not trap its worker behind another long generation."""
    cancel_event = threading.Event()
    progress: list[str] = []

    async def _cancel() -> None:
        task = asyncio.create_task(
            asyncio.to_thread(
                crate2nix._run_crate2nix_generate,
                ["unused"],
                env={},
                generated_outputs=(tmp_path / "Cargo.nix",),
                cancel_event=cancel_event,
                progress=progress.append,
            )
        )
        await asyncio.sleep(0.05)
        cancel_event.set()
        await asyncio.sleep(0.2)
        finished_promptly = task.done()
        crate2nix._CRATE2NIX_GENERATE_LOCK.release()
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        assert finished_promptly
        assert isinstance(result, crate2nix.Crate2NixCommandCancelledError)
        assert progress == ["Waiting for the shared crate2nix Cargo cache"]

    crate2nix._CRATE2NIX_GENERATE_LOCK.acquire()
    try:
        asyncio.run(_cancel())
    finally:
        if crate2nix._CRATE2NIX_GENERATE_LOCK.locked():
            crate2nix._CRATE2NIX_GENERATE_LOCK.release()


def test_run_crate2nix_generate_does_not_retry_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Permanent crate2nix errors should fail immediately."""
    calls = 0

    def _run(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del env, timeout
        calls += 1
        message = f"{' '.join(args)}\nreal crate graph error"
        raise RuntimeError(message)

    monkeypatch.setattr(crate2nix, "_run", _run)
    monkeypatch.setattr(crate2nix.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="real crate graph error"):
        crate2nix._run_crate2nix_generate(
            ["nix", "run", "nixpkgs#crate2nix"],
            env={},
            generated_outputs=(tmp_path / "Cargo.nix",),
        )

    assert calls == 1


@pytest.mark.parametrize(
    "failure",
    [
        crate2nix.Crate2NixCommandTimeoutError("timed out"),
        crate2nix.Crate2NixCommandCancelledError("cancelled"),
        crate2nix.Crate2NixResourceError("ENOSPC"),
    ],
    ids=("timeout", "cancelled", "enospc"),
)
def test_run_crate2nix_generate_does_not_retry_terminal_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: RuntimeError,
) -> None:
    """Timeout, cancellation, and disk exhaustion must consume one attempt only."""
    calls = 0

    def _run(
        _args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del env, timeout
        calls += 1
        raise failure

    monkeypatch.setattr(crate2nix, "_run", _run)
    monkeypatch.setattr(
        crate2nix.time,
        "sleep",
        lambda _seconds: pytest.fail("terminal failures must not sleep"),
    )

    with pytest.raises(type(failure), match=str(failure)):
        crate2nix._run_crate2nix_generate(
            ["nix", "run", "nixpkgs#crate2nix"],
            env={},
            generated_outputs=(tmp_path / "Cargo.nix",),
        )

    assert calls == 1


def test_run_crate2nix_generate_bounds_all_retries_by_one_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Retries share one wall-clock budget instead of multiplying the timeout."""
    now = 100.0
    timeouts: list[float] = []

    def _monotonic() -> float:
        return now

    def _run(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal now
        del env
        timeouts.append(timeout)
        if len(timeouts) == 1:
            now += 7.0
            raise RuntimeError("nix-prefetch-git\nfatal: early EOF")
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    def _sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    monkeypatch.setattr(crate2nix.time, "monotonic", _monotonic)
    monkeypatch.setattr(crate2nix.time, "sleep", _sleep)
    monkeypatch.setattr(crate2nix, "_run", _run)

    result = crate2nix._run_crate2nix_generate(
        ["nix", "run", "nixpkgs#crate2nix"],
        env={},
        generated_outputs=(tmp_path / "Cargo.nix",),
        total_timeout=10.0,
    )

    assert result.stdout == "ok\n"
    assert timeouts == [10.0, 1.0]


def test_run_crate2nix_generate_classifies_seed_enospc_before_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A full temporary volume should fail before starting another prefetch."""
    generated_hashes = tmp_path / "crate-hashes.json"

    def _write_bytes(_path: Path, _content: bytes) -> int:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", _write_bytes)
    monkeypatch.setattr(
        crate2nix,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("subprocess must not start"),
    )

    with pytest.raises(crate2nix.Crate2NixResourceError, match="ENOSPC"):
        crate2nix._run_crate2nix_generate(
            ["nix", "run", "nixpkgs#crate2nix"],
            env={},
            generated_outputs=(tmp_path / "Cargo.nix", generated_hashes),
            seeded_outputs={generated_hashes: b"{}\n"},
        )


def test_refresh_target_classifies_temporary_directory_enospc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Temporary-space exhaustion should surface as a distinct resource failure."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
    )
    monkeypatch.setattr(crate2nix, "_build_patched_src", lambda _target: tmp_path)
    monkeypatch.setattr(
        crate2nix,
        "load_normalizer",
        lambda _path: lambda text: (text, 0, False),
    )
    monkeypatch.setattr(
        crate2nix.tempfile,
        "TemporaryDirectory",
        lambda **_kwargs: (_ for _ in ()).throw(
            OSError(errno.ENOSPC, "No space left on device")
        ),
    )

    with pytest.raises(crate2nix.Crate2NixResourceError, match="ENOSPC"):
        crate2nix._refresh_target(target)


def test_run_crate2nix_generate_gives_up_after_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Retryable failures remain failures when every bounded attempt fails."""
    calls = 0
    sleeps: list[float] = []

    def _run(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del args, env, timeout
        calls += 1
        raise RuntimeError(
            "Error: while prefetching crates for calculating sha256: "
            "nix-prefetch-git\n"
            "fatal: early EOF"
        )

    monkeypatch.setattr(crate2nix, "_run", _run)
    monkeypatch.setattr(crate2nix.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="early EOF"):
        crate2nix._run_crate2nix_generate(
            ["nix", "run", "nixpkgs#crate2nix"],
            env={},
            generated_outputs=(tmp_path / "Cargo.nix",),
            attempts=2,
        )

    assert calls == 2
    assert sleeps == [2.0]


def test_retryable_crate2nix_generate_failure_requires_prefetch_context() -> None:
    """Retry classification should stay scoped to crate2nix prefetch flakes."""
    assert crate2nix._is_retryable_crate2nix_generate_failure(
        "nix-prefetch-git\nRPC failed"
    )
    assert crate2nix._is_retryable_crate2nix_generate_failure(
        "cargo metadata\n"
        "failed to download from https://index.crates.io/config.json\n"
        "[28] Timeout was reached (Operation too slow)"
    )
    assert crate2nix._is_retryable_crate2nix_generate_failure(
        "cargo metadata\n"
        "error: cannot create the lock file /nix/store/source/Cargo.lock"
    )
    assert crate2nix._is_retryable_crate2nix_generate_failure(
        "git fetch\n"
        "fatal: could not open '/tmp/git/db/repo/objects/pack/tmp_pack' "
        "for reading: No such file or directory"
    )
    assert not crate2nix._is_retryable_crate2nix_generate_failure("RPC failed")
    assert not crate2nix._is_retryable_crate2nix_generate_failure(
        "nix-prefetch-git\nreal crate graph error"
    )


def test_read_generated_hash_text_defaults_to_empty_json(tmp_path: Path) -> None:
    """crate2nix may skip the hash file when a target has no git dependencies."""
    missing_hashes = tmp_path / "crate-hashes.json"

    assert crate2nix._read_generated_hash_text(missing_hashes) == "{}\n"

    missing_hashes.write_text('{"demo": "hash"}\n', encoding="utf-8")
    assert crate2nix._read_generated_hash_text(missing_hashes) == '{"demo": "hash"}\n'


def test_refresh_target_materializes_normalized_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Refresh should run crate2nix generation and normalize both outputs."""
    patched_src = tmp_path / "patched-src"
    patched_src.mkdir()
    (patched_src / "Cargo.toml").write_text(
        "[package]\nname = 'demo'\n", encoding="utf-8"
    )
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
        cargo_manifest_relpath=Path("nested/Cargo.toml"),
    )
    monkeypatch.setattr(crate2nix, "_build_patched_src", lambda _target: patched_src)
    cargo_home = tmp_path / "cargo-cache"
    monkeypatch.setattr(crate2nix, "_crate2nix_cargo_home", lambda: cargo_home)
    monkeypatch.setattr(
        crate2nix,
        "load_normalizer",
        lambda _path: lambda text: (text.replace("raw", "normalized") + "\n", 2, True),
    )

    def _run(
        args: list[str],
        *,
        cwd: Path = crate2nix.REPO_ROOT,
        env: dict[str, str] | None = None,
        timeout: float,
    ):
        del cwd, timeout
        assert args[:6] == [
            "nix",
            "run",
            "--inputs-from",
            ".",
            "nixpkgs#crate2nix",
            "--",
        ]
        assert args[args.index("-f") + 1] == str(patched_src / "nested/Cargo.toml")
        generated_cargo = Path(args[args.index("-o") + 1])
        generated_hashes = Path(args[args.index("-h") + 1])
        assert env is not None
        assert env["CARGO_HOME"] == str(cargo_home)
        assert cargo_home.is_dir()
        assert env["CARGO_NET_GIT_FETCH_WITH_CLI"] == "true"
        generated_cargo.write_text(
            "# This file was @generated by crate2nix 0.15.0 with the command:\n"
            '#   "generate" "-f" "./Cargo.toml" "-o" "/tmp/Cargo.nix" '
            '"-h" "/tmp/crate-hashes.json" "--default-features"\n'
            "# See https://github.com/kolloch/crate2nix for more info.\n"
            "raw cargo\n",
            encoding="utf-8",
        )
        generated_hashes.write_text('{"b": 1, "a": 2}\n', encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(crate2nix, "_run", _run)
    refreshed = crate2nix._refresh_target(target)
    assert (
        refreshed.cargo_nix
        == "# This file was @generated by crate2nix 0.15.0 with the command:\n"
        '#   "generate" "-f" "nested/Cargo.toml" "-o" "demo/Cargo.nix" '
        '"-h" "demo/crate-hashes.json" "--default-features"\n'
        "# See https://github.com/kolloch/crate2nix for more info.\n"
        "normalized cargo\n"
    )
    assert refreshed.crate_hashes == '{\n  "a": 2,\n  "b": 1\n}\n'


def test_refresh_target_seeds_only_unchanged_locked_git_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reuse checked hashes only when the new lock preserves the resolved source."""
    repo = tmp_path / "repo"
    package_dir = repo / "demo"
    package_dir.mkdir(parents=True)
    patched_src = tmp_path / "patched-src"
    patched_src.mkdir()
    (patched_src / "Cargo.lock").write_text(
        """version = 3

[[package]]
name = "current"
version = "1.0.0"
source = "git+https://example.com/current?rev=aaaaaaaa#aaaaaaaa"

[[package]]
name = "branch-source"
version = "2.0.0"
source = "git+https://example.com/branch?branch=main#cccccccc"

[[package]]
name = "same-version-new-revision"
version = "3.0.0"
source = "git+https://example.com/stale?rev=dddddddd#dddddddd"
""",
        encoding="utf-8",
    )
    checked_hashes = {
        "git+https://example.com/current?rev=aaaaaaaa#current@1.0.0": "hash-current",
        "git+https://example.com/branch?branch=main#branch-source@2.0.0": "hash-branch",
        "git+https://example.com/stale?rev=bbbbbbbb#same-version-new-revision@3.0.0": "hash-stale",
        "git+https://example.com/removed?rev=eeeeeeee#removed@4.0.0": "hash-removed",
    }
    checked_hash_path = package_dir / "crate-hashes.json"
    checked_hash_path.write_text(json.dumps(checked_hashes), encoding="utf-8")
    (package_dir / "Cargo.nix").write_text(
        """{ pkgs }:
{
  current.src = pkgs.fetchgit {
    url = "https://example.com/current";
    rev = "aaaaaaaa";
    sha256 = "hash-current";
  };
  branch.src = pkgs.fetchgit {
    url = "https://example.com/branch";
    rev = "cccccccc";
    sha256 = "hash-branch";
  };
  stale.src = pkgs.fetchgit {
    url = "https://example.com/stale";
    rev = "bbbbbbbb";
    sha256 = "hash-stale";
  };
  removed.src = pkgs.fetchgit {
    url = "https://example.com/removed";
    rev = "eeeeeeee";
    sha256 = "hash-removed";
  };
}
""",
        encoding="utf-8",
    )
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
    )
    monkeypatch.setattr(crate2nix, "REPO_ROOT", repo)
    monkeypatch.setattr(crate2nix, "_build_patched_src", lambda _target: patched_src)
    monkeypatch.setattr(
        crate2nix,
        "load_normalizer",
        lambda _path: lambda text: (text, 0, False),
    )

    def _generate(
        args: list[str],
        *,
        env: dict[str, str],
        generated_outputs: tuple[Path, ...],
        seeded_outputs: dict[Path, bytes],
    ) -> subprocess.CompletedProcess[str]:
        del args, env
        generated_cargo, generated_hashes = generated_outputs
        assert json.loads(seeded_outputs[generated_hashes]) == {
            "git+https://example.com/branch?branch=main#branch-source@2.0.0": "hash-branch",
            "git+https://example.com/current?rev=aaaaaaaa#current@1.0.0": "hash-current",
        }
        assert checked_hash_path.read_text(encoding="utf-8") == json.dumps(
            checked_hashes
        )
        generated_cargo.write_text("{}\n", encoding="utf-8")
        generated_hashes.write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(crate2nix, "_run_crate2nix_generate", _generate)

    crate2nix._refresh_target(target)

    assert checked_hash_path.read_text(encoding="utf-8") == json.dumps(checked_hashes)


def test_refresh_target_emits_source_slices_from_the_final_generated_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Refresh should hash local crates only after all Cargo.nix normalization."""
    patched_src = tmp_path / "patched-src"
    patched_src.mkdir()
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
        source_input="demo",
        crate_sources=Path("demo/crate-sources.json"),
    )
    monkeypatch.setattr(crate2nix, "_build_patched_src", lambda _target: patched_src)
    monkeypatch.setattr(
        crate2nix,
        "load_normalizer",
        lambda _path: lambda text: (text, 1, True),
    )

    def _generate(
        args: list[str],
        *,
        env: dict[str, str],
        generated_outputs: tuple[Path, ...],
        seeded_outputs: dict[Path, bytes],
    ) -> subprocess.CompletedProcess[str]:
        del args, env
        assert seeded_outputs == {}
        cargo_nix, crate_hashes = generated_outputs
        cargo_nix.write_text(
            """{ lib
, rootSrc ? ./.
}:
rec {
  demo = {
    src = lib.cleanSourceWith {
      filter = sourceFilter;
      src = "${rootSrc}/crates/demo";
    };
  };
  sourceFilter = _name: _type: true;
}
""",
            encoding="utf-8",
        )
        crate_hashes.write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(crate2nix, "_run_crate2nix_generate", _generate)
    seen: dict[str, object] = {}

    def _manifest(
        manifest_target: crate2nix.Crate2NixTarget,
        source_paths: tuple[str, ...],
        *,
        cargo_nix: Path,
    ) -> str:
        seen["target"] = manifest_target
        seen["paths"] = source_paths
        seen["cargo"] = cargo_nix.read_text(encoding="utf-8")
        return '{"source": {}, "slices": {}}\n'

    monkeypatch.setattr(crate2nix, "_render_crate_source_manifest", _manifest)

    refreshed = crate2nix._refresh_target(target)

    assert seen["target"] == target
    assert seen["paths"] == ("crates/demo",)
    cargo = expect_instance(seen["cargo"], str)
    cargo_function = expect_instance(parse_nix_expr(cargo), FunctionDefinition)
    cargo_body = expect_instance(cargo_function.output, AttributeSet)
    demo = expect_instance(
        expect_binding(cargo_body.values, "demo").value, AttributeSet
    )
    assert_nix_ast_equal(
        expect_binding(demo.values, "src").value,
        'crateSource sourceFilter "crates/demo"',
    )
    assert refreshed.crate_sources == '{"source": {}, "slices": {}}\n'


def test_crate2nix_cargo_home_defaults_to_xdg_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """crate2nix should reuse a writable nixcfg-owned Cargo cache."""
    cache_home = tmp_path / "cache"
    monkeypatch.delenv("NIXCFG_CRATE2NIX_CARGO_HOME", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))

    assert (
        crate2nix._crate2nix_cargo_home()
        == cache_home / "nixcfg" / "crate2nix-cargo-home"
    )


def test_crate2nix_cargo_home_respects_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Allow CI or local debugging to choose the crate2nix Cargo cache."""
    cargo_home = tmp_path / "custom-cargo-home"
    monkeypatch.setenv("NIXCFG_CRATE2NIX_CARGO_HOME", str(cargo_home))

    assert crate2nix._crate2nix_cargo_home() == cargo_home


@pytest.mark.parametrize(
    "validation_error",
    [
        ValueError("Invalid local crate source path: '../escape'"),
        TypeError("Nix returned invalid crate source path metadata"),
    ],
    ids=("value-error", "type-error"),
)
def test_run_reports_validation_failures_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    validation_error: ValueError | TypeError,
) -> None:
    """Expected validation failures should remain inside the CLI boundary."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("test-system",),
    )
    monkeypatch.setattr(crate2nix, "TARGETS", {target.name: target})
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "test-system")

    def fail_validation(
        _target: crate2nix.Crate2NixTarget,
        **_kwargs,
    ) -> crate2nix.RefreshResult:
        raise validation_error

    monkeypatch.setattr(crate2nix, "_refresh_target", fail_validation)

    assert crate2nix.run(packages=(target.name,)) == 1
    assert capsys.readouterr().err == (
        f"Refreshing crate2nix artifacts for demo...\nFAIL demo: {validation_error}\n"
    )


def test_run_reports_live_crate2nix_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The synchronous maintenance command should not look hung during generation."""
    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("test-system",),
    )
    package_dir = tmp_path / "demo"
    package_dir.mkdir()
    (package_dir / "Cargo.nix").write_text("same\n", encoding="utf-8")
    (package_dir / "crate-hashes.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(crate2nix, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(crate2nix, "TARGETS", {target.name: target})
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "test-system")

    def _refresh(
        _target: crate2nix.Crate2NixTarget,
        *,
        progress,
    ) -> crate2nix.RefreshResult:
        progress("Prefetching cached Git sources")
        return crate2nix.RefreshResult(cargo_nix="same\n", crate_hashes="{}\n")

    monkeypatch.setattr(crate2nix, "_refresh_target", _refresh)

    assert crate2nix.run(packages=(target.name,)) == 0
    assert "demo: Prefetching cached Git sources\n" in capsys.readouterr().err


def test_resolve_targets_and_run_cover_remaining_control_flow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise unsupported, empty, failure, and success run branches."""
    assert crate2nix.run(packages=("missing",), write=False) == 1
    assert "Error: Unknown crate2nix target" in capsys.readouterr().err

    with pytest.raises(RuntimeError, match="Unknown crate2nix target"):
        crate2nix._resolve_targets(("missing",))

    target = crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("linux",),
    )
    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    (demo_dir / "Cargo.nix").write_text("same\n", encoding="utf-8")
    (demo_dir / "crate-hashes.json").write_text('{\n  "a": 1\n}\n', encoding="utf-8")

    monkeypatch.setattr(crate2nix, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(crate2nix, "TARGETS", {"demo": target})

    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "darwin")
    assert crate2nix.run(packages=("demo",), write=False) == 1
    assert "unsupported on this platform" in capsys.readouterr().err

    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "darwin")
    monkeypatch.setattr(crate2nix, "TARGETS", {})
    assert crate2nix.run() == 0
    assert "No crate2nix targets are runnable" in capsys.readouterr().err

    monkeypatch.setattr(crate2nix, "TARGETS", {"demo": target})
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "linux")
    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert crate2nix.run() == 1
    assert "Skipping unsupported crate2nix targets" not in capsys.readouterr().err

    skipped_target = crate2nix.Crate2NixTarget(
        name="zed",
        patched_src_installable="path:.#zed",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("darwin",),
    )
    monkeypatch.setattr(crate2nix, "TARGETS", {"demo": target, "zed": skipped_target})
    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target, **_kwargs: crate2nix.RefreshResult(
            cargo_nix="same\n",
            crate_hashes='{\n  "a": 1\n}\n',
        ),
    )
    assert crate2nix.run() == 0
    assert (
        "Skipping unsupported crate2nix targets on this platform: zed"
        in capsys.readouterr().err
    )
    monkeypatch.setattr(crate2nix, "TARGETS", {"demo": target})
    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert crate2nix.run() == 1
    assert "FAIL demo: boom" in capsys.readouterr().err

    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target, **_kwargs: crate2nix.RefreshResult(
            cargo_nix="same\n",
            crate_hashes='{\n  "a": 1\n}\n',
        ),
    )
    assert crate2nix.run() == 0
    assert (
        "All checked-in crate2nix artifacts are up to date." in capsys.readouterr().err
    )
