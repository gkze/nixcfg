"""Validate Buzz desktop runtime load contracts."""

import ctypes
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Never


class AbiVersion(ctypes.Structure):
    """Native return layout of ``skippy_abi_version``."""

    _fields_ = [
        ("major", ctypes.c_uint32),
        ("minor", ctypes.c_uint32),
        ("patch", ctypes.c_uint32),
    ]


def fail(message: str) -> Never:
    """Stop before accepting a runtime with an incompatible native ABI."""
    detail = f"runtime load validation failed: {message}"
    raise SystemExit(detail)


def validate(root_path: Path, skippy_abi: str) -> None:
    """Load the declared libraries and compare their exported Skippy ABI."""
    expected_abi = tuple(int(piece) for piece in skippy_abi.split("."))
    root = root_path
    if root.is_symlink() or not root.is_dir():
        fail(f"runtime root is not a regular directory: {root}")
    root = root.resolve(strict=True)
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        fail("manifest.json is not a regular file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"invalid manifest.json: {error}")
    runtime = manifest.get("runtime") if isinstance(manifest, dict) else None
    libraries = runtime.get("libraries") if isinstance(runtime, dict) else None
    if (
        not isinstance(libraries, list)
        or not libraries
        or not all(isinstance(library, str) and library for library in libraries)
    ):
        fail("runtime.libraries is not a nonempty string list")

    handles = []
    for relative in libraries:
        normalized = PurePosixPath(relative)
        if normalized.is_absolute() or normalized.as_posix() != relative:
            fail(f"runtime library path is not normalized: {relative}")
        if ".." in normalized.parts:
            fail(f"runtime library path is unsafe: {relative}")
        candidate = root.joinpath(*normalized.parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            fail(f"runtime library escapes the bundle: {relative}")
        if not resolved.is_file():
            fail(f"runtime library is not a file: {relative}")
        try:
            handles.append(ctypes.CDLL(str(resolved), mode=ctypes.RTLD_GLOBAL))
        except OSError as error:
            fail(f"could not load {relative}: {error}")

    abi_function = None
    for handle in reversed(handles):
        try:
            abi_function = handle.skippy_abi_version
        except AttributeError:
            continue
        break
    if abi_function is None:
        fail("native runtime symbol not found: skippy_abi_version")
    abi_function.restype = AbiVersion
    version = abi_function()
    actual_abi = (version.major, version.minor, version.patch)
    if actual_abi != expected_abi:
        fail(
            f"Skippy ABI differs from {skippy_abi}: "
            f"{actual_abi[0]}.{actual_abi[1]}.{actual_abi[2]}"
        )


if __name__ == "__main__":
    validate(Path(sys.argv[2]), sys.argv[1])
