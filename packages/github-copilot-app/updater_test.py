"""Focused tests for the GitHub Copilot desktop release updater."""

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._package_registry import registry_override_metadata
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import load_repo_module
from lib.update.derivation_validation import DerivationValidation
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

_PACKAGE_DIR = REPO_ROOT / "packages/github-copilot-app"
_VERSION = "1.1.14"
_ARM64_URL = (
    f"https://github.com/github/app/releases/download/v{_VERSION}/"
    "GitHub-Copilot-darwin-arm64.dmg"
)
_UNSUPPORTED_X64_URL = (
    f"https://github.com/github/app/releases/download/v{_VERSION}/"
    "GitHub-Copilot-darwin-x64.dmg"
)
_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_ARM64_HASH = "sha256-avOISG0qdkLdmmsXA3rAfXmOnCqeUsaBCGzfRkVpi9Q="
_ED25519_POINT_BYTES = 32
_ED25519_FIELD = 2**255 - 19
_ED25519_D = (-121665 * pow(121666, -1, _ED25519_FIELD)) % _ED25519_FIELD


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/github-copilot-app/updater.py",
        "github_copilot_app_updater_test",
    )


def _load_patch_module() -> ModuleType:
    return load_repo_module(
        "packages/github-copilot-app/patch_updater.py",
        "github_copilot_app_patch_test",
    )


def _reviewed_patch_fixture(module: ModuleType) -> bytes:
    return b"fixture:" + b"|".join(
        original * expected_count
        for _label, original, _disabled, expected_count in module.PATCHES
    )


def _is_valid_ed25519_point(encoded: bytes) -> bool:
    """Validate the compressed point without requiring a crypto test dependency."""
    if len(encoded) != _ED25519_POINT_BYTES:
        return False
    point = bytearray(encoded)
    x_sign = point[-1] >> 7
    point[-1] &= 0x7F
    y = int.from_bytes(point, "little")
    if y >= _ED25519_FIELD:
        return False
    y_squared = y * y % _ED25519_FIELD
    x_squared = (
        (y_squared - 1)
        * pow(_ED25519_D * y_squared + 1, -1, _ED25519_FIELD)
        % _ED25519_FIELD
    )
    if x_squared == 0:
        return x_sign == 0
    return pow(x_squared, (_ED25519_FIELD - 1) // 2, _ED25519_FIELD) == 1


def _release_payload() -> dict[str, object]:
    return {
        "tag_name": f"v{_VERSION}",
        "assets": [
            {
                "name": "GitHub-Copilot-darwin-x64.dmg",
                "browser_download_url": _UNSUPPORTED_X64_URL,
            },
            {
                "name": "checksums.txt",
                "browser_download_url": "https://example.invalid",
            },
            {
                "name": "GitHub-Copilot-darwin-arm64.dmg",
                "browser_download_url": _ARM64_URL,
            },
        ],
    }


def test_latest_release_resolves_only_the_supported_arm64_dmg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish only the architecture exported by this flake."""
    module = _load_module()
    updater = module.GitHubCopilotAppUpdater()
    calls: list[tuple[str, object]] = []

    async def _fetch(_session: object, endpoint: str, *, config: object) -> object:
        calls.append((endpoint, config))
        return _release_payload()

    monkeypatch.setattr("lib.update.updaters.github_release.fetch_github_api", _fetch)

    info = asyncio.run(updater.fetch_latest(object()))
    result = updater.build_result(info, dict.fromkeys(updater.PLATFORMS, _HASH))

    assert info == VersionInfo(
        version=_VERSION,
        metadata=AssetURLsMetadata(
            asset_urls={
                "aarch64-darwin": _ARM64_URL,
            },
        ),
    )
    assert result.version == _VERSION
    assert result.urls == {
        "aarch64-darwin": _ARM64_URL,
    }
    assert updater.PLATFORMS == {"aarch64-darwin": "arm64"}
    assert calls == [("repos/github/app/releases/latest", updater.config)]
    assert updater.derivation_validations == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )


def test_latest_release_requires_the_supported_arm64_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a release that omits the only supported architecture."""
    module = _load_module()
    updater = module.GitHubCopilotAppUpdater()
    payload = _release_payload()
    assets = payload["assets"]
    assert isinstance(assets, list)
    payload["assets"] = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and asset.get("name") != "GitHub-Copilot-darwin-arm64.dmg"
    ]

    async def _fetch(*_args: object, **_kwargs: object) -> object:
        return payload

    monkeypatch.setattr("lib.update.updaters.github_release.fetch_github_api", _fetch)

    with pytest.raises(
        RuntimeError,
        match="Could not find github-copilot-app release asset .*darwin-arm64",
    ):
        asyncio.run(updater.fetch_latest(object()))


