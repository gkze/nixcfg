"""AST-level checks for standalone Home Manager flake outputs."""

from __future__ import annotations

import textwrap
from functools import cache
from pathlib import Path

from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT


@cache
def _flake_output_tail() -> BinaryExpression:
    """Return the parseable tail expression that exports flake outputs."""
    source = Path(REPO_ROOT / "flake.nix").read_text(encoding="utf-8")
    start = source.index("    (builtins.removeAttrs baseOutputs [")
    end = source.index("    };\n}", start) + len("    };")
    tail = textwrap.dedent(source[start:end]).removesuffix(";")
    return expect_instance(parse_nix_expr(tail), BinaryExpression)


@cache
def _flake_output_additions() -> AttributeSet:
    """Return the attrset added back after removing base flakelight outputs."""
    return expect_instance(_flake_output_tail().right, AttributeSet)


def test_standalone_home_output_is_exported_outside_flakelight_home_checks() -> None:
    """Keep standalone home exports separate from the shared flakelight checks."""
    assert_nix_ast_equal(
        _flake_output_tail().left,
        """
        (builtins.removeAttrs baseOutputs [
          "checks"
          "legacyPackages"
        ])
        """,
    )

    home_configurations = expect_instance(
        expect_binding(_flake_output_additions().values, "homeConfigurations").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(home_configurations.values, "george").value,
        'mkStandaloneHomeConfiguration "george" (import ./home/george { outputs = self; })',
    )
    checks = expect_instance(
        expect_binding(_flake_output_additions().values, "checks").value,
        FunctionCall,
    )
    checks_mapper = expect_instance(checks.name, FunctionCall)
    mapper_lambda = expect_instance(
        expect_instance(checks_mapper.argument, Parenthesis).value,
        FunctionDefinition,
    )
    system_checks_lambda = expect_instance(mapper_lambda.output, FunctionDefinition)
    filtered_checks = expect_instance(system_checks_lambda.output, FunctionCall)
    filter_attrs = expect_instance(filtered_checks.name, FunctionCall)
    filter_predicate = expect_instance(
        expect_instance(filter_attrs.argument, Parenthesis).value,
        FunctionDefinition,
    )
    filter_ignore_value = expect_instance(filter_predicate.output, FunctionDefinition)

    assert_nix_ast_equal(checks.argument, "baseOutputs.checks")
    assert_nix_ast_equal(checks_mapper.name, "builtins.mapAttrs")
    assert_nix_ast_equal(mapper_lambda.argument_set, "_")
    assert_nix_ast_equal(system_checks_lambda.argument_set, "systemChecks")
    assert_nix_ast_equal(filtered_checks.argument, "systemChecks")
    assert_nix_ast_equal(filter_attrs.name, "inputs.nixpkgs.lib.filterAttrs")
    assert_nix_ast_equal(filter_predicate.argument_set, "name")
    assert_nix_ast_equal(filter_ignore_value.argument_set, "_")
    assert_nix_ast_equal(
        filter_ignore_value.output,
        'name != "formatting" && !(inputs.nixpkgs.lib.hasPrefix "home-" name)',
    )
