"""Static materialization contracts for source-backed migration candidates."""

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance, expect_not_none
from lib.tests._nix_ast import expect_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT


def _package_function(package_name: str) -> FunctionDefinition:
    return expect_instance(
        parse_nix_expr(
            (REPO_ROOT / f"packages/{package_name}/package.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )


def _package_scope(package: FunctionDefinition) -> list[object]:
    output = package.output
    if isinstance(output, Assertion):
        output = output.body
    return expect_instance(output, IfExpression).scope


def _assert_shared_injector_contract() -> None:
    helper = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "lib/package-self-source.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    helper_output = expect_instance(helper.output, AttributeSet)
    injector = expect_instance(
        expect_binding(helper_output.scope, "injectIntoFunction").value,
        FunctionDefinition,
    )
    assert expect_instance(injector.argument_set, Identifier).name == "name"
    package_injector = expect_instance(injector.output, FunctionDefinition)
    assert expect_instance(package_injector.argument_set, Identifier).name == "pkg"
    injection = expect_instance(package_injector.output, IfExpression)
    function_args = expect_instance(
        expect_binding(injection.scope, "pkgArgs").value,
        FunctionCall,
    )
    function_args_name = expect_instance(function_args.name, Select)
    assert expect_instance(function_args_name.expression, Identifier).name == "builtins"
    assert function_args_name.attribute == "functionArgs"
    assert expect_instance(function_args.argument, Identifier).name == "pkg"
    assert expect_instance(injection.consequence, Identifier).name == "pkg"


def _assert_sibling_source_fallback(formal: Identifier) -> None:
    fallback = expect_instance(
        expect_not_none(
            formal.default_value, "selfSource must retain a direct-build fallback"
        ),
        FunctionCall,
    )
    from_json = expect_instance(fallback.name, Select)
    assert expect_instance(from_json.expression, Identifier).name == "builtins"
    assert from_json.attribute == "fromJSON"
    read_parenthesis = expect_instance(fallback.argument, Parenthesis)
    read_file = expect_instance(read_parenthesis.value, FunctionCall)
    read_file_name = expect_instance(read_file.name, Select)
    assert expect_instance(read_file_name.expression, Identifier).name == "builtins"
    assert read_file_name.attribute == "readFile"
    assert expect_instance(read_file.argument, NixPath).path == "./sources.json"


@pytest.mark.parametrize("package_name", ["paseo", "unsloth"])
def test_source_migration_uses_shared_self_source_materialization(
    package_name: str,
) -> None:
    """Promotion must inject source metadata without breaking direct build plans."""
    _assert_shared_injector_contract()
    package = _package_function(package_name)
    formals = {
        formal.name: formal
        for formal in package.argument_set
        if isinstance(formal, Identifier)
    }

    assert "sourceData" not in formals
    self_source = expect_instance(formals["selfSource"], Identifier)
    _assert_sibling_source_fallback(self_source)
    source_binding = expect_binding(_package_scope(package), "source")
    assert expect_instance(source_binding.value, Identifier).name == "selfSource"
