"""Structural tests for the Emdash Darwin Node build contract."""

from nix_manipulator.expressions.indented_string import IndentedString

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._nix_source import nix_file_binding_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell


def test_emdash_allows_darwin_loopback_outbound() -> None:
    """Loopback outbound stays on; it is not treated as a listen() grant."""
    assert_nix_ast_equal(
        nix_file_binding_expr(
            "packages/emdash/default.nix",
            "__darwinAllowLocalNetworking",
        ),
        "true",
    )


def test_emdash_build_env_keeps_esbuild_in_process() -> None:
    """The esbuild service thread is a leftover localhost listener after Nx is gone."""
    assert_nix_ast_equal(
        nix_file_binding_expr("packages/emdash/default.nix", "env"),
        """electronBuild.commonEnv // {
          CI = "1";
          EMDASH_NIXCFG_BUILD_REV = "3";
          ESBUILD_WORKER_THREADS = "0";
          npm_config_build_from_source = "true";
          npm_config_manage_package_manager_versions = "false";
          npm_config_node_linker = "hoisted";
        }""",
    )


def test_emdash_builds_workspace_packages_without_nx() -> None:
    """The aborting `ensure-packages-built` / Nx path must not run."""
    build_phase = nix_file_binding_expr("packages/emdash/default.nix", "buildPhase")
    shell = parse_shell(indented_string_body(build_phase.rebuild()))
    pnpm_commands = command_texts(shell, "pnpm")
    node_commands = command_texts(shell, "node")

    assert not any(
        "tooling/scripts/ensure-packages-built.mjs" in command
        for command in node_commands
    )
    assert not any("nx" in command.split() for command in pnpm_commands)

    workspace_builds = [
        command
        for command in pnpm_commands
        if "--filter '@emdash/emdash-desktop^...'" in command
        and "run build" in command
    ]
    assert len(workspace_builds) == 1
    assert "--filter '!@emdash/workspace-server'" in workspace_builds[0]
    assert "--workspace-concurrency=1" in workspace_builds[0]
    assert any(
        command.startswith("pnpm exec electron-rebuild") for command in pnpm_commands
    )
    assert any(command == "pnpm run build" for command in pnpm_commands)


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
