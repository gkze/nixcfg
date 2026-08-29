#!/usr/bin/env python3
"""Patch GitButler upstream sources for the Nix crate2nix build."""

import json
import sys
from pathlib import Path

from lib.codemods.errors import CodemodError
from lib.codemods.text import replace_file_once

WORKSPACE_ARG_COUNT = 2
ISOLATED_CRATE_ARG_COUNT = 3
EXPECTED_ARG_COUNTS = {WORKSPACE_ARG_COUNT, ISOLATED_CRATE_ARG_COUNT}
PATCHED_CRATES = {"but", "gitbutler-tauri"}
ISOLATED_TAURI_MANIFEST = """[package]
name = "gitbutler-tauri"
version = "0.0.0"
edition = "2024"
publish = false

[lib]
doctest = false
crate-type = ["lib", "staticlib", "cdylib"]

[[bin]]
name = "gitbutler-tauri"
path = "src/main.rs"
test = false

[build-dependencies]
tauri-build = { version = "2.6.1", features = [] }

[dependencies]
tauri = { version = "^2.11.1", features = ["unstable"] }
"""


def _patch_tauri_config(crate_root: Path) -> None:
    config_path = crate_root / "tauri.conf.json"
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


def _patch_build_rs(crate_root: Path) -> None:
    build_rs_path = crate_root / "build.rs"
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


def _write_isolated_tauri_manifest(crate_root: Path) -> None:
    """Give tauri-build the package metadata it normally inherits from the workspace."""
    (crate_root / "Cargo.toml").write_text(
        ISOLATED_TAURI_MANIFEST,
        encoding="utf-8",
    )


def _patch_but_cli_alias_guard(crate_root: Path) -> None:
    lib_rs_path = crate_root / "src/lib.rs"
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


def _patch_but_uncommitted_file_index_guard(crate_root: Path) -> None:
    id_mod_path = crate_root / "src/id/mod.rs"
    old = """        match element.strip_prefix(INDEX_SEPARATOR) {
            Some(maybe_index) if let Ok(index) = usize::from_str(maybe_index) => {
"""
    new = """        let maybe_index = element
            .strip_prefix(INDEX_SEPARATOR)
            .and_then(|value| usize::from_str(value).ok());
        match maybe_index {
            Some(index) => {
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


def _patch_crate(crate_name: str, crate_root: Path) -> None:
    if crate_name == "but":
        _patch_but_cli_alias_guard(crate_root)
        _patch_but_uncommitted_file_index_guard(crate_root)
    elif crate_name == "gitbutler-tauri":
        _patch_tauri_config(crate_root)
        _patch_build_rs(crate_root)
    else:  # pragma: no cover -- guarded by main before dispatch
        msg = f"unsupported GitButler crate: {crate_name}"
        raise SystemExit(msg)


def main() -> int:
    """Patch a GitButler workspace or one isolated workspace crate."""
    if len(sys.argv) not in EXPECTED_ARG_COUNTS:
        msg = "usage: patch_sources.py <source-root> [but|gitbutler-tauri]"
        raise SystemExit(msg)
    source_root = Path(sys.argv[1])
    if len(sys.argv) == ISOLATED_CRATE_ARG_COUNT:
        crate_name = sys.argv[2]
        if crate_name not in PATCHED_CRATES:
            msg = f"unsupported GitButler crate: {crate_name}"
            raise SystemExit(msg)
        _patch_crate(crate_name, source_root)
        if crate_name == "gitbutler-tauri":
            _write_isolated_tauri_manifest(source_root)
    else:
        for crate_name in sorted(PATCHED_CRATES):
            _patch_crate(crate_name, source_root / "crates" / crate_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
