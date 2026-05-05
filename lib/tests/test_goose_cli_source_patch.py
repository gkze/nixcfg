"""Tests for Goose CLI source-tree patching before crate2nix builds."""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lib.tests._updater_helpers import load_repo_module


def _load_module() -> ModuleType:
    return load_repo_module(
        "overlays/goose-cli/patch_source.py", "goose_cli_patch_source_test"
    )


def _toml_payload(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _cargo_lock_packages(path: Path) -> dict[str, dict[str, Any]]:
    packages = _toml_payload(path).get("package", [])
    assert isinstance(packages, list)
    by_name: dict[str, dict[str, Any]] = {}
    for package in packages:
        assert isinstance(package, dict)
        name = package.get("name")
        assert isinstance(name, str)
        by_name[name] = package
    return by_name


def _write_minimal_goose_tree(root: Path) -> None:
    (root / "crates/goose-cli/src").mkdir(parents=True)
    (root / "crates/goose-cli/src/main.rs").write_text(
        '"../../../../documentation/static/img/logo_dark.png"\n'
        '"../../../../documentation/static/img/logo_light.png"\n',
        encoding="utf-8",
    )
    (root / "crates/goose-cli/src/untouched.rs").write_text(
        "fn main() {}\n", encoding="utf-8"
    )
    (root / "documentation/static/img").mkdir(parents=True)
    (root / "documentation/static/img/logo_dark.png").write_text(
        "dark", encoding="utf-8"
    )
    (root / "documentation/static/img/logo_light.png").write_text(
        "light", encoding="utf-8"
    )
    (root / "vendor/v8").mkdir(parents=True)
    (root / "vendor/v8/Cargo.toml").write_text(
        '[dependencies]\nv8-goose = "0.0.1"\n',
        encoding="utf-8",
    )
    (root / "vendor/v8-goose-src").mkdir(parents=True)
    (root / "vendor/v8-goose-src/Cargo.toml").write_text(
        """
[package]
name = "v8-goose"
version = "1.2.3"

[workspace]
members = ["."]

[workspace.dependencies]
foo = "1"

[profile.dev]
debug = true

[dev-dependencies]
tempfile = "3"

[[example]]
name = "demo"

[[test]]
name = "demo-test"

[[bench]]
name = "demo-bench"

[dependencies]
serde = "1"
""".lstrip(),
        encoding="utf-8",
    )
    (root / "Cargo.lock").write_text(
        """
[[package]]
name = "v8-goose"
version = "0.0.1"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "abc"
dependencies = [
 "serde",
]

[[package]]
name = "serde"
version = "1.0.0"
""".lstrip(),
        encoding="utf-8",
    )


def test_patch_source_rewrites_goose_workspace(tmp_path: Path) -> None:
    """Patch the copied source tree without embedding Python in Nix."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)

    module.patch_source(tmp_path)

    main_rs = (tmp_path / "crates/goose-cli/src/main.rs").read_text(encoding="utf-8")
    assert main_rs == (
        '"../../static/img/logo_dark.png"\n"../../static/img/logo_light.png"\n'
    )
    assert (
        tmp_path / "crates/goose-cli/static/img/logo_dark.png"
    ).read_text() == "dark"
    assert (
        tmp_path / "crates/goose-cli/static/img/logo_light.png"
    ).read_text() == "light"

    v8_cargo = _toml_payload(tmp_path / "vendor/v8/Cargo.toml")
    assert v8_cargo["dependencies"]["v8-goose"] == {"path": "../v8-goose-src"}

    v8_goose_cargo = _toml_payload(tmp_path / "vendor/v8-goose-src/Cargo.toml")
    assert "workspace" not in v8_goose_cargo
    assert "dependencies" in v8_goose_cargo

    cargo_lock_packages = _cargo_lock_packages(tmp_path / "Cargo.lock")
    assert cargo_lock_packages["v8-goose"]["version"] == "1.2.3"
    assert "source" not in cargo_lock_packages["v8-goose"]
    assert "checksum" not in cargo_lock_packages["v8-goose"]


def test_patch_source_allows_no_goose_logo_rewrites(tmp_path: Path) -> None:
    """Logo copying is conditional; the V8 rewrites still run without it."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "crates/goose-cli/src/main.rs").write_text(
        "fn main() {}\n", encoding="utf-8"
    )

    module.main([str(tmp_path)])

    assert not (tmp_path / "crates/goose-cli/static").exists()
    assert _toml_payload(tmp_path / "vendor/v8/Cargo.toml")["dependencies"][
        "v8-goose"
    ] == {"path": "../v8-goose-src"}


def test_rewrite_goose_logo_paths_returns_false_without_source_dir(
    tmp_path: Path,
) -> None:
    """The logo rewrite helper should tolerate source trees without the CLI crate."""
    module = _load_module()

    assert module.rewrite_goose_logo_paths(tmp_path) is False


def test_rewrite_v8_dependency_requires_exact_dependency_line(tmp_path: Path) -> None:
    """The V8 dependency rewrite should fail loudly when upstream changes shape."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "vendor/v8/Cargo.toml").write_text("[dependencies]\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="expected one v8-goose dependency"):
        module.rewrite_v8_dependency(tmp_path)


def test_rewrite_v8_goose_lock_entry_requires_existing_lock_entry(
    tmp_path: Path,
) -> None:
    """The lockfile rewrite should not silently skip a missing V8 package."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.lock").write_text(
        '[[package]]\nname = "serde"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="Cargo.lock entry"):
        module.rewrite_v8_goose_lock_entry(tmp_path, "1.2.3")
