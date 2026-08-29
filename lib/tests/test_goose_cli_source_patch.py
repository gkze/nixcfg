"""Tests for Goose CLI source-tree patching before crate2nix builds."""

import re
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
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = []\n[workspace.package]\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
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


def _write_workspace_member(root: Path, member: str, manifest: str) -> None:
    manifest_path = root / member / "Cargo.toml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest, encoding="utf-8")


def _append_lock_packages(root: Path, packages: str) -> None:
    with (root / "Cargo.lock").open("a", encoding="utf-8") as lock_file:
        lock_file.write(packages)


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


def test_patch_source_reconciles_v1_48_workspace_lock_versions(
    tmp_path: Path,
) -> None:
    """Inherited workspace versions must match the release before crate2nix runs."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n'
        '[workspace.package]\nversion = "1.48.0"\n',
        encoding="utf-8",
    )
    _write_workspace_member(
        tmp_path,
        "crates/goose-roaming",
        '[package]\nname = "goose-roaming"\nversion.workspace = true\n',
    )
    _write_workspace_member(
        tmp_path,
        "crates/goose-sdk",
        '[package]\nname = "goose-sdk"\nversion = "0.1.0-alpha.6"\n',
    )
    _append_lock_packages(
        tmp_path,
        '\n[[package]]\nname = "goose-roaming"\nversion = "1.47.0"\n'
        '\n[[package]]\nname = "goose-sdk"\nversion = "0.1.0-alpha.6"\n',
    )

    module.patch_source(tmp_path)

    cargo_lock_packages = _cargo_lock_packages(tmp_path / "Cargo.lock")
    assert cargo_lock_packages["goose-roaming"]["version"] == "1.48.0"
    assert cargo_lock_packages["goose-sdk"]["version"] == "0.1.0-alpha.6"


def test_workspace_lock_reconciliation_honors_workspace_excludes(
    tmp_path: Path,
) -> None:
    """Packages excluded from the workspace must not inherit its release version."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\nexclude = ["crates/excluded"]\n'
        '[workspace.package]\nversion = "1.48.0"\n',
        encoding="utf-8",
    )
    inherited_manifest = '[package]\nname = "{name}"\nversion.workspace = true\n'
    _write_workspace_member(
        tmp_path,
        "crates/included",
        inherited_manifest.format(name="included"),
    )
    _write_workspace_member(
        tmp_path,
        "crates/excluded",
        inherited_manifest.format(name="excluded"),
    )
    _append_lock_packages(
        tmp_path,
        '\n[[package]]\nname = "included"\nversion = "1.47.0"\n'
        '\n[[package]]\nname = "excluded"\nversion = "1.47.0"\n',
    )

    module.rewrite_workspace_lock_versions(tmp_path)

    packages = _cargo_lock_packages(tmp_path / "Cargo.lock")
    assert packages["included"]["version"] == "1.48.0"
    assert packages["excluded"]["version"] == "1.47.0"


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ('[package]\nname = "goose"\n', "expected [workspace]"),
        ('workspace = "invalid"\n', "expected [workspace]"),
        ("[workspace]\nmembers = []\n", "expected [workspace.package]"),
        (
            '[workspace]\nmembers = []\npackage = "invalid"\n',
            "expected [workspace.package]",
        ),
        (
            "[workspace]\nmembers = []\n[workspace.package]\n",
            "expected workspace.package.version",
        ),
        (
            "[workspace]\nmembers = []\n[workspace.package]\nversion = 148\n",
            "expected workspace.package.version",
        ),
        (
            '[workspace]\n[workspace.package]\nversion = "1.48.0"\n',
            "expected workspace.members",
        ),
        (
            '[workspace]\nmembers = "crates/*"\n'
            '[workspace.package]\nversion = "1.48.0"\n',
            "expected workspace.members",
        ),
        (
            '[workspace]\nmembers = [1]\n[workspace.package]\nversion = "1.48.0"\n',
            "expected workspace.members",
        ),
        (
            '[workspace]\nmembers = []\nexclude = "vendor/*"\n'
            '[workspace.package]\nversion = "1.48.0"\n',
            "expected workspace.exclude",
        ),
        (
            "[workspace]\nmembers = []\nexclude = [1]\n"
            '[workspace.package]\nversion = "1.48.0"\n',
            "expected workspace.exclude",
        ),
    ],
)
def test_workspace_lock_reconciliation_rejects_malformed_root_manifests(
    tmp_path: Path,
    manifest: str,
    message: str,
) -> None:
    """Reject root manifests whose workspace shape cannot be interpreted safely."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(manifest, encoding="utf-8")

    with pytest.raises(SystemExit, match=re.escape(message)):
        module.rewrite_workspace_lock_versions(tmp_path)


def test_workspace_lock_reconciliation_ignores_non_package_member_matches(
    tmp_path: Path,
) -> None:
    """Wildcard matches without package manifests do not become lockfile targets."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n'
        '[workspace.package]\nversion = "1.48.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "crates/no-manifest").mkdir(parents=True)
    _write_workspace_member(
        tmp_path,
        "crates/virtual-workspace",
        "[workspace]\nmembers = []\n",
    )

    before = (tmp_path / "Cargo.lock").read_bytes()
    module.rewrite_workspace_lock_versions(tmp_path)

    assert (tmp_path / "Cargo.lock").read_bytes() == before


