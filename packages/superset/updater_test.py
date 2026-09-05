"""Dedicated tests for the Superset updater's pure-Python edge cases."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import HashEntry, SourceEntry, SourcesFile
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update import cli_inventory as cli_inventory_module
from lib.update.artifacts import GeneratedArtifact
from lib.update.cli import _sources_refresh_flake_lock
from lib.update.config import resolve_config
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    CommandResult,
    UpdateEvent,
    UpdateEventKind,
    expect_artifact_updates,
)
from lib.update.paths import REPO_ROOT
from lib.update.persistence import planned_update_paths
from lib.update.source_runner import (
    SourcesPhaseContext,
    UpdatePhaseResult,
    run_sources_phase,
)
from lib.update.updaters import VersionInfo

if TYPE_CHECKING:
    from lib.update.process import RunCommandOptions

_COMMIT = "a" * 40
_ELECTRON_VERSION = "42.0.1"
_BUN_VERSION = "1.3.14"
_RELEASE_VERSION = "1.2.3"
_TAG = f"desktop-v{_RELEASE_VERSION}"
_ASSET_URL = "https://example.test/superset-1.2.3-x86_64.AppImage"
_ASSET_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_BUN_HASHES = (
    "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
    "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
)


def _release_payload() -> dict[str, object]:
    return {
        "tag_name": _TAG,
        "assets": [
            {
                "name": "other-asset",
                "browser_download_url": "https://example.test/other",
            },
            {
                "name": f"superset-{_RELEASE_VERSION}-x86_64.AppImage",
                "browser_download_url": _ASSET_URL,
            },
        ],
    }


def _desktop_manifest(
    *,
    version: str = _RELEASE_VERSION,
    electron_spec: str = _ELECTRON_VERSION,
) -> dict[str, object]:
    return {
        "version": version,
        "devDependencies": {"electron": electron_spec},
    }


def _root_manifest(*, package_manager: str = f"bun@{_BUN_VERSION}") -> dict[str, str]:
    return {"packageManager": package_manager}


def _bun_lock_text(
    *,
    workspace_spec: str = _ELECTRON_VERSION,
    resolution: str = f"electron@{_ELECTRON_VERSION}",
) -> str:
    return json.dumps({
        "lockfileVersion": 1,
        "workspaces": {
            "apps/desktop": {"devDependencies": {"electron": workspace_spec}}
        },
        "packages": {"electron": [resolution, "", {}, "sha512-test"]},
    })


def _flake_node(
    *,
    tag: str = _TAG,
    commit: str = _COMMIT,
) -> FlakeLockNode:
    return FlakeLockNode.model_validate({
        "flake": False,
        "original": {
            "type": "github",
            "owner": "superset-sh",
            "repo": "superset",
            "ref": tag,
        },
        "locked": {
            "type": "github",
            "owner": "superset-sh",
            "repo": "superset",
            "rev": commit,
            "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        },
    })


def _install_release_metadata(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: object | None = None,
    release_commit: str = _COMMIT,
    node: FlakeLockNode | None = None,
    manifest: object | None = None,
    root_manifest: object | None = None,
    bun_lock: bytes | None = None,
) -> None:
    release = _release_payload() if payload is None else payload

    async def _fetch_github_api(
        _session: object,
        path: str,
        **_kwargs: object,
    ) -> object:
        if path.endswith("/releases/latest"):
            return release
        if "/commits/" in path:
            return {"sha": release_commit}
        msg = f"unexpected GitHub API path: {path}"
        raise AssertionError(msg)

    async def _fetch_json(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> object:
        if url.endswith(f"/{release_commit}/apps/desktop/package.json"):
            return _desktop_manifest() if manifest is None else manifest
        if url.endswith(f"/{release_commit}/package.json"):
            return _root_manifest() if root_manifest is None else root_manifest
        msg = f"unexpected raw JSON URL: {url}"
        raise AssertionError(msg)

    async def _fetch_url(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> bytes:
        assert url.endswith(f"/{release_commit}/bun.lock")
        return _bun_lock_text().encode() if bun_lock is None else bun_lock

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _fetch_github_api,
    )
    monkeypatch.setattr(module, "fetch_json", _fetch_json)
    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(
        module.update_flake,
        "get_flake_input_node",
        lambda _name: _flake_node() if node is None else node,
    )


def _install_url_hashes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    asset_url: str,
    asset_hash: str,
) -> None:
    async def _compute_url_hashes(
        source: str,
        urls: Iterable[str],
        **_kwargs: object,
    ) -> AsyncIterator[UpdateEvent]:
        url_list = list(urls)
        hashes = (asset_hash,) if url_list == [asset_url] else _BUN_HASHES
        yield UpdateEvent.value(
            source,
            dict(zip(url_list, hashes, strict=True)),
        )

    monkeypatch.setattr(
        "lib.update.updaters.core.update_process.compute_url_hashes",
        _compute_url_hashes,
    )


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/superset/updater.py", "superset_updater_dedicated_test"
    )


@pytest.mark.parametrize("failure", ["asset", "runtime"])
def test_missing_download_hashes_prevent_artifact_generation(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Do not run Bun generation until both artifact and runtime hashes exist."""
    module = _load_module()
    _install_release_metadata(module, monkeypatch)
    updater = module.SupersetUpdater()
    info = _run(updater.fetch_latest(object()))
    seen_urls: list[list[str]] = []

    async def _hashes(source, urls, **_kwargs):
        requested = list(urls)
        seen_urls.append(requested)
        yield UpdateEvent.status(source, "downloading")
        if failure == "runtime" and requested == [_ASSET_URL]:
            yield UpdateEvent.value(source, {_ASSET_URL: _ASSET_HASH})

    monkeypatch.setattr(module.update_process, "compute_url_hashes", _hashes)
    with pytest.raises(RuntimeError, match="Missing.*hash"):
        _run(_collect(updater.fetch_hashes(info, object())))
    assert len(seen_urls) == (1 if failure == "asset" else 2)


