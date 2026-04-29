"""AST-level guardrails for the GitHub Desktop overlay."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._shell_ast import command_texts, parse_shell
from lib.update.paths import REPO_ROOT


@cache
def _overlay_output() -> AttributeSet:
    """Return the attribute set emitted by ``overlays/github-desktop``."""
    root = expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "overlays/github-desktop/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, AttributeSet)


@cache
def _override_attrs() -> AttributeSet:
    """Return the attrset passed to ``prev.github-desktop.overrideAttrs``."""
    override = expect_instance(
        expect_binding(_overlay_output().values, "github-desktop").value,
        FunctionCall,
    )
    assert_nix_ast_equal(override.name, "prev.github-desktop.overrideAttrs")

    outer = expect_instance(
        expect_instance(override.argument, Parenthesis).value, FunctionDefinition
    )
    inner = expect_instance(outer.output, FunctionDefinition)
    return expect_instance(inner.output, AttributeSet)


def _replace_strings_parts(
    attr_name: str,
) -> tuple[NixList, NixList, object]:
    """Return search list, replacement list, and final argument."""
    expression = expect_instance(
        expect_binding(_override_attrs().values, attr_name).value,
        FunctionCall,
    )
    replacement_call = expect_instance(expression.name, FunctionCall)
    search_call = expect_instance(replacement_call.name, FunctionCall)

    assert_nix_ast_equal(search_call.name, "prev.lib.replaceStrings")

    return (
        expect_instance(search_call.argument, NixList),
        expect_instance(replacement_call.argument, NixList),
        expression.argument,
    )


def _string_items(items: NixList) -> list[str]:
    """Return plain and indented string values from a Nix list."""
    values: list[str] = []
    for item in items.value:
        if isinstance(item, StringPrimitive | IndentedString):
            values.append(item.value)
            continue
        msg = f"expected string list item, got {type(item).__name__}"
        raise AssertionError(msg)
    return values


def test_post_configure_patches_bundled_node_addon_api_before_native_rebuilds() -> None:
    """Native modules should compile with the current Darwin toolchain."""
    searches, replacements, final_argument = _replace_strings_parts("postConfigure")

    search_values = _string_items(searches)
    assert (
        search_values[0]
        == "yarn --cwd app/node_modules/desktop-notifications run install"
    )
    assert command_texts(parse_shell(search_values[1])) == [
        "touch electron",
        "zip -0Xqr __NIX_INTERP__ electron",
        "rm electron",
    ]
    assert_nix_ast_equal(final_argument, "oldAttrs.postConfigure")

    commands = command_texts(
        parse_shell(expect_instance(replacements.value[0], IndentedString).value)
    )
    assert (
        "find app/node_modules node_modules -path '*/node-addon-api/napi.h' -type f"
        in commands
    )
    assert any(
        command.startswith('substituteInPlace "$node_addon_api_header"')
        for command in commands
    )
    assert "yarn --cwd app/node_modules/desktop-notifications run install" in commands

    electron_zip_branch = expect_instance(
        expect_instance(replacements.value[1], Parenthesis).value,
        IfExpression,
    )
    assert_nix_ast_equal(
        electron_zip_branch.condition,
        "prev.stdenv.hostPlatform.isDarwin",
    )
    assert command_texts(
        parse_shell(
            expect_instance(electron_zip_branch.consequence, IndentedString).value
        )
    ) == [
        "cp -R __NIX_INTERP__/Applications/Electron.app Electron.app",
        "chmod -R u+w Electron.app",
        "zip -0Xqr __NIX_INTERP__ Electron.app",
        "rm -rf Electron.app",
    ]
    assert command_texts(
        parse_shell(
            expect_instance(electron_zip_branch.alternative, IndentedString).value
        )
    ) == [
        "touch electron",
        "zip -0Xqr __NIX_INTERP__ electron",
        "rm electron",
    ]


def test_install_phase_handles_darwin_packaged_resources_and_real_icon() -> None:
    """Darwin packaging should not leave desktopToDarwinBundle a broken icon."""
    searches, replacements, final_argument = _replace_strings_parts("installPhase")

    assert _string_items(searches) == [
        "cp -r dist/*/resources $out/share/github-desktop",
        "ln -s $out/share/github-desktop/resources/app/static/icon-logo.png $out/share/icons/hicolor/512x512/apps/github-desktop.png",
    ]
    assert_nix_ast_equal(final_argument, "oldAttrs.installPhase")

    commands = command_texts(
        parse_shell(expect_instance(replacements.value[0], IndentedString).value)
    )
    assert 'cp -r "__NIX_INTERP__" "$out/share/github-desktop/resources"' in commands
    assert 'cp -r dist/*/resources "$out/share/github-desktop"' in commands

    assert expect_instance(replacements.value[1], StringPrimitive).value == (
        "install -Dm444 app/static/linux/icon-logo.png "
        "$out/share/icons/hicolor/512x512/apps/github-desktop.png"
    )
