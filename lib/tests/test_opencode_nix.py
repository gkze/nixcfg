"""Regression checks for OpenCode profile materialization."""

from __future__ import annotations

import shutil
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._nix_eval import (
    nix_attrset,
    nix_eval_json,
    nix_import,
    nix_let,
    nix_list,
)
from lib.update.flake import nixpkgs_expression
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT


@cache
def _opencode_module_output() -> AttributeSet:
    """Parse the OpenCode home-manager module once."""
    expr = expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "modules/home/opencode.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(expr.output, AttributeSet)


@cache
def _opencode_config_attrset() -> AttributeSet:
    """Return the ``config = mkIf ...`` attribute set for the OpenCode module."""
    config_call = expect_instance(
        expect_binding(_opencode_module_output().values, "config").value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        config_call.name,
        FunctionCall(
            name=Identifier(name="mkIf"),
            argument=identifier_attr_path("cfg", "enable"),
        ),
    )
    return expect_instance(config_call.argument, AttributeSet)


def _mk_option(
    type_expr: FunctionCall | AttributeSet | Select, default: object
) -> FunctionCall:
    """Build one ``lib.mkOption`` call for the test eval harness."""
    return FunctionCall(
        name=identifier_attr_path("lib", "mkOption"),
        argument=nix_attrset({"type": type_expr, "default": default}),
    )


def _eval_opencode_json(profile_config: AttributeSet) -> dict[str, Any]:
    """Evaluate the module and return the parsed ``opencode/active.json`` payload."""
    result_config_file = identifier_attr_path("result", "config", "xdg", "configFile")
    expression = nix_let(
        {
            "pkgs": nixpkgs_expression(),
            "lib": identifier_attr_path("pkgs", "lib"),
            "result": FunctionCall(
                name=identifier_attr_path("lib", "evalModules"),
                argument=nix_attrset({
                    "modules": nix_list([
                        Parenthesis(
                            value=nix_import(REPO_ROOT / "modules/home/opencode.nix")
                        ),
                        nix_attrset({
                            "options": nix_attrset({
                                "assertions": _mk_option(
                                    FunctionCall(
                                        name=identifier_attr_path(
                                            "lib",
                                            "types",
                                            "listOf",
                                        ),
                                        argument=identifier_attr_path(
                                            "lib",
                                            "types",
                                            "anything",
                                        ),
                                    ),
                                    [],
                                ),
                                "theme.slug": _mk_option(
                                    identifier_attr_path(
                                        "lib",
                                        "types",
                                        "str",
                                    ),
                                    "catppuccin-frappe",
                                ),
                                "programs.opencode": _mk_option(
                                    FunctionCall(
                                        name=identifier_attr_path(
                                            "lib",
                                            "types",
                                            "attrsOf",
                                        ),
                                        argument=identifier_attr_path(
                                            "lib",
                                            "types",
                                            "anything",
                                        ),
                                    ),
                                    {},
                                ),
                                "xdg.configFile": _mk_option(
                                    FunctionCall(
                                        name=identifier_attr_path(
                                            "lib",
                                            "types",
                                            "attrsOf",
                                        ),
                                        argument=identifier_attr_path(
                                            "lib",
                                            "types",
                                            "anything",
                                        ),
                                    ),
                                    {},
                                ),
                            }),
                            "config.nixcfg.opencode": profile_config,
                        }),
                    ])
                }),
            ),
        },
        FunctionCall(
            name=identifier_attr_path("builtins", "fromJSON"),
            argument=Select(
                expression=Select(
                    expression=result_config_file,
                    attribute='"opencode/active.json"',
                ),
                attribute="text",
            ),
        ),
    )
    payload = nix_eval_json(expression)
    assert isinstance(payload, dict)
    return payload


def test_program_settings_keep_shared_base_config_in_the_module_ast() -> None:
    """The base ``programs.opencode.settings`` binding should stay shared."""
    config = _opencode_config_attrset()
    programs = expect_instance(
        expect_binding(config.values, "programs").value,
        AttributeSet,
    )
    opencode = expect_instance(
        expect_binding(programs.values, "opencode").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(opencode.values, "settings").value,
        BinaryExpression(
            left=Identifier(name="baseOpencodeSettings"),
            operator=Operator(name="//"),
            right=nix_attrset({
                "mcp": FunctionCall(
                    name=Identifier(name="renderMcpServers"),
                    argument=identifier_attr_path("cfg", "mcpServers"),
                )
            }),
        ),
    )


