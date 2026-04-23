"""AST-level guardrails for OpenCode overlay packaging."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT


@cache
def _overlay_args() -> AttributeSet:
    """Return the attribute set emitted by overlays/opencode/default.nix."""
    root = expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "overlays/opencode/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, AttributeSet)


@cache
def _opencode_override_args() -> AttributeSet:
    """Return the attrset passed to opencode.overrideAttrs."""
    override = expect_instance(
        expect_binding(_overlay_args().values, "opencode").value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        override.name,
        "inputs.opencode.packages.${system}.opencode.overrideAttrs",
    )
    inner = expect_instance(
        expect_instance(override.argument, Parenthesis).value,
        FunctionDefinition,
    )
    return expect_instance(inner.output, AttributeSet)


@cache
def _node_modules_override_args() -> AttributeSet:
    """Return the attrset passed to old.node_modules.overrideAttrs."""
    override = expect_instance(
        expect_binding(_opencode_override_args().values, "node_modules").value,
        FunctionCall,
    )
    assert_nix_ast_equal(override.name, "old.node_modules.overrideAttrs")
    inner = expect_instance(
        expect_instance(override.argument, Parenthesis).value,
        FunctionDefinition,
    )
    return expect_instance(inner.output, AttributeSet)


def test_opencode_overlay_overrides_the_flake_input_package() -> None:
    """The overlay should keep wrapping the flake input's opencode package."""
    assert_nix_ast_equal(
        expect_instance(
            expect_binding(_overlay_args().values, "opencode").value,
            FunctionCall,
        ).name,
        "inputs.opencode.packages.${system}.opencode.overrideAttrs",
    )


def test_opencode_overlay_keeps_platform_specific_node_modules_hash_lookup() -> None:
    """The overlay should keep node_modules hashing delegated to sourceHashForPlatform."""
    assert_nix_ast_equal(
        expect_binding(_node_modules_override_args().values, "outputHash").value,
        'slib.sourceHashForPlatform "opencode" "nodeModulesHash" system',
    )


def test_opencode_overlay_keeps_build_phase_as_ast_transform() -> None:
    """The node_modules buildPhase should remain a Nix-level string rewrite."""
    build_phase = expect_binding(
        _node_modules_override_args().values, "buildPhase"
    ).value
    final_call = expect_instance(build_phase, FunctionCall)
    replacement_call = expect_instance(final_call.name, FunctionCall)
    search_call = expect_instance(replacement_call.name, FunctionCall)

    assert_nix_ast_equal(search_call.name, "builtins.replaceStrings")

    search_values = [
        expect_instance(item, StringPrimitive).value
        for item in expect_instance(search_call.argument, NixList).value
    ]
    replacement_values = [
        expect_instance(item, StringPrimitive).value
        for item in expect_instance(replacement_call.argument, NixList).value
    ]

    assert len(search_values) == 3
    assert len(replacement_values) == 3
    assert any("packages/shared" in value for value in search_values)
    assert any("packages/script" in value for value in replacement_values)
    assert_nix_ast_equal(final_call.argument, '(nodeOld.buildPhase or "")')
