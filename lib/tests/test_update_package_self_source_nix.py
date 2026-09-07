"""AST contracts for updater package probes that require selfSource."""

import json

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.nix import _build_package_path_attr_expr


def test_package_probe_passes_overridden_source_to_package_materialization() -> None:
    """Native package-probes.nix covers callPackage injection; this checks its input."""
    override = SourceEntry.model_validate({
        "version": "9.9.9-test",
        "hashes": {
            "aarch64-darwin": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        },
        "urls": {
            "aarch64-darwin": "https://example.invalid/Wispr-Flow.dmg",
        },
    })
    expression = expect_instance(
        parse_nix_expr(
            _build_package_path_attr_expr(
                "wispr-flow",
                ".version",
                system="aarch64-darwin",
                source_overrides={"wispr-flow": override},
            )
        ),
        Select,
    )
    assert expression.attribute == "version"
    assert_nix_ast_equal(
        expression.expression,
        """
        (pkgs.lib.callPackageWith applied
          (packageMaterialization.packageFunctionsForSystem system)."wispr-flow"
          { inputs = rootFlake.inputs; outputs = flake; })
        """,
    )
    assert_nix_ast_equal(
        expect_binding(expression.scope, "packageMaterialization").value,
        """
        import (rootFlake.outPath + "/lib/package-materialization.nix") {
          src = rootFlake.outPath;
          lib = pkgs.lib;
          outputs = flake;
        }
        """,
    )
    contextual_import = expect_instance(
        expect_binding(expression.scope, "flake").value, FunctionCall
    )
    arguments = expect_instance(contextual_import.argument, AttributeSet)
    context = expect_instance(
        expect_binding(arguments.values, "evaluationContext").value, AttributeSet
    )
    assert_nix_ast_equal(expect_binding(context.values, "fakeHashes").value, "false")
    overrides = expect_instance(
        expect_binding(context.values, "sourceOverrides").value, FunctionCall
    )
    assert_nix_ast_equal(overrides.name, "builtins.fromJSON")
    payload = expect_instance(overrides.argument, StringPrimitive)
    assert_nix_ast_equal(
        payload,
        StringPrimitive(
            value=json.dumps(
                {"wispr-flow": override.to_dict()},
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    )
