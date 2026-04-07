"""Regression checks for Zen Home Manager wiring."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT


@cache
def _module_output(relative_path: str) -> AttributeSet:
    """Parse one Nix module and return its top-level output attrset."""
    expr = expect_instance(
        parse_nix_expr(Path(REPO_ROOT / relative_path).read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    return expect_instance(expr.output, AttributeSet)


def test_zen_module_installs_both_wrappers() -> None:
    """Zen should install both packaged wrappers so shell and activation agree."""
    root = _module_output("modules/home/zen.nix")
    config_call = expect_instance(
        expect_binding(root.values, "config").value, FunctionCall
    )
    assert_nix_ast_equal(
        config_call.name,
        FunctionCall(
            name=Identifier(name="mkIf"),
            argument=identifier_attr_path("cfg", "enable"),
        ),
    )
    config = expect_instance(config_call.argument, AttributeSet)
    home = expect_instance(expect_binding(config.values, "home").value, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(home.values, "packages").value,
        NixList(
            value=[
                Identifier(name="zenProfileSync"),
                Identifier(name="zenFolders"),
            ]
        ),
    )


def test_george_local_bin_exports_leave_zen_commands_to_packaged_wrappers() -> None:
    """Raw repo scripts should not shadow the packaged Zen wrapper commands."""
    root = _module_output("home/george/configuration.nix")
    home = expect_instance(expect_binding(root.values, "home").value, AttributeSet)
    file_attrset = expect_instance(
        expect_binding(home.values, "file").value, AttributeSet
    )

    local_bin_entries = {
        binding.name
        for binding in file_attrset.values
        if binding.name.startswith('".local/bin')
    }

    assert local_bin_entries == {
        '".local/bin/code-insiders"',
        '".local/bin/cursor"',
        '".local/bin/git-ignore"',
    }
