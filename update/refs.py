from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Iterable

    import aiohttp

from libnix.update.events import (
    CommandResult,
    EventStream,
    RefUpdatePayload,
    UpdateEvent,
    UpdateEventKind,
)
from update.config import UpdateConfig, _resolve_active_config
from update.flake import load_flake_lock
from update.net import fetch_github_api
from update.process import _run_queue_task, stream_command

_BRANCH_REF_PATTERNS = {
    "master",
    "main",
    "nixos-unstable",
    "nixos-stable",
    "nixpkgs-unstable",
}

_MIN_COMMIT_HEX_LEN = 7


def _is_version_ref(ref: str) -> bool:
    if ref in _BRANCH_REF_PATTERNS:
        return False
    if ref.startswith(("nixos-", "nixpkgs-")):
        return False
    if re.fullmatch(r"[0-9a-f]+", ref) and len(ref) >= _MIN_COMMIT_HEX_LEN:
        return False
    return re.search(r"\d", ref) is not None


@dataclass(frozen=True)
class FlakeInputRef:
    name: str
    owner: str
    repo: str
    ref: str
    input_type: str  # "github", "gitlab"


def get_flake_inputs_with_refs() -> list[FlakeInputRef]:
    model = load_flake_lock()
    root = model.root_node
    if not root.inputs:
        return []

    result = []
    for input_name, node_name in sorted(root.inputs.items()):
        if isinstance(node_name, list):
            continue
        node = model.nodes.get(node_name or input_name)
        if node is None or node.original is None:
            continue
        ref = node.original.ref
        if not ref or not _is_version_ref(ref):
            continue
        owner = node.original.owner
        repo = node.original.repo
        input_type = node.original.type or "github"
        if owner and repo and input_type in ("github", "gitlab"):
            result.append(
                FlakeInputRef(
                    name=input_name,
                    owner=owner,
                    repo=repo,
                    ref=ref,
                    input_type=input_type,
                )
            )
    return result


def _extract_version_prefix(ref: str) -> str:
    match = re.match(r"^(.*?)\d", ref)
    if match:
        return match.group(1)
    return ""


def _build_version_prefixes(prefix: str) -> list[str]:
    prefixes = [prefix]
    lowered = prefix.lower()
    if lowered.endswith("v") and lowered != "v":
        prefixes.append("v")
    if lowered == "v":
        prefixes.append("")
    return list(dict.fromkeys(prefixes))


def _tag_matches_prefix(tag: str, prefix: str) -> bool:
    if prefix:
        return tag.startswith(prefix)
    return bool(re.match(r"\d", tag))


def _select_tag(tags: Iterable[str], prefix: str) -> str | None:
    for tag in tags:
        if _tag_matches_prefix(tag, prefix):
            return tag
    return None


def _select_tag_from_releases(
    releases: Iterable[dict[str, str]], prefix: str
) -> str | None:
    return _select_tag(
        (
            release.get("tag_name", "")
            for release in releases
            if not release.get("draft") and not release.get("prerelease")
        ),
        prefix,
    )


def _select_tag_from_tags(tags: Iterable[dict[str, str]], prefix: str) -> str | None:
    return _select_tag((tag.get("name", "") for tag in tags), prefix)


async def fetch_github_latest_version_ref(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    prefix: str,
    *,
    config: UpdateConfig | None = None,
) -> str | None:
    config = _resolve_active_config(config)
    prefixes = _build_version_prefixes(prefix)
    for candidate_prefix in prefixes:
        try:
            releases = cast(
                "list[dict[str, str]]",
                await fetch_github_api(
                    session,
                    f"repos/{owner}/{repo}/releases",
                    per_page="20",
                    config=config,
                ),
            )
            tag = _select_tag_from_releases(releases, candidate_prefix)
            if tag:
                return tag
        except RuntimeError:
            pass

        try:
            tags = cast(
                "list[dict[str, str]]",
                await fetch_github_api(
                    session,
                    f"repos/{owner}/{repo}/tags",
                    per_page="30",
                    config=config,
                ),
            )
            tag = _select_tag_from_tags(tags, candidate_prefix)
            if tag:
                return tag
        except RuntimeError:
            pass

    return None


@dataclass(frozen=True)
class RefUpdateResult:
    name: str
    current_ref: str
    latest_ref: str | None
    error: str | None = None


