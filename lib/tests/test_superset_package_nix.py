"""AST-level tests for Superset's Darwin packaging contract."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT


@cache
def _superset_package_text() -> str:
    """Return Superset's package expression source text."""
    return Path(REPO_ROOT / "packages/superset/default.nix").read_text(encoding="utf-8")


@cache
def _superset_darwin_derivation() -> FunctionCall:
    """Parse Superset's package expression and return the Darwin derivation."""
    root = expect_instance(
        parse_nix_expr(_superset_package_text()),
        FunctionDefinition,
    )
    platform_switch = expect_instance(root.output, IfExpression)
    assert_nix_ast_equal(platform_switch.condition, "stdenv.hostPlatform.isLinux")
    return expect_instance(platform_switch.alternative, FunctionCall)


def test_superset_fake_node_shim_routes_npx_through_bunx() -> None:
    """Superset's Bun shim should provide a real bunx entrypoint for npx calls."""
    source = _superset_package_text()

    assert "for node_binary in node npm bunx; do" in source
    assert 'cat > "$out/bin/npx" <<EOF' in source
    assert 'exec "$out/bin/bunx" "\\$@"' in source


def test_superset_darwin_build_phase_uses_explicit_unsigned_packaging_flags() -> None:
    """Superset should bypass electron-builder's ad-hoc signing fallback in Nix."""
    derivation_args = expect_instance(
        _superset_darwin_derivation().argument,
        AttributeSet,
    )

    post_patch = expect_instance(
        expect_binding(derivation_args.values, "postPatch").value,
        IndentedString,
    )
    assert "substituteInPlace package.json" in post_patch.value
    assert "electron-builder.ts" not in post_patch.value

    build_phase = expect_instance(
        expect_binding(derivation_args.values, "buildPhase").value,
        IndentedString,
    )
    assert "export CSC_IDENTITY_AUTO_DISCOVERY=false" in build_phase.value
    assert "bun run --cwd apps/desktop install:deps" in build_phase.value
    assert "bun x electron-builder" in build_phase.value
    assert "--config electron-builder.ts" in build_phase.value
    assert "--mac" in build_phase.value
    assert "--dir" in build_phase.value
    assert "--publish never" in build_phase.value
    assert "-c.mac.identity=null" in build_phase.value
    assert "-c.mac.hardenedRuntime=false" in build_phase.value
    assert "-c.mac.notarize=false" in build_phase.value
