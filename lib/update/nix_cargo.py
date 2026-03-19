"""Cargo.lock parsing and importCargoLock hash computation."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import aiohttp
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.hash import is_sri
from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.events import (
    CommandResult,
    EventStream,
    GatheredValues,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_command_result,
    expect_str,
    gather_event_streams,
    require_value,
)
from lib.update.flake import get_flake_input_node
from lib.update.net import fetch_url
from lib.update.nix_expr import identifier_attr_path
from lib.update.process import RunCommandOptions, run_command

if TYPE_CHECKING:
    from lib.update.updaters.base import CargoLockGitDep


_CARGO_LOCK_GIT_SOURCE_RE = re.compile(
    r'^source = "git\+(?P<url>[^?#]+)\?[^#]*#(?P<commit>[0-9a-f]+)"$',
)


def _parse_quoted_assignment(line: str, field: str) -> str | None:
    prefix = f'{field} = "'
    if not line.startswith(prefix) or '"' not in line:
        return None
    return line.split('"')[1]


def _select_matching_git_dep(
    unmatched: dict[str, CargoLockGitDep],
    *,
    dep_key: str,
    crate_name: str,
) -> CargoLockGitDep | None:
    direct = unmatched.get(dep_key)
    if direct is not None:
        return direct
    exact_matches = [dep for dep in unmatched.values() if crate_name == dep.match_name]
    if len(exact_matches) == 1:
        return exact_matches[0]
    prefix_matches = [
        dep for dep in unmatched.values() if crate_name.startswith(f"{dep.match_name}-")
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    return None


def _parse_git_source_line(line: str) -> tuple[str, str] | None:
    match = _CARGO_LOCK_GIT_SOURCE_RE.match(line)
    if match is None:
        return None
    return match.group("url"), match.group("commit")


def _record_git_source_match(
    *,
    current_name: str,
    current_version: str | None,
    git_source: tuple[str, str],
    unmatched: dict[str, CargoLockGitDep],
    result: dict[str, tuple[str, str]],
) -> None:
    dep_key = f"{current_name}-{current_version}" if current_version else current_name
    selected = _select_matching_git_dep(
        unmatched,
        dep_key=dep_key,
        crate_name=current_name,
    )
    if selected is None:
        return
    result[selected.git_dep] = git_source
    del unmatched[selected.git_dep]


def _parse_cargo_lock_git_sources(
    lockfile_content: str,
    git_deps: list[CargoLockGitDep],
) -> dict[str, tuple[str, str]]:
    """Parse a Cargo.lock and return ``{git_dep_name: (url, rev)}`` for each dep.

    Matching priority for each package entry is:
    1) exact ``name-version`` key, 2) exact ``match_name``,
    3) ``match_name`` as a hyphenated crate-name prefix.
    """
    result: dict[str, tuple[str, str]] = {}
    unmatched = {dep.git_dep: dep for dep in git_deps}

    current_name: str | None = None
    current_version: str | None = None

    for raw_line in lockfile_content.splitlines():
        line = raw_line.strip()
        name = _parse_quoted_assignment(line, "name")
        if name is not None:
            current_name = name
            current_version = None
            continue

        version = _parse_quoted_assignment(line, "version")
        if version is not None:
            current_version = version
            continue

        if current_name is None:
            continue

        git_source = _parse_git_source_line(line)
        if git_source is None:
            continue
        _record_git_source_match(
            current_name=current_name,
            current_version=current_version,
            git_source=git_source,
            unmatched=unmatched,
            result=result,
        )

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
        name=identifier_attr_path("builtins", "fetchGit"),
        argument=AttributeSet.from_dict(
            {
                "url": url,
                "rev": rev,
                "allRefs": True,
            },
        ),
    )
    expr = Select(
        expression=Parenthesis(value=fetch_git), attribute="narHash"
    ).rebuild()
    args = ["nix", "eval", "--json", "--expr", expr]
    result_drain = ValueDrain[CommandResult]()
    async for event in drain_value_events(
        run_command(
            args,
            options=RunCommandOptions(
                source=source,
                error="builtins.fetchGit failed",
                config=config,
            ),
        ),
        result_drain,
        parse=expect_command_result,
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
    lockfile_content: str | None = None,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute ``importCargoLock`` output hashes via ``builtins.fetchGit``.

    Parses the upstream Cargo.lock to extract git dependency URLs and revisions,
    then prefetches each one directly.  This avoids evaluating nixpkgs entirely
    and works regardless of inter-repo workspace dependencies.
    """
    config = resolve_active_config(config)

    if lockfile_content is None:
        yield UpdateEvent.status(
            source,
            "Fetching upstream Cargo.lock...",
            operation="compute_hash",
            status="computing_hash",
            detail="upstream Cargo.lock",
        )
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
            hashes: dict[str, str] = {}
            for git_dep, hash_value in item.values.items():
                if not isinstance(git_dep, str):
                    msg = f"Expected git dep key to be str, got {type(git_dep)}"
                    raise TypeError(msg)
                hashes[git_dep] = expect_str(hash_value)
            yield UpdateEvent.value(source, hashes)
        else:
            yield item


__all__ = [
    "compute_import_cargo_lock_output_hashes",
]
