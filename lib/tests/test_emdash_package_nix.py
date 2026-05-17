"""AST-level checks for Emdash packaging wrappers."""

from __future__ import annotations

from functools import cache

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._shell_ast import command_texts, parse_shell
from lib.update.paths import REPO_ROOT


@cache
def _derivation() -> FunctionCall:
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/emdash/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, FunctionCall)


@cache
def _derivation_args() -> AttributeSet:
    return expect_instance(_derivation().argument, AttributeSet)


def _install_branch_scripts() -> tuple[IndentedString, IndentedString]:
    install_phase = expect_instance(
        expect_binding(_derivation_args().values, "installPhase").value,
        IfExpression,
    )
    return (
        expect_instance(install_phase.consequence, IndentedString),
        expect_instance(install_phase.alternative, IndentedString),
    )


def _compact_shell(text: str) -> str:
    return " ".join(text.split())


def test_emdash_darwin_build_loads_upstream_app_identity_config() -> None:
    """Darwin packaging should not fall back to Electron's generic bundle metadata."""
    build_phase = expect_instance(
        expect_binding(_derivation_args().values, "buildPhase").value,
        IndentedString,
    )

    expected_entrypoints = (
        "node_modules/@octokit/request/dist-bundle/index.js",
        "node_modules/@gitbeaker/requester-utils/dist/index.mjs",
        "node_modules/@gitbeaker/core/dist/index.mjs",
        "node_modules/@gitbeaker/rest/dist/index.mjs",
    )
    for entrypoint in expected_entrypoints:
        assert entrypoint in build_phase.value

    for required_contract in (
        "pnpm exec electron-builder",
        "--config electron-builder.config.ts",
        "-c.directories.output=dist",
        "pnpm exec esbuild",
        "__nixcfgCreateRequire",
        "pnpm exec asar extract",
        'EMDASH_ASAR_DIR="$asar_dir" ${lib.getExe nodejs} ${materializeAsarNodeModules}',
        'rm -rf "$old_unpacked"',
        'pnpm exec asar pack "$asar_dir" "$app_resources/app.asar" --unpack "*.node"',
        "${../../lib/asar_integrity.py}",
        "set-info-plist-hash",
    ):
        assert required_contract in build_phase.value


def test_emdash_install_check_exercises_octokit_request_entrypoint() -> None:
    """The build should fail if Octokit's packaged ESM dependency graph breaks."""
    install_check = expect_instance(
        expect_binding(_derivation_args().values, "installCheckPhase").value,
        IndentedString,
    )

    shell = parse_shell(install_check.value)
    electron_smokes = [
        command
        for command in command_texts(shell)
        if command.startswith("ELECTRON_RUN_AS_NODE=1")
    ]
    assert len(electron_smokes) == 4

    for required_contract in (
        "check-info-plist-hash",
        "app.asar.unpacked",
        "@octokit/request/dist-bundle/index.js",
        "@gitbeaker/requester-utils/dist/index.mjs",
        "@gitbeaker/core/dist/index.mjs",
        "@gitbeaker/rest/dist/index.mjs",
        "package dependency closure ok",
        '"node-pty", "better-sqlite3", "@parcel/watcher"',
        "form-data/lib/form_data.js",
        "CFBundleIdentifier",
        "com.emdash.stable",
    ):
        assert required_contract in install_check.value


def test_emdash_exposes_managed_mac_app_metadata() -> None:
    """Emdash should be routed through the managed macOS app surface."""
    passthru = expect_instance(
        expect_binding(_derivation_args().values, "passthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "macApp").value,
        """
{
  bundleName = "Emdash.app";
  bundleRelPath = "Applications/Emdash.app";
  installMode = "copy";
}
""",
    )


def test_emdash_launchers_are_installed_from_repo_scripts() -> None:
    """The platform launchers should live as shell files, not inline heredocs."""
    darwin_install, linux_install = _install_branch_scripts()
    darwin_shell = parse_shell(darwin_install.value)
    linux_shell = parse_shell(linux_install.value)

    assert command_texts(darwin_shell, "install") == [
        'install -d "$out/Applications"',
        'install -d "$out/bin"',
        'install -m755 __NIX_INTERP__ "$out/bin/emdash"',
    ]
    assert command_texts(linux_shell, "install") == [
        'install -d "$out/share/emdash"',
        'install -d "$out/bin"',
        'install -m755 __NIX_INTERP__ "$out/bin/emdash"',
    ]
    assert "linux*-unpacked" in linux_install.value
    assert '"$out/share/emdash/linux-unpacked"' in linux_install.value
    expected_substitute = (
        'substituteInPlace "$out/bin/emdash" \\ '
        '--replace-fail "#!/usr/bin/env bash" "#!__NIX_INTERP__" \\ '
        '--replace-fail "@out@" "$out"'
    )
    assert [
        _compact_shell(text)
        for text in command_texts(darwin_shell, "substituteInPlace")
    ] == [expected_substitute]
    assert [
        _compact_shell(text) for text in command_texts(linux_shell, "substituteInPlace")
    ] == [expected_substitute]