def test_workspace_lock_reconciliation_skips_non_inherited_versions(
    tmp_path: Path,
) -> None:
    """Only version.workspace = true packages inherit the release version."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n'
        '[workspace.package]\nversion = "1.48.0"\n',
        encoding="utf-8",
    )
    _write_workspace_member(
        tmp_path,
        "crates/explicit",
        '[package]\nname = "explicit"\nversion = "2.0.0"\n',
    )
    _write_workspace_member(
        tmp_path,
        "crates/not-inherited",
        '[package]\nname = "not-inherited"\nversion.workspace = false\n',
    )

    before = (tmp_path / "Cargo.lock").read_bytes()
    module.rewrite_workspace_lock_versions(tmp_path)

    assert (tmp_path / "Cargo.lock").read_bytes() == before


@pytest.mark.parametrize(
    ("package_fields", "message"),
    [
        ("version.workspace = true\n", "expected package.name"),
        ("name = 148\nversion.workspace = true\n", "expected package.name"),
    ],
)
def test_workspace_lock_reconciliation_requires_inherited_package_names(
    tmp_path: Path,
    package_fields: str,
    message: str,
) -> None:
    """Every inherited workspace package must have a textual Cargo package name."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n'
        '[workspace.package]\nversion = "1.48.0"\n',
        encoding="utf-8",
    )
    _write_workspace_member(
        tmp_path,
        "crates/broken",
        "[package]\n" + package_fields,
    )

    with pytest.raises(SystemExit, match=message):
        module.rewrite_workspace_lock_versions(tmp_path)


