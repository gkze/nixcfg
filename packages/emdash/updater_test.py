"""Behavioral tests for Emdash's release-derived source contracts."""

import json
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import SourceEntry
from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.locked_source import LockedSource
from lib.update.updaters import VersionInfo
from lib.update.updaters.node_compatibility import NodejsSelection

_VERSION = "1.2.3"
_REF = f"v{_VERSION}"
_COMMIT = "a" * 40
_ELECTRON_SPEC = "^42.1.0"
_ELECTRON_VERSION = "42.3.4"
_NODE_ENGINE = ">=24.0.0"
_NODEJS_VERSION = "24.19.0"
_PACKAGE_MANAGER = "pnpm@10.28.2"
_PNPM_ENGINE = ">=10.28.0"
_PNPM_VERSION = "10.34.5"
_SHELL_ENV_CAPTURE_PATH = "packages/core/src/runtime/capture.ts"
_SHELL_ENV_DELEGATE_PATH = "apps/emdash-desktop/src/main/lib/userEnv.ts"
_INTERACTIVE_SHELL_ENV_PROBE = "spawnSync(shell, ['-ilc', 'env'], shellEnvProbeOptions)"


@pytest.fixture(scope="module")
def updater_module() -> ModuleType:
    """Load the updater from its repository path."""
    return load_repo_module(
        "packages/emdash/updater.py",
        "emdash_updater_dedicated_test",
    )


