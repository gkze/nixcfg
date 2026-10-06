"""Structural tests for the Emdash Darwin Node build contract."""

from nix_manipulator.expressions.indented_string import IndentedString

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._nix_source import nix_file_binding_expr
from lib.tests._shell_ast import (
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)


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
          CHOKIDAR_USEPOLLING = "1";
          EMDASH_NIXCFG_BUILD_REV = "9";
          ESBUILD_WORKER_THREADS = "0";
          NODE_OPTIONS = "--max-old-space-size=6144";
          UV_THREADPOOL_SIZE = "1";
          WATCHPACK_POLLING = "true";
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
        if "--filter '@emdash/emdash-desktop^...'" in command and "run build" in command
    ]
    assert len(workspace_builds) == 1
    assert "--filter '!@emdash/workspace-server'" in workspace_builds[0]
    assert "--workspace-concurrency=1" in workspace_builds[0]
    assert any(
        command.startswith("pnpm exec electron-rebuild") for command in pnpm_commands
    )
    assert any(command == "pnpm exec electron-vite build" for command in pnpm_commands)
    assert not any(command == "pnpm run build" for command in pnpm_commands)
    patch_commands = [
        command
        for command in command_texts(shell)
        if command.split()[:1] == ["patchShebangs"]
    ]
    assert patch_commands == ["patchShebangs node_modules"]
    node_shims = [
        command
        for command in command_texts(shell, "ln")
        if command.endswith("node_modules/.bin/node")
    ]
    assert len(node_shims) == 1
    assert node_shims[0].startswith("ln -sfn ")
    rebuilds = [
        command
        for command in pnpm_commands
        if command.startswith("pnpm exec electron-rebuild")
    ]
    assert len(rebuilds) == 1
    patch_node = next(
        node
        for node in iter_nodes(shell.tree.root_node, "command")
        if node_text(node, shell.sanitized).startswith("patchShebangs")
    )
    shim_node = next(
        node
        for node in iter_nodes(shell.tree.root_node, "command")
        if node_text(node, shell.sanitized).startswith("ln -sfn ")
        and node_text(node, shell.sanitized).endswith("node_modules/.bin/node")
    )
    rebuild_node = next(
        node
        for node in iter_nodes(shell.tree.root_node, "command")
        if node_text(node, shell.sanitized).startswith("pnpm exec electron-rebuild")
    )
    assert patch_node.end_byte < shim_node.start_byte < rebuild_node.start_byte


def test_emdash_opens_darwin_sandbox_for_electron_vite() -> None:
    """#201 opened the profile; 37406317879's abort was heap OOM, not sandbox."""
    assert_nix_ast_equal(
        nix_file_binding_expr("packages/emdash/default.nix", "sandboxProfile"),
        """lib.optionalString stdenv.hostPlatform.isDarwin ''
          (allow default)
        ''""",
    )


def test_emdash_disables_electron_vite_server_watch() -> None:
    """Renderer server.port 3000 must not start @parcel/watcher under the sandbox."""
    post_patch = nix_file_binding_expr("packages/emdash/default.nix", "postPatch")
    shell = parse_shell(indented_string_body(post_patch.rebuild()))
    assignments = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "variable_assignment")
        if node_text(node, shell.sanitized).endswith("electron.vite.config.ts")
    ]
    substitutes = [
        command
        for command in command_texts(shell, "substituteInPlace")
        if "watch: null" in command
    ]
    assert len(assignments) == 1
    assert assignments[0].startswith("electron_vite_config=")
    assert assignments[0].endswith("electron.vite.config.ts")
    assert len(substitutes) == 1
    assert "port: 3000, watch: null, hmr: false," in substitutes[0]


def test_emdash_strips_build_node_modules_after_install() -> None:
    """Sandbox cleanup must not walk the desktop node_modules symlink."""
    post_install = nix_file_binding_expr("packages/emdash/default.nix", "postInstall")
    shell = parse_shell(indented_string_body(post_install.rebuild()))
    clean_commands = [
        command
        for command in command_texts(shell)
        if command.split()[-2:] == ["clean", '"$PWD"']
    ]
    assert len(clean_commands) == 1


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
