#!/usr/bin/env python3
"""Patch GitButler upstream sources for the Nix crate2nix build."""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
    original = build_rs_path.read_text()
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
    if old not in original:
        msg = f"expected GitButler build.rs frontendDist snippet not found in {build_rs_path}"
        raise SystemExit(msg)
    build_rs_path.write_text(original.replace(old, new, 1))


def main() -> int:
    """Patch the GitButler source tree named on the command line."""
    if len(sys.argv) != EXPECTED_ARG_COUNT:
        msg = "usage: patch_sources.py <source-root>"
        raise SystemExit(msg)
    source_root = Path(sys.argv[1])
    _patch_tauri_config(source_root)
    _patch_build_rs(source_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
