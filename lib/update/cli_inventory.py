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

from lib.update.updaters.base import (
    ChecksumProvidedUpdater,
    DenoManifestUpdater,
    DownloadHashUpdater,
    FlakeInputHashUpdater,
    FlakeInputUpdater,
    HashEntryUpdater,
    Updater,
    UvLockUpdater,
)
from lib.update.updaters.platform_api import PlatformAPIUpdater

if TYPE_CHECKING:
    from collections.abc import Callable

    from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode
    from lib.nix.models.sources import SourceEntry, SourcesFile
    from lib.update.cli_options import UpdateOptions
    from lib.update.refs import FlakeInputRef


@dataclass(frozen=True)
class InventoryDependencies:
    """Collaborators required to build update inventory rows."""

    load_sources: Callable[[], SourcesFile]
    source_path_map: Callable[[str], dict[str, Path]]
    list_ref_inputs: Callable[[], list[FlakeInputRef]]
    load_lock: Callable[[], FlakeLock]
    get_updaters: Callable[[], dict[str, type[Updater]]]
    source_file_for: Callable[[str], Path | None]
    resolve_root_input_node: Callable[
        [FlakeLock, str], tuple[FlakeLockNode | None, str | None]
    ]
    source_backing_input_name: Callable[
        [str, type[Updater] | None, SourceEntry | None], str | None
    ]
    generated_artifact_paths: Callable[[str, type[Updater]], tuple[str, ...]]
    source_hash_kinds: Callable[[SourceEntry | None], tuple[str, ...]]
    classify_updater_kind: Callable[[type[Updater]], str]
    repo_relative_path: Callable[[Path | None], str | None]


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


@dataclass(frozen=True)
class _ListRow:
    name: str
    item_type: str
    source: str
    ref: str | None
    rev: str | None


def _row_sort_value(row: _ListRow, sort_by: str) -> str:
    if sort_by == "type":
        return row.item_type
    if sort_by == "source":
        return row.source
    if sort_by == "ref":
        return row.ref or ""
    if sort_by == "rev":
        return row.rev or ""
    return row.name


def _flake_source_string(node: FlakeLockNode | None, follows: str | None) -> str:
    original = node.original if node is not None else None
    locked = node.locked if node is not None else None

    source_type = (
        original.type
        if original is not None and original.type
        else locked.type
        if locked is not None
        else None
    )
    owner = (
        original.owner
        if original is not None and original.owner
        else locked.owner
        if locked is not None
        else None
    )
    repo = (
        original.repo
        if original is not None and original.repo
        else locked.repo
        if locked is not None
        else None
    )
    url = (
        original.url
        if original is not None and original.url
        else locked.url
        if locked is not None
        else None
    )
    path = (
        original.path
        if original is not None and original.path
        else locked.path
        if locked is not None
        else None
    )

    source = "<unknown>"
    if source_type in {"github", "gitlab"} and owner and repo:
        source = f"{source_type}:{owner}/{repo}"
    elif source_type and url:
        source = f"{source_type}:{url}"
    elif source_type and path:
        source = f"{source_type}:{path}"
    elif url:
        source = url
    elif path:
        source = path
    elif follows:
        source = f"follows:{follows}"
    elif source_type:
        source = source_type
    return source


def _repo_relative_path(
    path: Path | None, *, repo_root: Callable[[], Path]
) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(repo_root()))
    except ValueError:
        return str(path)


def _source_backing_input_name(
    name: str,
    updater_cls: type[Updater] | None,
    entry: SourceEntry | None = None,
) -> str | None:
    if updater_cls is not None:
        input_name = getattr(updater_cls, "input_name", None)
        if isinstance(input_name, str) and input_name:
            return input_name
        if issubclass(updater_cls, FlakeInputUpdater):
            return name
    if entry is not None and entry.input:
        return entry.input
    return None


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
    *,
    package_dir_for: Callable[[str], Path | None],
    repo_relative_path: Callable[[Path | None], str | None],
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
            if (resolved := repo_relative_path(pkg_dir / relative)) is not None
        )

    if not artifact_paths:
        artifact_name: str | None = None
        if issubclass(updater_cls, UvLockUpdater):
            artifact_name = getattr(updater_cls, "lock_file", "uv.lock")
        elif issubclass(updater_cls, DenoManifestUpdater):
            artifact_name = getattr(updater_cls, "manifest_file", "deno-deps.json")

        if artifact_name is not None:
            path = repo_relative_path(pkg_dir / artifact_name)
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


