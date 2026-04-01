"""Regression checks for OpenCode profile materialization."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
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
    assert_nix_ast_equal(config_call.name, "mkIf cfg.enable")
    return expect_instance(config_call.argument, AttributeSet)


def _eval_opencode_json(profile_config: str) -> dict[str, Any]:
    """Evaluate the module and return the parsed ``opencode/active.json`` payload."""
    root = Path(REPO_ROOT).resolve()
    nix = shutil.which("nix")
    assert nix is not None
    expr = textwrap.dedent(
        f"""
        let
          nixpkgs = builtins.getFlake "nixpkgs";
          lib = nixpkgs.lib;
          result = lib.evalModules {{
            modules = [
              (import {root}/modules/home/opencode.nix)
              {{
                options = {{
                  assertions = lib.mkOption {{
                    type = lib.types.listOf lib.types.anything;
                    default = [ ];
                  }};
                  theme.slug = lib.mkOption {{
                    type = lib.types.str;
                    default = "catppuccin-frappe";
                  }};
                  programs.opencode = lib.mkOption {{
                    type = lib.types.attrsOf lib.types.anything;
                    default = {{ }};
                  }};
                  xdg.configFile = lib.mkOption {{
                    type = lib.types.attrsOf lib.types.anything;
                    default = {{ }};
                  }};
                }};
                config.nixcfg.opencode = {profile_config};
              }}
            ];
          }};
        in builtins.fromJSON result.config.xdg.configFile."opencode/active.json".text
        """
    )
    result = subprocess.run(  # noqa: S603
        [nix, "eval", "--impure", "--json", "--expr", expr],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


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
        """
        baseOpencodeSettings // {
          mcp = renderMcpServers cfg.mcpServers;
        }
        """,
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
    assert_nix_ast_equal(profile_map_builder.name, "mapAttrs'")

    profile_name_fn = expect_instance(
        expect_instance(profile_map_builder.argument, Parenthesis).value,
        FunctionDefinition,
    )
    assert_nix_ast_equal(profile_name_fn.argument_set, "profileName")

    profile_fn = expect_instance(profile_name_fn.output, FunctionDefinition)
    assert_nix_ast_equal(profile_fn.argument_set, "profile")
    assert_nix_ast_equal(
        profile_fn.output,
        """
        nameValuePair "opencode/${profileName}.json" {
          text = builtins.toJSON (mkProfileConfig profile);
        }
        """,
    )

    assert_nix_ast_equal(profile_map.argument, "cfg.profiles")
    assert_nix_ast_equal(
        config_file.right,
        """
        {
          "opencode/active.json".text = builtins.toJSON (mkProfileConfig activeProfileConfig);
        }
        """,
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_active_profile_json_includes_shared_settings_and_profile_overrides() -> None:
    """The materialized active profile should merge shared config with profile overrides."""
    active = _eval_opencode_json(
        """
        {
          activeProfile = "personal";
          profiles.personal = {
            settings.theme = "override-theme";
            mcpServers.macos-automator.enable = true;
          };
        }
        """
    )

    assert active["$schema"] == "https://opencode.ai/config.json"
    assert active["theme"] == "override-theme"
    assert active["plugin"] == [
        "@franlol/opencode-md-table-formatter",
    ]
    assert active["tui"]["scroll_acceleration"]["enabled"] is True
    assert active["mcp"]["chrome-devtools"]["command"] == [
        "npx",
        "-y",
        "chrome-devtools-mcp@latest",
        "--autoConnect",
        "--channel=stable",
    ]
    assert active["mcp"]["macos-automator"]["command"] == [
        "bunx",
        "--bun",
        "@steipete/macos-automator-mcp@latest",
    ]
    assert active["mcp"]["macos-automator"]["enabled"] is True
    assert active["mcp"]["next-devtools"]["enabled"] is False