def test_copilot_sources_pin_the_authoritative_latest_release() -> None:
    """Persist only the updater-produced arm64 pin."""
    assert json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8")) == {
        "hashes": {
            "aarch64-darwin": _ARM64_HASH,
        },
        "urls": {
            "aarch64-darwin": _ARM64_URL,
        },
        "version": _VERSION,
    }


def test_copilot_package_patches_then_resigns_the_exact_executable() -> None:
    """The realized app must disable updates before its replacement signature."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkSimpleDarwinApp"))
    assert_nix_ast_equal(
        expect_binding(arguments.values, "builder").value,
        Identifier(name="mkDmgApp"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "pname").value,
        StringPrimitive(value="github-copilot-app"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "dontFixup").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )

    post_install = expect_instance(
        expect_binding(arguments.values, "postInstallApp").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(post_install.rebuild()))
    assert command_texts(shell, "__NIX_INTERP__") == [
        '__NIX_INTERP__ __NIX_INTERP__ "$main_executable"',
        '__NIX_INTERP__ __NIX_INTERP__ --check "$main_executable"',
    ]
    assert command_texts(shell, "/usr/bin/codesign") == [
        "/usr/bin/codesign \\\n"
        "      --force \\\n"
        "      --deep \\\n"
        "      --sign - \\\n"
        "      --preserve-metadata=identifier,entitlements,flags,runtime \\\n"
        '      "$app_bundle"',
        '/usr/bin/codesign --verify --deep --strict "$app_bundle"',
    ]


def test_copilot_registry_exports_only_on_arm64_darwin() -> None:
    """Registry discovery must not expose the package on unsupported systems."""
    registry = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/registry.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    registry_output = expect_instance(registry.output, AttributeSet)

    assert registry_override_metadata(registry_output)["github-copilot-app"] == {
        "constraint": ["aarch64-darwin"]
    }


def test_copilot_patch_disables_acquisition_and_staged_install() -> None:
    """No update can be fetched, downloaded, or verified after transformation."""
    module = _load_patch_module()
    original = _reviewed_patch_fixture(module)

    patched = module.patch_payload(
        original,
        expected_sha256=hashlib.sha256(original).hexdigest(),
    )

    assert len(patched) == len(original)
    module.validate_disabled_payload(patched)
    for _label, vendor, disabled, expected_count in module.PATCHES:
        assert vendor not in patched
        assert patched.count(disabled) == expected_count

    vendor_key = base64.b64decode(module.VENDOR_PUBLIC_KEY)
    disabled_key = base64.b64decode(module.DISABLED_PUBLIC_KEY)
    assert vendor_key.startswith(b"untrusted comment: minisign public key:")
    assert disabled_key.startswith(b"untrusted comment: minisign public key:")
    assert vendor_key != disabled_key
    vendor_key_bytes = base64.b64decode(vendor_key.splitlines()[1], validate=True)
    disabled_key_bytes = base64.b64decode(disabled_key.splitlines()[1], validate=True)
    assert vendor_key_bytes[:2] == disabled_key_bytes[:2] == b"Ed"
    assert vendor_key_bytes[2:10] != disabled_key_bytes[2:10]
    assert _is_valid_ed25519_point(vendor_key_bytes[10:])
    assert _is_valid_ed25519_point(disabled_key_bytes[10:])


def test_copilot_patch_isolates_preexisting_tauri_staged_state() -> None:
    """A vendor build's already-staged payload and manifest must be undiscoverable."""
    module = _load_patch_module()
    vendor_stage_names = (b"staged-update.bin", b"staged-manifest.json")
    original = b"fixture:" + b"|".join(
        anchor * expected_count
        for _label, anchor, _disabled, expected_count in module.PATCHES
        if anchor not in vendor_stage_names
    )
    original += b"|" + b"|".join(vendor_stage_names)

    patched = module.patch_payload(
        original,
        expected_sha256=hashlib.sha256(original).hexdigest(),
    )

    assert b"staged-update.bin" not in patched
    assert b"staged-manifest.json" not in patched
    assert patched.count(b"nix-owned-upd.bin") == 1
    assert patched.count(b"nix-owned-state.json") == 1


