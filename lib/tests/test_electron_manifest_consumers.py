"""Contracts for flake-manifest Electron runtime contributions."""

from types import ModuleType

import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.electron_manifest import ElectronManifestMetadata
from lib.update.updaters import FlakeInputUpdater, VersionInfo
from lib.update.updaters.metadata import FlakeInputMetadata

_COMMIT = "b" * 40


def _node() -> FlakeLockNode:
    return FlakeLockNode.model_validate({
        "locked": {
            "type": "github",
            "owner": "example",
            "repo": "desktop",
            "rev": _COMMIT,
            "narHash": "sha256-source",
        },
        "original": {
            "type": "github",
            "owner": "example",
            "repo": "desktop",
            "ref": "main",
        },
    })


@pytest.mark.parametrize(
    (
        "module_path",
        "module_name",
        "class_name",
        "manifest_path",
        "dependency_group",
    ),
    [
        (
            "packages/hermes-desktop/updater.py",
            "hermes_electron_contribution_test",
            "HermesDesktopUpdater",
            "apps/desktop/package.json",
            "devDependencies",
        ),
        (
            "packages/t3code-desktop/updater.py",
            "t3code_electron_contribution_test",
            "T3CodeDesktopUpdater",
            "apps/desktop/package.json",
            "dependencies",
        ),
    ],
)
def test_flake_manifest_consumers_persist_exact_electron_version(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    module_name: str,
    class_name: str,
    manifest_path: str,
    dependency_group: str,
) -> None:
    """Carry immutable manifest resolution into each same-run source override."""
    module: ModuleType = load_repo_module(module_path, module_name)
    updater = getattr(module, class_name)()
    node = _node()
    base_info = VersionInfo(
        version="main",
        metadata=FlakeInputMetadata(node=node, commit=_COMMIT),
    )
    calls: list[dict[str, object]] = []

    async def _base_fetch_latest(
        _self: object,
        _session: object,
    ) -> VersionInfo:
        return base_info

    async def _fetch_manifest(
        _session: object,
        **kwargs: object,
    ) -> ElectronManifestMetadata:
        calls.append(kwargs)
        return ElectronManifestMetadata(
            node=node,
            commit=_COMMIT,
            electron_version="42.3.3",
            manifest_path=manifest_path,
            manifest_version="1.2.3",
        )

    monkeypatch.setattr(FlakeInputUpdater, "fetch_latest", _base_fetch_latest)
    monkeypatch.setattr(module, "fetch_flake_electron_manifest", _fetch_manifest)

    info = _run(updater.fetch_latest(object()))
    result = updater.build_result(info, [])

    assert info.metadata == ElectronManifestMetadata(
        node=node,
        commit=_COMMIT,
        electron_version="42.3.3",
        manifest_path=manifest_path,
        manifest_version="1.2.3",
    )
    assert result.electron_version == "42.3.3"
    assert result.input == updater.input_name
    assert len(calls) == 1
    call = calls[0]
    assert call["node"] == node
    assert call["manifest_path"] == manifest_path
    assert call["dependency_group"] == dependency_group
    assert call["config"] == updater.config
    assert isinstance(call["context"], str)
    if updater.name == "t3code-desktop":
        assert result.pins == {"electronBuilderVersion": "26.15.7"}

    with pytest.raises(TypeError, match="resolved Electron manifest"):
        updater.build_result(VersionInfo(version="main"), [])