def test_superset_declares_no_ifd_evaluation_for_supported_systems() -> None:
    """Validate the checked-in Bun graph on every platform that builds Superset."""
    module = _load_module()

    assert module.SupersetUpdater.get_derivation_validations() == (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}.drvPath",
            systems=("aarch64-darwin", "x86_64-linux"),
        ),
    )
    assert module.SupersetUpdater.supported_platforms == (
        "aarch64-darwin",
        "x86_64-linux",
    )


def test_update_inventory_declares_superset_input_and_bun_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Describe every file written when the logical Superset target updates."""
    module = _load_module()
    monkeypatch.setattr(
        cli_inventory_module,
        "UPDATERS",
        {"superset": module.SupersetUpdater},
    )
    monkeypatch.setattr(
        cli_inventory_module,
        "get_flake_inputs_with_refs",
        list,
    )
    monkeypatch.setattr(cli_inventory_module, "load_flake_lock", object)

    [target] = cli_inventory_module.build_update_inventory()

    assert target.name == "superset"
    assert target.classification == "sourceWithInputRefresh"
    assert target.backing_input == "superset"
    assert target.generated_artifacts == (
        "packages/superset/bun.lock",
        "packages/superset/bun.nix",
    )
    assert target.write_labels() == (
        "flake.lock",
        "sources.json",
        "bun.lock",
        "bun.nix",
    )


def test_update_plan_owns_superset_lock_and_generated_outputs() -> None:
    """Reserve flake.lock and both Bun outputs before the update phases run."""
    module = _load_module()
    updaters = {"superset": module.SupersetUpdater}

    assert _sources_refresh_flake_lock(["superset"], updaters) is True
    assert set(planned_update_paths(["superset"], updaters)) == {
        Path(REPO_ROOT) / "packages/superset/sources.json",
        Path(REPO_ROOT) / "packages/superset/bun.lock",
        Path(REPO_ROOT) / "packages/superset/bun.nix",
    }


def test_current_superset_still_materializes_bun_artifacts_before_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh Bun outputs even when the AppImage release is already current."""
    module = _load_module()
    package_dir = tmp_path / "packages/superset"
    package_dir.mkdir(parents=True)
    (tmp_path / ".root").write_text("\n", encoding="utf-8")
    bun_lock = package_dir / "bun.lock"
    bun_nix = package_dir / "bun.nix"
    bun_lock.write_text("old lock\n", encoding="utf-8")
    bun_nix.write_text("old nix\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    seen_commands: list[list[str]] = []
    asset_url = _ASSET_URL
    asset_hash = _ASSET_HASH

    async def _run_command(
        args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        seen_commands.append(args)
        bun_lock.write_text("new lock\n", encoding="utf-8")
        bun_nix.write_text("new nix\n", encoding="utf-8")
        yield UpdateEvent.value(
            options.source,
            CommandResult(args=args, returncode=0, stdout="", stderr=""),
        )

    candidate_sources: list[SourceEntry] = []

    def _build_update_script_expr(
        package: str,
        attr_path: str,
        **kwargs: object,
    ) -> str:
        assert package == "superset"
        assert attr_path == ".passthru.updateScript"
        overrides = kwargs["source_overrides"]
        assert isinstance(overrides, dict)
        candidate = overrides["superset"]
        assert isinstance(candidate, SourceEntry)
        candidate_sources.append(candidate)
        assert kwargs["fake_hashes"] is False
        return "candidate-update-script"

    _install_release_metadata(module, monkeypatch)
    monkeypatch.setattr(
        "lib.update.generated_artifact_commands._run_command",
        _run_command,
    )
    _install_url_hashes(
        monkeypatch,
        asset_url=asset_url,
        asset_hash=asset_hash,
    )
    monkeypatch.setattr(
        module,
        "_build_package_path_attr_expr",
        _build_update_script_expr,
    )
    monkeypatch.setattr(
        module,
        "normalize_bun_nix",
        lambda text: text.replace("new nix", "normalized nix"),
    )
    current = SourceEntry(
        version=_RELEASE_VERSION,
        commit=_COMMIT,
        hashes={"x86_64-linux": asset_hash},
        urls={"x86_64-linux": asset_url},
        pins={"electronVersion": _ELECTRON_VERSION},
    )

    events = _run(_collect(module.SupersetUpdater().update_stream(current, object())))

    assert seen_commands == [
        ["nix", "run", "--impure", "--expr", "candidate-update-script"]
    ]
    [candidate] = candidate_sources
    assert candidate.pins == {"bunVersion": _BUN_VERSION}
    assert candidate.electron_version == _ELECTRON_VERSION
    assert candidate.hashes.entries is not None
    assert [entry.hash_type for entry in candidate.hashes.entries] == [
        "bunRuntimeHash",
        "bunRuntimeHash",
        "sha256",
    ]
    artifact_event = next(
        event for event in events if event.kind is UpdateEventKind.ARTIFACT
    )
    assert expect_artifact_updates(artifact_event.payload) == [
        GeneratedArtifact.text("packages/superset/bun.lock", "new lock\n"),
        GeneratedArtifact.text("packages/superset/bun.nix", "normalized nix\n"),
    ]
    assert bun_lock.read_text(encoding="utf-8") == "old lock\n"
    assert bun_nix.read_text(encoding="utf-8") == "old nix\n"


def test_superset_materialization_failure_remains_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generated-artifact failure must survive source and phase aggregation."""
    module = _load_module()
    asset_url = _ASSET_URL
    asset_hash = _ASSET_HASH

    async def _failed_command(
        args: list[str],
        *,
        options: RunCommandOptions,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value(
            options.source,
            CommandResult(
                args=args,
                returncode=17,
                stdout="",
                stderr="broken generator",
            ),
        )

    _install_release_metadata(module, monkeypatch)
    monkeypatch.setattr(
        "lib.update.generated_artifact_commands._run_command",
        _failed_command,
    )
    _install_url_hashes(
        monkeypatch,
        asset_url=asset_url,
        asset_hash=asset_hash,
    )
    monkeypatch.setattr(
        "lib.update.source_runner.UPDATERS",
        {"superset": module.SupersetUpdater},
    )
    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    current = SourceEntry(
        version=_RELEASE_VERSION,
        commit=_COMMIT,
        hashes={"x86_64-linux": asset_hash},
        urls={"x86_64-linux": asset_url},
        pins={"electronVersion": _ELECTRON_VERSION},
    )

    phase_result = _run(
        run_sources_phase(
            SourcesPhaseContext(
                source_names=["superset"],
                sources=SourcesFile(entries={"superset": current}),
                queue=queue,
                update_input=False,
                native_only=False,
                config=resolve_config(max_nix_builds=1),
            )
        )
    )
    events = []
    while not queue.empty():
        event = queue.get_nowait()
        if event is not None:
            events.append(event)

    assert phase_result.details == {"superset": "error"}
    assert phase_result.source_updates == {}
    assert phase_result.artifact_updates == {}
    assert phase_result.merged(
        UpdatePhaseResult(details={"superset": "updated"})
    ).details == {"superset": "error"}
    assert any(
        event.kind is UpdateEventKind.ERROR
        and "Refresh Superset Bun lock artifacts failed (exit 17)"
        in (event.message or "")
        for event in events
    )


@pytest.mark.parametrize(
    ("payload", "error_type", "message"),
    [
        ([], TypeError, "Unexpected release payload type: list"),
        ({}, RuntimeError, "Missing tag_name in release payload"),
        ({"tag_name": ""}, RuntimeError, "Missing tag_name in release payload"),
        (
            {"tag_name": "desktop-v", "assets": []},
            RuntimeError,
            "Missing version segment in Superset release tag: desktop-v",
        ),
        (
            {"tag_name": "desktop-v1.2.3", "assets": "bad"},
            TypeError,
            "Missing assets in release payload for tag desktop-v1.2.3",
        ),
    ],
)
def test_fetch_latest_rejects_invalid_payload_shapes(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    error_type: type[Exception],
    message: str,
) -> None:
    """Reject malformed GitHub release payloads before resolving asset URLs."""
    module = _load_module()
    updater = module.SupersetUpdater()
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(0, result=payload),
    )

    with pytest.raises(error_type, match=message):
        _run(updater.fetch_latest(object()))


def test_fetch_latest_ignores_non_dict_and_empty_asset_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip malformed assets and fail if no usable download URL remains."""
    module = _load_module()
    updater = module.SupersetUpdater()
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "desktop-v1.2.3",
                "assets": [
                    "noise",
                    {
                        "name": "superset-1.2.3-x86_64.AppImage",
                        "browser_download_url": "",
                    },
                ],
            },
        ),
    )

    with pytest.raises(
        RuntimeError, match="Could not find Superset desktop release asset"
    ):
        _run(updater.fetch_latest(object()))


