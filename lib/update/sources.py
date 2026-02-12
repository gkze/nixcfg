"""Aggregate per-package sources.json files into a single SourcesFile."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from filelock import FileLock
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.parser import parse

from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.update.paths import REPO_ROOT, package_dir_for, package_file_map


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
    flake_url = f"git+file://{REPO_ROOT}?dirty=1"
    expression = LetExpression(
        local_variables=[
            Binding(
                name="flake",
                value=FunctionCall(
                    name="builtins.getFlake",
                    argument=parse(f'"{flake_url}"').expr,
                ),
            ),
        ],
        value=parse("builtins.attrNames flake.outputs.lib.sources").expr,
    )
    expr = expression.rebuild()
    nix_executable = shutil.which("nix")
    if nix_executable is None:
        msg = "nix executable not found in PATH"
        raise RuntimeError(msg)
    result = subprocess.run(  # noqa: S603
        [nix_executable, "eval", "--impure", "--json", "--expr", expr],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or "nix eval failed"
        msg = f"Failed to evaluate nix source names: {msg}"
        raise RuntimeError(msg)
    payload = json.loads(result.stdout)
    if not isinstance(payload, list) or not all(isinstance(x, str) for x in payload):
        msg = f"Unexpected nix source name payload: {payload!r}"
        raise RuntimeError(msg)
    return set(payload)


def validate_source_discovery_consistency() -> None:
    """Ensure Python and Nix source discovery produce the same keys."""
    py_names = python_source_names()
    nix_names = nix_source_names()
    missing_in_nix = sorted(py_names - nix_names)
    missing_in_python = sorted(nix_names - py_names)
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


def _atomic_write_json(path: Path, payload: object) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            json.dump(payload, tmp_file, indent=2, sort_keys=True)
            tmp_file.write("\n")
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = Path(tmp_file.name)
            if mode is not None:
                os.fchmod(tmp_file.fileno(), mode)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def save_sources(sources: SourcesFile) -> None:
    """Write each entry back to its per-package ``sources.json``.

    Per-package files store a bare entry (not wrapped in ``{name: entry}``).
    """
    path_map = _source_file_map()

    missing: list[str] = []
    for name in sources.entries:
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

    for name, entry in sources.entries.items():
        path = path_map.get(name)
        if path is None:
            continue
        lock_path = path.with_suffix(".json.lock")
        with FileLock(lock_path):
            _atomic_write_json(path, entry.to_dict())


def save_source_entry(path: Path, entry: SourceEntry) -> None:
    """Write one per-package ``sources.json`` entry atomically."""
    _atomic_write_json(path, entry.to_dict())
