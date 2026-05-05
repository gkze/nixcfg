"""Regression checks for Zen Home Manager wiring."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
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


@cache
def _zen_module_output() -> AttributeSet:
    """Return the parsed ``modules/home/zen.nix`` output attrset."""
    return _module_output("modules/home/zen.nix")


@cache
def _zen_config_attrset() -> AttributeSet:
    """Return the ``config = mkIf ...`` attrset for the Zen module."""
    config_call = expect_instance(
        expect_binding(_zen_module_output().values, "config").value,
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


@cache
def _zen_options_attrset() -> AttributeSet:
    """Return the ``options.nixcfg.zen`` attrset for the Zen module."""
    options = expect_instance(
        expect_binding(_zen_module_output().values, "options").value,
        AttributeSet,
    )
    nixcfg = expect_instance(
        expect_binding(options.values, "nixcfg").value,
        AttributeSet,
    )
    return expect_instance(expect_binding(nixcfg.values, "zen").value, AttributeSet)


def test_zen_module_installs_only_zentool() -> None:
    """Zen should install only the packaged zentool entrypoint."""
    home = expect_instance(
        expect_binding(_zen_config_attrset().values, "home").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(home.values, "packages").value,
        NixList(value=[Identifier(name="zenTool")]),
    )


def test_zen_module_defaults_tool_command_to_packaged_zentool() -> None:
    """The default tool command should stay aligned with the packaged wrapper."""
    options = _zen_options_attrset()

    option = expect_instance(
        expect_binding(options.values, "toolCommand").value,
        FunctionCall,
    )
    argument = expect_instance(option.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(argument.values, "default").value,
        FunctionCall(
            name=identifier_attr_path("lib", "getExe"),
            argument=Identifier(name="zenTool"),
        ),
    )


def test_zen_module_python_runtime_includes_schema_sync_dependencies() -> None:
    """The packaged zentool runtime should include its import-time deps."""
    zen_python = expect_scope_binding(_zen_module_output(), "zenPython")
    with_packages = expect_instance(zen_python.value, FunctionCall)
    argument = expect_instance(with_packages.argument, Parenthesis)
    package_lambda = expect_instance(argument.value, FunctionDefinition)

    assert_nix_ast_equal(
        with_packages.name,
        identifier_attr_path("pkgs", "python3", "withPackages"),
    )
    assert_nix_ast_equal(
        package_lambda.output,
        """
with ps; [
  click
  deepdiff
  lz4
  pydantic
  pyyaml
  typer
]
""",
    )


def test_george_local_bin_exports_expected_helpers() -> None:
    """George's local bin exports should stay limited to explicit helper links."""
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
        '".local/bin/but"',
        '".local/bin/git-ignore"',
    }


def test_zen_module_activation_assembles_scope_specific_zentool_args() -> None:
    """Activation should append only requested scope arguments and skip empty applies."""
    home = expect_instance(
        expect_binding(_zen_config_attrset().values, "home").value,
        AttributeSet,
    )
    activation_call = expect_instance(
        expect_binding(home.values, "activation").value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        activation_call.name,
        FunctionCall(
            name=Identifier(name="mkIf"),
            argument=identifier_attr_path("cfg", "syncOnActivation"),
        ),
    )
    activation = expect_instance(activation_call.argument, AttributeSet)

    expected = "\n".join([
        "lib.hm.dag.entryAfter [ \"linkGeneration\" ] ''",
        "        sync_cmd=(${lib.escapeShellArg cfg.toolCommand})",
        "        sync_args=(apply --yes)",
        "        profile_args=()",
        "        state_args=()",
        "        asset_args=()",
        (
            "        state_config="
            '${lib.escapeShellArg (managedConfigDir + "/folders.yaml")}'
        ),
        "",
        "        ${lib.optionalString (cfg.profile != null) ''",
        "          profile_args+=(--profile ${lib.escapeShellArg cfg.profile})",
        "        ''}",
        "",
        "        ${lib.optionalString cfg.applyStateOnActivation ''",
        '          if [ -e "$state_config" ]; then',
        "            state_args+=(--state)",
        '            state_args+=(--config "$state_config")',
        "          fi",
        "        ''}",
        "",
        "        ${lib.optionalString cfg.applyAssetsOnActivation ''",
        "          asset_args+=(--assets)",
        "          asset_args+=(--asset-dir ${lib.escapeShellArg managedConfigDir})",
        "        ''}",
        "",
        "        if [ \"''${#state_args[@]}\" -gt 0 ]; then",
        "          runtime_check_cmd=(\"''${sync_cmd[@]}\" profile)",
        "          if [ \"''${#profile_args[@]}\" -gt 0 ]; then",
        "            runtime_check_cmd+=(\"''${profile_args[@]}\")",
        "          fi",
        "          runtime_check_cmd+=(is-running)",
        "",
        "          if \"''${runtime_check_cmd[@]}\" >/dev/null 2>&1; then",
        '            echo "warning: skipping Zen state sync during activation because Zen is running" >&2',
        "            state_args=()",
        "          fi",
        "        fi",
        "",
        "        if [ \"''${#state_args[@]}\" -gt 0 ]; then",
        "          sync_args+=(\"''${state_args[@]}\")",
        "        fi",
        "",
        "        if [ \"''${#asset_args[@]}\" -gt 0 ]; then",
        "          sync_args+=(\"''${asset_args[@]}\")",
        "        fi",
        "",
        "        if [ \"''${#profile_args[@]}\" -gt 0 ]; then",
        "          sync_args+=(\"''${profile_args[@]}\")",
        "        fi",
        "",
        "        if [ \"''${#state_args[@]}\" -gt 0 ] || [ \"''${#asset_args[@]}\" -gt 0 ]; then",
        "          run --silence \"''${sync_cmd[@]}\" \"''${sync_args[@]}\"",
        "        fi",
        "      ''",
    ])

    assert str(expect_binding(activation.values, "nixcfgZenSync").value) == expected
