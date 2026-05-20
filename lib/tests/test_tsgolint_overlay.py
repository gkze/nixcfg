"""AST-level guardrails for the tsgolint overlay."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT


@cache
def _overlay() -> AttributeSet:
    """Return the attrset emitted by overlays/tsgolint/default.nix."""
    root = expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "overlays/tsgolint/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, AttributeSet)


def _let_bindings() -> list[object]:
    """Return top-level let bindings attached to the overlay attrset."""
    return list(_overlay().scope)


@cache
def _override_args() -> AttributeSet:
    """Return the attrset passed to prev.tsgolint.overrideAttrs."""
    override = expect_instance(
        expect_binding(_let_bindings(), "tsgolint").value,
        FunctionCall,
    )
    assert_nix_ast_equal(override.name, "prev.tsgolint.overrideAttrs")
    inner = expect_instance(
        expect_instance(override.argument, Parenthesis).value,
        FunctionDefinition,
    )
    return expect_instance(inner.output, AttributeSet)


def test_overlay_overrides_upstream_tsgolint_package() -> None:
    """The local package should wrap nixpkgs tsgolint instead of rebuilding it."""
    assert_nix_ast_equal(
        expect_instance(
            expect_binding(_let_bindings(), "tsgolint").value,
            FunctionCall,
        ).name,
        "prev.tsgolint.overrideAttrs",
    )


def test_overlay_exports_upstream_tsgolint_name() -> None:
    """The overlay should expose the upstream package name only."""
    inherit_entries = [
        entry for entry in _overlay().values if isinstance(entry, Inherit)
    ]

    assert any(
        entry.names == [Identifier(name="tsgolint")] for entry in inherit_entries
    )
    assert len(_overlay().values) == 1


def test_overlay_fetches_release_without_recursive_submodules() -> None:
    """The override should avoid the upstream recursive typescript-go checkout."""
    src = expect_instance(
        expect_binding(_let_bindings(), "src").value,
        FunctionCall,
    )
    assert_nix_ast_equal(src.name, "prev.fetchFromGitHub")
    args = expect_instance(src.argument, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(args.values, "fetchSubmodules").value,
        Primitive(value=False),
    )


def test_overlay_replaces_upstream_submodule_patch_phase() -> None:
    """Without the submodule checkout, the upstream pushd/patch list cannot run."""
    assert_nix_ast_equal(
        expect_binding(_override_args().values, "patches").value,
        NixList(value=[]),
    )
    assert_nix_ast_equal(
        expect_binding(_override_args().values, "prePatch").value,
        StringPrimitive(value=""),
    )
