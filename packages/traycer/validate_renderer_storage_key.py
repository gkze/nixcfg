"""Validate Traycer's production renderer storage-key build contract."""

import os
import re
import sys
from pathlib import Path

_KEY_ENVIRONMENT_VARIABLE = "VITE_DESKTOP_LOCAL_STORAGE_KEY"
_PUBLIC_FALLBACK = b"traycer-desktop-default-secret"
_UNRESOLVED_ENVIRONMENT_TOKEN = _KEY_ENVIRONMENT_VARIABLE.encode()
_COMPILED_ASSET_SUFFIXES = frozenset({".cjs", ".html", ".js", ".mjs"})
_BUNDLED_KEY = re.compile(r"[A-Za-z0-9+/]{64}", flags=re.ASCII)
_EXPECTED_ARGUMENT_COUNT = 2


class RendererKeyValidationError(RuntimeError):
    """The packaged renderer violates its fail-closed storage-key contract."""


def validate_renderer_bundle(renderer_dir: Path, expected_key: str) -> tuple[int, int]:
    """Validate compiled assets without disclosing the expected bundle key."""
    if _BUNDLED_KEY.fullmatch(expected_key) is None:
        msg = "expected renderer storage key must be 64 base64-alphabet characters"
        raise RendererKeyValidationError(msg)
    if not renderer_dir.is_dir():
        msg = f"Traycer renderer is not a directory: {renderer_dir}"
        raise RendererKeyValidationError(msg)

    assets = sorted(
        candidate
        for candidate in renderer_dir.rglob("*")
        if candidate.is_file() and candidate.suffix in _COMPILED_ASSET_SUFFIXES
    )
    if not assets:
        msg = f"Traycer renderer has no compiled assets: {renderer_dir}"
        raise RendererKeyValidationError(msg)

    expected_key_bytes = expected_key.encode()
    key_occurrences = 0
    for asset in assets:
        contents = asset.read_bytes()
        if _UNRESOLVED_ENVIRONMENT_TOKEN in contents:
            msg = f"Traycer renderer contains an unresolved storage-key token: {asset}"
            raise RendererKeyValidationError(msg)
        if _PUBLIC_FALLBACK in contents:
            msg = f"Traycer renderer contains the public development fallback key: {asset}"
            raise RendererKeyValidationError(msg)
        key_occurrences += contents.count(expected_key_bytes)

    if key_occurrences == 0:
        msg = "Traycer renderer does not contain the expected production storage key"
        raise RendererKeyValidationError(msg)
    return len(assets), key_occurrences


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by the Nix install check."""
    args = sys.argv if argv is None else argv
    if len(args) != _EXPECTED_ARGUMENT_COUNT:
        sys.stderr.write("usage: validate_renderer_storage_key.py <renderer-dir>\n")
        return 2

    expected_key = os.environ.get(_KEY_ENVIRONMENT_VARIABLE)
    if expected_key is None:
        sys.stderr.write(
            "validate_renderer_storage_key.py: "
            "VITE_DESKTOP_LOCAL_STORAGE_KEY is not set\n"
        )
        return 1
    try:
        asset_count, key_occurrences = validate_renderer_bundle(
            Path(args[1]),
            expected_key,
        )
    except (OSError, RendererKeyValidationError) as exc:
        sys.stderr.write(f"validate_renderer_storage_key.py: {exc}\n")
        return 1
    sys.stdout.write(
        f"validated {asset_count} Traycer renderer assets "
        f"({key_occurrences} key occurrence{'s' if key_occurrences != 1 else ''})\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover -- direct CLI entry point
    raise SystemExit(main())
