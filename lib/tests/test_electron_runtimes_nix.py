"""AST checks for centrally packaged Electron runtimes."""

from __future__ import annotations

from functools import cache

from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import binding_map, expect_scope_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT

_EXPECTED_ELECTRON_VERSIONS = [
    "38.7.2",
    "40.1.0",
    "40.7.0",
    "40.8.5",
    "40.9.3",
    "41.2.1",
]
_EXPECTED_HASH_KEYS = {
    "headers",
    "aarch64-darwin",
    "aarch64-linux",
    "x86_64-darwin",
    "x86_64-linux",
}


@cache
def _electron_helper() -> FunctionDefinition:
    """Return the parsed central Electron helper."""
    return expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "overlays/_lib/helpers/electron.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )


def _string_list(value: NixList) -> list[str]:
    return [expect_instance(item, StringPrimitive).value for item in value.value]


def test_central_electron_runtime_versions_cover_packaged_apps() -> None:
    """Every exact Electron app runtime should be declared in one shared table."""
    versions = expect_instance(
        expect_scope_binding(_electron_helper().output, "allVersions").value,
        NixList,
    )

    assert _string_list(versions) == _EXPECTED_ELECTRON_VERSIONS


def test_central_electron_runtime_hashes_cover_supported_platforms() -> None:
    """Each runtime should carry zip hashes plus one unpacked headers hash."""
    hashes = expect_instance(
        expect_scope_binding(_electron_helper().output, "hashes").value,
        AttributeSet,
    )
    version_entries = binding_map(hashes.values)

    assert set(version_entries) == {
        f'"{version}"' for version in _EXPECTED_ELECTRON_VERSIONS
    }
    for version_entry in version_entries.values():
        platform_hashes = binding_map(
            expect_instance(version_entry.value, AttributeSet).values
        )
        assert set(platform_hashes) == _EXPECTED_HASH_KEYS
        for hash_entry in platform_hashes.values():
            hash_value = expect_instance(hash_entry.value, StringPrimitive).value
            assert hash_value.startswith("sha256-")
