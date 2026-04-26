"""Tests for updater-specific Nix expression builders."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import load_repo_module
from lib.update.flake import nixpkgs_expression
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT
from packages.scratch.updater import ScratchUpdater

if TYPE_CHECKING:
    from types import ModuleType


def _load_sentry_cli_updater_module() -> ModuleType:
    return load_repo_module("overlays/sentry-cli/updater.py", "_sentry_cli_updater")


def _load_t3code_workspace_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/t3code-workspace/updater.py", "_t3code_workspace_updater"
    )


def test_scratch_npm_expr_is_parseable() -> None:
    """Scratch NPM hash expression should be valid Nix."""
    expr = object.__getattribute__(ScratchUpdater, "_expr_for_npm_deps")()

    assert_nix_ast_equal(
        expr,
        LetExpression(
            local_variables=[
                Binding(
                    name="flake",
                    value=FunctionCall(
                        name=identifier_attr_path("builtins", "getFlake"),
                        argument=StringPrimitive(
                            value=f"git+file://{REPO_ROOT}?dirty=1"
                        ),
                    ),
                ),
                Binding(name="pkgs", value=nixpkgs_expression()),
            ],
            value=FunctionCall(
                name=identifier_attr_path("pkgs", "fetchNpmDeps"),
                argument=AttributeSet.from_dict(
                    {
                        "name": "scratch-npm-deps",
                        "src": identifier_attr_path("flake", "inputs", "scratch"),
                        "hash": identifier_attr_path("pkgs", "lib", "fakeHash"),
                    },
                ),
            ),
        ),
    )


def test_scratch_cargo_expr_is_parseable() -> None:
    """Scratch cargo hash expression should be valid Nix."""
    expr = object.__getattribute__(ScratchUpdater, "_expr_for_cargo_vendor")()

    assert_nix_ast_equal(
        expr,
        LetExpression(
            local_variables=[
                Binding(
                    name="flake",
                    value=FunctionCall(
                        name=identifier_attr_path("builtins", "getFlake"),
                        argument=StringPrimitive(
                            value=f"git+file://{REPO_ROOT}?dirty=1"
                        ),
                    ),
                ),
                Binding(name="pkgs", value=nixpkgs_expression()),
            ],
            value=FunctionCall(
                name=identifier_attr_path("pkgs", "rustPlatform", "fetchCargoVendor"),
                argument=AttributeSet.from_dict(
                    {
                        "src": BinaryExpression(
                            left=identifier_attr_path("flake", "inputs", "scratch"),
                            operator=Operator(name="+"),
                            right=StringPrimitive(value="/src-tauri"),
                        ),
                        "hash": identifier_attr_path("pkgs", "lib", "fakeHash"),
                    },
                ),
            ),
        ),
    )


def test_sentry_src_expr_is_parseable() -> None:
    """Sentry source hash expression should be valid Nix."""
    module = _load_sentry_cli_updater_module()
    updater = module.SentryCliUpdater()

    expr = object.__getattribute__(updater, "_src_nix_expr")("v9.9.9")

    assert_nix_ast_equal(expr, updater._src_nix_expression("v9.9.9"))


def test_sentry_cargo_expr_is_parseable() -> None:
    """Sentry cargo hash expression should be valid Nix."""
    module = _load_sentry_cli_updater_module()
    updater = module.SentryCliUpdater()

    expr = object.__getattribute__(updater, "_cargo_nix_expr")(
        "v9.9.9", "sha256-someHashValue="
    )

    assert_nix_ast_equal(
        expr,
        FunctionCall(
            name=identifier_attr_path("pkgs", "rustPlatform", "fetchCargoVendor"),
            argument=AttributeSet.from_dict(
                {
                    "src": updater._src_nix_expression(
                        "v9.9.9",
                        "sha256-someHashValue=",
                    ),
                    "hash": identifier_attr_path("pkgs", "lib", "fakeHash"),
                },
            ),
        ),
    )


def test_t3code_workspace_expr_is_parseable() -> None:
    """T3 Code workspace hash expression should be valid Nix."""
    module = _load_t3code_workspace_updater_module()
    updater = module.T3CodeWorkspaceUpdater

    expr = updater._workspace_expr()

    assert_nix_ast_equal(expr, updater._workspace_expression())
