"""Validate bb's pristine upstream manifests against evaluator-visible pins."""

import argparse
import json
from pathlib import Path
from typing import cast


class SourceMetadataValidationError(ValueError):
    """Raised when bb's upstream manifests drift from tracked metadata."""


def _read_manifest(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"Expected a JSON object in {path}"
        raise SourceMetadataValidationError(msg)
    return cast("dict[str, object]", payload)


def _require_nonempty_string(
    payload: dict[str, object],
    key: str,
    *,
    context: str,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        msg = f"Expected non-empty string {context}.{key}"
        raise SourceMetadataValidationError(msg)
    return value


def validate_source_metadata(
    source: Path,
    *,
    expected_version: str,
    expected_electron_version: str,
) -> None:
    """Require pristine source manifests to match tracked release metadata."""
    desktop_path = source / "apps" / "desktop" / "package.json"
    bb_app_path = source / "packages" / "bb-app" / "package.json"
    desktop = _read_manifest(desktop_path)
    bb_app = _read_manifest(bb_app_path)

    desktop_version = _require_nonempty_string(
        desktop,
        "version",
        context="desktop package.json",
    )
    if desktop_version != expected_version:
        msg = (
            f"desktop version {desktop_version!r} does not match "
            f"tracked version {expected_version!r}"
        )
        raise SourceMetadataValidationError(msg)

    bb_app_version = _require_nonempty_string(
        bb_app,
        "version",
        context="bb-app package.json",
    )
    if bb_app_version != expected_version:
        msg = (
            f"bb-app version {bb_app_version!r} does not match "
            f"tracked version {expected_version!r}"
        )
        raise SourceMetadataValidationError(msg)

    dev_dependencies = desktop.get("devDependencies")
    if not isinstance(dev_dependencies, dict):
        msg = "Expected desktop package.json.devDependencies to be a JSON object"
        raise SourceMetadataValidationError(msg)
    electron_version = _require_nonempty_string(
        cast("dict[str, object]", dev_dependencies),
        "electron",
        context="desktop package.json.devDependencies",
    )
    if electron_version != expected_electron_version:
        msg = (
            f"Electron version {electron_version!r} does not match "
            f"tracked Electron version {expected_electron_version!r}"
        )
        raise SourceMetadataValidationError(msg)


def main(argv: list[str] | None = None) -> int:
    """Validate command-line-selected source metadata."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-electron-version", required=True)
    args = parser.parse_args(argv)
    validate_source_metadata(
        args.source,
        expected_version=args.expected_version,
        expected_electron_version=args.expected_electron_version,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised through public main
    raise SystemExit(main())
