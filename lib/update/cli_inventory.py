"""Inventory models and helpers for the update CLI."""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from lib.update import updaters as updater_module
from lib.update.flake import load_flake_lock, resolve_root_input_node
from lib.update.paths import (
    get_repo_root,
    package_dir_for,
    package_file_map,
    sources_file_for,
)
from lib.update.planner import source_backing_input_name
from lib.update.refs import get_flake_inputs_with_refs
from lib.update.sources import load_all_sources
from lib.update.updaters import UPDATERS, ensure_updaters_loaded
from lib.update.updaters.core import (
    ChecksumProvidedUpdater,
    DownloadHashUpdater,
    HashEntryUpdater,
    Updater,
)
from lib.update.updaters.flake_backed import (
    DenoManifestUpdater,
    FlakeInputHashUpdater,
    UvLockUpdater,
)
from lib.update.updaters.platform_api import PlatformAPIUpdater

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry
    from lib.update.cli_options import UpdateOptions
    from lib.update.updaters import UpdaterClass


def _get_updaters() -> dict[str, UpdaterClass]:
    return updater_module.resolve_registry_alias(UPDATERS, ensure_updaters_loaded)


@dataclass(frozen=True)
class _InventoryHandles:
    ref_update: bool
    input_refresh: bool
    source_update: bool
    artifact_write: bool

    def to_dict(self) -> dict[str, bool]:
        return {
            "refUpdate": self.ref_update,
            "inputRefresh": self.input_refresh,
            "sourceUpdate": self.source_update,
            "artifactWrite": self.artifact_write,
        }

    def touch_labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        if self.ref_update:
            labels.append("ref")
        if self.input_refresh:
            labels.append("lock")
        if self.source_update:
            labels.append("sources")
        if self.artifact_write:
            labels.append("art")
        return tuple(labels)


@dataclass(frozen=True)
class _InventoryRefTarget:
    input_name: str
    source_type: str
    owner: str
    repo: str
    selector: str
    locked_rev: str | None

    def source_locator(self) -> str:
        return f"{self.source_type}:{self.owner}/{self.repo}"

    def to_dict(self) -> dict[str, str | None]:
        return {
            "input": self.input_name,
            "sourceType": self.source_type,
            "owner": self.owner,
            "repo": self.repo,
            "selector": self.selector,
            "lockedRev": self.locked_rev,
        }


@dataclass(frozen=True)
class _InventorySourceTarget:
    path: str | None
    version: str | None
    commit: str | None
    hash_kinds: tuple[str, ...]
    updater_kind: str
    updater_class: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "version": self.version,
            "commit": self.commit,
            "hashKinds": list(self.hash_kinds),
            "updaterKind": self.updater_kind,
            "updaterClass": self.updater_class,
        }


@dataclass(frozen=True)
class _InventoryTarget:
    name: str
    handles: _InventoryHandles
    classification: str
    backing_input: str | None
    ref_target: _InventoryRefTarget | None
    source_target: _InventorySourceTarget | None
    generated_artifacts: tuple[str, ...]

    def selector_value(self) -> str | None:
        if self.ref_target is not None:
            return self.ref_target.selector
        if self.source_target is not None:
            return self.source_target.version
        return None

    def revision_value(self) -> str | None:
        if self.ref_target is not None and self.ref_target.locked_rev is not None:
            return self.ref_target.locked_rev
        if self.source_target is not None:
            return self.source_target.commit
        return None

    def source_value(self) -> str:
        if self.backing_input:
            return self.backing_input
        if self.ref_target is not None:
            return self.ref_target.source_locator()
        if self.source_target is not None and self.source_target.path is not None:
            return self.source_target.path
        return ""

    def write_labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        if self.handles.ref_update:
            labels.append("flake.lock")
        if self.source_target is not None:
            source_path = self.source_target.path
            labels.append(Path(source_path).name if source_path else "sources.json")
        labels.extend(Path(path).name for path in self.generated_artifacts)
        return tuple(dict.fromkeys(labels))

    def classification_label(self) -> str:
        labels = {
            "refOnly": "ref",
            "sourceOnly": "source",
            "sourceWithInputRefresh": "source+input",
            "refAndSourceWithInputRefresh": "ref+source+input",
            "refAndSource": "ref+source",
        }
        return labels.get(self.classification, self.classification)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "handles": self.handles.to_dict(),
            "classification": self.classification,
            "backingInput": self.backing_input,
            "refTarget": None if self.ref_target is None else self.ref_target.to_dict(),
            "sourceTarget": (
                None if self.source_target is None else self.source_target.to_dict()
            ),
            "generatedArtifacts": list(self.generated_artifacts),
        }


