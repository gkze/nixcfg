"""Cargo.lock parsing and importCargoLock hash computation."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, cast

import aiohttp
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.parser import parse

from lib.nix.models.hash import is_sri
from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.events import (
    CommandResult,
    EventStream,
    GatheredValues,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    gather_event_streams,
    require_value,
)
from lib.update.flake import get_flake_input_node
from lib.update.net import fetch_url
from lib.update.process import run_command

if TYPE_CHECKING:
    from lib.update.updaters.base import CargoLockGitDep


_CARGO_LOCK_GIT_SOURCE_RE = re.compile(
    r'^source = "git\+(?P<url>[^?#]+)\?[^#]*#(?P<commit>[0-9a-f]+)"$',
)


def _parse_cargo_lock_git_sources(  # noqa: C901
    lockfile_content: str,
    git_deps: list[CargoLockGitDep],
) -> dict[str, tuple[str, str]]:
    """Parse a Cargo.lock and return ``{git_dep_name: (url, rev)}`` for each dep.

    Multiple crates may share the same git URL; we deduplicate by matching each
    ``CargoLockGitDep`` to the first ``[[package]]`` whose ``name`` starts with
    the dep's ``match_name``.
    """
    result: dict[str, tuple[str, str]] = {}
    unmatched = {dep.git_dep: dep for dep in git_deps}

    def _select_dep(dep_key: str, crate_name: str) -> CargoLockGitDep | None:
        direct = unmatched.get(dep_key)
        if direct is not None:
            return direct
        prefix_matches = [
            dep for dep in unmatched.values() if crate_name.startswith(dep.match_name)
        ]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        return None

    current_name: str | None = None
    current_version: str | None = None

    for raw_line in lockfile_content.splitlines():
        line = raw_line.strip()
        if line.startswith("name = "):
            current_name = line.split('"')[1]
            current_version = None
        elif line.startswith("version = ") and '"' in line:
            current_version = line.split('"')[1]
        elif line.startswith("source = ") and current_name is not None:
            match = _CARGO_LOCK_GIT_SOURCE_RE.match(line)
            if match is None:
                continue
            url, commit = match.group("url"), match.group("commit")
            dep_key = (
                f"{current_name}-{current_version}" if current_version else current_name
            )
            selected = _select_dep(dep_key, current_name)
            if selected is not None:
                result[selected.git_dep] = (url, commit)
                del unmatched[selected.git_dep]

    if unmatched:
        msg = f"Could not find git sources in Cargo.lock for: {list(unmatched)}"
        raise RuntimeError(
            msg,
        )
    return result


async def _prefetch_git_hash(
    source: str,
    url: str,
    rev: str,
    *,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Fetch a git repo and yield its SRI narHash via ``builtins.fetchGit``."""
    config = resolve_active_config(config)
    fetch_git = FunctionCall(
        name="builtins.fetchGit",
        argument=AttributeSet.from_dict(
            {
                "url": url,
                "rev": rev,
                "allRefs": True,
            },
        ),
    )
    expr = parse(f"({fetch_git.rebuild()}).narHash").expr.rebuild()
    args = ["nix", "eval", "--json", "--expr", expr]
    result_drain = ValueDrain[CommandResult]()
    async for event in drain_value_events(
        run_command(
            args,
            source=source,
            error="builtins.fetchGit failed",
            config=config,
        ),
        result_drain,
    ):
        yield event
    result = require_value(result_drain, "builtins.fetchGit did not return output")
    if result.returncode != 0:
        msg = f"builtins.fetchGit failed:\n{result.stderr}"
        raise RuntimeError(msg)
    sri_hash = json.loads(result.stdout)
    if not isinstance(sri_hash, str) or not is_sri(sri_hash):
        msg = f"Unexpected hash format from builtins.fetchGit: {sri_hash}"
        raise RuntimeError(msg)
    yield UpdateEvent.value(source, sri_hash)


async def compute_import_cargo_lock_output_hashes(
    source: str,
    input_name: str,
    *,
    lockfile_path: str,
    git_deps: list[CargoLockGitDep],
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute ``importCargoLock`` output hashes via ``builtins.fetchGit``.

    Parses the upstream Cargo.lock to extract git dependency URLs and revisions,
    then prefetches each one directly.  This avoids evaluating nixpkgs entirely
    and works regardless of inter-repo workspace dependencies.
    """
    config = resolve_active_config(config)

    yield UpdateEvent.status(source, "Fetching upstream Cargo.lock...")
    node = get_flake_input_node(input_name)
    locked = node.locked
    if locked is None:
        msg = f"Flake input '{input_name}' has no locked info"
        raise RuntimeError(msg)
    owner = locked.owner
    repo = locked.repo
    rev = locked.rev
    if not all([owner, repo, rev]):
        msg = f"Flake input '{input_name}' missing owner/repo/rev in locked info"
        raise RuntimeError(
            msg,
        )

    lockfile_url = (
        f"https://raw.githubusercontent.com/{owner}/{repo}/{rev}/{lockfile_path}"
    )
    async with aiohttp.ClientSession() as session:
        payload = await fetch_url(
            session,
            lockfile_url,
            request_timeout=config.default_timeout,
            config=config,
            user_agent=config.default_user_agent,
        )
    lockfile_content = payload.decode(errors="replace")

    git_sources = _parse_cargo_lock_git_sources(lockfile_content, git_deps)

    streams = {
        dep.git_dep: _prefetch_git_hash(
            source,
            *git_sources[dep.git_dep],
            config=config,
        )
        for dep in git_deps
    }
    async for item in gather_event_streams(streams):
        if isinstance(item, GatheredValues):
            yield UpdateEvent.value(source, cast("dict[str, str]", item.values))
        else:
            yield item


__all__ = [
    "compute_import_cargo_lock_output_hashes",
]
