"""Tests for GitButler source patching."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import ModuleType

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT

_BUT_ID_SOURCE = """impl<'a> Node<'a> for &'a UncommittedFile {
    fn parse(
        self: Box<Self>,
        element: &str,
        _id_map: &'a IdMap,
        _changes_in_commit_fn: &mut ChangesInCommitFn<'a>,
    ) -> anyhow::Result<Vec<Box<dyn Node<'a> + 'a>>> {
        match element.strip_prefix(INDEX_SEPARATOR) {
            Some(maybe_index) if let Ok(index) = usize::from_str(maybe_index) => {
                if let Some((hunk_id, hunk_assignment)) = self.short_id_hunk_assignments.get(index)
                {
                    let cli_id = CliId::UncommittedHunkOrFile(UncommittedHunkOrFile {
                        id: format!("{}:{}", self.short_id, hunk_id.short_id()),
                        hunk_assignments: NonEmpty::new(hunk_assignment.to_owned()),
                        is_entire_file: false,
                    });
                    Ok(vec![Box::new(Leaf { cli_id })])
                } else {
                    Ok(vec![])
                }
            }
            _ => {
                let matches = self
                    .short_id_hunk_assignments
                    .iter()
                    .filter(|(hunk_id, _)| hunk_id.matches_prefix(element))
                    .map(|(hunk_id, hunk_assignment)| {
                        let cli_id = CliId::UncommittedHunkOrFile(UncommittedHunkOrFile {
                            id: format!("{}:{}", self.short_id, hunk_id.short_id()),
                            hunk_assignments: NonEmpty::new(hunk_assignment.to_owned()),
                            is_entire_file: false,
                        });
                        Box::new(Leaf { cli_id }) as Box<dyn Node<'a> + 'a>
                    });

                Ok(matches.collect())
            }
        }
    }
}
"""

_PATCHED_BUT_ID_SOURCE = """impl<'a> Node<'a> for &'a UncommittedFile {
    fn parse(
        self: Box<Self>,
        element: &str,
        _id_map: &'a IdMap,
        _changes_in_commit_fn: &mut ChangesInCommitFn<'a>,
    ) -> anyhow::Result<Vec<Box<dyn Node<'a> + 'a>>> {
        if let Some(maybe_index) = element.strip_prefix(INDEX_SEPARATOR) {
            if let Ok(index) = usize::from_str(maybe_index) {
                return if let Some((hunk_id, hunk_assignment)) =
                    self.short_id_hunk_assignments.get(index)
                {
                    let cli_id = CliId::UncommittedHunkOrFile(UncommittedHunkOrFile {
                        id: format!("{}:{}", self.short_id, hunk_id.short_id()),
                        hunk_assignments: NonEmpty::new(hunk_assignment.to_owned()),
                        is_entire_file: false,
                    });
                    Ok(vec![Box::new(Leaf { cli_id })])
                } else {
                    Ok(vec![])
                };
            }
        }

        let matches = self
            .short_id_hunk_assignments
            .iter()
            .filter(|(hunk_id, _)| hunk_id.matches_prefix(element))
            .map(|(hunk_id, hunk_assignment)| {
                let cli_id = CliId::UncommittedHunkOrFile(UncommittedHunkOrFile {
                    id: format!("{}:{}", self.short_id, hunk_id.short_id()),
                    hunk_assignments: NonEmpty::new(hunk_assignment.to_owned()),
                    is_entire_file: false,
                });
                Box::new(Leaf { cli_id }) as Box<dyn Node<'a> + 'a>
            });

        Ok(matches.collect())
    }
}
"""


def _load_patch_module() -> ModuleType:
    module_path = REPO_ROOT / "packages" / "gitbutler" / "patch_sources.py"
    return load_module_from_path(module_path, "_gitbutler_patch_sources")


def _write_tauri_source(root: Path, build_rs: str) -> Path:
    tauri_root = root / "crates" / "gitbutler-tauri"
    tauri_root.mkdir(parents=True)
    (tauri_root / "build.rs").write_text(build_rs, encoding="utf-8")
    (tauri_root / "tauri.conf.json").write_text(
        json.dumps({
            "build": {"beforeBuildCommand": "pnpm build", "frontendDist": "../app"},
            "bundle": {"active": True, "icon": []},
            "identifier": "dev.gitbutler.app",
            "plugins": {
                "deep-link": {"desktop": {"schemes": []}},
                "updater": {"endpoints": []},
            },
            "productName": "GitButler Dev",
        }),
        encoding="utf-8",
    )
    return tauri_root


def _write_but_source(root: Path, lib_rs: str | None = None) -> Path:
    but_root = root / "crates" / "but" / "src"
    but_root.mkdir(parents=True)
    if lib_rs is None:
        lib_rs = """fn expand_aliases(args: Vec<OsString>) -> Vec<OsString> {
    match &parsed_args.cmd {
        Some(Subcommands::External(subcommand_args))
            if let Some(command_name) = subcommand_args.first() =>
        {
            if let Some(command_name) = command_name.to_str() {
                command_name.into()
            } else {
                args
            }
        }
        _ => args,
    }
}
"""
    (but_root / "lib.rs").write_text(lib_rs, encoding="utf-8")
    id_root = but_root / "id"
    id_root.mkdir()
    (id_root / "mod.rs").write_text(_BUT_ID_SOURCE, encoding="utf-8")
    return but_root


def test_patch_sources_rewrites_tauri_config_and_build_script(tmp_path: Path) -> None:
    """Patched sources should match the Nix-packaged app layout."""
    module = _load_patch_module()
    build_rs = """fn main() {
    let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    assert_eq!(manifest_dir.file_name().unwrap(), "gitbutler-tauri");
    let build_dir = manifest_dir
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("apps")
        .join("desktop")
        .join("build");
}
"""
    tauri_root = _write_tauri_source(tmp_path, build_rs)

    module._patch_tauri_config(tmp_path)
    module._patch_build_rs(tmp_path)

    config = json.loads((tauri_root / "tauri.conf.json").read_text(encoding="utf-8"))
    assert config["build"]["frontendDist"] == "frontend-dist"
    assert config["bundle"]["active"] is False
    assert config["identifier"] == "com.gitbutler.app"

    assert (
        (tauri_root / "build.rs").read_text(encoding="utf-8")
        == """fn main() {
    let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    assert_eq!(manifest_dir.file_name().unwrap(), "gitbutler-tauri");
    let build_dir = manifest_dir.join("frontend-dist");
}
"""
    )


def test_patch_sources_rewrites_but_alias_guard(tmp_path: Path) -> None:
    """Patched sources should avoid Rust's experimental if-let guards."""
    module = _load_patch_module()
    but_root = _write_but_source(tmp_path)

    module._patch_but_cli_alias_guard(tmp_path)

    assert (
        (but_root / "lib.rs").read_text(encoding="utf-8")
        == """fn expand_aliases(args: Vec<OsString>) -> Vec<OsString> {
    match &parsed_args.cmd {
        Some(Subcommands::External(subcommand_args)) => {
            if let Some(command_name) = subcommand_args.first().and_then(|arg| arg.to_str()) {
                command_name.into()
            } else {
                args
            }
        }
        _ => args,
    }
}
"""
    )


