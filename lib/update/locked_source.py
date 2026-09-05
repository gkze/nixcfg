"""Safely read bounded files from realized immutable flake sources."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from lib.nix.commands.eval import nix_eval_raw
from lib.update.flake import flake_source_path_expr

if TYPE_CHECKING:
    from lib.nix.models.flake_lock import FlakeLockNode


@dataclass(frozen=True, slots=True)
class LockedSource:
    """One canonical realized source root with path-safe bounded readers."""

    root: Path
    context: str

    def __post_init__(self) -> None:
        """Canonicalize and validate the source root once at construction."""
        if not self.root.is_absolute():
            msg = f"{self.context} locked source must resolve to an absolute path"
            raise RuntimeError(msg)
        try:
            source_root = self.root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            msg = f"{self.context} locked source path is unavailable: {self.root}"
            raise RuntimeError(msg) from exc
        if not source_root.is_dir():
            msg = f"{self.context} locked source path is not a directory: {source_root}"
            raise RuntimeError(msg)
        object.__setattr__(self, "root", source_root)

    async def read_bytes(
        self,
        relative_path: str,
        *,
        max_bytes: int,
        description: str,
    ) -> bytes:
        """Read one bounded regular file that remains inside this source tree."""
        if max_bytes < 1:
            msg = "max_bytes must be positive"
            raise ValueError(msg)
        path = _relative_source_path(
            relative_path,
            context=self.context,
            description=description,
        )
        return await asyncio.to_thread(
            _read_bounded_file,
            self.root,
            path,
            max_bytes=max_bytes,
            context=self.context,
            description=description,
        )

    async def read_json(
        self,
        relative_path: str,
        *,
        max_bytes: int,
        description: str,
    ) -> object:
        """Decode one bounded UTF-8 JSON file from this source tree."""
        payload = await self.read_bytes(
            relative_path,
            max_bytes=max_bytes,
            description=description,
        )
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = f"{self.context} {description} is not valid UTF-8"
            raise RuntimeError(msg) from exc
        try:
            return json.loads(text)
        except (json.JSONDecodeError, RecursionError) as exc:
            msg = f"{self.context} {description} is not valid JSON: {exc}"
            raise RuntimeError(msg) from exc


def _relative_source_path(
    relative_path: str,
    *,
    context: str,
    description: str,
) -> PurePosixPath:
    """Validate a nonempty source-relative POSIX path without traversal."""
    if not relative_path or "\0" in relative_path:
        msg = f"{context} {description} path must be a nonempty POSIX path"
        raise RuntimeError(msg)
    path = PurePosixPath(relative_path)
    if path == PurePosixPath() or path.is_absolute():
        msg = f"{context} {description} path must be relative, got {relative_path!r}"
        raise RuntimeError(msg)
    if ".." in path.parts:
        msg = f"{context} {description} path must stay within the source tree"
        raise RuntimeError(msg)
    return path


def _read_bounded_file(
    source_root: Path,
    relative_path: PurePosixPath,
    *,
    max_bytes: int,
    context: str,
    description: str,
) -> bytes:
    """Resolve and read one source file under the declared byte limit."""
    try:
        source_file = source_root.joinpath(*relative_path.parts).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        msg = f"{context} {description} is unavailable at {relative_path}"
        raise RuntimeError(msg) from exc
    if not source_file.is_relative_to(source_root):
        msg = f"{context} {description} path escapes the locked source tree"
        raise RuntimeError(msg)
    if not source_file.is_file():
        msg = f"{context} {description} path is not a regular file: {relative_path}"
        raise RuntimeError(msg)
    try:
        if source_file.stat().st_size > max_bytes:
            msg = f"{context} {description} exceeds {max_bytes} bytes"
            raise RuntimeError(msg)
        payload = source_file.read_bytes()
    except OSError as exc:
        msg = f"{context} {description} could not be read at {relative_path}"
        raise RuntimeError(msg) from exc
    if len(payload) > max_bytes:
        msg = f"{context} {description} exceeds {max_bytes} bytes"
        raise RuntimeError(msg)
    return payload


async def resolve_locked_source(
    node: FlakeLockNode,
    *,
    context: str,
    command_timeout: float,
) -> LockedSource:
    """Realize one locked flake node and return its validated source root."""
    source_path_text = await nix_eval_raw(
        flake_source_path_expr(node),
        command_timeout=command_timeout,
    )
    source_path_text = source_path_text.strip()
    if not source_path_text:
        msg = f"{context} locked source resolved to an empty path"
        raise RuntimeError(msg)
    return await asyncio.to_thread(
        LockedSource,
        root=Path(source_path_text),
        context=context,
    )


__all__ = ["LockedSource", "resolve_locked_source"]
