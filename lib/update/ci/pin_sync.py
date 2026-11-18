"""Keep a flake input and the source files that name it on one pin.

Update 44258507999 advanced ``goose`` and ``superset`` in the pins seed, then
restored ``sources.json`` to the baseline without the input. Plan-shards
evaluated the new revision against the old source metadata and aborted.
A bump is promotable only when the input and every file the source owns move
together. An input that moved while those files stayed on the baseline is put
back, and every other input in the same flake files stays.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from lib.nix.models.flake_lock import FlakeLock
from lib.nix.models.sources import SourceEntry
from lib.update import artifacts as update_artifacts
from lib.update import crate2nix as update_crate2nix
from lib.update import planner as update_planner
from lib.update.ci.superset_hold import FileBlob
from lib.update.flake_inputs import input_declaration_text, restore_input_declaration
from lib.update.lock_policy import LockRollback, render_lock, rollback_input_locks
from lib.update.paths import package_file_map_in, sources_file_for_updater
from lib.update.updaters import Updater, ensure_updaters_loaded

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_FLAKE_NIX = "flake.nix"
_FLAKE_LOCK = "flake.lock"


def owned_paths_by_input(repo: Path) -> dict[str, frozenset[str]]:
    """Return each backing input and the repo-relative files its sources own."""
    root = repo.resolve()
    updaters = ensure_updaters_loaded()
    source_paths = package_file_map_in(root, "sources.json")
    updater_paths = package_file_map_in(root, "updater.py")
    owned: dict[str, set[str]] = {}
    for name, updater in updaters.items():
        inputs = _input_names(name, updater)
        if not inputs:
            continue
        relative = _owned_relative_paths(
            root,
            name,
            updater,
            source_paths=source_paths,
            updater_paths=updater_paths,
        )
        if not relative:
            continue
        for input_name in inputs:
            owned.setdefault(input_name, set()).update(relative)
    return {input_name: frozenset(paths) for input_name, paths in owned.items()}


def rewind_unpaired_inputs(
    base: dict[str, FileBlob],
    merged: dict[str, FileBlob],
    owned_by_input: Mapping[str, frozenset[str]],
) -> tuple[dict[str, FileBlob], bool]:
    """Return ``merged`` with baseline pins restored where source files did not move.

    The second value is true when the returned map differs from ``merged``.
    An input stays when any file its sources own changed, or when its
    declaration is not an attribute set that can be restored on its own.
    """
    unpaired = tuple(
        name
        for name, paths in sorted(owned_by_input.items())
        if _unpaired(base, merged, paths) and _input_moved(base, merged, name)
    )
    spliceable = tuple(name for name in unpaired if _can_splice(base, merged, name))
    if not spliceable:
        return merged, False
    restored = dict(merged)
    if not _restore_declarations(base, restored, spliceable):
        return merged, False
    if not _restore_lock(base, restored, spliceable):
        return merged, False
    return restored, restored != merged


def _input_names(name: str, updater: type[Updater]) -> tuple[str, ...]:
    """Return the flake inputs one source's files are checked against."""
    backing = update_planner.source_backing_input_name(name, updater)
    return tuple(
        dict.fromkeys((
            *((backing,) if backing else ()),
            *update_planner.source_additional_input_names(updater),
        ))
    )


def _owned_relative_paths(
    root: Path,
    name: str,
    updater: type[Updater],
    *,
    source_paths: Mapping[str, Path],
    updater_paths: Mapping[str, Path],
) -> frozenset[str]:
    """Return repo-relative files one source updater may rewrite."""
    repo = root.resolve()
    paths: set[str] = set()
    source = source_paths.get(name)
    updater_path = updater_paths.get(name)
    if source is None and updater_path is not None:
        source = sources_file_for_updater(updater_path, name)
    if source is not None:
        relative = _inside_repo(repo, source)
        if relative is not None:
            paths.add(relative)
    if updater_path is not None and updater_path.name == "updater.py":
        for artifact in updater.get_generated_artifact_files():
            relative = _inside_repo(repo, updater_path.parent / artifact)
            if relative is not None:
                paths.add(relative)
    target = update_crate2nix.TARGETS.get(name)
    if target is not None:
        for artifact in target.artifact_paths:
            relative = _inside_repo(repo, artifact)
            if relative is not None:
                paths.add(relative)
    return frozenset(paths)