def test_workspace_lock_reconciliation_rejects_duplicate_inherited_names(
    tmp_path: Path,
) -> None:
    """Ambiguous workspace package names must fail before editing Cargo.lock."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n'
        '[workspace.package]\nversion = "1.48.0"\n',
        encoding="utf-8",
    )
    manifest = '[package]\nname = "duplicate"\nversion.workspace = true\n'
    _write_workspace_member(tmp_path, "crates/one", manifest)
    _write_workspace_member(tmp_path, "crates/two", manifest)

    with pytest.raises(SystemExit, match="duplicate inherited workspace package"):
        module.rewrite_workspace_lock_versions(tmp_path)


def test_workspace_lock_reconciliation_preserves_registry_duplicate_bytes(
    tmp_path: Path,
) -> None:
    """A registry crate with the same name is not the local workspace package."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n'
        '[workspace.package]\nversion = "1.48.0"\n',
        encoding="utf-8",
    )
    _write_workspace_member(
        tmp_path,
        "crates/goose-roaming",
        '[package]\nname = "goose-roaming"\nversion.workspace = true\n',
    )
    registry_entry = (
        '\n[[package]]\nname = "goose-roaming"\nversion = "0.9.0"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        'checksum = "registry-bytes-stay-identical"\n'
    )
    _append_lock_packages(
        tmp_path,
        registry_entry + '\n[[package]]\nname = "goose-roaming"\nversion = "1.47.0"\n',
    )

    module.rewrite_workspace_lock_versions(tmp_path)

    lock_text = (tmp_path / "Cargo.lock").read_text(encoding="utf-8")
    assert registry_entry in lock_text
    local_packages = [
        package
        for package in _toml_payload(tmp_path / "Cargo.lock")["package"]
        if package["name"] == "goose-roaming" and "source" not in package
    ]
    assert local_packages == [{"name": "goose-roaming", "version": "1.48.0"}]


def test_workspace_lock_reconciliation_rejects_duplicate_local_entries(
    tmp_path: Path,
) -> None:
    """Multiple local entries for one workspace package are ambiguous."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n'
        '[workspace.package]\nversion = "1.48.0"\n',
        encoding="utf-8",
    )
    _write_workspace_member(
        tmp_path,
        "crates/goose-roaming",
        '[package]\nname = "goose-roaming"\nversion.workspace = true\n',
    )
    _append_lock_packages(
        tmp_path,
        '\n[[package]]\nname = "goose-roaming"\nversion = "1.46.0"\n'
        '\n[[package]]\nname = "goose-roaming"\nversion = "1.47.0"\n',
    )

    with pytest.raises(SystemExit, match="duplicate local Cargo.lock entry"):
        module.rewrite_workspace_lock_versions(tmp_path)


def test_workspace_lock_reconciliation_requires_local_lock_version(
    tmp_path: Path,
) -> None:
    """A matched local lock entry must retain Cargo's single version field."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n'
        '[workspace.package]\nversion = "1.48.0"\n',
        encoding="utf-8",
    )
    _write_workspace_member(
        tmp_path,
        "crates/goose-roaming",
        '[package]\nname = "goose-roaming"\nversion.workspace = true\n',
    )
    _append_lock_packages(
        tmp_path,
        '\n[[package]]\nname = "goose-roaming"\ndependencies = ["serde"]\n',
    )

    with pytest.raises(SystemExit, match="expected one version"):
        module.rewrite_workspace_lock_versions(tmp_path)


