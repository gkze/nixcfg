"""Structural contracts for the shared Electron runtime inventory."""

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import expect_binding
from lib.tests._nix_source import nix_file_expr

_HASH_FIELDS = {
    "headers",
    "aarch64-darwin",
    "aarch64-linux",
    "x86_64-darwin",
    "x86_64-linux",
}


def test_electron_runtime_inventory_is_complete() -> None:
    """Keep supported versions and their complete immutable hash records aligned."""
    overlay = expect_instance(
        nix_file_expr("overlays/_lib/helpers/electron.nix"),
        FunctionDefinition,
    )
    versions_expr = expect_instance(
        expect_binding(overlay.output.scope, "allVersions").value,
        NixList,
    )
    hashes_expr = expect_instance(
        expect_binding(overlay.output.scope, "hashes").value,
        AttributeSet,
    )
    versions = [
        expect_instance(version, StringPrimitive).value
        for version in versions_expr.value
    ]
    hashes_by_version: dict[str, dict[str, str]] = {}

    for version_entry in hashes_expr.values:
        version_binding = expect_instance(version_entry, Binding)
        version = version_binding.name.strip('"')
        fields_expr = expect_instance(version_binding.value, AttributeSet)
        fields: dict[str, str] = {}

        for field_entry in fields_expr.values:
            field_binding = expect_instance(field_entry, Binding)
            fields[field_binding.name] = expect_instance(
                field_binding.value,
                StringPrimitive,
            ).value

        assert len(fields_expr.values) == len(fields)
        assert set(fields) == _HASH_FIELDS
        hashes_by_version[version] = fields

    assert len(hashes_expr.values) == len(hashes_by_version)
    assert versions == list(hashes_by_version)
    assert len(versions) == len(set(versions))
    assert "43.3.0" in versions
