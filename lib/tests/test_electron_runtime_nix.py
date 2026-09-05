"""Contracts for the updater-owned shared Electron runtime inventory."""

import asyncio
import base64
import binascii
import json
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest
from nix_manipulator import parse
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.select import Select

from lib.nix.commands import run_nix, run_nix_json
from lib.nix.models.sources import HashCollection, SourceEntry
from lib.system_policy import electron_artifact_tags
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._nix_eval import nix_attrset, nix_eval_json, nix_import
from lib.tests._nix_source import nix_file_binding_expr, nix_file_expr
from lib.update import sources as update_sources
from lib.update.constants import FAKE_HASH
from lib.update.nix import _build_package_path_attr_expr
from lib.update.paths import REPO_ROOT
from lib.update.planner import aggregate_source_members
from lib.update.updaters import UpdateContext, ensure_updaters_loaded
from lib.update.updaters.metadata import metadata_as_mapping

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression
    from tree_sitter import Node

_ARTIFACT_TAGS = electron_artifact_tags()
_ARTIFACTS = {"headers", *_ARTIFACT_TAGS}
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


def _artifact_url(version: str, artifact: str) -> str:
    if artifact == "headers":
        return (
            "https://artifacts.electronjs.org/headers/dist/"
            f"v{version}/node-v{version}-headers.tar.gz"
        )
    return (
        "https://github.com/electron/electron/releases/download/"
        f"v{version}/electron-v{version}-{_ARTIFACT_TAGS[artifact]}.zip"
    )


def _harness_expression(
    *,
    inventory: object | None = None,
    source_overrides: object | None = None,
    fake_hash_mode: bool | None = None,
    target_system: str | None = None,
    runtime_version: str | None = None,
) -> FunctionCall:
    arguments = {}
    if inventory is not None:
        arguments["inventoryJson"] = json.dumps(inventory, sort_keys=True)
    if source_overrides is not None:
        arguments["sourceOverrides"] = source_overrides
    if fake_hash_mode is not None:
        arguments["fakeHashMode"] = fake_hash_mode
    if target_system is not None:
        arguments["targetSystem"] = target_system
    if runtime_version is not None:
        arguments["runtimeVersion"] = runtime_version
    return FunctionCall(
        name=nix_import(_HARNESS),
        argument=nix_attrset(arguments),
    )


def test_electron_runtime_hashes_and_urls_match_the_system_policy() -> None:
    """Require every persisted Electron artifact identity to match shared policy."""
    source = _load_source()
    assert source.hashes.entries is not None
    assert source.urls is not None
    hash_keys: set[str] = set()

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
        assert entry.platform is not None
        version, separator, artifact = entry.platform.partition(":")
        assert separator == ":"
        hash_keys.add(entry.platform)
        assert source.urls[entry.platform] == _artifact_url(version, artifact)

    assert set(source.urls) == hash_keys


def test_electron_runtime_policy_is_completely_materialized() -> None:
    """Keep every exact version aligned with all policy-required artifacts."""
    versions = _load_policy_versions()
    inventory = _inventory_by_version(_load_source())

    assert set(inventory) == set(versions)
    assert len(versions) == len(set(versions))
    assert all(
        set(version_hashes) == _ARTIFACTS for version_hashes in inventory.values()
    )


def _select_path(value: NixExpression) -> tuple[str, ...] | None:
    while isinstance(value, Parenthesis):
        value = value.value
    attribute_paths: list[tuple[str, ...]] = []
    while isinstance(value, Select) and value.default is None:
        attribute_paths.append(tuple(value.attribute.split(".")))
        value = value.expression
    if not isinstance(value, Identifier):
        return None
    return (
        value.name,
        *(part for path in reversed(attribute_paths) for part in path),
    )