def test_fetch_latest_rejects_non_desktop_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject releases whose tag names do not follow the desktop-v convention."""
    module = _load_module()
    updater = module.SupersetUpdater()
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "v1.2.3",
                "assets": [],
            },
        ),
    )

    with pytest.raises(RuntimeError, match="Unexpected Superset release tag format"):
        _run(updater.fetch_latest(object()))


def test_fetch_latest_returns_version_info_with_asset_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve one release's asset, source commit, and locked Electron runtime."""
    module = _load_module()
    updater = module.SupersetUpdater()
    _install_release_metadata(module, monkeypatch)

    info = _run(updater.fetch_latest(object()))

    assert info.version == _RELEASE_VERSION
    assert info.metadata == {
        "asset_urls": {"x86_64-linux": _ASSET_URL},
        "bunVersion": _BUN_VERSION,
        "commit": _COMMIT,
        "electronVersion": _ELECTRON_VERSION,
        "tag": _TAG,
    }
    result = updater.build_result(
        info,
        [
            HashEntry.create(
                "bunRuntimeHash",
                _BUN_HASHES[0],
                platform="aarch64-darwin",
                url=(
                    "https://github.com/oven-sh/bun/releases/download/"
                    f"bun-v{_BUN_VERSION}/bun-darwin-aarch64.zip"
                ),
            ),
            HashEntry.create(
                "sha256",
                _ASSET_HASH,
                platform="x86_64-linux",
            ),
        ],
    )
    assert result.commit == _COMMIT
    assert result.electron_version == _ELECTRON_VERSION
    assert result.pins == {"bunVersion": _BUN_VERSION}
    assert result.urls == {"x86_64-linux": _ASSET_URL}


