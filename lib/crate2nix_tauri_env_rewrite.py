"""Rewrite crate2nix Tauri env exports to stable metadata paths."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

_EXPORT_RE = re.compile(r"^(export\s+)([^=]+)(=.*)$")
_TEMP_SOURCE_PREFIXES = (
    "/nix/var/nix/builds/",
    Path("/private").joinpath("tmp").as_posix() + "/",
    Path("/").joinpath("tmp").as_posix() + "/",
)


def _env_path(name: str) -> Path:
    value = os.environ.get(name)
    if value is None or not value:
        msg = f"Missing required environment variable {name}"
        raise RuntimeError(msg)
    return Path(value)


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        return
    shutil.copy2(source, destination)


def _rewrite_nested_json_file(destination: Path, metadata_dir: Path, key: str) -> None:
    try:
        payload = json.loads(destination.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(payload, list) or not all(
        isinstance(item, str) for item in payload
    ):
        return

    rewritten: list[str] = []
    nested_dir = metadata_dir / f"{key}-files"
    nested_dir.mkdir(parents=True, exist_ok=True)
    for item in payload:
        nested_source = Path(item)
        if nested_source.is_absolute() and nested_source.exists():
            nested_dest = nested_dir / nested_source.name
            _copy_path(nested_source, nested_dest)
            rewritten.append(str(nested_dest))
        else:
            rewritten.append(item)
    destination.write_text(json.dumps(rewritten))


def _materialize_metadata_path(source_path: Path, metadata_dir: Path, key: str) -> Path:
    destination = metadata_dir / f"{key}-{source_path.name}"
    _copy_path(source_path, destination)
    if destination.is_file():
        _rewrite_nested_json_file(destination, metadata_dir, key)
    return destination


def _rewrite_line(line: str, metadata_dir: Path) -> str:
    match = _EXPORT_RE.match(line)
    if match is None:
        return line

    prefix, name, suffix = match.groups()
    raw_value = suffix[1:]
    quoted = raw_value.startswith('"') and raw_value.endswith('"')
    value = raw_value[1:-1] if quoted else raw_value
    source_path = Path(value)
    normalized_name = name.replace(":", "_")
    normalized_key = normalized_name.lower()

    if (
        source_path.is_absolute()
        and source_path.exists()
        and str(source_path).startswith(_TEMP_SOURCE_PREFIXES)
    ):
        value = str(
            _materialize_metadata_path(source_path, metadata_dir, normalized_key)
        )

    rendered_suffix = f'="{value}"' if quoted else f"={value}"
    return f"{prefix}{normalized_name}{rendered_suffix}"


def rewrite_env_file(env_path: Path, metadata_dir: Path) -> None:
    """Rewrite one env export file in place."""
    if not env_path.exists():
        return
    lines = [
        _rewrite_line(line, metadata_dir) for line in env_path.read_text().splitlines()
    ]
    env_path.write_text("\n".join(lines) + "\n")


def main() -> int:
    """Rewrite the crate2nix Tauri env files named in the process environment."""
    metadata_dir = _env_path("TAURI_ENV_METADATA_DIR")
    metadata_dir.mkdir(parents=True, exist_ok=True)
    for env_path in (_env_path("TAURI_ENV_OUT"), _env_path("TAURI_ENV_LIB")):
        rewrite_env_file(env_path, metadata_dir)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