def _electron_runtime_consumer_names() -> set[str]:
    consumers: set[str] = set()
    for root_name in ("packages", "overlays"):
        root = REPO_ROOT / root_name
        for path in root.rglob("*.nix"):
            relative_path = path.relative_to(root)
            if len(relative_path.parts) < 2:
                continue
            source = path.read_text(encoding="utf-8")
            parsed = parse(source)
            encoded = source.encode()
            calls_runtime = False

            def _visit(node: Node, encoded_source: bytes = encoded) -> None:
                nonlocal calls_runtime
                if node.type == "select_expression":
                    selection = parse_nix_expr(
                        encoded_source[node.start_byte : node.end_byte].decode()
                    )
                    calls_runtime = calls_runtime or (
                        (_select_path(selection) or ())[-2:]
                        in {
                            ("nixcfgElectron", "runtimeFor"),
                            ("nixcfgElectron", "sourceBuildFor"),
                        }
                    )
                for child in node.named_children:
                    _visit(child)

            _visit(parsed.node)
            if calls_runtime:
                consumers.add(relative_path.parts[0])
    consumers.discard("electron-runtimes")
    return consumers


def test_runtime_aggregate_declares_every_structural_nix_consumer() -> None:
    """Make adding a Nix Electron consumer require aggregate policy coverage."""
    declared = set(
        aggregate_source_members(ensure_updaters_loaded(), "electron-runtimes")
    )

    assert declared == _electron_runtime_consumer_names()


def test_runtime_aggregate_accepts_every_persisted_consumer_source() -> None:
    """Keep generated consumer metadata complete and aligned with the inventory."""
    updaters = ensure_updaters_loaded()
    info = asyncio.run(
        updaters["electron-runtimes"]().fetch_latest(
            object(),
            context=UpdateContext(
                current=_load_source(),
                effective_sources=update_sources.load_all_sources().entries,
            ),
        )
    )
    metadata = metadata_as_mapping(
        info.metadata,
        context="persisted Electron runtime inventory",
    )

    assert metadata["versions"] == _load_policy_versions()


def test_persisted_runtime_inventory_is_current_for_policy_urls() -> None:
    """Keep an unchanged update from needlessly rehashing every runtime artifact."""
    updater = ensure_updaters_loaded()["electron-runtimes"]()
    current = _load_source()
    info = asyncio.run(
        updater.fetch_latest(
            object(),
            context=UpdateContext(
                current=current,
                effective_sources=update_sources.load_all_sources().entries,
            ),
        )
    )

    assert asyncio.run(updater._is_latest(current, info)) is True


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


@pytest.mark.parametrize("target_system", ["aarch64-darwin", "x86_64-darwin"])
def test_electron_runtime_build_uses_the_persisted_policy_url(
    target_system: str,
) -> None:
    """Use Nix because AST checks cannot resolve selected runtime source URLs."""
    version = _load_policy_versions()[-1]
    result = nix_eval_json(
        _harness_expression(
            target_system=target_system,
            runtime_version=version,
        )
    )

    assert isinstance(result, dict)
    runtime = expect_instance(result["runtime"], dict)
    source = expect_instance(runtime["src"], dict)
    passthru = expect_instance(runtime["passthru"], dict)
    headers = expect_instance(passthru["headers"], dict)
    assert source["url"] == _artifact_url(version, target_system)
    assert headers["url"] == _artifact_url(version, "headers")


def test_electron_overlay_rejects_a_url_outside_the_system_policy() -> None:
    """Use Nix because AST checks cannot prove the URL assertion is forced."""
    source_payload = _load_source().to_dict()
    urls = expect_instance(source_payload["urls"], dict)
    first_key = next(iter(urls))
    urls[first_key] = "https://example.invalid/stale-electron-artifact.zip"

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        nix_eval_json(_harness_expression(inventory=source_payload))

    assert "URL does not match system policy" in exc_info.value.stderr


def test_electron_overlay_synthesizes_only_update_candidate_versions() -> None:
    """Use Nix because AST checks cannot resolve dynamic candidate artifacts."""
    candidate_version = "99.1.2"
    result = nix_eval_json(
        _harness_expression(
            source_overrides={
                "candidate": {
                    "electronVersion": candidate_version,
                    "hashes": [],
                }
            }
        )
    )
    assert isinstance(result, dict)
    hashes = result["hashes"]
    assert isinstance(hashes, dict)
    assert hashes[candidate_version] == dict.fromkeys(
        _ARTIFACTS,
        FAKE_HASH,
    )