def _repo_relative_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(get_repo_root()))
    except ValueError:
        return str(path)


def _source_hash_kinds(entry: SourceEntry | None) -> tuple[str, ...]:
    if entry is None:
        return ()
    hashes = entry.hashes
    if hashes.entries:
        return tuple(sorted({hash_entry.hash_type for hash_entry in hashes.entries}))
    if hashes.mapping:
        return ("sha256",)
    return ()


def _classify_updater_kind(updater_cls: type[Updater]) -> str:
    if issubclass(updater_cls, DenoManifestUpdater):
        return "deno-manifest"
    if issubclass(updater_cls, FlakeInputHashUpdater):
        return "flake-input-hash"
    if issubclass(updater_cls, PlatformAPIUpdater):
        return "platform-api"
    if issubclass(updater_cls, ChecksumProvidedUpdater):
        return "checksum-api"
    if issubclass(updater_cls, DownloadHashUpdater):
        return "download"
    if issubclass(updater_cls, HashEntryUpdater):
        return "custom-hash"
    return "custom-hash"


def _generated_artifact_paths(
    name: str,
    updater_cls: type[Updater],
) -> tuple[str, ...]:
    crate2nix_paths = _crate2nix_generated_artifact_paths(name)
    declared_paths = getattr(updater_cls, "generated_artifact_files", ())
    needs_package_dir = bool(declared_paths) or issubclass(
        updater_cls, (DenoManifestUpdater, UvLockUpdater)
    )
    if not needs_package_dir:
        return crate2nix_paths

    pkg_dir = package_dir_for(name)
    if pkg_dir is None:
        return ()

    artifact_paths: tuple[str, ...] = ()
    if declared_paths:
        artifact_paths = tuple(
            resolved
            for relative in declared_paths
            if (resolved := _repo_relative_path(pkg_dir / relative)) is not None
        )

    if not artifact_paths:
        artifact_name: str | None = None
        if issubclass(updater_cls, UvLockUpdater):
            artifact_name = getattr(updater_cls, "lock_file", "uv.lock")
        elif issubclass(updater_cls, DenoManifestUpdater):
            artifact_name = getattr(updater_cls, "manifest_file", "deno-deps.json")

        if artifact_name is not None:
            path = _repo_relative_path(pkg_dir / artifact_name)
            if path is not None:
                artifact_paths = (path,)

    return tuple(dict.fromkeys((*artifact_paths, *crate2nix_paths)))


def _crate2nix_generated_artifact_paths(name: str) -> tuple[str, ...]:
    try:
        crate2nix_module = importlib.import_module("lib.update.crate2nix")
    except ImportError:
        return ()

    target = getattr(crate2nix_module, "TARGETS", {}).get(name)
    if target is None:
        return ()
    return (target.cargo_nix.as_posix(), target.crate_hashes.as_posix())


def _inventory_classification(handles: _InventoryHandles) -> str:
    if handles.ref_update and handles.source_update and handles.input_refresh:
        return "refAndSourceWithInputRefresh"
    if handles.source_update and handles.input_refresh:
        return "sourceWithInputRefresh"
    if handles.ref_update and handles.source_update:
        return "refAndSource"
    if handles.source_update:
        return "sourceOnly"
    if handles.ref_update:
        return "refOnly"
    return "unclassified"


def _build_inventory_summary(targets: list[_InventoryTarget]) -> dict[str, object]:
    counts: dict[str, int] = {
        "refOnly": 0,
        "sourceOnly": 0,
        "sourceWithInputRefresh": 0,
        "refAndSource": 0,
        "refAndSourceWithInputRefresh": 0,
        "unclassified": 0,
    }
    for target in targets:
        counts[target.classification] = counts.get(target.classification, 0) + 1
    return {"totalTargets": len(targets), "counts": counts}


