"""Test helper for packages that build ``passthru.macApp`` with the shared helper."""

from typing import TYPE_CHECKING

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression


def mac_app_passthru_args(passthru: NixExpression) -> AttributeSet:
    """Return the ``mkMacAppPassthru`` arguments that define a package's macApp.

    *passthru* is either ``mkMacAppPassthru { ... }`` itself or a passthru set
    containing ``inherit (mkMacAppPassthru { ... }) macApp;``.
    """
    call = passthru
    if isinstance(passthru, AttributeSet):
        [call] = [
            value.from_expression
            for value in passthru.values
            if isinstance(value, Inherit)
            and [name.name for name in value.names] == ["macApp"]
        ]
    helper_call = expect_instance(call, FunctionCall)
    assert_nix_ast_equal(helper_call.name, "mkMacAppPassthru")
    return expect_instance(helper_call.argument, AttributeSet)