def _flake_node(
    *,
    source_type: str = "github",
    commit: str = _COMMIT,
) -> FlakeLockNode:
    return FlakeLockNode.model_validate({
        "locked": {
            "type": source_type,
            "owner": "example",
            "repo": "emdash",
            "rev": commit,
            "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        },
        "original": {
            "type": source_type,
            "owner": "example",
            "repo": "emdash",
            "ref": _REF,
        },
    })


def _manifest(
    *,
    version: str = _VERSION,
    electron_spec: str = _ELECTRON_SPEC,
) -> dict[str, object]:
    return {
        "version": version,
        "devDependencies": {"electron": electron_spec},
    }


def _root_manifest(
    *,
    node_engine: object = _NODE_ENGINE,
    package_manager: object = _PACKAGE_MANAGER,
    pnpm_engine: object = _PNPM_ENGINE,
) -> dict[str, object]:
    return {
        "engines": {
            "node": node_engine,
            "pnpm": pnpm_engine,
        },
        "packageManager": package_manager,
    }


def _lock_payload(
    *,
    electron_spec: str = _ELECTRON_SPEC,
    electron_version: str = _ELECTRON_VERSION,
) -> bytes:
    return yaml.safe_dump({
        "lockfileVersion": "9.0",
        "importers": {
            "apps/emdash-desktop": {
                "devDependencies": {
                    "electron": {
                        "specifier": electron_spec,
                        "version": electron_version,
                    },
                },
            },
        },
    }).encode()


def _shell_env_capture_payload() -> bytes:
    return ("const result = " + _INTERACTIVE_SHELL_ENV_PROBE + ";\n").encode()


def _write_source_tree(source_root: Path) -> None:
    (source_root / "package.json").write_text(
        json.dumps(_root_manifest()),
        encoding="utf-8",
    )
    manifest = source_root / "apps/emdash-desktop/package.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    (source_root / "pnpm-lock.yaml").write_bytes(_lock_payload())

    capture = source_root / _SHELL_ENV_CAPTURE_PATH
    capture.parent.mkdir(parents=True)
    capture.write_bytes(_shell_env_capture_payload())
    delegate = source_root / _SHELL_ENV_DELEGATE_PATH
    delegate.parent.mkdir(parents=True)
    delegate.write_text(
        "export async function refreshUserEnv() {}\n",
        encoding="utf-8",
    )
    (source_root / "packages/core/src/other.ts").write_text(
        "export const unrelated = true;\n",
        encoding="utf-8",
    )
    (source_root / "apps/ignored.ts").mkdir()


def test_fetch_latest_derives_identity_from_immutable_manifest_and_lock(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node = _flake_node()
    _write_source_tree(tmp_path)
    source = LockedSource(root=tmp_path, context="Emdash flake input")
    resolutions: list[tuple[FlakeLockNode, str, float]] = []

    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda input_name: node if input_name == "emdash" else None,
    )

    async def resolve_locked_source(
        resolved_node: FlakeLockNode,
        *,
        context: str,
        command_timeout: float,
    ) -> LockedSource:
        resolutions.append((resolved_node, context, command_timeout))
        return source

    monkeypatch.setattr(updater_module, "resolve_locked_source", resolve_locked_source)

    async def resolve_nixpkgs_package_version(
        package_attr: str,
        *,
        command_timeout: float,
        source_name: str,
    ) -> str:
        assert command_timeout > 0
        assert source_name == "Emdash"
        assert package_attr == "pnpm_10"
        return _PNPM_VERSION

    async def resolve_nixpkgs_nodejs_for_engine(
        engine: object,
        *,
        command_timeout: float,
        source_name: str,
    ) -> NodejsSelection:
        assert engine == _NODE_ENGINE
        assert command_timeout > 0
        assert source_name == "Emdash"
        return NodejsSelection(
            engine=_NODE_ENGINE,
            attribute="nodejs_24",
            version=_NODEJS_VERSION,
        )

    monkeypatch.setattr(
        updater_module,
        "resolve_nixpkgs_package_version",
        resolve_nixpkgs_package_version,
    )
    monkeypatch.setattr(
        updater_module,
        "resolve_nixpkgs_nodejs_for_engine",
        resolve_nixpkgs_nodejs_for_engine,
    )

    updater = updater_module.EmdashUpdater()
    info = run_async(updater.fetch_latest(object()))

    assert info.version == _REF
    assert isinstance(info.metadata, updater_module.EmdashSourceMetadata)
    assert info.metadata.to_dict() == {
        "node": node,
        "commit": _COMMIT,
        "electronVersion": _ELECTRON_VERSION,
        "shellEnvCapturePath": _SHELL_ENV_CAPTURE_PATH,
        "nodeEngine": _NODE_ENGINE,
        "nodejsAttr": "nodejs_24",
        "nodejsVersion": _NODEJS_VERSION,
        "packageManager": _PACKAGE_MANAGER,
        "pnpmEngine": _PNPM_ENGINE,
        "pnpmAttr": "pnpm_10",
        "pnpmVersion": _PNPM_VERSION,
    }
    assert info.commit == _COMMIT
    assert updater.compatibility_pins is None
    result = updater.build_result(info, [])
    assert result.electron_version == _ELECTRON_VERSION
    assert result.pins == {
        "nodeEngine": _NODE_ENGINE,
        "nodejsAttr": "nodejs_24",
        "nodejsVersion": _NODEJS_VERSION,
        "packageManager": _PACKAGE_MANAGER,
        "pnpmEngine": _PNPM_ENGINE,
        "pnpmAttr": "pnpm_10",
        "pnpmVersion": _PNPM_VERSION,
    }
    # A legacy source using pins must refresh into the typed top-level field.
    stale = SourceEntry(
        version=_REF,
        input="emdash",
        hashes=[],
        pins={"electronVersion": _ELECTRON_VERSION},
        drv_hash="unchanged-fingerprint",
    )
    assert run_async(updater._is_latest(stale, info)) is False
    assert resolutions == [
        (
            node,
            "Emdash flake input",
            updater.config.default_subprocess_timeout,
        )
    ]


@pytest.mark.parametrize("pnpm_engine", [_PNPM_ENGINE, ">=10.28.0 <10.35.0"])
def test_toolchain_contract_derives_nix_attributes_from_upstream_manifest(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    pnpm_engine: str,
) -> None:
    calls: list[tuple[str, object, float, str]] = []

    async def resolve_nixpkgs_package_version(
        package_attr: str,
        *,
        command_timeout: float,
        source_name: str,
    ) -> str:
        calls.append(("package", package_attr, command_timeout, source_name))
        assert package_attr == "pnpm_10"
        return _PNPM_VERSION

    async def resolve_nixpkgs_nodejs_for_engine(
        engine: object,
        *,
        command_timeout: float,
        source_name: str,
    ) -> NodejsSelection:
        calls.append(("node", engine, command_timeout, source_name))
        return NodejsSelection(
            engine=_NODE_ENGINE,
            attribute="nodejs_24",
            version=_NODEJS_VERSION,
        )

    monkeypatch.setattr(
        updater_module,
        "resolve_nixpkgs_package_version",
        resolve_nixpkgs_package_version,
    )
    monkeypatch.setattr(
        updater_module,
        "resolve_nixpkgs_nodejs_for_engine",
        resolve_nixpkgs_nodejs_for_engine,
    )

    toolchain = run_async(
        updater_module._resolve_toolchain_contract(
            _root_manifest(
                package_manager=f"{_PACKAGE_MANAGER}+sha512.AbCd_0123=-",
                pnpm_engine=pnpm_engine,
            ),
            command_timeout=19,
        )
    )
    assert toolchain.to_pins() == {
        "nodeEngine": _NODE_ENGINE,
        "nodejsAttr": "nodejs_24",
        "nodejsVersion": _NODEJS_VERSION,
        "packageManager": f"{_PACKAGE_MANAGER}+sha512.AbCd_0123=-",
        "pnpmEngine": pnpm_engine,
        "pnpmAttr": "pnpm_10",
        "pnpmVersion": _PNPM_VERSION,
    }
    assert calls == [
        ("node", _NODE_ENGINE, 19, "Emdash"),
        ("package", "pnpm_10", 19, "Emdash"),
    ]


@pytest.mark.parametrize(
    ("manifest", "error_type", "message"),
    [
        ([], TypeError, "root manifest"),
        ({}, TypeError, "root manifest engines"),
        (_root_manifest(node_engine="workspace:*"), RuntimeError, "semantic-version"),
        (_root_manifest(package_manager="npm@10.28.2"), RuntimeError, "exact pnpm"),
    ],
)
def test_toolchain_contract_rejects_missing_or_ambiguous_source_policy(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    manifest: object,
    error_type: type[Exception],
    message: str,
) -> None:
    async def unexpected_resolve(
        _package_attr: str,
        *,
        command_timeout: float,
        source_name: str,
    ) -> str:
        _ = command_timeout
        pytest.fail(f"unexpected package resolution for {source_name}")

    monkeypatch.setattr(
        updater_module,
        "resolve_nixpkgs_package_version",
        unexpected_resolve,
    )
    monkeypatch.setattr(
        updater_module,
        "resolve_nixpkgs_nodejs_for_engine",
        unexpected_resolve,
    )

    with pytest.raises(error_type, match=message):
        run_async(
            updater_module._resolve_toolchain_contract(
                manifest,
                command_timeout=19,
            )
        )


@pytest.mark.parametrize(
    ("manifest", "nodejs_version", "pnpm_version", "message"),
    [
        (_root_manifest(), "23.9.0", _PNPM_VERSION, "does not satisfy Node engine"),
        (_root_manifest(), _NODEJS_VERSION, "10.27.9", "does not satisfy"),
        (
            _root_manifest(pnpm_engine=">=10.30.0"),
            _NODEJS_VERSION,
            _PNPM_VERSION,
            "packageManager pnpm",
        ),
        (
            _root_manifest(pnpm_engine=">=10.28.0 <10.30.0"),
            _NODEJS_VERSION,
            _PNPM_VERSION,
            "nixpkgs pnpm engine",
        ),
    ],
)
def test_toolchain_contract_rejects_incompatible_selected_versions(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    manifest: object,
    nodejs_version: str,
    pnpm_version: str,
    message: str,
) -> None:
    async def resolve_nixpkgs_package_version(
        package_attr: str,
        *,
        command_timeout: float,
        source_name: str,
    ) -> str:
        assert command_timeout == 19
        assert source_name == "Emdash"
        assert package_attr == "pnpm_10"
        return pnpm_version

    async def resolve_nixpkgs_nodejs_for_engine(
        engine: object,
        *,
        command_timeout: float,
        source_name: str,
    ) -> NodejsSelection:
        assert engine == _NODE_ENGINE or isinstance(engine, str)
        assert command_timeout == 19
        assert source_name == "Emdash"
        return NodejsSelection(
            engine=str(engine),
            attribute="nodejs_24",
            version=nodejs_version,
        )

    monkeypatch.setattr(
        updater_module,
        "resolve_nixpkgs_package_version",
        resolve_nixpkgs_package_version,
    )
    monkeypatch.setattr(
        updater_module,
        "resolve_nixpkgs_nodejs_for_engine",
        resolve_nixpkgs_nodejs_for_engine,
    )

    with pytest.raises(RuntimeError, match=message):
        run_async(
            updater_module._resolve_toolchain_contract(
                manifest,
                command_timeout=19,
            )
        )


def test_shell_env_source_tree_fails_closed(
    updater_module: ModuleType,
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="no shell environment probe candidates"):
        updater_module._shell_env_candidate_paths(tmp_path)


def test_shell_env_source_tree_bounds_candidate_count(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(updater_module, "_MAX_SHELL_ENV_CANDIDATES", 2)
    candidate_root = tmp_path / "packages/core"
    candidate_root.mkdir(parents=True)
    for index in range(updater_module._MAX_SHELL_ENV_CANDIDATES + 1):
        (candidate_root / f"source-{index}.ts").write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="too many shell environment probe"):
        updater_module._shell_env_candidate_paths(tmp_path)


@pytest.mark.parametrize(
    ("payloads", "message"),
    [
        ({"capture.ts": b"\xff"}, "not UTF-8"),
        ({"capture.ts": b"const result = changed();\n"}, "found 0"),
        (
            {
                "capture.ts": _shell_env_capture_payload(),
                "userEnv.ts": _shell_env_capture_payload(),
            },
            "found 2",
        ),
    ],
)
def test_shell_env_patch_contract_rejects_changed_or_ambiguous_behavior(
    updater_module: ModuleType,
    payloads: dict[str, bytes],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        updater_module._shell_env_patch_target(payloads)


@pytest.mark.parametrize(
    ("manifest", "lock_payload", "message"),
    [
        (_manifest(version="9.9.9"), _lock_payload(), "manifest version"),
        (
            _manifest(),
            _lock_payload(electron_spec="~42.1.0"),
            "Electron spec mismatch",
        ),
        (
            _manifest(),
            _lock_payload(electron_version="^42.3.4"),
            "exact semantic version",
        ),
    ],
)
def test_release_contract_rejects_mismatched_or_inexact_metadata(
    updater_module: ModuleType,
    manifest: dict[str, object],
    lock_payload: bytes,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        updater_module._resolve_electron_version(
            release_version=_VERSION,
            manifest=manifest,
            lock_payload=lock_payload,
        )


def test_release_contract_rejects_unsupported_electron_spec(
    updater_module: ModuleType,
) -> None:
    """Fail closed for package tags that are not semantic-version ranges."""
    with pytest.raises(RuntimeError, match="valid npm semantic-version range"):
        updater_module._resolve_electron_version(
            release_version=_VERSION,
            manifest=_manifest(electron_spec="latest"),
            lock_payload=_lock_payload(electron_spec="latest"),
        )


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b"importers: [unterminated",
    ],
)
def test_pnpm_lock_rejects_invalid_encoding_or_yaml(
    updater_module: ModuleType,
    payload: bytes,
) -> None:
    with pytest.raises(RuntimeError, match="not valid UTF-8 YAML"):
        updater_module._pnpm_lock_contract(payload)


def test_pnpm_lock_requires_the_desktop_electron_resolution(
    updater_module: ModuleType,
) -> None:
    payload = yaml.safe_dump({"importers": {}}).encode()
    with pytest.raises(TypeError, match="desktop lock importer"):
        updater_module._pnpm_lock_contract(payload)


@pytest.mark.parametrize(
    ("node", "message"),
    [
        (_flake_node(source_type="gitlab"), "complete GitHub source"),
        (_flake_node(commit="not-a-commit"), "immutable commit"),
    ],
)
def test_source_resolution_requires_an_immutable_github_commit(
    updater_module: ModuleType,
    node: FlakeLockNode,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        updater_module._locked_github_source(node)


def test_version_and_source_identity_metadata_fail_closed(
    updater_module: ModuleType,
) -> None:
    updater = updater_module.EmdashUpdater()
    with pytest.raises(RuntimeError, match="exact semantic version"):
        updater_module._release_version("main")
    with pytest.raises(TypeError, match="resolved source contracts"):
        updater.build_result(VersionInfo(version=_REF), [])
    with pytest.raises(TypeError, match="resolved source contracts"):
        updater.source_pins_for(VersionInfo(version=_REF))
