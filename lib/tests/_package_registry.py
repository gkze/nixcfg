"""Test helpers for the package registry Nix metadata table."""

from __future__ import annotations

from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import Primitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import expect_scope_binding


def _constraint_value(value: Primitive | NixList) -> str | list[str] | None:
    if isinstance(value, NixList):
        decoded = [expect_instance(item, Primitive).value for item in value.value]
        assert all(isinstance(item, str) for item in decoded)
        return decoded

    decoded = expect_instance(value, Primitive).value
    assert decoded is None or isinstance(decoded, str)
    return decoded


def _string_list(value: NixList) -> list[str]:
    decoded = [expect_instance(item, Primitive).value for item in value.value]
    assert all(isinstance(item, str) for item in decoded)
    return decoded


def _grouped_override_metadata(
    overrides_expr: BinaryExpression,
) -> dict[str, dict[str, object]]:
    groups = {
        "helperPackages": {"helper": True},
        "darwinPackages": {"constraint": "darwin"},
        "aarch64DarwinPackages": {"constraint": ["aarch64-darwin"]},
        "darwinLinuxPackages": {"constraint": ["aarch64-darwin", "x86_64-linux"]},
        "nonX86DarwinLinuxPackages": {
            "constraint": ["aarch64-darwin", "aarch64-linux", "x86_64-linux"]
        },
        "allLocalSystemsPackages": {
            "constraint": [
                "aarch64-darwin",
                "x86_64-darwin",
                "aarch64-linux",
                "x86_64-linux",
            ]
        },
    }
    decoded: dict[str, dict[str, object]] = {}
    for group_name, metadata in groups.items():
        group = expect_instance(
            expect_scope_binding(overrides_expr, group_name).value,
            NixList,
        )
        decoded.update({name: dict(metadata) for name in _string_list(group)})

    decoded["sculptor"] = {
        "constraint": ["aarch64-darwin", "x86_64-darwin", "x86_64-linux"]
    }
    return decoded


def registry_override_metadata(
    registry_output: AttributeSet,
) -> dict[str, dict[str, object]]:
    """Decode the literal override metadata table used by packages/registry.nix."""
    overrides_expr = expect_scope_binding(
        registry_output, "packageMetadataOverrides"
    ).value
    if isinstance(overrides_expr, BinaryExpression):
        return _grouped_override_metadata(overrides_expr)

    overrides = expect_instance(overrides_expr, AttributeSet)
    decoded: dict[str, dict[str, object]] = {}
    for binding in overrides.values:
        entry = expect_instance(binding, Binding)
        entry_value = expect_instance(entry.value, AttributeSet)
        metadata: dict[str, object] = {}
        for meta in entry_value.values:
            field = expect_instance(meta, Binding)
            if field.name == "helper":
                metadata[field.name] = expect_instance(field.value, Primitive).value
                continue
            assert field.name == "constraint"
            metadata[field.name] = _constraint_value(field.value)
        decoded[entry.name.strip('"')] = metadata
    return decoded