def test_active_profile_binding_uses_the_merged_profile_config_ast() -> None:
    """The generated ``active.json`` binding should route through ``activeProfileConfig``."""
    config = _opencode_config_attrset()
    xdg = expect_instance(
        expect_binding(config.values, "xdg").value,
        AttributeSet,
    )
    config_file = expect_instance(
        expect_binding(xdg.values, "configFile").value,
        BinaryExpression,
    )
    profile_map = expect_instance(config_file.left, FunctionCall)
    profile_map_builder = expect_instance(profile_map.name, FunctionCall)
    assert_nix_ast_equal(profile_map_builder.name, Identifier(name="mapAttrs'"))

    profile_name_fn = expect_instance(
        expect_instance(profile_map_builder.argument, Parenthesis).value,
        FunctionDefinition,
    )
    assert_nix_ast_equal(profile_name_fn.argument_set, Identifier(name="profileName"))

    profile_fn = expect_instance(profile_name_fn.output, FunctionDefinition)
    assert_nix_ast_equal(profile_fn.argument_set, Identifier(name="profile"))
    profile_output = expect_instance(profile_fn.output, FunctionCall)
    name_value_pair = expect_instance(profile_output.name, FunctionCall)
    assert_nix_ast_equal(name_value_pair.name, Identifier(name="nameValuePair"))
    profile_name = expect_instance(name_value_pair.argument, StringPrimitive)
    assert profile_name.value == "opencode/${profileName}.json"

    profile_json = expect_instance(profile_output.argument, AttributeSet)
    text_call = expect_instance(
        expect_binding(profile_json.values, "text").value, FunctionCall
    )
    assert_nix_ast_equal(text_call.name, identifier_attr_path("builtins", "toJSON"))
    assert_nix_ast_equal(
        text_call.argument,
        FunctionCall(
            name=Identifier(name="mkProfileConfig"),
            argument=Identifier(name="profile"),
        ),
    )

    assert_nix_ast_equal(profile_map.argument, identifier_attr_path("cfg", "profiles"))
    active_json = expect_instance(config_file.right, AttributeSet)
    active_binding = expect_binding(active_json.values, '"opencode/active.json"')
    active_payload = expect_instance(active_binding.value, AttributeSet)
    active_text = expect_instance(
        expect_binding(active_payload.values, "text").value, FunctionCall
    )
    assert_nix_ast_equal(active_text.name, identifier_attr_path("builtins", "toJSON"))
    assert_nix_ast_equal(
        active_text.argument,
        FunctionCall(
            name=Identifier(name="mkProfileConfig"),
            argument=Identifier(name="activeProfileConfig"),
        ),
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_active_profile_json_keeps_default_theme_under_tui() -> None:
    """The materialized active profile should keep the shared theme under ``tui``."""
    active = _eval_opencode_json(
        nix_attrset({
            "activeProfile": "personal",
            "profiles.personal": {},
        })
    )

    assert "theme" not in active
    assert active["tui"]["theme"] == "catppuccin-frappe"
    assert active["tui"]["scroll_acceleration"]["enabled"] is True


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_active_profile_json_includes_shared_settings_and_profile_overrides() -> None:
    """The materialized active profile should merge shared config with profile overrides."""
    active = _eval_opencode_json(
        nix_attrset({
            "activeProfile": "personal",
            "profiles.personal": {
                "settings.tui.theme": "override-theme",
                "mcpServers.macos-automator.enable": True,
            },
        })
    )

    assert active["$schema"] == "https://opencode.ai/config.json"
    assert "theme" not in active
    assert active["tui"]["theme"] == "override-theme"
    assert "plugin" not in active
    assert active["tui"]["scroll_acceleration"]["enabled"] is True
    assert active["mcp"]["chrome-devtools"]["command"] == [
        "npx",
        "-y",
        "chrome-devtools-mcp@latest",
        "--autoConnect",
        "--channel=stable",
    ]
    assert active["mcp"]["chrome-devtools"]["enabled"] is False
    assert active["mcp"]["macos-automator"]["command"] == [
        "bunx",
        "--bun",
        "@steipete/macos-automator-mcp@latest",
    ]
    assert active["mcp"]["macos-automator"]["enabled"] is True
    assert active["mcp"]["next-devtools"]["enabled"] is False


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_profile_mcp_command_override_preserves_disabled_shared_server_default() -> (
    None
):
    """Profile command overrides should not implicitly enable shared disabled servers."""
    active = _eval_opencode_json(
        nix_attrset({
            "activeProfile": "work",
            "profiles.work": {
                "mcpServers.chrome-devtools.command": [
                    "npx",
                    "-y",
                    "chrome-devtools-mcp@latest",
                    "--autoConnect",
                    "--channel=stable",
                ],
            },
        })
    )

    assert active["mcp"]["chrome-devtools"]["command"] == [
        "npx",
        "-y",
        "chrome-devtools-mcp@latest",
        "--autoConnect",
        "--channel=stable",
    ]
    assert active["mcp"]["chrome-devtools"]["enabled"] is False
