"""AST-level guardrails for George's nixvim formatter wiring."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.update.paths import REPO_ROOT


@cache
def _conform_settings() -> AttributeSet:
    """Return the ``conform-nvim.settings`` attrset from George's nixvim module."""
    root = expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "home/george/nixvim.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    output = expect_instance(root.output, AttributeSet)
    programs = expect_instance(
        expect_binding(output.values, "programs").value, AttributeSet
    )
    nixvim = expect_instance(
        expect_binding(programs.values, "nixvim").value, AttributeSet
    )
    config = expect_instance(
        expect_binding(nixvim.values, "config").value, AttributeSet
    )
    plugins = expect_instance(
        expect_binding(config.values, "plugins").value, AttributeSet
    )
    conform = expect_instance(
        expect_binding(plugins.values, "conform-nvim").value, AttributeSet
    )
    return expect_instance(
        expect_binding(conform.values, "settings").value, AttributeSet
    )


def test_web_formatters_use_oxc_tooling_with_tsgolint_backend() -> None:
    """Web buffers should use Oxfmt/Oxlint plus the tsgolint bridge."""
    formatters_by_ft = expect_instance(
        expect_binding(_conform_settings().values, "formatters_by_ft").value,
        AttributeSet,
    )
    formatters = expect_instance(
        expect_binding(_conform_settings().values, "formatters").value,
        AttributeSet,
    )
    oxlint = expect_instance(
        expect_binding(formatters.values, "oxlint").value, AttributeSet
    )
    oxlint_env = expect_instance(
        expect_binding(oxlint.values, "env").value, AttributeSet
    )
    formatter_bindings = binding_map(formatters.values)

    assert "biome" not in formatter_bindings

    for filetype in ("css", "html", "json", "jsonc"):
        assert_nix_ast_equal(
            expect_binding(formatters_by_ft.values, filetype).value,
            '[ "oxfmt" ]',
        )

    assert_nix_ast_equal(
        expect_binding(formatters_by_ft.values, "javascript").value,
        '[ "oxlint" "oxfmt" ]',
    )
    assert_nix_ast_equal(
        expect_binding(formatters_by_ft.values, "javascriptreact").value,
        '[ "oxlint" "oxfmt" ]',
    )

    assert_nix_ast_equal(
        expect_binding(formatters_by_ft.values, "typescript").value,
        '[ "oxlint" "oxfmt" ]',
    )
    assert_nix_ast_equal(
        expect_binding(formatters_by_ft.values, "typescriptreact").value,
        '[ "oxlint" "oxfmt" ]',
    )
    assert_nix_ast_equal(
        expect_binding(oxlint_env.values, "OXLINT_TSGOLINT_PATH").value,
        "oxlintTsgolintCmd",
    )


def test_oxfmt_falls_back_to_repo_defaults_when_project_config_is_missing() -> None:
    """Oxfmt should use the repo default config when a project does not provide one."""
    formatters = expect_instance(
        expect_binding(_conform_settings().values, "formatters").value,
        AttributeSet,
    )
    oxfmt = expect_instance(
        expect_binding(formatters.values, "oxfmt").value, AttributeSet
    )
    args = expect_instance(expect_binding(oxfmt.values, "args").value, AttributeSet)
    raw_args = expect_instance(
        expect_binding(args.values, "__raw").value,
        IndentedString,
    )

    assert '"--config", "${oxfmtDefaultConfigPath}"' in raw_args.value
    for config_name in (".oxfmtrc.json", ".oxfmtrc.jsonc", "oxfmt.config.ts"):
        assert f'"{config_name}"' in raw_args.value