def test_fetch_latest_rejects_source_input_release_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never combine one release's binary with another source revision."""
    module = _load_module()
    _install_release_metadata(
        module,
        monkeypatch,
        release_commit="b" * 40,
        node=_flake_node(commit=_COMMIT),
    )

    with pytest.raises(RuntimeError, match="does not match release tag commit"):
        _run(module.SupersetUpdater().fetch_latest(object()))


def test_fetch_latest_rejects_non_utf8_bun_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an undecodable release lock before selecting a runtime."""
    module = _load_module()
    _install_release_metadata(module, monkeypatch, bun_lock=b"\xff")

    with pytest.raises(ValueError, match="bun.lock is not UTF-8 text"):
        _run(module.SupersetUpdater().fetch_latest(object()))


def test_fetch_latest_requires_exact_root_bun_package_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The immutable root manifest owns the exact Bun runtime version."""
    module = _load_module()
    _install_release_metadata(
        module,
        monkeypatch,
        root_manifest=_root_manifest(package_manager="bun@latest"),
    )

    with pytest.raises(RuntimeError, match="exact semantic version"):
        _run(module.SupersetUpdater().fetch_latest(object()))


def test_locked_source_commit_requires_matching_release_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source input itself must be a versioned desktop release."""
    module = _load_module()
    monkeypatch.setattr(
        module.update_flake,
        "get_flake_input_node",
        lambda _name: _flake_node(tag="main"),
    )

    with pytest.raises(RuntimeError, match="same immutable desktop release tag"):
        module.SupersetUpdater._locked_source_commit(_TAG)


def test_locked_source_commit_requires_immutable_github_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a mutable or malformed locked source witness."""
    module = _load_module()
    node = _flake_node()
    assert node.locked is not None
    node.locked.rev = "main"
    monkeypatch.setattr(
        module.update_flake,
        "get_flake_input_node",
        lambda _name: node,
    )

    with pytest.raises(RuntimeError, match="no immutable GitHub source commit"):
        module.SupersetUpdater._locked_source_commit(_TAG)