async def check_flake_ref_update(
    input_ref: FlakeInputRef,
    session: aiohttp.ClientSession,
    *,
    config: UpdateConfig | None = None,
) -> RefUpdateResult:
    config = _resolve_active_config(config)
    prefix = _extract_version_prefix(input_ref.ref)

    if input_ref.input_type == "github":
        latest = await fetch_github_latest_version_ref(
            session, input_ref.owner, input_ref.repo, prefix, config=config
        )
    else:
        return RefUpdateResult(
            name=input_ref.name,
            current_ref=input_ref.ref,
            latest_ref=None,
            error=f"Unsupported input type: {input_ref.input_type}",
        )

    if latest is None:
        return RefUpdateResult(
            name=input_ref.name,
            current_ref=input_ref.ref,
            latest_ref=None,
            error="Could not determine latest version",
        )

    return RefUpdateResult(
        name=input_ref.name,
        current_ref=input_ref.ref,
        latest_ref=latest,
    )


async def update_flake_ref(
    input_ref: FlakeInputRef,
    new_ref: str,
    *,
    source: str,
) -> EventStream:
    yield UpdateEvent.status(source, f"Updating ref: {input_ref.ref} -> {new_ref}")

    if input_ref.input_type == "github":
        new_url = f"github:{input_ref.owner}/{input_ref.repo}/{new_ref}"
    elif input_ref.input_type == "gitlab":
        new_url = f"gitlab:{input_ref.owner}/{input_ref.repo}/{new_ref}"
    else:
        msg = f"Unsupported input type: {input_ref.input_type}"
        raise RuntimeError(msg)
    change_result: CommandResult | None = None
    async for event in stream_command(
        ["flake-edit", "change", input_ref.name, new_url],
        source=source,
    ):
        if event.kind == UpdateEventKind.COMMAND_END and isinstance(
            event.payload, CommandResult
        ):
            change_result = event.payload
        yield event
    if change_result and change_result.returncode != 0:
        msg = (
            f"flake-edit change failed (exit {change_result.returncode}): "
            f"{change_result.stderr.strip()}"
        )
        raise RuntimeError(msg)

    lock_result: CommandResult | None = None
    async for event in stream_command(
        ["nix", "flake", "lock", "--update-input", input_ref.name],
        source=source,
    ):
        if event.kind == UpdateEventKind.COMMAND_END and isinstance(
            event.payload, CommandResult
        ):
            lock_result = event.payload
        yield event
    if lock_result and lock_result.returncode != 0:
        msg = (
            f"nix flake lock failed (exit {lock_result.returncode}): "
            f"{lock_result.stderr.strip()}"
        )
        raise RuntimeError(msg)


async def _update_refs_task(
    input_ref: FlakeInputRef,
    session: aiohttp.ClientSession,
    queue: asyncio.Queue[UpdateEvent | None],
    *,
    dry_run: bool = False,
    flake_edit_lock: asyncio.Lock | None = None,
    config: UpdateConfig | None = None,
) -> None:
    async def _run() -> None:
        resolved_config = _resolve_active_config(config)
        source = input_ref.name
        put = queue.put

        await put(
            UpdateEvent.status(
                source,
                f"Checking {input_ref.owner}/{input_ref.repo} (current: {input_ref.ref})",
            )
        )
        result = await check_flake_ref_update(
            input_ref, session, config=resolved_config
        )

        if result.error:
            await put(UpdateEvent.error(source, result.error))
            return

        if result.latest_ref == result.current_ref:
            await put(
                UpdateEvent.status(source, f"Up to date (ref: {result.current_ref})")
            )
            await put(UpdateEvent.result(source))
            return

        update_payload: RefUpdatePayload = {
            "current": result.current_ref,
            "latest": cast("str", result.latest_ref),
        }
        if dry_run:
            await put(
                UpdateEvent.status(
                    source,
                    f"Update available: {result.current_ref} -> {result.latest_ref}",
                )
            )
            await put(UpdateEvent.result(source, update_payload))
            return

        latest_ref = result.latest_ref
        if latest_ref is None:
            await put(UpdateEvent.error(source, "Missing latest ref"))
            return

        async def do_update() -> None:
            async for event in update_flake_ref(input_ref, latest_ref, source=source):
                await put(event)

        if flake_edit_lock:
            async with flake_edit_lock:
                await do_update()
        else:
            await do_update()

        await put(UpdateEvent.result(source, update_payload))

    await _run_queue_task(source=input_ref.name, queue=queue, task=_run)


__all__ = [
    "FlakeInputRef",
    "RefUpdateResult",
    "_update_refs_task",
    "check_flake_ref_update",
    "get_flake_inputs_with_refs",
]
