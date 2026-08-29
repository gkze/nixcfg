"""Contracts for evaluator-visible uv2nix lockfile inputs."""

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import expect_binding
from lib.tests._nix_source import nix_file_expr


@pytest.mark.parametrize(
    "package_file",
    [
        "packages/nix-manipulator/default.nix",
        "packages/toad/default.nix",
    ],
)
def test_uv2nix_packages_pass_their_tracked_lockfile(package_file: str) -> None:
    """Package evaluation must not depend on a GC-able builtins.path lock copy."""
    package = expect_instance(nix_file_expr(package_file), FunctionDefinition)
    call = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(call.argument, AttributeSet)
    lockfile = expect_instance(
        expect_binding(arguments.values, "uvLockFile").value,
        NixPath,
    )

    assert lockfile.path == "./uv.lock"
