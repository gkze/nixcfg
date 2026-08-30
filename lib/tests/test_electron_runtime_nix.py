"""Contracts for the updater-owned shared Electron runtime inventory."""

import base64
import binascii
import json
import subprocess

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition

from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_eval import nix_attrset, nix_eval_json, nix_import
from lib.tests._nix_source import nix_file_binding_expr, nix_file_expr
from lib.update.paths import REPO_ROOT

_ARTIFACTS = {
    "headers",
    "aarch64-darwin",
    "aarch64-linux",
    "x86_64-darwin",
    "x86_64-linux",
}
_PACKAGE_DIR = REPO_ROOT / "packages" / "electron-runtimes"
_HARNESS = REPO_ROOT / "tests" / "nix" / "electron-runtime-inventory.nix"


def _load_policy_versions() -> list[str]:
    payload = json.loads((_PACKAGE_DIR / "versions.json").read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    return payload["versions"]


def _load_source() -> SourceEntry:
    payload = json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    return SourceEntry.model_validate(payload)


def _inventory_by_version(source: SourceEntry) -> dict[str, dict[str, str]]:
    assert source.hashes.entries is not None
    inventory: dict[str, dict[str, str]] = {}
    for entry in source.hashes.entries:
        assert entry.hash_type == "sha256"
        assert entry.platform is not None
        version, separator, artifact = entry.platform.partition(":")
        assert separator == ":"
        version_hashes = inventory.setdefault(version, {})
        assert artifact not in version_hashes
        version_hashes[artifact] = entry.hash
    return inventory


def _harness_expression(*, inventory: object | None = None) -> FunctionCall:
    arguments = {} if inventory is None else {"inventory": inventory}
    return FunctionCall(
        name=nix_import(_HARNESS),
        argument=nix_attrset(arguments),
    )


def test_electron_runtime_hashes_are_valid_sha256_sris() -> None:
    """Require every persisted Electron artifact hash to decode to SHA-256."""
    source = _load_source()
    assert source.hashes.entries is not None

    for entry in source.hashes.entries:
        algorithm, separator, encoded = entry.hash.partition("-")
        assert (algorithm, separator) == ("sha256", "-")
        try:
            digest = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            message = f"{entry.platform}: invalid base64 SRI digest"
            raise AssertionError(message) from error
        assert len(digest) == 32, (
            f"{entry.platform}: expected a 32-byte SHA-256 digest, "
            f"got {len(digest)} bytes"
        )


def test_electron_runtime_policy_is_completely_materialized() -> None:
    """Keep every exact policy version aligned with five immutable artifacts."""
    versions = _load_policy_versions()
    inventory = _inventory_by_version(_load_source())

    assert set(inventory) == set(versions)
    assert len(versions) == len(set(versions))
    assert all(
        set(version_hashes) == _ARTIFACTS for version_hashes in inventory.values()
    )
    assert inventory["41.7.0"]["headers"] == (
        "sha256-/8zpNnFpMBN83RDs94SOCW1i/Rht7huivDVOpyRMvQQ="
    )
    assert inventory["42.0.1"]["aarch64-darwin"] == (
        "sha256-DtNyFEeOSeKGW//Wht6jgzW8RgZxae45pcFljQc0gG4="
    )
    assert inventory["43.4.0"] == {
        "headers": "sha256-VPjc6ixDtFrIhUpZNAeSGg1Vx9UYbAkGrUnWlImtJdY=",
        "aarch64-darwin": "sha256-gn+fGCVm9GhGN3V1tRxUe5kmsRFjcxOjc7b3F0Yq66w=",
        "aarch64-linux": "sha256-FwIdSHOYVxBqJt2Vv3Sflbia6SSVXDx+f/Wj8GJRrBQ=",
        "x86_64-darwin": "sha256-erOewbC89UY/LcAEAUL7wcMM17w/mQhgZvWIxxexHiQ=",
        "x86_64-linux": "sha256-fF95GLyudKBagUVDlA6yhGnAVe2qPPz0HQ/xeHsxTFI=",
    }


def test_flake_insecure_policy_reuses_the_updater_owned_inventory() -> None:
    """The evaluator policy must not duplicate exact Electron versions."""
    assert_nix_ast_equal(
        nix_file_binding_expr("flake.nix", "electronRuntimePolicy"),
        "builtins.fromJSON (builtins.readFile ./packages/electron-runtimes/versions.json)",
    )
    assert_nix_ast_equal(
        nix_file_binding_expr("flake.nix", "electronRuntimeVersions"),
        """
        assert electronRuntimePolicy.schemaVersion == 1;
        electronRuntimePolicy.versions
        """,
    )
    assert_nix_ast_equal(
        nix_file_binding_expr("flake.nix", "allowInsecurePredicate"),
        """
        pkg:
        let
          pname = pkg.pname or "";
          version = pkg.version or "";
        in
        pname == "google-chrome"
        || (pname == "electron" && builtins.elem version electronRuntimeVersions)
        """,
    )


def test_electron_overlay_reconstructs_the_updater_inventory() -> None:
    """Use Nix because grouping dynamic attr keys cannot be proven from one AST."""
    result = nix_eval_json(_harness_expression())
    inventory = _inventory_by_version(_load_source())

    assert result == {
        "allVersions": _load_policy_versions(),
        "hashes": inventory,
    }


def test_electron_overlay_rejects_an_incomplete_runtime() -> None:
    """Use Nix because AST checks cannot prove a lazy fail-closed branch is forced."""
    source_payload = _load_source().to_dict()
    hashes = source_payload["hashes"]
    assert isinstance(hashes, list)
    source_payload["hashes"] = hashes[1:]

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        nix_eval_json(_harness_expression(inventory=source_payload))

    assert "incomplete runtime 38.7.2" in exc_info.value.stderr


def test_github_desktop_selects_its_updater_owned_runtime() -> None:
    """Keep the consumer wired to source metadata represented in the inventory."""
    source_payload = json.loads(
        (REPO_ROOT / "overlays" / "github-desktop" / "sources.json").read_text(
            encoding="utf-8"
        )
    )
    source = SourceEntry.model_validate(source_payload)
    assert source.electron_version in _load_policy_versions()

    overlay = expect_instance(
        nix_file_expr("overlays/github-desktop/default.nix"),
        FunctionDefinition,
    )
    electron_runtime = expect_binding(
        overlay.output.scope,
        "electronRuntime",
    ).value
    assert_nix_ast_equal(
        electron_runtime,
        "final.nixcfgElectron.runtimeFor electronVersion",
    )


def test_hermes_desktop_selects_its_source_owned_runtime() -> None:
    """Do not duplicate the pinned input manifest's Electron version in Nix."""
    package = expect_instance(
        nix_file_expr("packages/hermes-desktop/default.nix"),
        FunctionDefinition,
    )
    electron_version_check = expect_binding(
        package.output.scope,
        "electronVersionCheck",
    ).value
    assert_nix_ast_equal(
        electron_version_check,
        """
        if electronRuntimeVersion == electronVersion then
          true
        else
          throw ''
            packages/hermes-desktop/default.nix needs an exact Electron runtime
            matching the selected source, but got ${electronVersion}/${electronRuntimeVersion}
          ''
        """,
    )