def build_update_inventory(
    *, dependencies: InventoryDependencies
) -> list[_InventoryTarget]:
    """Build logical update targets from one cohesive dependency bundle."""
    sources = dependencies.load_sources()
    path_map = dependencies.source_path_map("sources.json")
    ref_inputs = {item.name: item for item in dependencies.list_ref_inputs()}
    lock = dependencies.load_lock()
    updaters = dependencies.get_updaters()

    targets: list[_InventoryTarget] = []
    all_names = sorted(set(updaters) | set(ref_inputs))
    for name in all_names:
        updater_cls = updaters.get(name)
        entry = sources.entries.get(name)
        ref_input = ref_inputs.get(name)
        source_backing_input = dependencies.source_backing_input_name(
            name,
            updater_cls,
            entry,
        )
        backing_input = source_backing_input or (
            ref_input.name if ref_input is not None else None
        )
        generated_artifacts = (
            ()
            if updater_cls is None
            else dependencies.generated_artifact_paths(name, updater_cls)
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
            node, _follows = dependencies.resolve_root_input_node(lock, name)
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
                path=dependencies.repo_relative_path(
                    path_map.get(name) or dependencies.source_file_for(name)
                ),
                version=entry.version if entry is not None else None,
                commit=entry.commit if entry is not None else None,
                hash_kinds=dependencies.source_hash_kinds(entry),
                updater_kind=dependencies.classify_updater_kind(updater_cls),
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


def _collect_flake_inputs_for_list(
    *,
    load_lock: Callable[[], FlakeLock],
    resolve_root_input_node: Callable[
        [FlakeLock, str], tuple[FlakeLockNode | None, str | None]
    ],
    flake_source_string: Callable[[FlakeLockNode | None, str | None], str],
    get_flake_input_version: Callable[[FlakeLockNode], str],
) -> list[_ListRow]:
    lock = load_lock()
    root_inputs = lock.root_node.inputs or {}
    items: list[_ListRow] = []

    for input_name in sorted(root_inputs):
        node, follows = resolve_root_input_node(lock, input_name)
        source = flake_source_string(node, follows)

        original = node.original if node is not None else None
        locked = node.locked if node is not None else None
        selector = getattr(original, "rev", None) if original is not None else None
        ref = original.ref if original is not None else None
        if ref is None and isinstance(selector, str):
            ref = selector
        if ref is None and node is not None:
            inferred = get_flake_input_version(node)
            if inferred != "unknown":
                ref = inferred
        rev = locked.rev if locked is not None else None

        items.append(
            _ListRow(
                name=input_name,
                item_type="flake",
                source=source,
                ref=ref,
                rev=rev,
            )
        )

    return items


def _collect_source_entries_for_list(
    *,
    load_sources: Callable[[], SourcesFile],
    source_path_map: Callable[[str], dict[str, Path]],
) -> list[_ListRow]:
    sources = load_sources()
    path_map = source_path_map("sources.json")
    items: list[_ListRow] = []

    for name in sorted(path_map):
        entry = sources.entries.get(name)
        source = "<none>"
        if entry is not None and entry.urls:
            urls = sorted(set(entry.urls.values()))
            source = urls[0] if len(urls) == 1 else f"{urls[0]} (+{len(urls) - 1} more)"
        ref = entry.version if entry is not None else None
        rev = entry.commit if entry is not None else None
        items.append(
            _ListRow(
                name=name,
                item_type="sources.json",
                source=source,
                ref=ref,
                rev=rev,
            )
        )

    return items


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


def _handle_list_targets_request(
    opts: UpdateOptions,
    *,
    dependencies: InventoryDependencies,
) -> int | None:
    if not opts.list_targets:
        return None

    targets = build_update_inventory(dependencies=dependencies)
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