def build_update_inventory() -> list[_InventoryTarget]:
    """Build logical update targets from updater and flake input metadata."""
    sources = load_all_sources()
    path_map = package_file_map("sources.json")
    ref_inputs = {item.name: item for item in get_flake_inputs_with_refs()}
    lock = load_flake_lock()
    updaters = _get_updaters()

    targets: list[_InventoryTarget] = []
    all_names = sorted(set(updaters) | set(ref_inputs))
    for name in all_names:
        updater_cls = updaters.get(name)
        entry = sources.entries.get(name)
        ref_input = ref_inputs.get(name)
        source_backing_input = source_backing_input_name(name, updater_cls, entry)
        backing_input = source_backing_input or (
            ref_input.name if ref_input is not None else None
        )
        generated_artifacts = (
            () if updater_cls is None else _generated_artifact_paths(name, updater_cls)
        )
        handles = _InventoryHandles(
            ref_update=name in ref_inputs,
            input_refresh=source_backing_input is not None and updater_cls is not None,
            source_update=updater_cls is not None,
            artifact_write=bool(generated_artifacts),
        )
        classification = _inventory_classification(handles)

        ref_target: _InventoryRefTarget | None = None
        if ref_input is not None:
            node, _follows = resolve_root_input_node(lock, name)
            locked = node.locked if node is not None else None
            ref_target = _InventoryRefTarget(
                input_name=ref_input.name,
                source_type=ref_input.input_type,
                owner=ref_input.owner,
                repo=ref_input.repo,
                selector=ref_input.ref,
                locked_rev=locked.rev if locked is not None else None,
            )

        source_target: _InventorySourceTarget | None = None
        if updater_cls is not None:
            source_target = _InventorySourceTarget(
                path=_repo_relative_path(path_map.get(name) or sources_file_for(name)),
                version=entry.version if entry is not None else None,
                commit=entry.commit if entry is not None else None,
                hash_kinds=_source_hash_kinds(entry),
                updater_kind=_classify_updater_kind(updater_cls),
                updater_class=updater_cls.__name__,
            )

        targets.append(
            _InventoryTarget(
                name=name,
                handles=handles,
                classification=classification,
                backing_input=backing_input,
                ref_target=ref_target,
                source_target=source_target,
                generated_artifacts=generated_artifacts,
            )
        )

    return targets


def _inventory_sort_value(target: _InventoryTarget, sort_by: str) -> str:
    if sort_by in {"type", "classification"}:
        return target.classification
    if sort_by in {"source", "input"}:
        return target.source_value()
    if sort_by in {"ref", "version"}:
        return target.selector_value() or ""
    if sort_by in {"rev", "commit"}:
        return target.revision_value() or ""
    if sort_by == "touches":
        return ",".join(target.handles.touch_labels())
    if sort_by == "writes":
        return ",".join(target.write_labels())
    return target.name


def _render_inventory_table(targets: list[_InventoryTarget]) -> None:
    no_color = not sys.stdout.isatty()
    console = Console(no_color=no_color, highlight=not no_color)

    table = Table(title="nixcfg update inventory", show_lines=False)
    table.add_column("name", style="cyan")
    table.add_column("class", style="magenta")
    table.add_column("touches", style="green")
    table.add_column("input")
    table.add_column("selector")
    table.add_column("writes")
    for target in targets:
        table.add_row(
            target.name,
            target.classification_label(),
            ", ".join(target.handles.touch_labels()) or "<none>",
            target.backing_input or "<none>",
            target.selector_value() or "<none>",
            ", ".join(target.write_labels()) or "<none>",
        )
    console.print(table)


def handle_list_targets_request(opts: UpdateOptions) -> int | None:
    """Handle ``--list`` by rendering the update inventory."""
    if not opts.list_targets:
        return None

    targets = build_update_inventory()
    targets.sort(
        key=lambda target: (_inventory_sort_value(target, opts.sort_by), target.name)
    )

    if opts.json:
        payload = {
            "schemaVersion": 1,
            "kind": "nixcfg-update-inventory",
            "summary": _build_inventory_summary(targets),
            "targets": [target.to_dict() for target in targets],
        }
        sys.stdout.write(f"{json.dumps(payload)}\n")
        return 0

    _render_inventory_table(targets)
    return 0
