"""Contracts for the shared Nix/Python target-system policy."""

from lib.system_policy import (
    bun_artifact_names,
    electron_artifact_tags,
    required_root_kinds,
    supported_systems,
    system_policy,
)
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._nix_source import nix_file_binding_expr
from lib.tests._package_registry import registry_capability_constraints
from lib.update.config import DEFAULT_CONFIG
from lib.update.updaters import ensure_updaters_loaded


def test_system_policy_separates_roots_from_artifact_support() -> None:
    """Keep configured roots narrower than public artifact compatibility."""
    expected = (
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    )

    assert system_policy().schema_version == 1
    assert supported_systems() == expected
    assert required_root_kinds() == ("darwin", "home")
    artifact_tags = electron_artifact_tags()
    assert artifact_tags == {
        "aarch64-darwin": "darwin-arm64",
        "aarch64-linux": "linux-arm64",
        "x86_64-darwin": "darwin-x64",
        "x86_64-linux": "linux-x64",
    }
    assert set(expected) < set(artifact_tags)
    assert bun_artifact_names() == {
        "aarch64-darwin": "bun-darwin-aarch64.zip",
        "aarch64-linux": "bun-linux-aarch64.zip",
        "x86_64-darwin": "bun-darwin-x64.zip",
        "x86_64-linux": "bun-linux-x64-baseline.zip",
    }
    assert DEFAULT_CONFIG.hash_build_platforms == expected
    assert (
        expect_instance(
            vars(ensure_updaters_loaded()["electron-runtimes"])["PLATFORMS"],
            dict,
        )
        == artifact_tags
    )
    assert_nix_ast_equal(
        nix_file_binding_expr("flake.nix", "systemPolicy"),
        "builtins.fromJSON (builtins.readFile ./lib/system-policy.json)",
    )
    assert_nix_ast_equal(
        nix_file_binding_expr("flake.nix", "systems"),
        """
        assert systemPolicy.schemaVersion == 1;
        builtins.attrNames systemPolicy.systems
        """,
    )
    assert_nix_ast_equal(
        nix_file_binding_expr(
            "packages/electron-runtimes/default.nix",
            "artifactSystems",
        ),
        """
        assert systemPolicy.schemaVersion == 1;
        builtins.attrNames systemPolicy.electronArtifacts
        """,
    )


def test_package_registry_filters_shared_systems_by_package_capability() -> None:
    """Registry constraints are projections of policy, not a second root-system list."""
    assert registry_capability_constraints() == {
        "aarch64DarwinPackages": ["aarch64-darwin"],
        "darwinLinuxPackages": ["aarch64-darwin", "x86_64-linux"],
        "nonX86DarwinLinuxPackages": [
            "aarch64-darwin",
            "aarch64-linux",
            "x86_64-linux",
        ],
    }
    assert_nix_ast_equal(
        nix_file_binding_expr("packages/registry.nix", "systemPolicy"),
        'builtins.fromJSON (builtins.readFile (src + "/lib/system-policy.json"))',
    )
    assert_nix_ast_equal(
        nix_file_binding_expr("packages/registry.nix", "rootSystems"),
        """
        assert systemPolicy.schemaVersion == 1;
        builtins.attrNames systemPolicy.systems
        """,
    )
