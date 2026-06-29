"""AST-level checks for Goose CLI packaging shims."""

from __future__ import annotations

from functools import cache

from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import expect_binding, expect_scope_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT


@cache
def _goose_overlay_output() -> AttributeSet:
    overlay = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "overlays/goose-cli/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(overlay.output, AttributeSet)


def test_llama_cpp_sys_suppresses_darwin_target_wrapper_warning() -> None:
    """Keep parallel llama.cpp compiles from flooding and blocking stderr pipes."""
    override = expect_instance(
        expect_scope_binding(_goose_overlay_output(), "llamaCppSysOverride").value,
        FunctionDefinition,
    )
    override_attrs = expect_instance(override.output, AttributeSet)

    suppress_warning = expect_instance(
        expect_binding(
            override_attrs.values,
            "NIX_CC_WRAPPER_SUPPRESS_TARGET_WARNING",
        ).value,
        StringPrimitive,
    )
    assert suppress_warning.value == "1"
