"""Behavioral and package-shape tests for Tembo desktop."""

import json
from types import ModuleType
from typing import TYPE_CHECKING, cast

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import PlatformAPIMetadata

if TYPE_CHECKING:
    from collections.abc import Callable

_ORIGIN = "https://tembo-desktop-releases-844506114394.s3.us-east-1.amazonaws.com"
_MANIFEST_URL = f"{_ORIGIN}/releases/manifest.json"
_VERSION = "0.0.7"
_COMMIT = "815c0dda6cdff60c8ad1df03b71876135245d064"
_ARCHES = {
    "aarch64-darwin": "arm64",
    "x86_64-darwin": "x64",
}
_HEX_HASHES = {
    "aarch64-darwin": "10239fa4ea47a7cdc21914b036253d9d85c724d139260725228d3eae96299d8c",
    "x86_64-darwin": "9cbc469e03538f088c2d536007ee21bf4374b17ff3b9171ee71d49ddbc58ddec",
}
_SRI_HASHES = {
    "aarch64-darwin": "sha256-ECOfpOpHp83CGRSwNiU9nYXHJNE5JgclIo0+rpYpnYw=",
    "x86_64-darwin": "sha256-nLxGngNTjwiMLVNgB+4hv0N0sX/zuRce5x1J3bxY3ew=",
}
_SIZES = {
    "aarch64-darwin": 120402266,
    "x86_64-darwin": 125347270,
}
_URLS = {
    platform: f"{_ORIGIN}/releases/{_VERSION}/Tembo-{_VERSION}-{arch}.dmg"
    for platform, arch in _ARCHES.items()
}


def _load_module() -> ModuleType:
    return load_repo_module("packages/tembo/updater.py", "tembo_updater_test")


def _artifact(platform: str) -> dict[str, object]:
    arch = _ARCHES[platform]
    file_name = f"Tembo-{_VERSION}-{arch}.dmg"
    latest_object_key = f"releases/latest/macos/Tembo-{arch}.dmg"
    return {
        "platform": "macos",
        "fileName": file_name,
        "objectKey": f"releases/{_VERSION}/{file_name}",
        "path": _URLS[platform],
        "latestObjectKey": latest_object_key,
        "latestPath": f"{_ORIGIN}/{latest_object_key}",
        "sha256": _HEX_HASHES[platform],
        "arch": arch,
        "contentType": "application/x-apple-diskimage",
        "contentDisposition": f'attachment; filename="{file_name}"',
        "sizeBytes": _SIZES[platform],
    }


def _manifest() -> dict[str, object]:
    return {
        # These deliberately conflict with the selected macOS release. The updater
        # must use latestByPlatform.macos, not max semver or the root latest field.
        "releases": [{"version": "0.1.0"}],
        "latest": {"version": "99.0.0"},
        "latestByPlatform": {
            "macos": {
                "version": _VERSION,
                "gitSha": _COMMIT,
                "uploadedAt": "2026-08-14T21:34:56Z",
                "branch": "main",
                "tag": "",
                "platform": "macos",
                "arch": "multi",
                "artifacts": [_artifact(platform) for platform in _ARCHES],
            },
            "linux": {"version": "99.0.0"},
        },
    }


def _macos_release(payload: dict[str, object]) -> dict[str, object]:
    latest_by_platform = payload["latestByPlatform"]
    assert isinstance(latest_by_platform, dict)
    release = latest_by_platform["macos"]
    assert isinstance(release, dict)
    return release


def _artifacts(payload: dict[str, object]) -> list[object]:
    artifacts = _macos_release(payload)["artifacts"]
    assert isinstance(artifacts, list)
    return artifacts


def _expected_platform_info() -> dict[str, dict[str, object]]:
    return {
        platform: {
            "sha256": _HEX_HASHES[platform],
            "sizeBytes": _SIZES[platform],
            "url": _URLS[platform],
        }
        for platform in _ARCHES
    }