@pytest.mark.parametrize("patch_index", range(6))
def test_copilot_patch_rejects_incomplete_vendor_inventory(patch_index: int) -> None:
    """Any missing acquisition or signature anchor must fail before mutation."""
    module = _load_patch_module()
    original = _reviewed_patch_fixture(module)
    _label, anchor, _disabled, _count = module.PATCHES[patch_index]
    drifted = original.replace(anchor, b"X" * len(anchor), 1)

    with pytest.raises(module.PatchError, match="inventory drifted"):
        module.patch_payload(
            drifted,
            expected_sha256=hashlib.sha256(drifted).hexdigest(),
        )


def test_copilot_patch_rejects_unreviewed_binary_digest() -> None:
    """Exact release executables require an explicitly reviewed digest."""
    module = _load_patch_module()
    original = _reviewed_patch_fixture(module)

    with pytest.raises(module.PatchError, match="SHA-256 drifted"):
        module.patch_payload(original, expected_sha256="0" * 64)

    assert (
        frozenset({
            "21b0f33962285782f0946f13780de5825ebb252e04ad9f0aff65a26608825dab",
        })
        == module.REVIEWED_EXECUTABLE_SHA256
    )


@pytest.mark.parametrize("restore_vendor_anchor", [True, False])
def test_copilot_post_sign_check_rejects_incomplete_suppression(
    restore_vendor_anchor: bool,
) -> None:
    """Post-sign validation must fail if either side of an anchor drifts."""
    module = _load_patch_module()
    original = _reviewed_patch_fixture(module)
    patched = module.patch_payload(
        original,
        expected_sha256=hashlib.sha256(original).hexdigest(),
    )
    _label, vendor, disabled, _expected_count = module.PATCHES[0]
    replacement = vendor if restore_vendor_anchor else b"X" * len(disabled)

    with pytest.raises(module.PatchError):
        module.validate_disabled_payload(patched.replace(disabled, replacement, 1))


def test_copilot_patch_file_preserves_mode_and_supports_post_sign_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The package hook must patch in place and revalidate its realized bytes."""
    module = _load_patch_module()
    executable = tmp_path / "github"
    original = _reviewed_patch_fixture(module)
    reviewed_digest = hashlib.sha256(original).hexdigest()
    executable.write_bytes(original)
    executable.chmod(0o751)

    with pytest.raises(module.PatchError, match="is not reviewed"):
        module.patch_file(executable)

    monkeypatch.setattr(
        module,
        "REVIEWED_EXECUTABLE_SHA256",
        frozenset({reviewed_digest}),
    )
    module.patch_file(executable)

    assert executable.stat().st_mode & 0o777 == 0o751
    module.validate_disabled_file(executable)
    assert module.main(["--check", str(executable)]) == 0
    assert module.main([str(tmp_path / "missing")]) == 1