@pytest.mark.parametrize(
    "electron_spec",
    [_ELECTRON_VERSION, f"^{_ELECTRON_VERSION}", f"~{_ELECTRON_VERSION}"],
)
def test_release_metadata_accepts_equivalent_electron_specs(
    electron_spec: str,
) -> None:
    """Let upstream choose normal exact-compatible spec spellings."""
    module = _load_module()

    assert (
        module.SupersetUpdater._validate_release_metadata(
            version=_RELEASE_VERSION,
            desktop_manifest=_desktop_manifest(electron_spec=electron_spec),
            bun_lock=json.loads(_bun_lock_text(workspace_spec=electron_spec)),
        )
        == _ELECTRON_VERSION
    )


def test_release_metadata_accepts_locked_electron_within_caret_range() -> None:
    """Accept a lock resolution above the lower bound but below the next major."""
    module = _load_module()
    electron_spec = "^41.0.0"

    assert (
        module.SupersetUpdater._validate_release_metadata(
            version=_RELEASE_VERSION,
            desktop_manifest=_desktop_manifest(electron_spec=electron_spec),
            bun_lock=json.loads(
                _bun_lock_text(
                    workspace_spec=electron_spec,
                    resolution="electron@41.10.3",
                )
            ),
        )
        == "41.10.3"
    )


