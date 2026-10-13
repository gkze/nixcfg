"""Safely read bounded files from realized immutable flake sources."""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, NamedTuple

from lib.nix.commands.eval import nix_eval_raw
from lib.nix.models.sources import IMMUTABLE_COMMIT_PATTERN as _PERSISTED_COMMIT_PATTERN
from lib.update.config import default_config
from lib.update.flake import flake_source_path_expr
from lib.update.runtime import memoize, resource_slot

if TYPE_CHECKING:
    from lib.nix.models.flake_lock import FlakeLockNode

# The single owner of "this revision is an immutable commit" for updaters and
# release fetches: they validate revisions through this pattern and
# is_immutable_commit(), so a future object format (for example SHA-256 commits)
# changes in one place. That one place is the persisted model constraint
# (lib.nix.models.sources.IMMUTABLE_COMMIT_PATTERN), which must not import this
# layer, so the pattern is compiled from it rather than restated here.
IMMUTABLE_COMMIT_PATTERN = re.compile(_PERSISTED_COMMIT_PATTERN)


class LockedGitHubSource(NamedTuple):
    """Validated immutable GitHub coordinates from a flake lock node."""

    owner: str
    repo: str
    rev: str


def is_immutable_commit(value: str | None) -> bool:
    """Return whether ``value`` is a full lowercase 40-hex commit revision."""
    return value is not None and IMMUTABLE_COMMIT_PATTERN.fullmatch(value) is not None


def locked_github_source(
    node: FlakeLockNode,
    *,
    context: str,
    expected: tuple[str, str] | None = None,
) -> LockedGitHubSource:
    """Return immutable GitHub coordinates from a complete flake lock node.

    ``context`` names the caller in every diagnostic, so each updater keeps its
    own wording while sharing this rule. ``expected`` additionally pins the
    owner/repository pair when the caller knows which upstream it requires.
    """
    locked = node.locked
    if (
        locked is None
        or locked.type != "github"
        or not locked.owner
        or not locked.repo
        or not locked.rev
    ):
        msg = f"{context} must resolve to a complete GitHub source"
        raise RuntimeError(msg)
    if expected is not None and (locked.owner, locked.repo) != expected:
        msg = (
            f"{context} must resolve to {expected[0]}/{expected[1]}, "
            f"got {locked.owner}/{locked.repo}"
        )
        raise RuntimeError(msg)
    if not is_immutable_commit(locked.rev):
        msg = f"{context} revision must be an immutable commit, got {locked.rev!r}"
        raise RuntimeError(msg)
    return LockedGitHubSource(locked.owner, locked.repo, locked.rev)


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
    expression = flake_source_path_expr(node)

    async def realize() -> Path:
        async with resource_slot("eval", source=context, config=default_config()):
            source_path_text = await nix_eval_raw(
                expression,
                command_timeout=command_timeout,
            )
        source_path_text = source_path_text.strip()
        if not source_path_text:
            msg = f"{context} locked source resolved to an empty path"
            raise RuntimeError(msg)
        source = await asyncio.to_thread(
            LockedSource,
            root=Path(source_path_text),
            context=context,
        )
        return source.root

    # The expression includes the immutable revision, content hash and fetch mode.
    # Context is deliberately not shared: each caller retains its own diagnostics.
    root = await memoize("locked_source", f"{command_timeout}:{expression}", realize)
    return await asyncio.to_thread(
        LockedSource,
        root=root,
        context=context,
    )


__all__ = [
    "IMMUTABLE_COMMIT_PATTERN",
    "LockedGitHubSource",
    "LockedSource",
    "is_immutable_commit",
    "locked_github_source",
    "resolve_locked_source",
]
