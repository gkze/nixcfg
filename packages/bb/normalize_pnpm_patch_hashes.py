"""Normalize pnpm 9 patch IDs to the SHA-256 form required by pnpm 10."""

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import cast

_MINIMUM_PATCH_HASH_REFERENCES = 2


class PnpmPatchHashError(ValueError):
    """Raised when bb's package manifest and frozen lock disagree."""


def _read_manifest(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"Expected a JSON object in {path}"
        raise PnpmPatchHashError(msg)
    return cast("dict[str, object]", payload)


def _patched_dependencies(manifest: dict[str, object]) -> dict[str, object]:
    pnpm = manifest.get("pnpm", {})
    if not isinstance(pnpm, dict):
        msg = "Expected package.json.pnpm object"
        raise PnpmPatchHashError(msg)
    patched = cast("dict[str, object]", pnpm).get("patchedDependencies", {})
    if not isinstance(patched, dict):
        msg = "Expected package.json.pnpm.patchedDependencies object"
        raise PnpmPatchHashError(msg)
    return cast("dict[str, object]", patched)


def _lock_patch_hash(
    lock_text: str,
    *,
    package_name: str,
    relative_path: str,
) -> str:
    quoted_or_plain_name = rf"(?:'{re.escape(package_name)}'|{re.escape(package_name)})"
    pattern = re.compile(
        rf"^  {quoted_or_plain_name}:\n"
        rf"    hash: (?P<hash>[a-z0-9]+)\n"
        rf"    path: {re.escape(relative_path)}$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(lock_text))
    if len(matches) != 1:
        msg = (
            f"Expected exactly one pnpm lock entry for {package_name!r} "
            f"and {relative_path!r}, got {len(matches)}"
        )
        raise PnpmPatchHashError(msg)
    return matches[0].group("hash")


def normalize_pnpm_patch_hashes(source: Path) -> None:
    """Rewrite every pnpm lock reference to each patch's SHA-256 digest."""
    manifest = _read_manifest(source / "package.json")
    patched = _patched_dependencies(manifest)
    if not patched:
        return

    lock_path = source / "pnpm-lock.yaml"
    lock_text = lock_path.read_text(encoding="utf-8")
    replacements: list[tuple[str, str]] = []
    for package_name, relative_path_value in patched.items():
        if not isinstance(package_name, str) or not package_name:
            msg = "Expected a non-empty patched dependency name"
            raise PnpmPatchHashError(msg)
        if not isinstance(relative_path_value, str) or not relative_path_value:
            msg = f"Expected a non-empty patch path for {package_name!r}"
            raise PnpmPatchHashError(msg)
        relative_path = PurePosixPath(relative_path_value)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            msg = f"Patch path for {package_name!r} escapes the source tree"
            raise PnpmPatchHashError(msg)
        old_hash = _lock_patch_hash(
            lock_text,
            package_name=package_name,
            relative_path=relative_path_value,
        )
        patch_path = source.joinpath(*relative_path.parts)
        if not patch_path.is_file():
            msg = f"Patch path for {package_name!r} is not a regular file"
            raise PnpmPatchHashError(msg)
        new_hash = hashlib.sha256(patch_path.read_bytes()).hexdigest()
        replacements.append((old_hash, new_hash))

    normalized = lock_text
    for old_hash, new_hash in replacements:
        occurrences = normalized.count(old_hash)
        if occurrences < _MINIMUM_PATCH_HASH_REFERENCES:
            msg = f"Expected pnpm patch hash {old_hash!r} in metadata and a lock entry"
            raise PnpmPatchHashError(msg)
        normalized = normalized.replace(old_hash, new_hash)
    if normalized != lock_text:
        lock_path.write_text(normalized, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Normalize the selected source tree for pnpm 10 frozen installs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args(argv)
    normalize_pnpm_patch_hashes(args.source)
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised through public main
    raise SystemExit(main())
