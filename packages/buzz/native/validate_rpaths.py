"""Validate Buzz desktop deployment targets and app-local library resolution."""

import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Never

from lib.macho import MachOMetadata, read_macho

MAXIMUM_MACOS_VERSION = (14, 0, 0)


def fail(message: str) -> Never:
    """Stop before publishing an app with unresolved or external native edges."""
    detail = f"Buzz candidate {message}"
    raise SystemExit(detail)


@dataclass(frozen=True)
class _AppPaths:
    app: Path
    executable: Path

    def app_local(
        self, path: Path, edge: str, label: str, *, require_directory: bool = False
    ) -> Path:
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(self.app)
        except OSError, ValueError:
            fail(f"{label} escapes Buzz.app: {self.executable} -> {edge}")
        if require_directory:
            if not resolved.is_dir():
                fail(
                    f"{label} is not an app-local directory: {self.executable} -> {edge}"
                )
        elif not resolved.is_file():
            fail(f"{label} is not an app-local file: {self.executable} -> {edge}")
        return resolved

    def origin_path(
        self, value: str, label: str, *, require_directory: bool = False
    ) -> Path | None:
        if value == "@loader_path" or value.startswith("@loader_path/"):
            suffix = value.removeprefix("@loader_path").removeprefix("/")
        elif value == "@executable_path" or value.startswith("@executable_path/"):
            suffix = value.removeprefix("@executable_path").removeprefix("/")
        else:
            return None
        return self.app_local(
            self.executable.parent.joinpath(*PurePosixPath(suffix).parts),
            value,
            label,
            require_directory=require_directory,
        )

    def resolve_rpath(self, dependency: str, rpaths: list[Path]) -> None:
        suffix = PurePosixPath(dependency.removeprefix("@rpath").removeprefix("/"))
        escaped = False
        for rpath in rpaths:
            try:
                candidate = rpath.joinpath(*suffix.parts).resolve(strict=True)
                candidate.relative_to(self.app)
            except ValueError:
                escaped = True
                continue
            except OSError:
                continue
            if candidate.is_file():
                return
        if escaped:
            fail(
                f"dynamic-library edge escapes Buzz.app: {self.executable} -> {dependency}"
            )
        fail(
            f"has an unresolved @rpath dynamic-library edge: {self.executable} -> {dependency}"
        )


def _load_paths(paths: _AppPaths, metadata: MachOMetadata) -> list[Path]:
    rpaths = []
    for value in metadata.rpaths:
        resolved = paths.origin_path(value, "LC_RPATH", require_directory=True)
        if resolved is None:
            fail(f"has a forbidden LC_RPATH: {paths.executable} -> {value}")
        rpaths.append(resolved)
    if any(platform != 1 for platform, _version in metadata.deployment_targets):
        fail(f"is not a macOS executable: {paths.executable}")
    if len(metadata.deployment_targets) != 1:
        fail(f"has no unique macOS deployment target: {paths.executable}")
    version = metadata.deployment_targets[0][1]
    minimum_version = (version >> 16, (version >> 8) & 255, version & 255)
    if minimum_version > MAXIMUM_MACOS_VERSION:
        fail(f"requires macOS newer than 14.0: {paths.executable} -> {minimum_version}")
    return rpaths


def _validate_dependencies(
    paths: _AppPaths, rpaths: list[Path], dependencies: tuple[str, ...]
) -> None:
    for dependency in dependencies:
        normalized = PurePosixPath(dependency)
        if dependency.startswith(("/usr/lib/", "/System/Library/")):
            if normalized.as_posix() != dependency or ".." in normalized.parts:
                fail(
                    f"has a forbidden dynamic-library edge: {paths.executable} -> {dependency}"
                )
            continue
        if paths.origin_path(dependency, "dynamic-library edge") is not None:
            continue
        if dependency == "@rpath" or dependency.startswith("@rpath/"):
            paths.resolve_rpath(dependency, rpaths)
            continue
        fail(
            f"has a forbidden dynamic-library edge: {paths.executable} -> {dependency}"
        )


def validate(app_path: Path, executable_path: Path) -> None:
    """Check one executable's deployment target and every dynamic-library edge."""
    paths = _AppPaths(
        app_path.resolve(strict=True), executable_path.resolve(strict=True)
    )
    try:
        paths.executable.relative_to(paths.app)
    except ValueError:
        fail(f"executable escapes Buzz.app: {paths.executable}")
    try:
        metadata = read_macho(paths.executable)
    except ValueError as error:
        fail(str(error))
    rpaths = _load_paths(paths, metadata)
    _validate_dependencies(paths, rpaths, metadata.dependencies)


if __name__ == "__main__":
    validate(*(Path(value) for value in sys.argv[1:]))
