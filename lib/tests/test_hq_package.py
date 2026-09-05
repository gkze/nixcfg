"""Behavioral and structural contracts for the Nix-managed HQ app."""

import json
import os
import plistlib
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._nix_source import nix_file_expr, nix_source_fragment_expr
from lib.tests._package_registry import registry_override_metadata
from lib.tests._shell_ast import (
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
from lib.tests._updater_helpers import load_repo_module
from lib.update.paths import REPO_ROOT

_PACKAGE_DIR = REPO_ROOT / "packages/hq"
_VERSION = "0.10.191"


def _load_patch_module() -> ModuleType:
    return _load_hq_module("patch_updater.py", "hq_patch_test")


def _load_signature_policy_module() -> ModuleType:
    return load_repo_module(
        "packages/hq/validate_signatures.py",
        "hq_signature_policy_test",
    )


def _load_artifact_validator_module() -> ModuleType:
    return _load_hq_module("validate_artifact.py", "hq_artifact_validator_test")


def _load_hq_module(path: str, module_name: str) -> ModuleType:
    sys.path.insert(0, str(_PACKAGE_DIR))
    try:
        return load_repo_module(_PACKAGE_DIR / path, module_name)
    finally:
        sys.path.pop(0)


def _signature_details(*, flags: str = "adhoc,runtime") -> str:
    return (
        "Executable=/fixture\n"
        "Identifier=fixture\n"
        f"CodeDirectory v=20500 size=500 flags=0x10002({flags}) "
        "hashes=3+7 location=embedded\n"
    )


def _reviewed_entitlements() -> bytes:
    return plistlib.dumps({
        "com.apple.security.cs.allow-jit": True,
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
        "com.apple.security.cs.disable-library-validation": True,
        "com.apple.security.device.audio-input": True,
    })


def test_hq_patch_and_validator_share_the_reviewed_mutation_contract() -> None:
    """Both independent algorithms must consume one semantic byte inventory."""
    patcher = _load_patch_module()
    contract = sys.modules["policy_contract"]
    validator = _load_artifact_validator_module()

    assert patcher.UPDATER_URL is contract.UPDATER_URL
    assert patcher.RELEASES_URL is contract.RELEASES_URL
    assert patcher.DISABLED_UPDATER_URL is contract.DISABLED_UPDATER_URL
    assert patcher.DISABLED_RELEASES_URL is contract.DISABLED_RELEASES_URL
    assert patcher.AUTOMATIC_MUTATION_PATCHES is contract.AUTOMATIC_MUTATION_PATCHES
    assert patcher.UPDATER_URL_COUNT == contract.UPDATER_URL_COUNT
    assert patcher.RELEASES_URL_COUNT == contract.RELEASES_URL_COUNT
    assert validator.ORIGINAL_UPDATER_URL is contract.ORIGINAL_UPDATER_URL
    assert validator.ORIGINAL_RELEASES_URL is contract.ORIGINAL_RELEASES_URL
    assert validator.DISABLED_UPDATER_URL is contract.DISABLED_UPDATER_URL
    assert validator.DISABLED_RELEASES_URL is contract.DISABLED_RELEASES_URL
    assert validator.UPDATER_URL_COUNT == contract.UPDATER_URL_COUNT
    assert validator.RELEASES_URL_COUNT == contract.RELEASES_URL_COUNT
    assert validator.AUTOMATIC_MUTATION_PATCHES is contract.AUTOMATIC_MUTATION_PATCHES


def test_hq_signature_policy_requires_entitlements_only_on_executable_code() -> None:
    """Mach-O dylibs omit entitlement blobs but still require hardened runtime."""
    module = _load_signature_policy_module()

    module.validate_signature_evidence(
        label="main executable",
        entitlements_payload=_reviewed_entitlements(),
        details=_signature_details(),
        require_entitlements=True,
    )
    module.validate_signature_evidence(
        label="runtime dylib",
        entitlements_payload=b"",
        details=_signature_details(),
        require_entitlements=False,
    )

    with pytest.raises(ValueError, match="lacks required entitlements"):
        module.validate_signature_evidence(
            label="main executable",
            entitlements_payload=b"",
            details=_signature_details(),
            require_entitlements=True,
        )
    with pytest.raises(ValueError, match="lacks hardened runtime"):
        module.validate_signature_evidence(
            label="runtime dylib",
            entitlements_payload=b"",
            details=_signature_details(flags="adhoc"),
            require_entitlements=False,
        )


@pytest.mark.parametrize(
    ("entitlements_payload", "details", "expected_error"),
    [
        (b"not a plist", _signature_details(), "invalid HQ entitlements"),
        (
            plistlib.dumps({"unexpected": True}),
            _signature_details(),
            "unexpected HQ entitlements",
        ),
        (_reviewed_entitlements(), "", "0 CodeDirectory lines"),
        (
            _reviewed_entitlements(),
            _signature_details() + _signature_details(),
            "2 CodeDirectory lines",
        ),
        (
            _reviewed_entitlements(),
            "CodeDirectory v=20500 flags=0x10002 hashes=3+7\n",
            "lacks hardened runtime",
        ),
    ],
)
def test_hq_signature_policy_rejects_malformed_or_drifted_evidence(
    entitlements_payload: bytes,
    details: str,
    expected_error: str,
) -> None:
    """Signature inspection drift must fail closed with an actionable reason."""
    module = _load_signature_policy_module()

    with pytest.raises(ValueError, match=expected_error):
        module.validate_signature_evidence(
            label="reviewed HQ code",
            entitlements_payload=entitlements_payload,
            details=details,
            require_entitlements=True,
        )


def test_hq_artifact_validator_accepts_only_reviewed_metadata_and_update_patch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The realized app must preserve identity and contain only disabled URLs."""
    module = _load_artifact_validator_module()
    patcher = _load_patch_module()
    info_plist = tmp_path / "Info.plist"
    info_plist.write_bytes(
        plistlib.dumps({
            "CFBundleExecutable": "hq-sync-menubar",
            "CFBundleIdentifier": "ai.indigo.hq-sync-menubar",
            "CFBundleShortVersionString": _VERSION,
            "CFBundleVersion": _VERSION,
            "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True,
        })
    )
    executable = tmp_path / "hq-sync-menubar"
    original_payload = _reviewed_fixture(patcher)
    patched_payload = patcher.patch_payload(original_payload)
    executable.write_bytes(patched_payload)

    module.validate_artifact(
        info_plist=info_plist,
        main_executable=executable,
        expected_version=_VERSION,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_artifact.py", str(info_plist), str(executable), _VERSION],
    )
    module.main()

    drifted = plistlib.loads(info_plist.read_bytes())
    drifted["CFBundleIdentifier"] = "example.unreviewed"
    info_plist.write_bytes(plistlib.dumps(drifted))
    with pytest.raises(ValueError, match="CFBundleIdentifier expected"):
        module.validate_artifact(
            info_plist=info_plist,
            main_executable=executable,
            expected_version=_VERSION,
        )

    drifted["CFBundleIdentifier"] = "ai.indigo.hq-sync-menubar"
    info_plist.write_bytes(plistlib.dumps(drifted))
    executable.write_bytes(module.ORIGINAL_UPDATER_URL)
    with pytest.raises(ValueError, match="retains app-owned update URL"):
        module.validate_artifact(
            info_plist=info_plist,
            main_executable=executable,
            expected_version=_VERSION,
        )

    executable.write_bytes(module.DISABLED_UPDATER_URL * 7)
    with pytest.raises(ValueError, match="disabled update URL expected 8, got 7"):
        module.validate_artifact(
            info_plist=info_plist,
            main_executable=executable,
            expected_version=_VERSION,
        )

    original_offset = original_payload.index(_CURRENT_AUTOMATIC_MUTATION_PATHS[0])
    executable_with_original = bytearray(patched_payload)
    executable_with_original[
        original_offset : original_offset + len(_CURRENT_AUTOMATIC_MUTATION_PATHS[0])
    ] = _CURRENT_AUTOMATIC_MUTATION_PATHS[0]
    executable.write_bytes(executable_with_original)
    with pytest.raises(ValueError, match="retains automatic mutation path"):
        module.validate_artifact(
            info_plist=info_plist,
            main_executable=executable,
            expected_version=_VERSION,
        )

    missing_index = 3
    missing_offset = original_payload.index(
        _CURRENT_AUTOMATIC_MUTATION_PATHS[missing_index]
    )
    executable_without_disabled = bytearray(patched_payload)
    executable_without_disabled[
        missing_offset : missing_offset
        + len(_CURRENT_AUTOMATIC_MUTATION_PATHS[missing_index])
    ] = b"X" * len(_CURRENT_AUTOMATIC_MUTATION_PATHS[missing_index])
    executable.write_bytes(executable_without_disabled)
    with pytest.raises(
        ValueError,
        match="disabled automatic mutation signature arm64 hq-core install guard",
    ):
        module.validate_artifact(
            info_plist=info_plist,
            main_executable=executable,
            expected_version=_VERSION,
        )

    duplicate_offset = original_payload.index(_CURRENT_AUTOMATIC_MUTATION_PATHS[3])
    duplicate_length = len(_CURRENT_AUTOMATIC_MUTATION_PATHS[3])
    executable.write_bytes(
        patched_payload
        + patched_payload[duplicate_offset : duplicate_offset + duplicate_length]
    )
    with pytest.raises(
        ValueError,
        match="disabled automatic mutation signature arm64 hq-core install guard expected 1, got 2",
    ):
        module.validate_artifact(
            info_plist=info_plist,
            main_executable=executable,
            expected_version=_VERSION,
        )


def test_hq_artifact_validator_requires_secure_reserved_fail_closed_endpoints(
    tmp_path: Path,
) -> None:
    """Realized-artifact validation rejects the runtime-breaking file URLs."""
    module = _load_artifact_validator_module()
    patcher = _load_patch_module()
    info_plist = tmp_path / "Info.plist"
    info_plist.write_bytes(
        plistlib.dumps({
            "CFBundleExecutable": "hq-sync-menubar",
            "CFBundleIdentifier": "ai.indigo.hq-sync-menubar",
            "CFBundleShortVersionString": _VERSION,
            "CFBundleVersion": _VERSION,
            "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True,
        })
    )
    executable = tmp_path / "hq-sync-menubar"
    secure_updater_url = (
        b"https://updates.invalid/nix-managed-hq-updater-disabled/"
        b"nix-managed-no-update.json"
    )
    secure_releases_url = (
        b"https://updates.invalid/nix-managed-hq-release-index-disabled/nix-owned.json"
    )
    legacy_updater_url = (
        b"file:///nonexistent/nix-managed-hq-updater-disabled?"
        b"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    )
    legacy_releases_url = (
        b"file:///nonexistent/nix-managed-hq-release-index-disabled?xxxxxxxxxxxxxxxxxx"
    )
    patched_payload = patcher.patch_payload(_reviewed_fixture(patcher))
    executable.write_bytes(patched_payload)

    module.validate_artifact(
        info_plist=info_plist,
        main_executable=executable,
        expected_version=_VERSION,
    )

    executable.write_bytes(
        patched_payload.replace(secure_updater_url, legacy_updater_url).replace(
            secure_releases_url,
            legacy_releases_url,
        )
    )
    with pytest.raises(ValueError, match="disabled update URL expected 8, got 0"):
        module.validate_artifact(
            info_plist=info_plist,
            main_executable=executable,
            expected_version=_VERSION,
        )


def test_hq_artifact_validator_accepts_rebased_patch_output(tmp_path: Path) -> None:
    """Independent readback accepts dynamic branches produced after layout drift."""
    module = _load_artifact_validator_module()
    patcher = _load_patch_module()
    info_plist = tmp_path / "Info.plist"
    info_plist.write_bytes(
        plistlib.dumps({
            "CFBundleExecutable": "hq-sync-menubar",
            "CFBundleIdentifier": "ai.indigo.hq-sync-menubar",
            "CFBundleShortVersionString": _VERSION,
            "CFBundleVersion": _VERSION,
            "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True,
        })
    )
    executable = tmp_path / "hq-sync-menubar"
    executable.write_bytes(patcher.patch_payload(_rebased_fixture(patcher)))

    module.validate_artifact(
        info_plist=info_plist,
        main_executable=executable,
        expected_version=_VERSION,
    )


@pytest.mark.parametrize("displacement", [-38, 0x7FFFFFFF])
def test_hq_artifact_validator_rejects_drifted_disabled_control_flow(
    tmp_path: Path,
    displacement: int,
) -> None:
    """Readback rejects branches into the patch or beyond the executable."""
    module = _load_artifact_validator_module()
    patcher = _load_patch_module()
    info_plist = tmp_path / "Info.plist"
    info_plist.write_bytes(
        plistlib.dumps({
            "CFBundleExecutable": "hq-sync-menubar",
            "CFBundleIdentifier": "ai.indigo.hq-sync-menubar",
            "CFBundleShortVersionString": _VERSION,
            "CFBundleVersion": _VERSION,
            "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True,
        })
    )
    original_payload = _reviewed_fixture(patcher)
    executable_payload = bytearray(patcher.patch_payload(original_payload))
    guard_offset = original_payload.index(_CURRENT_AUTOMATIC_MUTATION_PATHS[2])
    executable_payload[guard_offset + 34 : guard_offset + 38] = displacement.to_bytes(
        4,
        byteorder="little",
        signed=True,
    )
    executable = tmp_path / "hq-sync-menubar"
    executable.write_bytes(executable_payload)

    with pytest.raises(ValueError, match="control flow drifted"):
        module.validate_artifact(
            info_plist=info_plist,
            main_executable=executable,
            expected_version=_VERSION,
        )


def test_hq_signature_inventory_accepts_executable_and_dylib_codesign_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The package-level inventory must apply the right policy to every entry."""
    module = _load_signature_policy_module()
    inventory = tmp_path / "machos"
    inventory.write_bytes(b"main executable\0runtime dylib\0")
    (tmp_path / "1.entitlements.plist").write_bytes(_reviewed_entitlements())
    (tmp_path / "1.details").write_text(_signature_details(), encoding="utf-8")
    (tmp_path / "1.requires-entitlements").touch()
    (tmp_path / "2.entitlements.plist").write_bytes(b"")
    (tmp_path / "2.details").write_text(_signature_details(), encoding="utf-8")
    (tmp_path / "app.entitlements.plist").write_bytes(_reviewed_entitlements())
    (tmp_path / "app.details").write_text(_signature_details(), encoding="utf-8")

    module.validate_signature_inventory(
        audit_root=tmp_path,
        inventory_path=inventory,
        expected_count=2,
        app_label="HQ.app",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_signatures.py", str(tmp_path), str(inventory), "2", "HQ.app"],
    )
    module.main()

    with pytest.raises(ValueError, match="expected 3, got 2"):
        module.validate_signature_inventory(
            audit_root=tmp_path,
            inventory_path=inventory,
            expected_count=3,
            app_label="HQ.app",
        )


_CURRENT_AUTOMATIC_MUTATION_PATHS = (
    bytes.fromhex(
        "55 48 89 e5 41 57 41 56 41 55 41 54 53 48 81 ec 98 00 00 00 48 8d 7d 90"
    ),
    bytes.fromhex(
        "ff 43 03 d1 f8 5f 09 a9 f6 57 0a a9 f4 4f 0b a9 "
        "fd 7b 0c a9 fd 03 03 91 e8 23 01 91"
    ),
    bytes.fromhex(
        "48 8d bb d0 00 00 00 e8 07 d8 51 00 "
        "48 8b bb d8 00 00 00 48 8b b3 e0 00 00 00 "
        "e8 04 d6 0d 01 84 c0 0f 84 7e 01 00 00 "
        "48 8d 83 61 01 00 00"
    ),
    # Captured from HQ 0.10.198: the guarded state address now uses x25.
    bytes.fromhex(
        "ed 64 1a 94 60 86 4d a9 e8 3e 39 94 e0 04 00 36 "
        "79 86 05 91 7f 86 05 39 76 62 05 91 39 00 00 14"
    ),
    bytes.fromhex(
        "48 8d bb 40 01 00 00 e8 5f 96 51 00 "
        "48 8b bb 48 01 00 00 48 8b b3 50 01 00 00 "
        "e8 5c 94 0d 01 84 c0 0f 84 00 03 00 00 "
        "48 8d bd 38 fc ff ff"
    ),
    bytes.fromhex(
        "79 0c 16 94 60 86 54 a9 23 e7 3a 94 40 13 00 34 "
        "e8 03 04 91 c0 de 56 94 f7 e3 50 a9 cd e0 56 94"
    ),
    bytes.fromhex(
        "48 8d 3d bc 1e d1 01 48 8d 15 bd 1f d1 01 "
        "be 0d 00 00 00 b9 4d 00 00 00 e8 32 83 11 01 "
        "e8 7d a8 52 00 eb 15"
    ),
    bytes.fromhex(
        "00 c4 00 d0 00 60 22 91 02 c4 00 d0 42 e0 26 91 "
        "a1 01 80 52 a3 09 80 52 93 2f 38 94 97 3b 16 94 "
        "07 00 00 14"
    ),
)

_CURRENT_DISABLED_MUTATION_PATHS = (
    bytes.fromhex("31 c0 c3") + (b"\x90" * 21),
    bytes.fromhex("00 00 80 52 c0 03 5f d6") + (bytes.fromhex("1f 20 03 d5") * 5),
    bytes.fromhex(
        "48 8d bb d0 00 00 00 e8 07 d8 51 00 "
        "48 8b bb d8 00 00 00 48 8b b3 e0 00 00 00 "
        "e8 04 d6 0d 01 84 c0 e9 7f 01 00 00 90 "
        "48 8d 83 61 01 00 00"
    ),
    bytes.fromhex(
        "ed 64 1a 94 60 86 4d a9 e8 3e 39 94 27 00 00 14 "
        "79 86 05 91 7f 86 05 39 76 62 05 91 39 00 00 14"
    ),
    bytes.fromhex(
        "48 8d bb 40 01 00 00 e8 5f 96 51 00 "
        "48 8b bb 48 01 00 00 48 8b b3 50 01 00 00 "
        "e8 5c 94 0d 01 84 c0 e9 01 03 00 00 90 "
        "48 8d bd 38 fc ff ff"
    ),
    bytes.fromhex(
        "79 0c 16 94 60 86 54 a9 23 e7 3a 94 9a 00 00 14 "
        "e8 03 04 91 c0 de 56 94 f7 e3 50 a9 cd e0 56 94"
    ),
    bytes.fromhex("e9 34 00 00 00") + (b"\x90" * 31),
    bytes.fromhex("0f 00 00 14") + (bytes.fromhex("1f 20 03 d5") * 8),
)

_REBASED_AUTOMATIC_MUTATION_PATHS = (
    _CURRENT_AUTOMATIC_MUTATION_PATHS[0],
    _CURRENT_AUTOMATIC_MUTATION_PATHS[1],
    bytes.fromhex(
        "48 8d bb c8 00 00 00 e8 17 d8 51 00 "
        "48 8b bb d0 00 00 00 48 8b b3 d8 00 00 00 "
        "e8 14 d6 0d 01 84 c0 0f 84 7d 01 00 00 "
        "48 8d 83 59 01 00 00"
    ),
    bytes.fromhex(
        "b2 18 16 94 60 86 4c a9 c0 a0 37 94 40 03 00 36 "
        "79 86 04 91 7f 86 05 39 76 62 05 91 2d 00 00 14"
    ),
    bytes.fromhex(
        "48 8d bb 38 01 00 00 e8 6f 96 51 00 "
        "48 8b bb 40 01 00 00 48 8b b3 48 01 00 00 "
        "e8 6c 94 0d 01 84 c0 0f 84 f4 04 00 00 "
        "48 8d bd 98 fc ff ff"
    ),
    bytes.fromhex(
        "89 0c 16 94 60 86 53 a9 5c 2a 3a 94 40 16 00 34 "
        "e8 03 08 91 d0 de 56 94 f7 e3 4f a9 dd e0 56 94"
    ),
    bytes.fromhex(
        "48 8d 3d 10 5a c1 01 48 8d 15 11 5b c1 01 "
        "be 0d 00 00 00 b9 4d 00 00 00 e8 a2 35 f7 00 "
        "e8 7d e1 41 00 eb 15"
    ),
    bytes.fromhex(
        "60 bd 00 d0 00 48 10 91 62 bd 00 d0 42 c8 14 91 "
        "a1 01 80 52 a3 09 80 52 f0 e9 38 94 b3 86 0a 94 "
        "07 00 00 14"
    ),
)


def _mutation_fixture(module: ModuleType, paths: tuple[bytes, ...]) -> bytes:
    return (
        b"hq-prefix\0"
        + (module.UPDATER_URL + b"\0") * module.UPDATER_URL_COUNT
        + b"hq-middle\0"
        + (module.RELEASES_URL + b"\0") * module.RELEASES_URL_COUNT
        + b"hq-automatic-mutation-paths\0"
        + b"\0".join(paths)
        + b"hq-suffix"
        + (b"\0" * 8192)
    )


def _reviewed_fixture(module: ModuleType) -> bytes:
    return _mutation_fixture(module, _CURRENT_AUTOMATIC_MUTATION_PATHS)


def _rebased_fixture(module: ModuleType) -> bytes:
    return _mutation_fixture(module, _REBASED_AUTOMATIC_MUTATION_PATHS)


def test_hq_binary_patch_accepts_rebased_machine_code() -> None:
    """Relocation-only layout drift must not require new byte fingerprints."""
    module = _load_patch_module()
    payload = _rebased_fixture(module)

    patched = module.patch_payload(payload)

    assert len(patched) == len(payload)
    assert module.UPDATER_URL not in patched
    assert module.RELEASES_URL not in patched


@pytest.mark.parametrize(
    ("patch_index", "opcode_index", "drifted_opcode"),
    [
        (2, 34, 0x85),
        (3, 16, 0x7A),
        (7, 35, 0x94),
    ],
)
def test_hq_binary_patch_rejects_opcode_drift(
    patch_index: int,
    opcode_index: int,
    drifted_opcode: int,
) -> None:
    """A different opcode or reviewed AArch64 register must fail closed."""
    module = _load_patch_module()
    paths = list(_CURRENT_AUTOMATIC_MUTATION_PATHS)
    drifted = bytearray(paths[patch_index])
    drifted[opcode_index] = drifted_opcode
    paths[patch_index] = bytes(drifted)

    with pytest.raises(ValueError, match="inventory drifted: expected 1, got 0"):
        module.patch_payload(_mutation_fixture(module, tuple(paths)))


@pytest.mark.parametrize("patch_index", [2, 3])
def test_hq_binary_patch_rejects_control_flow_into_mutation_block(
    patch_index: int,
) -> None:
    """Masked operands may move, but the reviewed branch must still skip the block."""
    module = _load_patch_module()
    paths = list(_CURRENT_AUTOMATIC_MUTATION_PATHS)
    drifted = bytearray(paths[patch_index])
    if patch_index == 2:
        drifted[35:39] = (-39).to_bytes(4, byteorder="little", signed=True)
    else:
        word = int.from_bytes(drifted[12:16], byteorder="little")
        word = (word & ~(0x3FFF << 5)) | (0x3FFD << 5)
        drifted[12:16] = word.to_bytes(4, byteorder="little")
    paths[patch_index] = bytes(drifted)

    with pytest.raises(ValueError, match="control flow drifted"):
        module.patch_payload(_mutation_fixture(module, tuple(paths)))


@pytest.mark.parametrize("patch_index", [2, 3])
def test_hq_binary_patch_rejects_control_flow_past_executable(
    patch_index: int,
) -> None:
    """A reviewed branch must land on a continuation inside the executable."""
    module = _load_patch_module()
    paths = list(_CURRENT_AUTOMATIC_MUTATION_PATHS)
    drifted = bytearray(paths[patch_index])
    if patch_index == 2:
        drifted[35:39] = (0x7FFFFFFF).to_bytes(4, byteorder="little")
    else:
        word = int.from_bytes(drifted[12:16], byteorder="little")
        word = (word & ~(0x3FFF << 5)) | (0x1FFF << 5)
        drifted[12:16] = word.to_bytes(4, byteorder="little")
    paths[patch_index] = bytes(drifted)

    with pytest.raises(ValueError, match="control flow drifted"):
        module.patch_payload(_mutation_fixture(module, tuple(paths)))


@pytest.mark.parametrize(
    ("architecture", "target", "message"),
    [
        ("x86_64", 1 << 40, "out of rel32 range"),
        ("aarch64", 1 << 30, "out of imm26 range"),
    ],
)
def test_hq_branch_encoder_rejects_unrepresentable_target(
    architecture: str,
    target: int,
    message: str,
) -> None:
    """The low-level encoder fails closed for independently supplied targets."""
    module = _load_patch_module()
    if architecture == "x86_64":
        replacement = bytearray(b"\xe9\0\0\0\0")
        branch = module.RelativeBranch(0, "x86-jmp-rel32")
    else:
        replacement = bytearray(bytes.fromhex("00 00 00 14"))
        branch = module.RelativeBranch(0, "aarch64-b-imm26")

    with pytest.raises(ValueError, match=message):
        module._encode_branch(
            replacement,
            base=0,
            target=target,
            branch=branch,
        )


def test_hq_binary_patch_disables_every_app_update_endpoint() -> None:
    """Patch the authenticated binary without changing its byte length."""
    module = _load_patch_module()
    payload = _reviewed_fixture(module)

    patched = module.patch_payload(payload)

    assert len(patched) == len(payload)
    assert module.UPDATER_URL not in patched
    assert module.RELEASES_URL not in patched
    assert patched.count(module.DISABLED_UPDATER_URL) == module.UPDATER_URL_COUNT
    assert patched.count(module.DISABLED_RELEASES_URL) == module.RELEASES_URL_COUNT


def test_hq_binary_patch_uses_secure_reserved_fail_closed_endpoints() -> None:
    """Tauri receives HTTPS URLs whose reserved host cannot serve an update."""
    module = _load_patch_module()
    payload = _reviewed_fixture(module)
    patched = module.patch_payload(payload)
    expected_replacements = (
        (
            b"https://updates.invalid/nix-managed-hq-updater-disabled/"
            b"nix-managed-no-update.json",
            module.UPDATER_URL_COUNT,
        ),
        (
            b"https://updates.invalid/nix-managed-hq-release-index-disabled/"
            b"nix-owned.json",
            module.RELEASES_URL_COUNT,
        ),
    )

    for replacement, expected_count in expected_replacements:
        parsed = urlsplit(replacement.decode("ascii"))
        assert parsed.scheme == "https"
        assert parsed.hostname == "updates.invalid"
        assert parsed.username is None
        assert parsed.password is None
        assert patched.count(replacement) == expected_count


def test_hq_binary_patch_disables_cli_and_core_automatic_mutations() -> None:
    """The reviewed CLI/core automatic mutation paths must become unreachable."""
    module = _load_patch_module()
    payload = _reviewed_fixture(module)

    patched = module.patch_payload(payload)

    assert len(patched) == len(payload)
    assert all(
        signature not in patched for signature in _CURRENT_AUTOMATIC_MUTATION_PATHS
    )
    assert all(
        patched.count(signature) == 1 for signature in _CURRENT_DISABLED_MUTATION_PATHS
    )


def _signed_field(value: int, width: int) -> int:
    sign_bit = 1 << (width - 1)
    return value - (1 << width) if value & sign_bit else value


def _aarch64_branch_target(
    instruction: bytes,
    *,
    pc: int,
    field_shift: int,
    field_width: int,
) -> int:
    word = int.from_bytes(instruction, byteorder="little")
    field = (word >> field_shift) & ((1 << field_width) - 1)
    return pc + (_signed_field(field, field_width) << 2)


def test_hq_disabled_branches_preserve_reviewed_control_flow_targets() -> None:
    """Every forced branch must retain the reviewed error or continuation target."""
    module = _load_patch_module()
    patches = {
        patch.label: (original, replacement)
        for patch, original, replacement in zip(
            module.AUTOMATIC_MUTATION_PATCHES,
            _CURRENT_AUTOMATIC_MUTATION_PATHS,
            _CURRENT_DISABLED_MUTATION_PATHS,
            strict=True,
        )
    }

    for label in (
        "x86_64 hq-core install guard",
        "x86_64 staging hq-core install guard",
    ):
        original, replacement = patches[label]
        original_target = 39 + int.from_bytes(
            original[35:39],
            byteorder="little",
            signed=True,
        )
        replacement_target = 38 + int.from_bytes(
            replacement[34:38],
            byteorder="little",
            signed=True,
        )
        assert replacement_target == original_target

    for label, field_width in (
        ("arm64 hq-core install guard", 14),
        ("arm64 staging hq-core install guard", 19),
    ):
        original, replacement = patches[label]
        assert replacement[:12] == original[:12]
        original_target = _aarch64_branch_target(
            original[12:16],
            pc=12,
            field_shift=5,
            field_width=field_width,
        )
        replacement_target = _aarch64_branch_target(
            replacement[12:16],
            pc=12,
            field_shift=0,
            field_width=26,
        )
        assert replacement_target == original_target

    original, replacement = patches["x86_64 CLI legacy-marker recovery"]
    original_target = len(original) + int.from_bytes(
        original[-1:],
        byteorder="little",
        signed=True,
    )
    replacement_target = 5 + int.from_bytes(
        replacement[1:5],
        byteorder="little",
        signed=True,
    )
    assert replacement_target == original_target

    original, replacement = patches["arm64 CLI legacy-marker recovery"]
    original_target = _aarch64_branch_target(
        original[-4:],
        pc=len(original) - 4,
        field_shift=0,
        field_width=26,
    )
    replacement_target = _aarch64_branch_target(
        replacement[:4],
        pc=0,
        field_shift=0,
        field_width=26,
    )
    assert replacement_target == original_target


@pytest.mark.parametrize(
    ("constant", "expected_error"),
    [
        ("DISABLED_UPDATER_URL", "updater replacement length"),
        ("DISABLED_RELEASES_URL", "release-index replacement length"),
    ],
)
def test_hq_binary_patch_rejects_mismatched_replacement_lengths(
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    expected_error: str,
) -> None:
    """A replacement may never move offsets in the authenticated Mach-O."""
    module = _load_patch_module()
    payload = _reviewed_fixture(module)
    monkeypatch.setattr(module, constant, getattr(module, constant) + b"!")

    with pytest.raises(RuntimeError, match=expected_error):
        module.patch_payload(payload)


@pytest.mark.parametrize("patch_index", range(8))
def test_hq_binary_patch_rejects_mismatched_code_replacement_lengths(
    monkeypatch: pytest.MonkeyPatch,
    patch_index: int,
) -> None:
    """Each architecture-specific code rewrite must preserve Mach-O offsets."""
    module = _load_patch_module()
    payload = _reviewed_fixture(module)
    patches = list(module.AUTOMATIC_MUTATION_PATCHES)
    patch = patches[patch_index]
    patches[patch_index] = replace(
        patch,
        disabled=replace(
            patch.disabled,
            pattern=patch.disabled.pattern + b"!",
        ),
    )
    monkeypatch.setattr(module, "AUTOMATIC_MUTATION_PATCHES", tuple(patches))

    with pytest.raises(RuntimeError, match=rf"{patch.label} replacement length"):
        module.patch_payload(payload)


def test_hq_binary_patch_rejects_incomplete_branch_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every target-preserving rewrite must define both branch operands."""
    module = _load_patch_module()
    patches = list(module.AUTOMATIC_MUTATION_PATCHES)
    patches[0] = replace(
        patches[0],
        original_branch=module.RelativeBranch(0, "x86-jmp-rel8"),
    )
    monkeypatch.setattr(module, "AUTOMATIC_MUTATION_PATCHES", tuple(patches))

    with pytest.raises(RuntimeError, match="branch contract is incomplete"):
        module.patch_payload(_reviewed_fixture(module))


@pytest.mark.parametrize("patch_index", [2, 7])
def test_hq_binary_patch_rejects_far_target_outside_executable(
    patch_index: int,
) -> None:
    """Large masked displacements cannot escape the executable boundary."""
    module = _load_patch_module()
    paths = list(_CURRENT_AUTOMATIC_MUTATION_PATHS)
    drifted = bytearray(paths[patch_index])
    if patch_index == 2:
        drifted[35:39] = ((1 << 31) - 1).to_bytes(
            4,
            byteorder="little",
            signed=True,
        )
    else:
        word = 0x14000000 | ((1 << 25) - 1)
        drifted[32:36] = word.to_bytes(4, byteorder="little")
    paths[patch_index] = bytes(drifted)

    with pytest.raises(ValueError, match="control flow drifted"):
        module.patch_payload(_mutation_fixture(module, tuple(paths)))


def test_hq_binary_patch_rejects_overlapping_semantic_patches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct policy mutations may never write the same executable range."""
    module = _load_patch_module()
    patches = list(module.AUTOMATIC_MUTATION_PATCHES)
    patches[1] = replace(
        patches[1],
        label="overlapping synthetic mutation",
        original=patches[0].original,
        disabled=patches[0].disabled,
    )
    monkeypatch.setattr(module, "AUTOMATIC_MUTATION_PATCHES", tuple(patches))

    with pytest.raises(ValueError, match="semantic patch ranges overlap"):
        module.patch_payload(_reviewed_fixture(module))


@pytest.mark.parametrize("patch_index", range(8))
@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_hq_binary_patch_rejects_code_signature_inventory_drift(
    patch_index: int,
    mutation: str,
) -> None:
    """The authenticated binary must contain each reviewed code seam once."""
    module = _load_patch_module()
    payload = _reviewed_fixture(module)
    patch = module.AUTOMATIC_MUTATION_PATCHES[patch_index]
    original = _CURRENT_AUTOMATIC_MUTATION_PATHS[patch_index]
    if mutation == "missing":
        payload = payload.replace(original, b"X" * len(original), 1)
    else:
        payload += _REBASED_AUTOMATIC_MUTATION_PATHS[patch_index]

    with pytest.raises(ValueError, match=rf"{patch.label} inventory drifted"):
        module.patch_payload(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        "updater-missing",
        "updater-duplicate",
        "releases-missing",
        "releases-duplicate",
    ],
)
def test_hq_binary_patch_rejects_ambiguous_url_inventories(
    mutation: str,
) -> None:
    """A future binary may not silently drift around the reviewed patch seam."""
    module = _load_patch_module()
    payload = _reviewed_fixture(module)
    if mutation == "updater-missing":
        payload = payload.replace(module.UPDATER_URL, b"X" * len(module.UPDATER_URL), 1)
    elif mutation == "updater-duplicate":
        payload += module.UPDATER_URL
    elif mutation == "releases-missing":
        payload = payload.replace(
            module.RELEASES_URL,
            b"X" * len(module.RELEASES_URL),
            1,
        )
    else:
        payload += module.RELEASES_URL

    if mutation.startswith("updater-"):
        expected_error = "updater URL inventory"
    else:
        expected_error = "release-index URL inventory"
    with pytest.raises(ValueError, match=expected_error):
        module.patch_payload(payload)


