"""AST-level tests for Superset's packaging contracts."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.update.paths import REPO_ROOT


@cache
def _platform_switch() -> IfExpression:
    """Parse the package and return its top-level platform branch."""
    root = expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "packages/superset/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, IfExpression)


@cache
def _darwin_derivation_args() -> AttributeSet:
    """Return the Darwin derivation arguments."""
    return expect_instance(
        expect_instance(_platform_switch().alternative, FunctionCall).argument,
        AttributeSet,
    )


def test_superset_fake_node_shim_routes_npx_through_bunx() -> None:
    """Superset's Bun shim should provide the node/npm/bunx/npx aliases it expects."""
    bun_with_fake_node = expect_instance(
        expect_scope_binding(_platform_switch(), "bunWithFakeNode").value,
        FunctionCall,
    )
    bun_with_fake_node_args = expect_instance(bun_with_fake_node.argument, AttributeSet)

    assert_nix_ast_equal(bun_with_fake_node.name, "stdenvNoCC.mkDerivation")
    install_phase = expect_instance(
        expect_binding(bun_with_fake_node_args.values, "installPhase").value,
        IndentedString,
    )

    assert "for node_binary in node npm bunx; do" in install_phase.value
    assert 'cat > "$out/bin/npx" <<EOF' in install_phase.value
    assert 'exec "$out/bin/bunx" "\\$@"' in install_phase.value


def test_superset_darwin_build_phase_uses_explicit_unsigned_packaging_flags() -> None:
    """Superset should stay offline and bypass signing/notarization fallback in Nix."""
    post_patch = expect_instance(
        expect_binding(_darwin_derivation_args().values, "postPatch").value,
        IndentedString,
    )
    build_phase = expect_instance(
        expect_binding(_darwin_derivation_args().values, "buildPhase").value,
        IndentedString,
    )

    assert "substituteInPlace package.json" in post_patch.value
    assert '"postinstall": "./scripts/postinstall.sh"' in post_patch.value
    assert '"postinstall": ""' in post_patch.value

    for snippet in (
        "export CSC_IDENTITY_AUTO_DISCOVERY=false",
        "export ELECTRON_SKIP_BINARY_DOWNLOAD=1",
        'electron_gyp_dir="$HOME/.electron-gyp/${electronVersion}"',
        'tar -xzf ${electronHeadersTarball} --strip-components=1 -C "$electron_gyp_dir"',
        'export npm_config_nodedir="$electron_gyp_dir"',
        "bun run --cwd apps/desktop install:deps",
        "bun x electron-builder",
        "--config electron-builder.ts",
        "--mac",
        "--dir",
        "--publish never",
        "-c.mac.identity=null",
        "-c.mac.hardenedRuntime=false",
        "-c.mac.notarize=false",
    ):
        assert snippet in build_phase.value
