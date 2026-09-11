"""Compatibility checks for packages built with nixpkgs Node.js toolchains."""

import re
from dataclasses import dataclass
from typing import Annotated, Literal

from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.parser import parse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from lib.nix.commands.base import run_nix
from lib.update import nix as update_nix
from lib.update.config import default_config
from lib.update.nix import _build_flake_attr_expr
from lib.update.nix_expr import compact_nix_expr, select_attrs
from lib.update.npm_semver import (
    npm_version_matches_spec,
    require_exact_semantic_version,
    require_valid_npm_range,
)
from lib.update.paths import local_flake_url
from lib.update.runtime import resource_slot, runtime_scope, workspace_access

_NIX_ATTRIBUTE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_'-]*$")
_NODEJS_ATTRIBUTE_PATTERN = re.compile(r"^nodejs_(?P<major>0|[1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class NodejsSelection:
    """One available nixpkgs Node.js runtime satisfying an upstream engine."""

    engine: str
    attribute: str
    version: str


def _require_node_engine(
    engine: object,
    *,
    source_name: str,
) -> str:
    if not isinstance(engine, str) or not engine:
        msg = f"{source_name} package Node engine is missing"
        raise TypeError(msg)
    return require_valid_npm_range(
        engine,
        context=f"{source_name} package Node engine",
    )


def require_supported_node_engine(
    engine: object,
    *,
    selected_attr: str,
    selected_version: str,
    source_name: str,
) -> str:
    """Require an npm-compatible engine satisfied by the selected Node.js."""
    node_engine = _require_node_engine(engine, source_name=source_name)
    if not npm_version_matches_spec(
        selected_version,
        context=f"{source_name} package-selected {selected_attr}",
        spec=node_engine,
    ):
        msg = (
            f"{source_name} package-selected {selected_attr} {selected_version!r} "
            f"does not satisfy Node engine {node_engine!r}"
        )
        raise RuntimeError(msg)
    return node_engine


def _nodejs_attribute_names_apply_expr() -> str:
    """Build the Nix function that enumerates versioned Node.js attributes."""
    name = Identifier(name="name")
    nodejs_name = BinaryExpression(
        operator=Operator(name="!="),
        left=FunctionCall(
            name=FunctionCall(
                name=select_attrs(Identifier(name="builtins"), "match"),
                argument=StringPrimitive(value="nodejs_[0-9]+"),
            ),
            argument=name,
        ),
        right=Primitive(value=None),
    )
    attribute_names = FunctionCall(
        name=select_attrs(Identifier(name="builtins"), "attrNames"),
        argument=Identifier(name="pkgs"),
    )
    expression = FunctionDefinition(
        argument_set=Identifier(name="pkgs"),
        output=FunctionCall(
            name=FunctionCall(
                name=select_attrs(Identifier(name="builtins"), "filter"),
                argument=Parenthesis(
                    value=FunctionDefinition(
                        argument_set=name,
                        output=nodejs_name,
                    )
                ),
            ),
            argument=Parenthesis(value=attribute_names),
        ),
    )
    return compact_nix_expr(expression.rebuild())


def _nixpkgs_package_set_expr(platform: str) -> str:
    """Build an expression for the pinned nixpkgs package set on one platform."""
    return _build_flake_attr_expr(
        local_flake_url(),
        "pkgs",
        platform,
        quoted_indices=(1,),
    )


def _nixpkgs_package_version_expr(platform: str, package_attr: str) -> str:
    """Build an expression for one exact nixpkgs package version."""
    return _build_flake_attr_expr(
        local_flake_url(),
        "pkgs",
        platform,
        package_attr,
        "version",
        quoted_indices=(1,),
    )


def _package_passthru_version_expr(
    platform: str,
    package_attr: str,
    passthru_attr: str,
) -> str:
    """Build an expression for one package-owned passthru version."""
    return _build_flake_attr_expr(
        local_flake_url(),
        "pkgs",
        platform,
        package_attr,
        "passthru",
        passthru_attr,
        quoted_indices=(1, 2, 4),
    )


async def _evaluate_version(
    expression: str,
    *,
    command_timeout: float,
    selection: str,
    source_name: str,
) -> str:
    async with (
        runtime_scope(default_config()),
        workspace_access(),
        resource_slot("eval", source=source_name, config=default_config()) as timing,
    ):
        result = await run_nix(
            [
                "nix",
                "eval",
                "--impure",
                "--raw",
                "--expr",
                expression,
            ],
            command_timeout=command_timeout,
            check=False,
        )
        timing.stdout_bytes += len(result.stdout.encode())
        timing.stderr_bytes += len(result.stderr.encode())
        timing.nonzero_exits += int(result.returncode != 0)
    version = result.stdout.strip()
    if result.returncode != 0 or not version:
        details = result.stderr.strip() or result.stdout.strip() or "nix eval failed"
        msg = f"Failed to evaluate {selection} for {source_name}: {details}"
        raise RuntimeError(msg)
    return version


class _AvailableNodeVersion(BaseModel):
    success: Literal[True]
    value: str


class _UnavailableNodeVersion(BaseModel):
    success: Literal[False]
    value: Literal[False]


_NodeVersionResult = Annotated[
    _AvailableNodeVersion | _UnavailableNodeVersion, Field(discriminator="success")
]
_NODE_INVENTORY = TypeAdapter(dict[str, _NodeVersionResult])


def _nodejs_inventory_apply_expr() -> str:
    """Inspect every candidate in one evaluation, isolating unsupported aliases."""
    name = Identifier(name="name")
    pkgs = Identifier(name="pkgs")
    version = Identifier(name="version")
    package = FunctionCall(
        name=FunctionCall(
            name=select_attrs(Identifier(name="builtins"), "getAttr"),
            argument=name,
        ),
        argument=pkgs,
    )
    checked_version = LetExpression(
        local_variables=[
            Binding(name="package", value=package),
            Binding(
                name="version",
                value=IfExpression(
                    condition=FunctionCall(
                        name=select_attrs(Identifier(name="builtins"), "isAttrs"),
                        argument=Identifier(name="package"),
                    ),
                    consequence=Select(
                        expression=Identifier(name="package"),
                        attribute="version",
                        default=Primitive(value=None),
                    ),
                    alternative=Primitive(value=None),
                ),
            ),
        ],
        value=IfExpression(
            condition=FunctionCall(
                name=select_attrs(Identifier(name="builtins"), "isString"),
                argument=version,
            ),
            consequence=version,
            alternative=FunctionCall(
                name=Identifier(name="throw"),
                argument=StringPrimitive(value="Node.js version is not a string"),
            ),
        ),
    )
    candidate = FunctionDefinition(
        argument_set=name,
        output=AttributeSet.from_dict({
            "name": name,
            "value": FunctionCall(
                name=select_attrs(Identifier(name="builtins"), "tryEval"),
                argument=Parenthesis(value=checked_version),
            ),
        }),
    )
    names = FunctionCall(
        name=Parenthesis(value=parse(_nodejs_attribute_names_apply_expr()).expr),
        argument=pkgs,
    )
    entries = FunctionCall(
        name=FunctionCall(
            name=select_attrs(Identifier(name="builtins"), "map"),
            argument=Parenthesis(value=candidate),
        ),
        argument=Parenthesis(value=names),
    )
    return compact_nix_expr(
        FunctionDefinition(
            argument_set=pkgs,
            output=FunctionCall(
                name=select_attrs(Identifier(name="builtins"), "listToAttrs"),
                argument=Parenthesis(value=entries),
            ),
        ).rebuild()
    )


async def _evaluate_nodejs_inventory(
    platform: str,
    *,
    command_timeout: float,
    source_name: str,
) -> dict[str, _AvailableNodeVersion | _UnavailableNodeVersion]:
    """Evaluate candidate versions together without caching mutable flake state.

    A lockfile alone cannot key this inventory: the package set also observes
    local overlays and generated files, which can change within an update run.
    """
    async with (
        runtime_scope(default_config()),
        workspace_access(),
        resource_slot("eval", source=source_name, config=default_config()) as timing,
    ):
        result = await run_nix(
            [
                "nix",
                "eval",
                "--impure",
                "--json",
                "--expr",
                _nixpkgs_package_set_expr(platform),
                "--apply",
                _nodejs_inventory_apply_expr(),
            ],
            command_timeout=command_timeout,
            check=False,
        )
        timing.stdout_bytes += len(result.stdout.encode())
        timing.stderr_bytes += len(result.stderr.encode())
        timing.nonzero_exits += int(result.returncode != 0)
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "nix eval failed"
        msg = f"Failed to inspect nixpkgs Node.js packages for {source_name}: {details}"
        raise RuntimeError(msg)
    try:
        inventory = _NODE_INVENTORY.validate_json(result.stdout)
    except ValidationError as error:
        msg = (
            f"Failed to inspect nixpkgs Node.js packages for {source_name}: "
            "nix eval returned an invalid JSON inventory"
        )
        raise RuntimeError(msg) from error
    for attribute in inventory:
        if _NODEJS_ATTRIBUTE_PATTERN.fullmatch(attribute) is None:
            msg = (
                f"Unexpected nixpkgs Node.js attribute for {source_name}: {attribute!r}"
            )
            raise RuntimeError(msg)
    return inventory


async def _resolve_nixpkgs_package_version_for_platform(
    package_attr: str,
    *,
    platform: str,
    command_timeout: float,
    source_name: str,
) -> str:
    return await _evaluate_version(
        _nixpkgs_package_version_expr(platform, package_attr),
        command_timeout=command_timeout,
        selection=f"{package_attr}.version",
        source_name=source_name,
    )


async def resolve_nixpkgs_package_version(
    package_attr: str,
    *,
    command_timeout: float,
    source_name: str,
) -> str:
    """Evaluate the version of one updater-selected nixpkgs package attribute."""
    if _NIX_ATTRIBUTE_PATTERN.fullmatch(package_attr) is None:
        msg = f"Invalid nixpkgs package attribute for {source_name}: {package_attr!r}"
        raise RuntimeError(msg)
    platform = update_nix.get_current_nix_platform()
    return await _resolve_nixpkgs_package_version_for_platform(
        package_attr,
        platform=platform,
        command_timeout=command_timeout,
        source_name=source_name,
    )


async def resolve_nixpkgs_nodejs_for_engine(
    engine: object,
    *,
    command_timeout: float,
    source_name: str,
) -> NodejsSelection:
    """Select the lowest available nixpkgs Node.js satisfying an npm engine."""
    node_engine = _require_node_engine(engine, source_name=source_name)
    platform = update_nix.get_current_nix_platform()
    inventory = await _evaluate_nodejs_inventory(
        platform,
        command_timeout=command_timeout,
        source_name=source_name,
    )

    available: list[str] = []
    failures: list[str] = []
    for attribute in sorted(
        inventory, key=lambda name: int(name.removeprefix("nodejs_"))
    ):
        candidate = inventory[attribute]
        if not candidate.success:
            failures.append(f"{attribute}: version evaluation failed")
            continue
        version = candidate.value
        try:
            satisfies = npm_version_matches_spec(
                version,
                node_engine,
                context=f"{source_name} nixpkgs {attribute}",
            )
        except RuntimeError as error:
            failures.append(f"{attribute}: {error}")
            continue
        available.append(f"{attribute}={version}")
        if satisfies:
            return NodejsSelection(
                engine=node_engine,
                attribute=attribute,
                version=version,
            )

    available_detail = ", ".join(available) or "none"
    failure_detail = f"; evaluation failures: {'; '.join(failures)}" if failures else ""
    msg = (
        f"No available nixpkgs Node.js package satisfies {source_name} engine "
        f"{node_engine!r} on {platform}; available versions: {available_detail}"
        f"{failure_detail}"
    )
    raise RuntimeError(msg)


async def resolve_package_passthru_version(
    package_attr: str,
    passthru_attr: str,
    *,
    command_timeout: float,
    source_name: str,
) -> str:
    """Evaluate one exact package-owned toolchain version from flake passthru."""
    if _NIX_ATTRIBUTE_PATTERN.fullmatch(package_attr) is None:
        msg = f"Invalid flake package attribute for {source_name}: {package_attr!r}"
        raise RuntimeError(msg)
    if _NIX_ATTRIBUTE_PATTERN.fullmatch(passthru_attr) is None:
        msg = f"Invalid package passthru attribute for {source_name}: {passthru_attr!r}"
        raise RuntimeError(msg)
    selection = f"{package_attr}.passthru.{passthru_attr}"
    platform = update_nix.get_current_nix_platform()
    version = await _evaluate_version(
        _package_passthru_version_expr(platform, package_attr, passthru_attr),
        command_timeout=command_timeout,
        selection=selection,
        source_name=source_name,
    )
    require_exact_semantic_version(
        version,
        context=f"{source_name} package-selected {selection}",
    )
    return version


__all__ = [
    "NodejsSelection",
    "require_supported_node_engine",
    "resolve_nixpkgs_nodejs_for_engine",
    "resolve_nixpkgs_package_version",
    "resolve_package_passthru_version",
]