def test_hq_binary_patch_is_transactional(tmp_path: Path) -> None:
    """Validation failure must leave the executable byte-for-byte unchanged."""
    module = _load_patch_module()
    executable = tmp_path / "hq-sync-menubar"
    payload = _reviewed_fixture(module).replace(
        module.UPDATER_URL,
        b"X" * len(module.UPDATER_URL),
        1,
    )
    executable.write_bytes(payload)

    with pytest.raises(ValueError, match="updater URL inventory"):
        module.patch_file(executable)

    assert executable.read_bytes() == payload


def test_hq_binary_patch_atomically_preserves_executable_mode(tmp_path: Path) -> None:
    """Successful file patching must preserve mode and leave no temporary file."""
    module = _load_patch_module()
    executable = tmp_path / "hq-sync-menubar"
    payload = _reviewed_fixture(module)
    executable.write_bytes(payload)
    executable.chmod(0o751)

    module.patch_file(executable)

    assert executable.stat().st_mode & 0o777 == 0o751
    assert module.UPDATER_URL not in executable.read_bytes()
    assert module.RELEASES_URL not in executable.read_bytes()
    assert list(tmp_path.glob(".hq-sync-menubar.*")) == []


def test_hq_binary_patch_cleans_temporary_file_when_replace_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed atomic replacement must preserve the input and clean its temp file."""
    module = _load_patch_module()
    executable = tmp_path / "hq-sync-menubar"
    payload = _reviewed_fixture(module)
    executable.write_bytes(payload)

    def _fail_replace(_source: Path, _target: Path) -> Path:
        msg = "simulated atomic replacement failure"
        raise OSError(msg)

    monkeypatch.setattr(Path, "replace", _fail_replace)

    with pytest.raises(OSError, match="simulated atomic replacement failure"):
        module.patch_file(executable)

    assert executable.read_bytes() == payload
    assert list(tmp_path.glob(".hq-sync-menubar.*")) == []


def test_hq_binary_patch_cli_applies_the_semantic_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The package-facing CLI must apply the reviewed semantic patches."""
    module = _load_patch_module()
    executable = tmp_path / "hq-sync-menubar"
    payload = _reviewed_fixture(module)
    executable.write_bytes(payload)
    monkeypatch.setattr(
        sys,
        "argv",
        ["patch_updater.py", str(executable)],
    )

    module.main()

    assert module.UPDATER_URL not in executable.read_bytes()
    assert module.RELEASES_URL not in executable.read_bytes()