def _inside_repo(root: Path, path: Path) -> str | None:
    """Return a repo-relative path, or None when it would leave the repository."""
    repo = root
    candidate = path
    try:
        resolved = update_artifacts.resolve_repo_path(candidate, repo_root=repo)
    except RuntimeError:
        return None
    return resolved.relative_to(repo.resolve()).as_posix()


def _unpaired(
    base: Mapping[str, FileBlob],
    merged: Mapping[str, FileBlob],
    paths: frozenset[str],
) -> bool:
    """Return whether every owned file stayed put and a source entry proves it."""
    if not paths or any(base.get(path) != merged.get(path) for path in paths):
        return False
    return any(
        _parsed_source_entry(base.get(path))
        for path in paths
        if path.endswith("/sources.json") or path == "sources.json"
    )


def _parsed_source_entry(blob: FileBlob | None) -> bool:
    """Return whether ``blob`` is a source entry the updater would persist."""
    if blob is None:
        return False
    try:
        SourceEntry.model_validate(json.loads(blob.content))
    except UnicodeError, ValueError:
        return False
    return True


def _input_moved(
    base: Mapping[str, FileBlob],
    merged: Mapping[str, FileBlob],
    name: str,
) -> bool:
    """Return whether ``name``'s declaration or lock node differs."""
    return _declaration(merged.get(_FLAKE_NIX), name) != _declaration(
        base.get(_FLAKE_NIX), name
    ) or _lock_node(merged.get(_FLAKE_LOCK), name) != _lock_node(
        base.get(_FLAKE_LOCK), name
    )


def _declaration(blob: FileBlob | None, name: str) -> str | None:
    """Return one input attrset, or None when the file has no such declaration."""
    if blob is None:
        return None
    try:
        return input_declaration_text(blob.content.decode(), name)
    except UnicodeError, ValueError:
        return None


def _lock_node(blob: FileBlob | None, name: str) -> object:
    """Return one lock node, or None when the lock does not name it."""
    if blob is None:
        return None
    try:
        payload = json.loads(blob.content)
    except UnicodeError, json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    nodes = payload.get("nodes")
    if not isinstance(nodes, dict):
        return None
    return nodes.get(name)


def _can_splice(
    base: Mapping[str, FileBlob],
    merged: Mapping[str, FileBlob],
    name: str,
) -> bool:
    """Return whether both flakes declare ``name`` as an attribute set."""
    return (
        _declaration(base.get(_FLAKE_NIX), name) is not None
        and _declaration(merged.get(_FLAKE_NIX), name) is not None
    )


def _restore_declarations(
    base: Mapping[str, FileBlob],
    restored: dict[str, FileBlob],
    names: tuple[str, ...],
) -> bool:
    """Splice baseline attrsets into the candidate flake."""
    current = restored.get(_FLAKE_NIX)
    baseline = base.get(_FLAKE_NIX)
    if current is None or baseline is None:
        return False
    text = current.content.decode()
    base_text = baseline.content.decode()
    for name in names:
        text = restore_input_declaration(text, base_text, name)
    restored[_FLAKE_NIX] = FileBlob(current.mode, text.encode())
    return True


def _restore_lock(
    base: Mapping[str, FileBlob],
    restored: dict[str, FileBlob],
    names: tuple[str, ...],
) -> bool:
    """Put baseline lock nodes back without rewriting inputs outside ``names``."""
    current = restored.get(_FLAKE_LOCK)
    baseline = base.get(_FLAKE_LOCK)
    if current is None or baseline is None:
        return False
    try:
        rollback = rollback_input_locks(
            FlakeLock.model_validate_json(baseline.content),
            FlakeLock.model_validate_json(current.content),
            frozenset(names),
        )
    except UnicodeError, ValidationError:
        return False
    if isinstance(rollback, LockRollback):
        restored[_FLAKE_LOCK] = FileBlob(
            current.mode, render_lock(rollback.lock).encode()
        )
        return True
    if rollback.reason != "failed inputs already match the baseline lock":
        return False
    # A follows edge or a missing baseline node is reported as a match even
    # though the node did not move back. Splicing the declaration then would
    # leave the lock on the candidate pin.
    return all(
        _lock_node(baseline, name) == _lock_node(current, name) for name in names
    )


__all__ = [
    "owned_paths_by_input",
    "rewind_unpaired_inputs",
]
