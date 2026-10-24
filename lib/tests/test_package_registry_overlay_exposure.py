"""Contracts for the registry-owned ``overlays.default`` package exposure."""

import shutil
import subprocess

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import Primitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_eval import (
    nix_attrset,
    nix_eval_json,
    nix_eval_result,
    nix_import,
)
from lib.tests._nix_source import nix_file_expr
from lib.tests._package_registry import (
    is_darwin_overlay_export,
    registry_overlay_exposure,
)
from lib.update.paths import REPO_ROOT


def _registry_output() -> AttributeSet:
    registry = expect_instance(
        nix_file_expr("packages/registry.nix"), FunctionDefinition
    )
    return expect_instance(registry.output, AttributeSet)


def _registry_call() -> Parenthesis:
    return Parenthesis(
        value=FunctionCall(
            name=nix_import(REPO_ROOT / "packages/registry.nix"),
            argument=AttributeSet(values=[]),
        )
    )


def test_overlay_exposure_lists_are_disjoint_and_explicit() -> None:
    """A package is either an explicit extra or owned by another overlay layer."""
    exposure = registry_overlay_exposure(_registry_output())
    assert set(exposure) == {"extraPackages", "overlayOwnedPackages"}
    assert not set(exposure["extraPackages"]) & set(exposure["overlayOwnedPackages"])


@pytest.mark.parametrize(
    ("name", "exported"),
    [
        ("airfoil", True),  # constraint "darwin"
        ("hq", True),  # aarch64-darwin only
        ("pants-preview", True),  # explicit extra on a Linux-capable package
        ("raycast", False),  # owned by overlays/raycast
        ("superset", False),  # also built for x86_64-linux
        ("nixcfg", False),  # unconstrained
    ],
)
def test_is_darwin_overlay_export_classifies_each_rule(
    name: str, *, exported: bool
) -> None:
    """Every branch of the mirrored exposure rule must match its intent."""
    assert is_darwin_overlay_export(_registry_output(), name) is exported


@pytest.mark.nix_eval(
    reason=(
        "overlayPackageNamesFor filters the registry by computed system support; "
        "only evaluation proves the Python mirror agrees with that rule"
    )
)
def test_python_mirror_matches_registry_darwin_exposure() -> None:
    """The AST mirror and the evaluated registry must name the same exports."""
    evaluated = nix_eval_json(
        FunctionCall(
            name=Select(
                expression=_registry_call(),
                attribute="overlayPackageNamesFor",
            ),
            argument=Primitive(value="aarch64-darwin"),
        )
    )
    assert isinstance(evaluated, list)
    registry_output = _registry_output()
    names = nix_eval_json(
        FunctionCall(
            name=Select(expression=Identifier(name="builtins"), attribute="attrNames"),
            argument=Parenthesis(
                value=Select(
                    expression=_registry_call(),
                    attribute="packagePaths",
                )
            ),
        )
    )
    assert isinstance(names, list)
    mirrored = sorted(
        name for name in names if is_darwin_overlay_export(registry_output, name)
    )
    assert sorted(evaluated) == mirrored


@pytest.mark.nix_eval(
    reason="Only evaluator diagnostics distinguish the collision guard from later override errors."
)
@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_registry_rejects_flat_package_companion_name_collision() -> None:
    """A directory companion and flat package must not silently share one export."""
    fixture = REPO_ROOT / "tests/nix/package-helpers/registry/collision"
    registry = Parenthesis(
        value=FunctionCall(
            name=nix_import(REPO_ROOT / "packages/registry.nix"),
            argument=nix_attrset({"src": NixPath(path=str(fixture))}),
        )
    )
    package_paths = Select(expression=registry, attribute="packagePaths")

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        nix_eval_result(
            FunctionCall(
                name=Select(
                    expression=Identifier(name="builtins"), attribute="attrNames"
                ),
                argument=Parenthesis(value=package_paths),
            ),
            raw=False,
        )

    assert (
        "packages/registry.nix: duplicate exported package names across ordinary "
        "and companion discovery: demo-crate2nix-src"
    ) in exc_info.value.stderr