def _compile_launcher(tmp_path: Path, node_executable: Path) -> Path:
    launcher = tmp_path / "HQ.app/Contents/MacOS/recall-desktop-sdk"
    launcher.parent.mkdir(parents=True)
    subprocess.run(  # noqa: S603 -- test compiles a repository-owned C source
        [
            "/usr/bin/clang",
            "-Wall",
            "-Wextra",
            "-Werror",
            f'-DNODE_EXECUTABLE="{node_executable}"',
            str(_PACKAGE_DIR / "recall-launcher.c"),
            "-o",
            str(launcher),
        ],
        check=True,
    )
    return launcher


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="Darwin launcher")
def test_hq_recall_launcher_uses_absolute_node_and_preserves_arguments(
    tmp_path: Path,
) -> None:
    """Finder launches must not depend on PATH to start the Recall bridge."""
    output = tmp_path / "arguments.txt"
    node = tmp_path / "node"
    node.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$HQ_LAUNCHER_TEST_OUTPUT"\n',
        encoding="utf-8",
    )
    node.chmod(0o755)
    launcher = _compile_launcher(tmp_path, node)
    bridge = tmp_path / "HQ.app/Contents/Resources/recall-sdk-bridge/bridge.mjs"
    bridge.parent.mkdir(parents=True)
    bridge.write_text("// fixture\n", encoding="utf-8")

    env = os.environ | {"HQ_LAUNCHER_TEST_OUTPUT": str(output), "PATH": "/nonexistent"}
    subprocess.run(  # noqa: S603 -- test executes its freshly compiled fixture
        [launcher, "--json", "meeting"],
        check=True,
        env=env,
    )

    assert output.read_text(encoding="utf-8").splitlines() == [
        str(bridge),
        "--json",
        "meeting",
    ]


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="Darwin launcher")
def test_hq_recall_launcher_fails_closed_without_packaged_bridge(
    tmp_path: Path,
) -> None:
    """Do not fall back to ambient override paths or an ambient Node runtime."""
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    node.chmod(0o755)
    launcher = _compile_launcher(tmp_path, node)

    result = subprocess.run(  # noqa: S603 -- test executes its compiled fixture
        [launcher],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "packaged Recall bridge" in result.stderr


def test_hq_package_is_an_unfree_nix_owned_signed_app() -> None:
    """Expose the repaired official app only on arm64 Darwin."""
    sources = json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    version = sources["version"]
    artifact_name = f"HQ_{version}_universal.app.tar.gz"
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    assertion = expect_instance(package.output, Assertion)
    derivation = expect_instance(assertion.body, FunctionCall)
    args = expect_instance(derivation.argument, AttributeSet)
    bindings = binding_map(args.values)

    assert set(sources) == {"hashes", "urls", "version"}
    assert sources["urls"] == {
        "aarch64-darwin": (
            "https://github.com/indigoai-us/hq-desktop-app/releases/download/"
            f"v{version}/{artifact_name}"
        )
    }
    assert set(sources["hashes"]) == {"aarch64-darwin"}
    assert_nix_ast_equal(
        assertion.expression,
        'stdenv.hostPlatform.system == "aarch64-darwin"',
    )
    assert_nix_ast_equal(derivation.name, "stdenv.mkDerivation")
    assert_nix_ast_equal(
        expect_binding(args.values, "pname").value,
        StringPrimitive(value="hq"),
    )
    assert_nix_ast_equal(
        expect_binding(args.values, "dontFixup").value,
        Primitive(value=True),
    )
    assert "passthru" in bindings
    assert "meta" in bindings

    install_phase = expect_instance(
        expect_binding(args.values, "installPhase").value,
        IndentedString,
    )
    sign_commands = command_texts(
        parse_shell(indented_string_body(install_phase.rebuild())),
        "/usr/bin/codesign",
    )
    assert len(sign_commands) == 4
    assert all("--options runtime" in command for command in sign_commands)
    assert all("--entitlements __NIX_INTERP__" in command for command in sign_commands)

    install_check = expect_instance(
        expect_binding(args.values, "installCheckPhase").value,
        IndentedString,
    )
    install_check_shell = parse_shell(indented_string_body(install_check.rebuild()))
    install_check_commands = command_texts(
        install_check_shell,
        "/usr/bin/codesign",
    )
    assert (
        '/usr/bin/codesign -d --verbose=4 --entitlements :- "$candidate"'
        in install_check_commands
    )
    signature_cases = [
        node_text(node, install_check_shell.sanitized)
        for node in iter_nodes(install_check_shell.tree.root_node, "case_statement")
    ]
    assert any(
        "*executable*)" in case
        and ': > "$signatureAudit/$machoCount.requires-entitlements"' in case
        for case in signature_cases
    )
    package_validators = [
        command
        for command in command_texts(install_check_shell, "__NIX_INTERP__")
        if '"$infoPlist"' in command or '"$signatureAudit"' in command
    ]
    assert len(package_validators) == 2
    assert package_validators[0].replace("\\\n", " ").split() == [
        "PYTHONPATH=__NIX_INTERP__",
        "__NIX_INTERP__",
        "__NIX_INTERP__",
        '"$infoPlist"',
        '"$mainExecutable"',
        '"__NIX_INTERP__"',
    ]
    assert package_validators[1].replace("\\\n", " ").split() == [
        "__NIX_INTERP__",
        "__NIX_INTERP__",
        '"$signatureAudit"',
        '"$signedMachoInventory"',
        '"$machoCount"',
        '"$app"',
    ]

    passthru = expect_instance(
        expect_binding(args.values, "passthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "macApp").value,
        """
        {
          bundleId = "ai.indigo.hq-sync-menubar";
          bundleName = "HQ.app";
          bundleRelPath = "Applications/HQ.app";
          installMode = "copy";
        }
        """,
    )


def test_hq_registry_exports_only_on_arm64_darwin() -> None:
    """Discovery and platform metadata must make the completed app reachable."""
    registry = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/registry.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    registry_output = expect_instance(registry.output, AttributeSet)
    assert registry_override_metadata(registry_output)["hq"] == {
        "constraint": ["aarch64-darwin"]
    }


def test_hq_overlay_and_system_route_replace_the_unmanaged_app() -> None:
    """The completed package must own the existing system application path."""
    overlay = expect_instance(
        nix_file_expr("overlays/binary-darwin-apps.nix"),
        FunctionDefinition,
    )
    exports = expect_instance(overlay.output, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(exports.values, "hq").value,
        'callDarwinAppPackage "hq"',
    )

    routing = expect_instance(
        nix_source_fragment_expr(
            "home/george/work.nix",
            "  routing = ",
            ";\n  projection =",
        ),
        AttributeSet,
    )
    hq_route = expect_instance(
        expect_binding(routing.values, "hq").value,
        FunctionCall,
    )
    assert_nix_ast_equal(hq_route.name, "systemApp")
    assert hq_route.argument is not None
    assert_nix_ast_equal(hq_route.argument, "pkgs.hq")