def test_patch_sources_rewrites_but_uncommitted_file_index_guard(
    tmp_path: Path,
) -> None:
    """Patched sources should avoid another Rust experimental if-let guard."""
    module = _load_patch_module()
    but_root = _write_but_source(tmp_path)

    module._patch_but_uncommitted_file_index_guard(tmp_path)

    assert (but_root / "id" / "mod.rs").read_text(encoding="utf-8") == (
        _PATCHED_BUT_ID_SOURCE
    )


def test_patch_build_rs_errors_when_frontend_dist_snippet_is_missing(
    tmp_path: Path,
) -> None:
    """Unexpected upstream build.rs shape should fail loudly."""
    module = _load_patch_module()
    _write_tauri_source(tmp_path, "fn main() {}\n")

    with pytest.raises(SystemExit, match="frontendDist snippet not found"):
        module._patch_build_rs(tmp_path)


def test_patch_but_cli_alias_guard_errors_when_snippet_is_missing(
    tmp_path: Path,
) -> None:
    """Unexpected upstream but CLI shape should fail loudly."""
    module = _load_patch_module()
    _write_but_source(tmp_path, "fn expand_aliases() {}\n")

    with pytest.raises(SystemExit, match="but alias guard snippet not found"):
        module._patch_but_cli_alias_guard(tmp_path)


def test_patch_but_uncommitted_file_index_guard_errors_when_snippet_is_missing(
    tmp_path: Path,
) -> None:
    """Unexpected upstream but ID parser shape should fail loudly."""
    module = _load_patch_module()
    but_root = _write_but_source(tmp_path)
    (but_root / "id" / "mod.rs").write_text("impl Parser {}\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="uncommitted file index guard snippet"):
        module._patch_but_uncommitted_file_index_guard(tmp_path)


def test_main_validates_argv_and_patches_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI entrypoint should validate arguments and patch the provided tree."""
    module = _load_patch_module()
    _write_tauri_source(
        tmp_path,
        """fn main() {
    let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let build_dir = manifest_dir
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("apps")
        .join("desktop")
        .join("build");
}
""",
    )
    _write_but_source(tmp_path)

    monkeypatch.setattr(module.sys, "argv", ["patch_sources.py"])
    with pytest.raises(SystemExit, match="usage: patch_sources.py"):
        module.main()

    monkeypatch.setattr(module.sys, "argv", ["patch_sources.py", str(tmp_path)])
    assert module.main() == 0


def test_main_guard_exits_with_main_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Executing the helper as __main__ should raise SystemExit(main())."""
    _write_tauri_source(
        tmp_path,
        """fn main() {
    let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let build_dir = manifest_dir
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("apps")
        .join("desktop")
        .join("build");
}
""",
    )
    _write_but_source(tmp_path)
    script_path = REPO_ROOT / "packages/gitbutler/patch_sources.py"

    monkeypatch.setattr("sys.argv", [str(script_path), str(tmp_path)])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(script_path), run_name="__main__")

    assert excinfo.value.code == 0
