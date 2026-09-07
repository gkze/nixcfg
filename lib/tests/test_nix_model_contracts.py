"""Conformance of the public compatibility views to vendored Nix schemas.

The views deliberately supply defaults, retain legacy full store paths, and
flatten experimental payloads. Field inventories, enums, scalar constraints,
and numeric bounds must still follow the wire contract when it changes.
"""

from enum import StrEnum
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from lib.nix.models import (
    ContentAddress,
    ContentAddressMethod,
    Derivation,
    DerivationInputs,
    DerivationOutput,
    FailedBuild,
    FailureStatus,
    HashAlgorithm,
    ImpureStoreObjectInfo,
    NarInfo,
    NixHash,
    StoreObjectInfo,
    StorePath,
    SuccessfulBuild,
    SuccessStatus,
)
from lib.nix.models._generated import ContentAddress as WireContentAddress

_SCHEMAS = Path(__file__).parents[1] / "nix" / "schemas"
_HASH = "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0="


def _schema(name: str, *keys: str) -> dict[str, object]:
    value = yaml.safe_load((_SCHEMAS / f"{name}.yaml").read_text())
    for key in keys:
        value = value[key]
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(
    ("model", "schema_name", "path"),
    [
        (ContentAddress, "content-address-v1", ()),
        (Derivation, "derivation-v4", ()),
        (DerivationInputs, "derivation-v4", ("properties", "inputs")),
        (StoreObjectInfo, "store-object-info-v2", ("$defs", "base")),
        (ImpureStoreObjectInfo, "store-object-info-v2", ("$defs", "impure")),
        (NarInfo, "store-object-info-v2", ("$defs", "narInfo")),
    ],
)
def test_public_field_names_follow_the_wire_schema(
    model: type[BaseModel], schema_name: str, path: tuple[str, ...]
) -> None:
    assert set(model.model_json_schema(by_alias=True)["properties"]) == set(
        _schema(schema_name, *path, "properties")
    )


@pytest.mark.parametrize(
    ("model", "variant"), [(SuccessfulBuild, "success"), (FailedBuild, "failure")]
)
def test_build_variants_include_common_and_variant_fields(
    model: type[BaseModel], variant: str
) -> None:
    common = _schema("build-result-v1", "properties")
    own = _schema("build-result-v1", "$defs", variant, "properties")
    assert (
        set(model.model_json_schema(by_alias=True)["properties"])
        == common.keys() | own.keys()
    )


def test_flattened_output_covers_every_wire_variant() -> None:
    variants = _schema("derivation-v4", "$defs", "output")
    fields = {
        name
        for variant, spec in variants.items()
        if variant != "overall"
        for name in spec.get("properties", {})
    }
    assert (
        set(DerivationOutput.model_json_schema(by_alias=True)["properties"]) == fields
    )


@pytest.mark.parametrize(
    ("field", "schema_name", "path"),
    [
        ("method", "content-address-v1", ("$defs", "method")),
        ("hashAlgo", "hash-v1", ("$defs", "algorithm")),
        ("hash", "hash-v1", ()),
        (
            "impure",
            "derivation-v4",
            ("$defs", "output", "impure", "properties", "impure"),
        ),
    ],
)
def test_flattened_output_retains_wire_scalar_constraints(
    field: str, schema_name: str, path: tuple[str, ...]
) -> None:
    runtime = DerivationOutput.model_json_schema(by_alias=True)
    scalar = next(
        branch
        for branch in runtime["properties"][field]["anyOf"]
        if branch.get("type") != "null"
    )
    if "$ref" in scalar:
        scalar = runtime["$defs"][scalar["$ref"].removeprefix("#/$defs/")]
    wire = _schema(schema_name, *path)
    for constraint in ("enum", "const", "pattern", "minLength", "maxLength"):
        assert scalar.get(constraint) == wire.get(constraint)


@pytest.mark.parametrize(
    "payload",
    [
        {"method": "unknown-method"},
        {"hashAlgo": "unknown-algorithm"},
        {"hash": "invalid-sri"},
        {"impure": False},
    ],
)
def test_flattened_output_rejects_invalid_wire_scalar_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        DerivationOutput.model_validate(payload)