@pytest.mark.parametrize(
    ("manifest", "lock", "message"),
    [
        (
            _desktop_manifest(version="9.9.9"),
            json.loads(_bun_lock_text()),
            "does not match release version",
        ),
        (
            _desktop_manifest(electron_spec="^42.0.1"),
            json.loads(_bun_lock_text(workspace_spec="~42.0.1")),
            "does not match bun.lock workspace spec",
        ),
        (
            _desktop_manifest(electron_spec="^43.0.0"),
            json.loads(_bun_lock_text(workspace_spec="^43.0.0")),
            "does not satisfy",
        ),
    ],
)
def test_release_metadata_rejects_incoherent_identity(
    manifest: object,
    lock: object,
    message: str,
) -> None:
    """Fail closed when release, manifest, workspace, and lock disagree."""
    module = _load_module()

    with pytest.raises(RuntimeError, match=message):
        module.SupersetUpdater._validate_release_metadata(
            version=_RELEASE_VERSION,
            desktop_manifest=manifest,
            bun_lock=lock,
        )


@pytest.mark.parametrize(
    ("resolution", "error_type", "message"),
    [
        ("", TypeError, "no exact Electron package resolution"),
        ("electron@not-semver", RuntimeError, "invalid Electron version"),
    ],
)
def test_locked_electron_version_rejects_invalid_resolution(
    resolution: str,
    error_type: type[Exception],
    message: str,
) -> None:
    """Require a conventional exact Electron resolution from Bun."""
    module = _load_module()

    with pytest.raises(error_type, match=message):
        module.SupersetUpdater._locked_electron_version(
            json.loads(_bun_lock_text(resolution=resolution))
        )


def test_source_identity_requires_release_derived_electron_version() -> None:
    """A missing runtime witness cannot silently fall back to a class constant."""
    module = _load_module()

    with pytest.raises(TypeError, match="electronVersion"):
        module.SupersetUpdater().build_result(
            VersionInfo(
                _RELEASE_VERSION,
                {
                    "asset_urls": {"x86_64-linux": _ASSET_URL},
                    "commit": _COMMIT,
                },
            ),
            {"x86_64-linux": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="},
        )


def test_asset_name_and_fallback_url_match_release_convention() -> None:
    """Build asset names and fallback URLs from the desktop tag convention."""
    module = _load_module()
    updater = module.SupersetUpdater()

    assert updater._asset_name("1.2.3", "x86_64") == ("superset-1.2.3-x86_64.AppImage")
    assert updater._fallback_url("1.2.3", "x86_64") == (
        "https://github.com/superset-sh/superset/releases/download/"
        "desktop-v1.2.3/superset-1.2.3-x86_64.AppImage"
    )


def test_get_download_url_prefers_metadata_asset_urls() -> None:
    """Read asset URLs from metadata that also carries source-build identity."""
    module = _load_module()
    updater = module.SupersetUpdater()
    info = VersionInfo(
        "1.2.3",
        {
            "asset_urls": {"x86_64-linux": "https://example.test/superset.AppImage"},
            "commit": _COMMIT,
            "electronVersion": _ELECTRON_VERSION,
        },
    )

    assert (
        updater.get_download_url("x86_64-linux", info)
        == "https://example.test/superset.AppImage"
    )


def test_get_download_url_falls_back_when_metadata_is_missing_or_empty() -> None:
    """Fallback URL generation should handle missing or blank metadata entries."""
    module = _load_module()
    updater = module.SupersetUpdater()

    empty_metadata = VersionInfo(
        "1.2.3",
        {"asset_urls": {"x86_64-linux": ""}},
    )
    foreign_metadata = VersionInfo("1.2.3", {"asset_urls": {}})

    expected = (
        "https://github.com/superset-sh/superset/releases/download/"
        "desktop-v1.2.3/superset-1.2.3-x86_64.AppImage"
    )
    assert updater.get_download_url("x86_64-linux", empty_metadata) == expected
    assert updater.get_download_url("x86_64-linux", foreign_metadata) == expected


def test_get_download_url_rejects_unsupported_platform() -> None:
    """Unknown platforms should fail instead of inventing a download URL."""
    module = _load_module()
    updater = module.SupersetUpdater()

    with pytest.raises(RuntimeError, match="Unsupported platform for superset updater"):
        updater.get_download_url("aarch64-linux", VersionInfo("1.2.3", {}))
