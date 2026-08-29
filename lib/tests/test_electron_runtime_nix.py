"""Structural contracts for the shared Electron runtime inventory."""

import base64
import binascii

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


def test_electron_runtime_hashes_are_valid_sha256_sris() -> None:
    """Require every exported Electron artifact hash to decode to SHA-256."""
    overlay = expect_instance(
        nix_file_expr("overlays/_lib/helpers/electron.nix"),
        FunctionDefinition,
    )
    hashes_expr = expect_instance(
        expect_binding(overlay.output.scope, "hashes").value,
        AttributeSet,
    )

    for version_entry in hashes_expr.values:
        version_binding = expect_instance(version_entry, Binding)
        version = version_binding.name.strip('"')
        fields_expr = expect_instance(version_binding.value, AttributeSet)

        for field_entry in fields_expr.values:
            field_binding = expect_instance(field_entry, Binding)
            hash_value = expect_instance(
                field_binding.value,
                StringPrimitive,
            ).value
            algorithm, separator, encoded = hash_value.partition("-")
            assert (algorithm, separator) == ("sha256", "-"), (
                f"{version}.{field_binding.name}: expected a sha256 SRI"
            )
            try:
                digest = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as error:
                message = f"{version}.{field_binding.name}: invalid base64 SRI digest"
                raise AssertionError(message) from error
            assert len(digest) == 32, (
                f"{version}.{field_binding.name}: expected a 32-byte SHA-256 digest, "
                f"got {len(digest)} bytes"
            )


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
    assert hashes_by_version["41.7.0"]["headers"] == (
        "sha256-/8zpNnFpMBN83RDs94SOCW1i/Rht7huivDVOpyRMvQQ="
    )
    assert hashes_by_version["40.10.2"]["aarch64-darwin"] == (
        "sha256-6ImzXjmfN09dypMhlSh7NzxLQ/i/JC5Qw1+Ip1FRGhM="
    )
    assert hashes_by_version["40.10.2"]["headers"] == (
        "sha256-wt18c5wqOeYbHvkMLd7gDzkVw0S1DQ7vYcPONHcCLDk="
    )
    assert hashes_by_version["41.7.0"]["aarch64-darwin"] == (
        "sha256-PenF66HPeaUSBYE01l7P+JliM3X/FBnf8v4RNGpcRd0="
    )
    assert hashes_by_version["41.2.0"] == {
        "headers": "sha256-5+fSd2fkH2hBVULp0rsr37R1Ej005evTmwnTZr3EQBg=",
        "aarch64-darwin": "sha256-4BhoT5bIc0FfvqRxP8fblrbR4r09tFE+K4wYh+yDpxk=",
        "aarch64-linux": "sha256-+Jg8h3348rk8dtNeRa+d+CyetfKUsYP4/lkw5RVf3E4=",
        "x86_64-darwin": "sha256-+zdQvPzMAUYGVwi/BlKIJS2gJInVFBSm1bd9BPlKPyo=",
        "x86_64-linux": "sha256-+wsx9bsrJI1XHAirV0N8CKabV/Y8zfnlXWaSthMoSNQ=",
    }
    assert hashes_by_version["42.7.1"] == {
        "headers": "sha256-bX7A+n4uauqBXjNoekq4/wv+2jlQoYqTKeA7eLBZLao=",
        "aarch64-darwin": "sha256-E7fDeCrHO6ZEEFQkEKLQbwIsUVcw6XxRSa/yGQpsjoA=",
        "aarch64-linux": "sha256-uh+AVaa2AenNdxp8dQ7Te5EsSGpR9R5YZU7ZQUvImLo=",
        "x86_64-darwin": "sha256-ovNhtmsktpRxWwV6nUuowwRXzplOGazZGFwTFTm8iQA=",
        "x86_64-linux": "sha256-6FsoJF3qp1w8Tyz12ghPTH++3VrxYwxZqCfEGsH14a8=",
    }
    assert hashes_by_version["42.9.1"] == {
        "headers": "sha256-w3uCtYSN3dHKftfTKNBBeMtI8xSSPUuhXcw7IRrn+9U=",
        "aarch64-darwin": "sha256-IZPD4LnlLoBR2ozDaaAcKJOhhb0vuLc8zMFFHWjHFbY=",
        "aarch64-linux": "sha256-YNdNSsIGXN9T7Ms1OdCpd1aQ1c1ejF0z/H5HbuyXULA=",
        "x86_64-darwin": "sha256-JRxZ7CAY3Z6zfp6YtnOK//OlnokBjYJHcG1vzACiGoI=",
        "x86_64-linux": "sha256-lFewpgghgXNFmoe0bWsI3DLIvTtRlUOa70TF43QljaA=",
    }
    assert hashes_by_version["43.4.0"] == {
        "headers": "sha256-VPjc6ixDtFrIhUpZNAeSGg1Vx9UYbAkGrUnWlImtJdY=",
        "aarch64-darwin": "sha256-gn+fGCVm9GhGN3V1tRxUe5kmsRFjcxOjc7b3F0Yq66w=",
        "aarch64-linux": "sha256-FwIdSHOYVxBqJt2Vv3Sflbia6SSVXDx+f/Wj8GJRrBQ=",
        "x86_64-darwin": "sha256-erOewbC89UY/LcAEAUL7wcMM17w/mQhgZvWIxxexHiQ=",
        "x86_64-linux": "sha256-fF95GLyudKBagUVDlA6yhGnAVe2qPPz0HQ/xeHsxTFI=",
    }
