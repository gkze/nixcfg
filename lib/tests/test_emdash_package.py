"""Structural tests for the Emdash Darwin Node build contract."""

from nix_manipulator.expressions.indented_string import IndentedString

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._nix_source import nix_file_binding_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell


def test_emdash_allows_darwin_loopback_for_nx_and_vite() -> None:
    """Hosted Darwin sandboxing aborts Node when Nx/Vite bind localhost."""
    assert_nix_ast_equal(
        nix_file_binding_expr(
            "packages/emdash/default.nix",
            "__darwinAllowLocalNetworking",
        ),
        "true",
    )


def test_emdash_build_env_disables_the_nx_daemon() -> None:
    """`ensure-packages-built` shells `pnpm exec nx`; the daemon is the abort."""
    assert_nix_ast_equal(
        nix_file_binding_expr("packages/emdash/default.nix", "env"),
        """electronBuild.commonEnv // {
          CI = "1";
          EMDASH_NIXCFG_BUILD_REV = "3";
          NX_DAEMON = "false";
          NX_NO_CLOUD = "true";
          npm_config_build_from_source = "true";
          npm_config_manage_package_manager_versions = "false";
          npm_config_node_linker = "hoisted";
        }""",
    )


def test_emdash_still_builds_workspace_packages_through_nx() -> None:
    """The abort fix must keep the Nx workspace build, not delete it."""
    build_phase = nix_file_binding_expr("packages/emdash/default.nix", "buildPhase")
    shell = parse_shell(indented_string_body(build_phase.rebuild()))
    node_commands = command_texts(shell, "node")
    assert any(
        "tooling/scripts/ensure-packages-built.mjs" in command
        for command in node_commands
    )
    assert any(
        command.startswith("pnpm exec electron-rebuild")
        for command in command_texts(shell, "pnpm")
    )
    assert any(
        command == "pnpm run build" for command in command_texts(shell, "pnpm")
    )


def test_emdash_keeps_darwin_install_checks() -> None:
    """Darwin installCheck is the packaged-app contract, not a skippable checkPhase."""
    assert_nix_ast_equal(
        nix_file_binding_expr("packages/emdash/default.nix", "doInstallCheck"),
        "stdenv.hostPlatform.isDarwin",
    )
    install_check = nix_file_binding_expr(
        "packages/emdash/default.nix",
        "installCheckPhase",
    )
    assert isinstance(install_check, IndentedString)
    shell = parse_shell(indented_string_body(install_check.rebuild()))
    assert any("check-info-plist-hash" in command for command in command_texts(shell))
