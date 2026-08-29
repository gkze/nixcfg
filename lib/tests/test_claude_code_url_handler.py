"""Contracts for the generated Claude Code URL handler app."""

import os
import plistlib
import subprocess
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.update.paths import REPO_ROOT

_PACKAGE_DIR = REPO_ROOT / "packages/claude-code-url-handler"
_INFO_PLIST = _PACKAGE_DIR / "Info.plist"


def _load_info_plist(path: Path = _INFO_PLIST) -> dict[str, object]:
    with path.open("rb") as plist_file:
        return plistlib.load(plist_file)


def test_info_plist_preserves_claude_cli_url_contract() -> None:
    """LaunchServices metadata should preserve the installed handler contract."""
    assert _load_info_plist() == {
        "CFBundleExecutable": "claude",
        "CFBundleIdentifier": "com.anthropic.claude-code-url-handler",
        "CFBundleName": "Claude Code URL Handler",
        "CFBundlePackageType": "APPL",
        "CFBundleURLTypes": [
            {
                "CFBundleURLName": "Claude Code Deep Link",
                "CFBundleURLSchemes": ["claude-cli"],
            }
        ],
        "CFBundleVersion": "1.0",
        "LSBackgroundOnly": True,
    }


def test_package_wires_the_app_to_the_managed_claude_cli() -> None:
    """The generated app should resolve its executable from the Nix CLI package."""
    package = expect_instance(
        nix_file_expr("packages/claude-code-url-handler/default.nix"),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    attrs = expect_instance(derivation.argument, AttributeSet)
    passthru = expect_instance(
        expect_binding(attrs.values, "passthru").value,
        AttributeSet,
    )
    mac_app = expect_instance(
        expect_binding(passthru.values, "macApp").value,
        AttributeSet,
    )
    claude_executable = expect_instance(
        expect_binding(attrs.values, "claudeExecutable").value,
        FunctionCall,
    )

    assert_nix_ast_equal(derivation.name, "stdenvNoCC.mkDerivation")
    assert_nix_ast_equal(
        expect_binding(attrs.values, "version").value,
        "claude-code.version",
    )
    assert_nix_ast_equal(claude_executable.name, "lib.getExe")
    assert_nix_ast_equal(
        claude_executable.argument,
        Identifier(name="claude-code"),
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "infoPlist").value,
        "./Info.plist",
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "installPhase").value,
        "./install.sh",
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "installCheckPhase").value,
        "./check.sh",
    )
    assert_nix_ast_equal(
        mac_app,
        """{
          bundleName = "Claude Code URL Handler.app";
          bundleRelPath = "Applications/Claude Code URL Handler.app";
          installMode = "copy";
        }""",
    )


def test_installer_builds_a_runnable_app_bundle(tmp_path: Path) -> None:
    """The generated executable link should point only at the supplied Nix CLI."""
    output = tmp_path / "output"
    fake_cli = tmp_path / "nix-store" / "claude-code" / "bin" / "claude"
    fake_cli.parent.mkdir(parents=True)
    fake_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_cli.chmod(0o755)

    env = {
        **os.environ,
        "out": str(output),
        "infoPlist": str(_INFO_PLIST),
        "claudeExecutable": str(fake_cli),
    }
    subprocess.run(  # noqa: S603
        [str(_PACKAGE_DIR / "install.sh")],
        check=True,
        env=env,
    )
    subprocess.run(  # noqa: S603
        [str(_PACKAGE_DIR / "check.sh")],
        check=True,
        env=env,
    )

    app = output / "Applications/Claude Code URL Handler.app"
    executable = app / "Contents/MacOS/claude"
    packaged_plist = app / "Contents/Info.plist"
    assert executable.is_symlink()
    assert executable.readlink() == fake_cli
    assert packaged_plist.read_bytes() == _INFO_PLIST.read_bytes()
    assert _load_info_plist(packaged_plist) == _load_info_plist()
