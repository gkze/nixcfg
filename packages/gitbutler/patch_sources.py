#!/usr/bin/env python3
"""Patch GitButler upstream sources for the Nix crate2nix build."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from lib.codemods.errors import CodemodError
from lib.codemods.text import replace_file_once

EXPECTED_ARG_COUNT = 2


def _patch_tauri_config(source_root: Path) -> None:
    config_path = source_root / "crates/gitbutler-tauri/tauri.conf.json"
    config = json.loads(config_path.read_text())

    config["productName"] = "GitButler"
    config["identifier"] = "com.gitbutler.app"
    config["build"]["beforeBuildCommand"] = ""
    config["build"]["frontendDist"] = "frontend-dist"
    config["bundle"]["active"] = False
    config["bundle"]["icon"] = [
        "icons/release/32x32.png",
        "icons/release/128x128.png",
        "icons/release/128x128@2x.png",
        "icons/release/icon.icns",
        "icons/release/icon.ico",
    ]
    config["plugins"]["updater"]["endpoints"] = [
        "https://app.gitbutler.com/releases/release/{{target}}-{{arch}}/{{current_version}}"
    ]
    config["plugins"]["deep-link"]["desktop"]["schemes"] = ["but"]

    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")


def _patch_build_rs(source_root: Path) -> None:
    build_rs_path = source_root / "crates/gitbutler-tauri/build.rs"
    old = """    let build_dir = manifest_dir
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("apps")
        .join("desktop")
        .join("build");
"""
    new = """    let build_dir = manifest_dir.join("frontend-dist");
"""
    try:
        replace_file_once(
            build_rs_path,
            old,
            new,
            context="GitButler build.rs frontendDist snippet",
        )
    except CodemodError as exc:
        msg = f"expected GitButler build.rs frontendDist snippet not found in {build_rs_path}"
        raise SystemExit(msg) from exc


def _patch_but_cli_alias_guard(source_root: Path) -> None:
    lib_rs_path = source_root / "crates/but/src/lib.rs"
    old = """    match &parsed_args.cmd {
        Some(Subcommands::External(subcommand_args))
            if let Some(command_name) = subcommand_args.first() =>
        {
            if let Some(command_name) = command_name.to_str() {
"""
    new = """    match &parsed_args.cmd {
        Some(Subcommands::External(subcommand_args)) => {
            if let Some(command_name) = subcommand_args.first().and_then(|arg| arg.to_str()) {
"""
    try:
        replace_file_once(
            lib_rs_path,
            old,
            new,
            context="GitButler but alias guard snippet",
        )
    except CodemodError as exc:
        msg = f"expected GitButler but alias guard snippet not found in {lib_rs_path}"
        raise SystemExit(msg) from exc


def _patch_but_uncommitted_file_index_guard(source_root: Path) -> None:
    id_mod_path = source_root / "crates/but/src/id/mod.rs"
    old = """        match element.strip_prefix(INDEX_SEPARATOR) {
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
"""
    new = """        if let Some(maybe_index) = element.strip_prefix(INDEX_SEPARATOR) {
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
"""
    try:
        replace_file_once(
            id_mod_path,
            old,
            new,
            context="GitButler but uncommitted file index guard snippet",
        )
    except CodemodError as exc:
        msg = (
            "expected GitButler but uncommitted file index guard snippet "
            f"not found in {id_mod_path}"
        )
        raise SystemExit(msg) from exc


def main() -> int:
    """Patch the GitButler source tree named on the command line."""
    if len(sys.argv) != EXPECTED_ARG_COUNT:
        msg = "usage: patch_sources.py <source-root>"
        raise SystemExit(msg)
    source_root = Path(sys.argv[1])
    _patch_tauri_config(source_root)
    _patch_build_rs(source_root)
    _patch_but_cli_alias_guard(source_root)
    _patch_but_uncommitted_file_index_guard(source_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