def test_electron_overlay_backfills_a_new_artifact_for_an_existing_candidate() -> None:
    """Use Nix because AST checks cannot resolve a dynamic artifact backfill."""
    source_payload = _load_source().to_dict()
    hashes = expect_instance(source_payload["hashes"], list)
    first_hash = expect_instance(hashes[0], dict)
    platform = expect_instance(first_hash["platform"], str)
    candidate_version, separator, missing_artifact = platform.partition(":")
    assert separator == ":"
    source_payload["hashes"] = [
        entry
        for entry in hashes
        if expect_instance(expect_instance(entry, dict)["platform"], str) != platform
    ]

    result = nix_eval_json(
        _harness_expression(
            inventory=source_payload,
            source_overrides={
                "candidate": {
                    "electronVersion": candidate_version,
                    "hashes": [],
                }
            },
            fake_hash_mode=True,
        )
    )

    assert isinstance(result, dict)
    candidate_hashes = expect_instance(result["hashes"], dict)[candidate_version]
    assert isinstance(candidate_hashes, dict)
    assert candidate_hashes[missing_artifact] == FAKE_HASH
    assert set(candidate_hashes) == _ARTIFACTS


def test_electron_overlay_rejects_legacy_pin_metadata() -> None:
    """Use Nix because AST checks cannot prove the legacy-pin branch is forced."""
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        nix_eval_json(
            _harness_expression(
                source_overrides={
                    "legacy": {
                        "hashes": [],
                        "pins": {"electronVersion": "99.1.2"},
                    }
                }
            )
        )

    assert "legacy pins.electronVersion" in exc_info.value.stderr


def test_absent_runtime_candidate_dependency_probe_does_not_depend_on_electron() -> (
    None
):
    """Use Nix because only a realized derivation graph proves probe laziness."""
    candidate_version = "99.1.2"
    assert candidate_version not in _load_policy_versions()
    source_payload = json.loads(
        (REPO_ROOT / "packages" / "emdash" / "sources.json").read_text(encoding="utf-8")
    )
    current = SourceEntry.model_validate(source_payload)
    candidate = current.model_copy(
        update={
            "hashes": HashCollection(entries=[]),
            "electron_version": candidate_version,
        }
    )
    expression = _build_package_path_attr_expr(
        "emdash",
        ".pnpmDeps",
        system="aarch64-darwin",
        source_overrides={"emdash": candidate},
        fake_hashes=True,
    )
    nix_instantiate = shutil.which("nix-instantiate")
    nix = shutil.which("nix")
    assert nix_instantiate is not None
    assert nix is not None
    instantiated = asyncio.run(
        run_nix(
            [nix_instantiate, "--impure", "--expr", expression],
            command_timeout=30,
        )
    )
    derivation = instantiated.stdout.strip()
    payload = asyncio.run(
        run_nix_json(
            [nix, "derivation", "show", derivation],
            command_timeout=30,
        )
    )
    payload_mapping = expect_instance(payload, dict)
    derivations = expect_instance(
        payload_mapping.get("derivations", payload_mapping),
        dict,
    )
    record = expect_instance(next(iter(derivations.values())), dict)
    inputs = record.get("inputDrvs")
    if inputs is None:
        record_inputs = expect_instance(record["inputs"], dict)
        inputs = record_inputs["drvs"]
    inputs = expect_instance(inputs, dict)
    outputs = expect_instance(record["outputs"], dict)
    output = expect_instance(outputs["out"], dict)

    assert output["hash"] == FAKE_HASH
    assert all("electron" not in name.lower() for name in inputs)


def test_electron_overlay_rejects_an_incomplete_runtime() -> None:
    """Use Nix because AST checks cannot prove a lazy fail-closed branch is forced."""
    source_payload = _load_source().to_dict()
    hashes = source_payload["hashes"]
    assert isinstance(hashes, list)
    first_hash = expect_instance(hashes[0], dict)
    platform = expect_instance(first_hash["platform"], str)
    incomplete_version, separator, _artifact = platform.partition(":")
    assert separator == ":"
    source_payload["hashes"] = hashes[1:]

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        nix_eval_json(_harness_expression(inventory=source_payload))

    assert f"incomplete runtime {incomplete_version}" in exc_info.value.stderr


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