def test_tembo_pins_manifest_selected_immutable_per_architecture_dmgs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the authoritative macOS selector and vendor-provided checksums."""
    module = _load_module()
    updater = module.TemboUpdater()
    calls: list[tuple[str, object]] = []

    async def _fetch_json(_session: object, url: str, *, config: object) -> object:
        calls.append((url, config))
        return _manifest()

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    info = _run(updater.fetch_latest(object()))
    checksums = _run(updater.fetch_checksums(info, object()))
    result = updater.build_result(info, _SRI_HASHES)

    assert updater.MANIFEST_URL == _MANIFEST_URL
    assert updater.PLATFORMS == _ARCHES
    assert updater.supported_platforms == tuple(_ARCHES)
    assert info == VersionInfo(
        version=_VERSION,
        metadata=PlatformAPIMetadata(
            platform_info=_expected_platform_info(),
            equality_fields={},
            commit=_COMMIT,
        ),
    )
    assert checksums == _HEX_HASHES
    assert result.version == _VERSION
    assert result.commit == _COMMIT
    assert result.hashes.to_json() == _SRI_HASHES
    assert result.urls == _URLS
    assert calls == [(_MANIFEST_URL, updater.config)]


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("root", "expected object for manifest root"),
        ("latestByPlatform", "expected object for latestByPlatform"),
        ("macos", "expected object for latestByPlatform.macos"),
        ("artifacts", "expected array for .*artifacts"),
    ],
)
def test_tembo_rejects_malformed_manifest_containers(
    target: str,
    message: str,
) -> None:
    """Every container on the authoritative selection path must retain its type."""
    module = _load_module()
    payload: object = _manifest()
    assert isinstance(payload, dict)
    if target == "root":
        payload = []
    elif target == "latestByPlatform":
        payload[target] = []
    elif target == "macos":
        latest_by_platform = cast(
            "dict[str, object]",
            payload["latestByPlatform"],
        )
        latest_by_platform["macos"] = []
    else:
        _macos_release(payload)[target] = {}

    with pytest.raises(RuntimeError, match=message):
        module.TemboUpdater._parse_manifest(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("version", "", "expected non-empty string 'version'"),
        ("version", "latest", "invalid version"),
        ("gitSha", "A" * 40, "invalid gitSha"),
        ("uploadedAt", "2026-08-14", "invalid uploadedAt"),
        ("platform", "linux", "expected 'platform'='macos'"),
        ("arch", "arm64", "expected 'arch'='multi'"),
    ],
)
def test_tembo_rejects_ambiguous_macos_release_identity(
    field: str,
    value: object,
    message: str,
) -> None:
    """Version, commit, timestamp, platform, and release arch are mandatory."""
    module = _load_module()
    payload = _manifest()
    _macos_release(payload)[field] = value

    with pytest.raises(RuntimeError, match=message):
        module.TemboUpdater._parse_manifest(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("arch", "universal", "unsupported macOS arch"),
        ("platform", "linux", "expected 'platform'='macos'"),
        ("fileName", "Tembo-latest-arm64.dmg", "expected 'fileName'"),
        ("objectKey", "releases/latest/Tembo-arm64.dmg", "expected 'objectKey'"),
        (
            "path",
            f"{_ORIGIN}/releases/latest/macos/Tembo-arm64.dmg",
            "expected 'path'",
        ),
        (
            "latestObjectKey",
            "releases/latest/Tembo-arm64.dmg",
            "expected 'latestObjectKey'",
        ),
        ("latestPath", _URLS["aarch64-darwin"], "expected 'latestPath'"),
        ("contentType", "application/octet-stream", "expected 'contentType'"),
        ("contentDisposition", "inline", "expected 'contentDisposition'"),
        ("sha256", "F" * 64, "invalid SHA-256"),
        ("sha256", None, "expected non-empty string 'sha256'"),
        ("sizeBytes", True, "implausible DMG size"),
        ("sizeBytes", 1024, "implausible DMG size"),
    ],
)
def test_tembo_rejects_mutable_or_malformed_artifacts(
    field: str,
    value: object,
    message: str,
) -> None:
    """Only exact immutable official DMG metadata may reach sources.json."""
    module = _load_module()
    payload = _manifest()
    artifact = cast("dict[str, object]", _artifacts(payload)[0])
    artifact[field] = value

    with pytest.raises(RuntimeError, match=message):
        module.TemboUpdater._parse_manifest(payload)


def test_tembo_requires_exactly_one_artifact_per_supported_architecture() -> None:
    """Reject both partial and duplicate macOS artifact sets."""
    module = _load_module()
    partial = _manifest()
    _macos_release(partial)["artifacts"] = _artifacts(partial)[:1]

    with pytest.raises(RuntimeError, match="exactly one DMG"):
        module.TemboUpdater._parse_manifest(partial)

    duplicate = _manifest()
    _macos_release(duplicate)["artifacts"] = [
        _artifact("aarch64-darwin"),
        _artifact("aarch64-darwin"),
    ]

    with pytest.raises(RuntimeError, match="duplicate artifact"):
        module.TemboUpdater._parse_manifest(duplicate)


@pytest.mark.parametrize(
    ("transform", "operation", "message"),
    [
        (
            lambda metadata: PlatformAPIMetadata(
                platform_info={
                    "aarch64-darwin": metadata.platform_info["aarch64-darwin"]
                },
                equality_fields={},
                commit=_COMMIT,
            ),
            "checksums",
            "incomplete platform map",
        ),
        (
            lambda metadata: PlatformAPIMetadata(
                platform_info=metadata.platform_info,
                equality_fields={},
            ),
            "checksums",
            "missing or invalid commit",
        ),
        (
            lambda metadata: PlatformAPIMetadata(
                platform_info={
                    **metadata.platform_info,
                    "aarch64-darwin": {
                        **metadata.platform_info["aarch64-darwin"],
                        "sha256": None,
                    },
                },
                equality_fields={},
                commit=_COMMIT,
            ),
            "checksums",
            "missing 'sha256'",
        ),
        (
            lambda metadata: PlatformAPIMetadata(
                platform_info={
                    **metadata.platform_info,
                    "aarch64-darwin": {
                        **metadata.platform_info["aarch64-darwin"],
                        "url": None,
                    },
                },
                equality_fields={},
                commit=_COMMIT,
            ),
            "result",
            "missing 'url'",
        ),
    ],
)
def test_tembo_rejects_incomplete_internal_metadata(
    transform: Callable[[PlatformAPIMetadata], PlatformAPIMetadata],
    operation: str,
    message: str,
) -> None:
    """Do not persist a partial map if updater metadata is reused incorrectly."""
    module = _load_module()
    valid = module.TemboUpdater._parse_manifest(_manifest())
    metadata = expect_instance(valid.metadata, PlatformAPIMetadata)
    info = VersionInfo(version=_VERSION, metadata=transform(metadata))
    updater = module.TemboUpdater()

    def action() -> object:
        if operation == "checksums":
            return _run(updater.fetch_checksums(info, object()))
        return updater.build_result(info, _SRI_HASHES)

    with pytest.raises(RuntimeError, match=message):
        action()


def test_tembo_package_preserves_vendor_bundle_for_system_scope_ownership() -> None:
    """Use the no-fixup DMG helper without patching or re-signing the app."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/tembo/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    bindings = binding_map(arguments.values)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkSimpleDarwinApp"))
    assert_nix_ast_equal(
        expect_binding(arguments.values, "builder").value,
        Identifier(name="mkDmgApp"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "pname").value,
        StringPrimitive(value="tembo"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "appName").value,
        StringPrimitive(value="Tembo"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "info").value,
        Identifier(name="selfSource"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "dontFixup").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "platforms").value,
        '[ "aarch64-darwin" "x86_64-darwin" ]',
    )
    assert "codesignApp" not in bindings
    assert "postInstallApp" not in bindings
    assert "license" not in bindings
    assert "sourceProvenance" not in bindings


def test_tembo_sources_pin_current_vendor_manifest_release() -> None:
    """Keep both current immutable vendor DMGs and the release commit atomic."""
    sources = json.loads(
        (REPO_ROOT / "packages/tembo/sources.json").read_text(encoding="utf-8")
    )

    assert sources == {
        "commit": _COMMIT,
        "hashes": _SRI_HASHES,
        "urls": _URLS,
        "version": _VERSION,
    }
