"""Structural tests for the Reason (ara) version-pinned DMG fetch."""

import json

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_binding_expr, nix_file_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.update.paths import REPO_ROOT


def test_ara_sources_pin_current_reason_stable() -> None:
    """37385596458 hashed 0.1.71 then fetched different latest bytes."""
    sources = json.loads((REPO_ROOT / "packages/ara/sources.json").read_text())
    assert sources["version"] == "0.1.73"
    assert sources["urls"] == {
        "aarch64-darwin": "https://reasonmachines.com/api/desktop-download?arch=aarch64"
    }
    assert sources["hashes"] == {
        "aarch64-darwin": "sha256-d5qI5pQ+tFRByV4w0n0wMHmdAMAUeSYES29Dle1t5EY="
    }


def test_ara_fetch_dmg_fail_closes_on_latest_redirect_drift() -> None:
    """Unsigned Tigris keys 403; signed ones expire; check version before GET."""
    package = expect_instance(
        nix_file_expr("packages/ara/fetch-dmg.nix"), FunctionDefinition
    )
    derivation = expect_instance(package.output, FunctionCall)
    assert_nix_ast_equal(derivation.name, "stdenvNoCC.mkDerivation")
    args = expect_instance(derivation.argument, AttributeSet)
    assert_nix_ast_equal(expect_binding(args.values, "outputHashMode").value, '"flat"')
    assert_nix_ast_equal(expect_binding(args.values, "outputHash").value, "hash")
    assert_nix_ast_equal(expect_binding(args.values, "preferLocalBuild").value, "true")
    build = expect_instance(
        expect_binding(args.values, "buildCommand").value, IndentedString
    )
    shell = parse_shell(indented_string_body(build.rebuild()))
    curls = command_texts(shell, "curl")
    assert any("--dump-header headers" in command for command in curls)
    assert any(
        "-fsSL" in command and '"$location"' in command and '"$out"' in command
        for command in curls
    )
    body = indented_string_body(build.rebuild())
    assert "/desktop/stable/$version/" in body
    assert "x-ara-desktop-version:" in body


def test_ara_package_overrides_moving_url_src() -> None:
    """mkDmgApp7zz would fetch the latest-only URL without a version guard."""
    src = nix_file_binding_expr("packages/ara/default.nix", "src")
    assert_nix_ast_equal(
        src,
        """callPackage ./fetch-dmg.nix {
          inherit (selfSource) version;
          url = selfSource.urls.${stdenv.hostPlatform.system};
          hash = selfSource.hashes.${stdenv.hostPlatform.system};
        }""",
    )