@pytest.mark.parametrize(
    ("enum", "schema_name", "path"),
    [
        (HashAlgorithm, "hash-v1", ("$defs", "algorithm")),
        (ContentAddressMethod, "content-address-v1", ("$defs", "method")),
        (
            SuccessStatus,
            "build-result-v1",
            ("$defs", "success", "properties", "status"),
        ),
        (
            FailureStatus,
            "build-result-v1",
            ("$defs", "failure", "properties", "status"),
        ),
    ],
)
def test_public_enum_values_follow_the_wire_schema(
    enum: type[StrEnum], schema_name: str, path: tuple[str, ...]
) -> None:
    assert {member.value for member in enum} == set(_schema(schema_name, *path)["enum"])


@pytest.mark.parametrize(
    ("annotation", "schema_name"), [(NixHash, "hash-v1"), (StorePath, "store-path-v1")]
)
def test_scalar_validation_constraints_follow_the_wire_schema(
    annotation: object, schema_name: str
) -> None:
    wire = _schema(schema_name)
    runtime = TypeAdapter(annotation).json_schema()
    for key in ("type", "pattern", "minLength", "maxLength"):
        assert runtime.get(key) == wire.get(key)


@pytest.mark.parametrize("model", [SuccessfulBuild, FailedBuild])
def test_build_timing_constraints_follow_the_wire_schema(
    model: type[BaseModel],
) -> None:
    runtime = model.model_json_schema(by_alias=True)["properties"]
    for name, wire in _schema("build-result-v1", "properties").items():
        branches = runtime[name].get("anyOf", [runtime[name]])
        numeric = next(branch for branch in branches if branch["type"] == "integer")
        assert numeric["minimum"] == wire["minimum"]


@pytest.mark.parametrize(
    ("model", "variant", "field"),
    [
        (StoreObjectInfo, "base", "narSize"),
        (ImpureStoreObjectInfo, "impure", "closureSize"),
        (NarInfo, "narInfo", "downloadSize"),
        (NarInfo, "narInfo", "closureDownloadSize"),
    ],
)
def test_store_sizes_follow_the_wire_schema(
    model: type[BaseModel], variant: str, field: str
) -> None:
    runtime = model.model_json_schema(by_alias=True)["properties"][field]
    branches = runtime.get("anyOf", [runtime])
    numeric = next(branch for branch in branches if branch["type"] == "integer")
    assert (
        numeric["minimum"]
        == _schema("store-object-info-v2", "$defs", variant, "properties", field)[
            "minimum"
        ]
    )


@pytest.mark.parametrize("method", list(ContentAddressMethod))
def test_content_address_round_trips_through_runtime_and_wire_models(
    method: ContentAddressMethod,
) -> None:
    payload = {"method": method.value, "hash": _HASH}
    runtime = ContentAddress.model_validate(payload)
    wire = WireContentAddress.model_validate(payload)
    assert runtime.model_dump(mode="json") == wire.model_dump(mode="json") == payload


@pytest.mark.parametrize("value", ["", "not-a-valid-sri", "sha256-invalid hash"])
def test_runtime_content_addresses_reject_invalid_wire_hashes(value: str) -> None:
    for model in (ContentAddress, WireContentAddress):
        with pytest.raises(ValidationError):
            model.model_validate({"method": "nar", "hash": value})


def test_derivation_compatibility_defaults_and_unknown_fields_are_preserved() -> None:
    model = Derivation.model_validate({
        "name": "example",
        "outputs": {"out": {"path": "/nix/store/legacy-output"}, "deferred": {}},
        "inputs": {},
        "system": "aarch64-darwin",
        "builder": "/bin/sh",
        "futureExtension": {"enabled": True},
    })
    assert model.version == _schema("derivation-v4", "properties", "version")["const"]
    assert model.outputs["out"].path == "/nix/store/legacy-output"
    assert model.outputs["deferred"] == DerivationOutput()
    assert model.args == []
    assert model.env == {}
    assert model.inputs == DerivationInputs()
    assert model.model_dump()["futureExtension"] == {"enabled": True}


def test_store_and_build_construction_defaults_are_intentional() -> None:
    store = StoreObjectInfo(narHash=_HASH, narSize=1)
    assert (
        store.version
        == _schema("store-object-info-v2", "$defs", "base", "properties", "version")[
            "const"
        ]
    )
    assert store.path is None
    assert store.references == []
    assert store.ca is None
    assert store.store_dir == "/nix/store"
    build = SuccessfulBuild(status=SuccessStatus.Built)
    assert build.success is True
    assert build.built_outputs == {}
