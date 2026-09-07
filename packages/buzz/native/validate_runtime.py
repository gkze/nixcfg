"""Validate Buzz desktop runtime contracts."""

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Never

DIGEST = re.compile(r"[0-9a-f]{64}")


def fail(message: str) -> Never:
    """Stop before accepting an invalid or incomplete runtime bundle."""
    detail = f"runtime validation failed: {message}"
    raise SystemExit(detail)


def _read_runtime(root: Path) -> dict[str, object]:
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        fail("manifest.json is not a regular file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"invalid manifest.json: {error}")
    if not isinstance(manifest, dict) or set(manifest) != {"runtime"}:
        fail("manifest top-level schema differs from the reviewed runtime")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        fail("manifest has no runtime object")
    return runtime


def _validate_metadata(
    runtime: dict[str, object], mesh_version: str, skippy_abi: str
) -> None:
    if set(runtime) != {
        "backend",
        "files",
        "id",
        "libraries",
        "mesh_version",
        "platform",
        "rank",
        "skippy_abi",
    }:
        fail("runtime schema differs from the reviewed runtime")
    if runtime.get("id") != "meshllm-native-runtime-darwin-aarch64-metal":
        fail("runtime.id differs from the reviewed runtime")
    if runtime.get("mesh_version") != mesh_version:
        fail("runtime.mesh_version differs from the reviewed Mesh version")
    if runtime.get("skippy_abi") != skippy_abi:
        fail("runtime.skippy_abi differs from the reviewed ABI")
    if runtime.get("platform") != {
        "os": "macos",
        "arch": "aarch64",
        "target": "aarch64-apple-darwin",
    }:
        fail("runtime.platform differs from the reviewed target")
    if runtime.get("backend") != {"kind": "metal"}:
        fail("runtime.backend differs from the reviewed Metal backend")
    if type(runtime.get("rank")) is not int or runtime.get("rank") != 0:
        fail("runtime.rank differs from the reviewed runtime")


def _validate_files(root: Path, files: dict[object, object]) -> set[str]:
    expected = set()
    for relative, expected_digest in files.items():
        if not isinstance(relative, str) or not relative:
            fail("manifest contains an invalid file path")
        normalized = PurePosixPath(relative)
        if normalized.is_absolute() or normalized.as_posix() != relative:
            fail(f"manifest file path is not normalized: {relative}")
        if ".." in normalized.parts or relative == "manifest.json":
            fail(f"manifest file path is unsafe: {relative}")
        if (
            not isinstance(expected_digest, str)
            or DIGEST.fullmatch(expected_digest) is None
        ):
            fail(f"manifest digest is invalid: {relative}")
        candidate = root.joinpath(*normalized.parts)
        if not candidate.is_file():
            fail(f"runtime file is missing: {relative}")
        try:
            candidate.resolve(strict=True).relative_to(root)
        except OSError, ValueError:
            fail(f"runtime file escapes the bundle: {relative}")
        actual_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual_digest != expected_digest:
            fail(f"runtime digest mismatch: {relative}")
        expected.add(relative)
    return expected


def validate(root_path: Path, mesh_version: str, skippy_abi: str) -> None:
    """Verify metadata, file digests, and exact library coverage of a runtime."""
    if root_path.is_symlink() or not root_path.is_dir():
        fail(f"runtime root is not a regular directory: {root_path}")
    root = root_path.resolve(strict=True)
    runtime = _read_runtime(root)
    _validate_metadata(runtime, mesh_version, skippy_abi)
    files = runtime.get("files")
    if not isinstance(files, dict) or not files:
        fail("manifest runtime.files is not a nonempty object")
    libraries = runtime.get("libraries")
    if (
        not isinstance(libraries, list)
        or not libraries
        or not all(isinstance(library, str) and library for library in libraries)
    ):
        fail("runtime.libraries is not a nonempty string list")
    if len(set(libraries)) != len(libraries):
        fail("runtime.libraries contains duplicates")
    expected = _validate_files(root, files)
    manifest_path = root / "manifest.json"
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != expected:
        fail(
            "runtime file inventory differs from manifest: "
            f"missing={sorted(expected - actual)!r}, extra={sorted(actual - expected)!r}"
        )
    if any(library not in expected for library in libraries):
        fail("runtime libraries are not all covered by runtime.files")


if __name__ == "__main__":
    validate(Path(sys.argv[3]), sys.argv[1], sys.argv[2])