def test_workspace_lock_reconciliation_sorts_missing_local_entries(
    tmp_path: Path,
) -> None:
    """Missing-package diagnostics are deterministic for updater logs."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n'
        '[workspace.package]\nversion = "1.48.0"\n',
        encoding="utf-8",
    )
    for name in ("zeta", "alpha"):
        _write_workspace_member(
            tmp_path,
            f"crates/{name}",
            f'[package]\nname = "{name}"\nversion.workspace = true\n',
        )

    with pytest.raises(SystemExit, match="alpha, zeta"):
        module.rewrite_workspace_lock_versions(tmp_path)


def test_workspace_lock_reconciliation_is_idempotent_and_preserves_other_bytes(
    tmp_path: Path,
) -> None:
    """A second reconciliation is byte-identical and unrelated entries stay intact."""
    module = _load_module()
    _write_minimal_goose_tree(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\n'
        '[workspace.package]\nversion = "1.48.0"\n',
        encoding="utf-8",
    )
    _write_workspace_member(
        tmp_path,
        "crates/goose-roaming",
        '[package]\nname = "goose-roaming"\nversion.workspace = true\n',
    )
    untouched_entry = (
        '\n[[package]]\nname = "unrelated"\nversion = "7.0.0"\n'
        'dependencies = [\n "serde",\n]\n'
    )
    _append_lock_packages(
        tmp_path,
        untouched_entry + '\n[[package]]\nname = "goose-roaming"\nversion = "1.47.0"\n',
    )

    module.rewrite_workspace_lock_versions(tmp_path)
    first_pass = (tmp_path / "Cargo.lock").read_bytes()
    module.rewrite_workspace_lock_versions(tmp_path)

    assert (tmp_path / "Cargo.lock").read_bytes() == first_pass
    assert untouched_entry.encode() in first_pass


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


def test_patch_goose_cli_crate_prepares_a_filtered_crate_source(
    tmp_path: Path,
) -> None:
    """Build-time crate preparation should not require the full workspace tree."""
    module = _load_module()
    crate_root = tmp_path / "goose-cli"
    (crate_root / "src").mkdir(parents=True)
    (crate_root / "src/main.rs").write_text(
        '"../../../../documentation/static/img/logo_dark.png"\n'
        '"../../../../documentation/static/img/logo_light.png"\n',
        encoding="utf-8",
    )
    logo_root = tmp_path / "logos"
    logo_root.mkdir()
    (logo_root / "logo_dark.png").write_text("dark", encoding="utf-8")
    (logo_root / "logo_light.png").write_text("light", encoding="utf-8")

    module.patch_goose_cli_crate(crate_root, logo_root)

    assert (crate_root / "src/main.rs").read_text(encoding="utf-8") == (
        '"../../static/img/logo_dark.png"\n"../../static/img/logo_light.png"\n'
    )
    assert (crate_root / "static/img/logo_dark.png").read_text() == "dark"
    assert (crate_root / "static/img/logo_light.png").read_text() == "light"


def test_patch_goose_cli_crate_skips_logos_without_references(tmp_path: Path) -> None:
    """Do not stage unused assets when a filtered crate has no logo references."""
    module = _load_module()
    crate_root = tmp_path / "goose-cli"
    (crate_root / "src").mkdir(parents=True)
    (crate_root / "src/main.rs").write_text("fn main() {}\n", encoding="utf-8")
    logo_root = tmp_path / "logos"
    logo_root.mkdir()

    module.patch_goose_cli_crate(crate_root, logo_root)

    assert not (crate_root / "static").exists()


def test_main_can_prepare_only_a_filtered_goose_cli_crate(tmp_path: Path) -> None:
    """The build-time CLI path should not require the workspace-only files."""
    module = _load_module()
    crate_root = tmp_path / "goose-cli"
    (crate_root / "src").mkdir(parents=True)
    (crate_root / "src/main.rs").write_text(
        '"../../../../documentation/static/img/logo_dark.png"\n'
        '"../../../../documentation/static/img/logo_light.png"\n',
        encoding="utf-8",
    )
    logo_root = tmp_path / "logos"
    logo_root.mkdir()
    (logo_root / "logo_dark.png").write_text("dark", encoding="utf-8")
    (logo_root / "logo_light.png").write_text("light", encoding="utf-8")

    module.main([
        "--goose-cli-crate",
        "--logo-source-dir",
        str(logo_root),
        str(crate_root),
    ])

    assert (crate_root / "src/main.rs").read_text(encoding="utf-8") == (
        '"../../static/img/logo_dark.png"\n"../../static/img/logo_light.png"\n'
    )
    assert (crate_root / "static/img/logo_dark.png").read_text() == "dark"
    assert (crate_root / "static/img/logo_light.png").read_text() == "light"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--goose-cli-crate", "crate"], "requires --logo-source-dir"),
        (["--logo-source-dir", "logos", "workspace"], "requires --goose-cli-crate"),
    ],
)
def test_main_rejects_incomplete_filtered_crate_options(
    argv: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Require the filtered-crate mode and logo source to be selected together."""
    module = _load_module()

    with pytest.raises(SystemExit, match="2"):
        module.main(argv)

    assert message in capsys.readouterr().err


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
