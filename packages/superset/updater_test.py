"""Dedicated tests for the Superset updater's pure-Python edge cases."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from lib.nix.models.sources import SourceEntry, SourcesFile
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
from lib.update.updaters.metadata import AssetURLsMetadata

if TYPE_CHECKING:
    from lib.update.process import RunCommandOptions


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/superset/updater.py", "superset_updater_dedicated_test"
    )


def test_superset_declares_no_ifd_evaluation_for_supported_systems() -> None:
    """Validate the checked-in Bun graph on every platform that builds Superset."""
    module = _load_module()

    assert module.SupersetUpdater.get_derivation_validations() == (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}.drvPath",
            systems=("aarch64-darwin", "x86_64-linux"),
        ),
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
    asset_url = "https://example.test/superset.AppImage"
    asset_hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    async def _fetch_github_api(*_args: object, **_kwargs: object) -> object:
        return {
            "tag_name": "desktop-v1.2.3",
            "assets": [
                {
                    "name": "superset-1.2.3-x86_64.AppImage",
                    "browser_download_url": asset_url,
                }
            ],
        }

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

    async def _compute_url_hashes(
        source: str,
        urls: object,
        *,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        _ = config
        assert list(urls) == [asset_url]  # type: ignore[arg-type]
        assert bun_lock.read_text(encoding="utf-8") == "new lock\n"
        assert bun_nix.read_text(encoding="utf-8") == "new nix\n"
        yield UpdateEvent.value(source, {asset_url: asset_hash})

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _fetch_github_api,
    )
    monkeypatch.setattr(
        "lib.update.generated_artifact_commands._run_command",
        _run_command,
    )
    monkeypatch.setattr(
        "lib.update.updaters.core.update_process.compute_url_hashes",
        _compute_url_hashes,
    )
    monkeypatch.setattr(
        module,
        "normalize_bun_nix",
        lambda text: text.replace("new nix", "normalized nix"),
    )
    current = SourceEntry(
        version="1.2.3",
        hashes={"x86_64-linux": asset_hash},
        urls={"x86_64-linux": asset_url},
    )

    events = _run(_collect(module.SupersetUpdater().update_stream(current, object())))

    assert seen_commands == [["nix", "run", ".#superset.passthru.updateScript"]]
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
    asset_url = "https://example.test/superset.AppImage"
    asset_hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    async def _fetch_github_api(*_args: object, **_kwargs: object) -> object:
        return {
            "tag_name": "desktop-v1.2.3",
            "assets": [
                {
                    "name": "superset-1.2.3-x86_64.AppImage",
                    "browser_download_url": asset_url,
                }
            ],
        }

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

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _fetch_github_api,
    )
    monkeypatch.setattr(
        "lib.update.generated_artifact_commands._run_command",
        _failed_command,
    )
    monkeypatch.setattr(
        "lib.update.source_runner.UPDATERS",
        {"superset": module.SupersetUpdater},
    )
    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    current = SourceEntry(
        version="1.2.3",
        hashes={"x86_64-linux": asset_hash},
        urls={"x86_64-linux": asset_url},
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
    """Resolve the desktop version and matching AppImage asset URL."""
    module = _load_module()
    updater = module.SupersetUpdater()
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "desktop-v1.2.3",
                "assets": [
                    {
                        "name": "other-asset",
                        "browser_download_url": "https://example.test/other",
                    },
                    {
                        "name": "superset-1.2.3-x86_64.AppImage",
                        "browser_download_url": "https://example.test/superset-1.2.3-x86_64.AppImage",
                    },
                ],
            },
        ),
    )

    info = _run(updater.fetch_latest(object()))

    assert info.version == "1.2.3"
    assert info.metadata == AssetURLsMetadata({
        "x86_64-linux": "https://example.test/superset-1.2.3-x86_64.AppImage"
    })


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
    """Return metadata-provided URLs before falling back to predictable release URLs."""
    module = _load_module()
    updater = module.SupersetUpdater()
    info = VersionInfo(
        "1.2.3",
        AssetURLsMetadata({"x86_64-linux": "https://example.test/superset.AppImage"}),
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
        AssetURLsMetadata({"x86_64-linux": ""}),
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
