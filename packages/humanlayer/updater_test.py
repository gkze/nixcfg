"""Behavioral tests for the HumanLayer macOS package and updater."""

import base64
import hashlib
import json
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.expressions.with_statement import WithStatement

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.derivation_validation import DerivationValidation
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import DownloadUrlMetadata

_VERSION = "0.166.0"
_URL = (
    "https://github.com/humanlayer/homebrew-humanlayer/releases/download/"
    f"riptide-v{_VERSION}/Riptide-darwin-arm64.dmg"
)
_HASH = "sha256-nrZrQN51tY3PvBiDEslV8klpTl96kah+4fK4HK3IYlw="
_ED25519_POINT_BYTES = 32
_ED25519_FIELD = 2**255 - 19
_ED25519_D = (-121665 * pow(121666, -1, _ED25519_FIELD)) % _ED25519_FIELD
_VALID_CASK = f"""\
cask "humanlayer" do
  version "{_VERSION}"
  sha256 "9eb66b40de75b58dcfbc188312c955f249694e5f7a91a87ee1f2b81cadc8625c"
  url "{_URL}",
      verified: "github.com/humanlayer/homebrew-humanlayer/"
  app "HumanLayer.app"
  binary "#{{appdir}}/HumanLayer.app/Contents/Resources/bin/riptided", target: "riptided"
end
""".encode()


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/humanlayer/updater.py",
        "humanlayer_updater_dedicated_test",
    )


def _load_patch_module() -> ModuleType:
    return load_repo_module(
        "packages/humanlayer/patch_updater.py",
        "humanlayer_patch_test",
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


def test_humanlayer_resolves_stable_versioned_arm64_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the official cask rather than the mutable website download route."""
    module = _load_module()
    updater = module.HumanLayerUpdater()

    async def _fetch_url(_session: object, url: str, **kwargs: object) -> bytes:
        assert url == updater.CASK_URL
        assert kwargs["request_timeout"] == updater.config.default_timeout
        assert kwargs["config"] == updater.config
        return _VALID_CASK

    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    info = run_async(updater.fetch_latest(object()))
    result = updater.build_result(info, {"aarch64-darwin": _HASH})

    assert info == VersionInfo(
        version=_VERSION,
        metadata=DownloadUrlMetadata(url=_URL),
    )
    assert result.urls == {"aarch64-darwin": _URL}
    assert result.hashes.to_json() == {"aarch64-darwin": _HASH}
    assert updater.derivation_validations == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )


@pytest.mark.parametrize(
    "cask",
    [
        _VALID_CASK.replace(f'version "{_VERSION}"'.encode(), b'version "latest"'),
        _VALID_CASK.replace(b"9eb66b40", b"not-a-sha"),
        _VALID_CASK.replace(
            f"riptide-v{_VERSION}".encode(),
            b"riptide-v0.165.0",
        ),
        _VALID_CASK.replace(b"github.com/humanlayer", b"example.com/humanlayer", 1),
        _VALID_CASK.replace(b"Riptide-darwin-arm64.dmg", b"Riptide-darwin-x64.dmg", 1),
    ],
)
def test_humanlayer_rejects_malformed_or_untrusted_cask(cask: bytes) -> None:
    """Fail closed if version, checksum, host, or architecture stops matching."""
    module = _load_module()

    with pytest.raises(RuntimeError, match="Could not parse stable HumanLayer release"):
        module.HumanLayerUpdater._parse_cask(cask)


def test_humanlayer_package_disables_updates_resigns_and_links_daemon() -> None:
    """Patch the exact executable before signing and exposing its nested daemon."""
    sources = json.loads(
        (REPO_ROOT / "packages/humanlayer/sources.json").read_text(encoding="utf-8")
    )
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/humanlayer/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    args = expect_instance(derivation.argument, AttributeSet)
    bindings = binding_map(args.values)
    meta = expect_instance(expect_binding(args.values, "meta").value, WithStatement)
    meta_attrs = expect_instance(meta.body, AttributeSet)

    assert sources == {
        "hashes": {"aarch64-darwin": _HASH},
        "urls": {"aarch64-darwin": _URL},
        "version": _VERSION,
    }
    assert_nix_ast_equal(derivation.name, Identifier(name="mkDmgApp"))
    assert_nix_ast_equal(
        expect_binding(args.values, "pname").value,
        StringPrimitive(value="humanlayer"),
    )
    assert_nix_ast_equal(
        expect_binding(args.values, "appName").value,
        StringPrimitive(value="HumanLayer"),
    )
    assert_nix_ast_equal(
        expect_binding(args.values, "sourceName").value,
        StringPrimitive(value="Riptide-darwin-arm64.dmg"),
    )
    assert_nix_ast_equal(
        expect_binding(args.values, "dontFixup").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(args.values, "makeBinary").value,
        Primitive(value=False),
    )
    assert "codesignApp" not in bindings
    post_install = expect_instance(
        expect_binding(args.values, "postInstallApp").value,
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
    assert_nix_ast_equal(
        expect_binding(meta_attrs.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )
    assert_nix_ast_equal(
        expect_binding(meta_attrs.values, "mainProgram").value,
        StringPrimitive(value="riptided"),
    )


def test_humanlayer_patch_disables_acquisition_and_staged_install() -> None:
    """The transformed executable cannot acquire or verify a staged update."""
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

    disabled_endpoint = urlsplit(module.DISABLED_ENDPOINT.decode())
    assert disabled_endpoint.scheme == "https"
    assert disabled_endpoint.hostname == "nix-owned.invalid"
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


@pytest.mark.parametrize("patch_index", range(2))
def test_humanlayer_patch_rejects_incomplete_vendor_inventory(
    patch_index: int,
) -> None:
    """Endpoint or public-key drift must fail before any mutation."""
    module = _load_patch_module()
    original = _reviewed_patch_fixture(module)
    _label, anchor, _disabled, _count = module.PATCHES[patch_index]
    drifted = original.replace(anchor, b"X" * len(anchor), 1)

    with pytest.raises(module.PatchError, match="inventory drifted"):
        module.patch_payload(
            drifted,
            expected_sha256=hashlib.sha256(drifted).hexdigest(),
        )


def test_humanlayer_patch_rejects_unreviewed_binary_digest() -> None:
    """Only explicitly inspected release executables may cross the package seam."""
    module = _load_patch_module()
    original = _reviewed_patch_fixture(module)

    with pytest.raises(module.PatchError, match="SHA-256 drifted"):
        module.patch_payload(original, expected_sha256="0" * 64)

    assert (
        frozenset({
            "90ee2763cbd9d8ca8128ac1cfee08c92cd7eda77f43730d402abde422f0b8e55",
            "2a3032d9c7f1f5ddc383cb20e0a3d25eadf357b53a2a6a47db20731fc9950006",
        })
        == module.REVIEWED_EXECUTABLE_SHA256
    )


@pytest.mark.parametrize("restore_vendor_anchor", [True, False])
def test_humanlayer_post_sign_check_rejects_incomplete_suppression(
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


def test_humanlayer_patch_file_preserves_mode_and_supports_post_sign_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The package hook must patch in place and revalidate its realized bytes."""
    module = _load_patch_module()
    executable = tmp_path / "HumanLayer-Local"
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
