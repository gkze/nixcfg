"""Aggregate per-package sources.json files into a single SourcesFile."""

from __future__ import annotations

import asyncio
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pathlib import Path

from filelock import FileLock
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.primitive import StringPrimitive

from lib.nix.commands.base import CommandResult, run_nix
from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.update.io import atomic_write_json
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import (
    REPO_ROOT,
    local_flake_url,
    package_dir_for,
    package_file_map,
)
from lib.update.surfaces import validate_repo_update_surface_coverage


def _run_nix_eval(expr: str) -> tuple[int, str, str]:
    command = ["nix", "eval", "--impure", "--json", "--expr", expr]

    def _invoke() -> CommandResult:
        return asyncio.run(run_nix(command, check=False))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        result = _invoke()
    else:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(_invoke).result()
    return result.returncode, result.stdout, result.stderr


def _source_file_map() -> dict[str, Path]:
    """Return ``{name: path}`` for every per-package ``sources.json``."""
    return package_file_map("sources.json")


def _load_entry(path: Path) -> SourceEntry:
    """Load a bare ``SourceEntry`` from a per-package ``sources.json``.

    Per-package files store a single entry directly (not wrapped in a
    ``{name: entry}`` dict like the old monolithic ``sources.json``).
    """
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, list):
        payload = {"hashes": payload}
    return SourceEntry.model_validate(payload)


def load_source_entry(path: Path) -> SourceEntry:
    """Load a single per-package ``sources.json`` entry."""
    return _load_entry(path)


def read_pinned_source_version(name: str) -> str:
    """Read the pinned version from one updater-managed ``sources.json`` file."""
    pkg_dir = package_dir_for(name)
    if pkg_dir is None:
        msg = f"Package directory not found for {name}"
        raise RuntimeError(msg)
    entry = load_source_entry(pkg_dir / "sources.json")
    version = entry.version
    if not isinstance(version, str) or not version:
        msg = f"{name} sources.json is missing a pinned version"
        raise RuntimeError(msg)
    return version


def load_all_sources() -> SourcesFile:
    """Load and merge every per-package ``sources.json`` into one :class:`SourcesFile`."""
    return SourcesFile(
        entries={name: _load_entry(path) for name, path in _source_file_map().items()},
    )


def python_source_names() -> set[str]:
    """Return the set of source names discovered by Python scanning."""
    return set(_source_file_map())


def nix_source_names() -> set[str]:
    """Return the set of source names discovered by ``outputs.lib.sources``."""
    flake_url = local_flake_url(REPO_ROOT)
    expression = LetExpression(
        local_variables=[
            Binding(
                name="flake",
                value=FunctionCall(
                    name=identifier_attr_path("builtins", "getFlake"),
                    argument=StringPrimitive(value=flake_url),
                ),
            ),
        ],
        value=FunctionCall(
            name=identifier_attr_path("builtins", "attrNames"),
            argument=identifier_attr_path("flake", "outputs", "lib", "sources"),
        ),
    )
    expr = expression.rebuild()
    if shutil.which("nix") is None:
        msg = "nix executable not found in PATH"
        raise RuntimeError(msg)
    returncode, stdout_text, stderr_text = _run_nix_eval(expr)
    if returncode != 0:
        msg = stderr_text.strip() or "nix eval failed"
        msg = f"Failed to evaluate nix source names: {msg}"
        raise RuntimeError(msg)
    payload = json.loads(stdout_text)
    if not isinstance(payload, list) or not all(isinstance(x, str) for x in payload):
        msg = f"Unexpected nix source name payload: {payload!r}"
        raise RuntimeError(msg)
    return set(cast("list[str]", payload))


def validate_source_discovery_consistency() -> None:
    """Ensure Python and Nix source discovery produce the same keys."""
    py_names = python_source_names()
    nix_names = nix_source_names()
    missing_in_nix = sorted(py_names - nix_names)
    missing_in_python = sorted(nix_names - py_names)
    validate_repo_update_surface_coverage()

    if not missing_in_nix and not missing_in_python:
        return
    lines = ["Python/Nix source discovery mismatch detected:"]
    if missing_in_nix:
        lines.append(
            f"- Missing in nix outputs.lib.sources: {', '.join(missing_in_nix)}",
        )
    if missing_in_python:
        lines.append(f"- Missing in Python source scan: {', '.join(missing_in_python)}")
    raise RuntimeError("\n".join(lines))


def save_source_updates(
    source_updates: dict[str, SourceEntry],
    *,
    merge_existing: bool = False,
) -> dict[str, SourceEntry]:
    """Write only the supplied entries to their per-package ``sources.json``.

    Per-package files store a bare entry (not wrapped in ``{name: entry}``).
    When ``merge_existing`` is true, read and merge the current entry while
    holding the same per-source lock used for the atomic write.
    """
    path_map = _source_file_map()

    missing: list[str] = []
    for name in source_updates:
        if name in path_map:
            continue
        pkg_dir = package_dir_for(name)
        if pkg_dir is None:
            missing.append(name)
            continue
        path_map[name] = pkg_dir / "sources.json"

    if missing:
        msg = "No per-package sources.json destination found for: " + ", ".join(
            sorted(missing)
        )
        raise RuntimeError(msg)

    persisted_updates: dict[str, SourceEntry] = {}
    for name, entry in source_updates.items():
        path = path_map.get(name)
        if path is None:
            continue
        lock_path = path.with_suffix(".json.lock")
        with FileLock(lock_path):
            persisted_entry = (
                _load_entry(path).merge(entry)
                if merge_existing and path.exists()
                else entry
            )
            atomic_write_json(path, persisted_entry.to_dict())
        persisted_updates[name] = persisted_entry
    return persisted_updates


def save_sources(sources: SourcesFile) -> None:
    """Write every entry in ``sources`` to its per-package ``sources.json``."""
    save_source_updates(sources.entries)


def save_source_entry(path: Path, entry: SourceEntry) -> None:
    """Write one per-package ``sources.json`` entry atomically."""
    atomic_write_json(path, entry.to_dict())
