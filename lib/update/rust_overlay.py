"""Advance the root rust-overlay pin to the first commit that ships a stable.

``nix flake update`` cannot move a revision embedded in a flake URL. Callers
that evaluate ``rust-bin.stable.<channel>`` from that pin ask this module to
rewrite the URL to the oldest oxalica/rust-overlay commit that contains
``manifests/stable/<channel>.nix``, then refresh the lock. Following
rust-overlay HEAD would pull every later nightly and recreate the #1265
Darwin closure explosion (warmup=4 to warmup=7812, 6515 ``rust_*``).
"""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING

from lib.update.events import (
    EventSink,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    ignore_event,
)
from lib.update.flake import (
    invalidate_flake_lock,
    load_flake_lock,
    resolve_root_input_node,
    update_flake_inputs,
)
from lib.update.flake_inputs import input_github_rev_url, set_input_github_rev
from lib.update.net import fetch_github_api, fetch_url, github_raw_url
from lib.update.paths import get_repo_root
from lib.update.rust_toolchain import EXACT_STABLE_CHANNEL

if TYPE_CHECKING:
    from pathlib import Path

    import aiohttp

    from lib.update.config import UpdateConfig

RUST_OVERLAY_INPUT = "rust-overlay"
_OWNER = "oxalica"
_REPO = "rust-overlay"
_REV = re.compile(r"^[0-9a-f]{40}$")
_MANIFEST = "manifests/stable/{channel}.nix"
_COMMITS = f"repos/{_OWNER}/{_REPO}/commits"
_PAGE_SIZE = 100
_MAX_PAGES = 20
_STATUS_CAUSE_LIMIT = 8


def _http_status(error: BaseException) -> int | None:
    """Return an HTTP status carried by ``error`` or one of its causes."""
    current: BaseException | None = error
    for _ in range(_STATUS_CAUSE_LIMIT):
        if current is None:
            break
        status = getattr(current, "status", None)
        if isinstance(status, int):
            return status
        current = current.__cause__
    return None


def _flake_path() -> Path:
    """Return the repository ``flake.nix`` path."""
    return get_repo_root() / "flake.nix"


def _read_flake() -> str:
    """Return the current ``flake.nix`` text."""
    return _flake_path().read_text(encoding="utf-8")


def _write_flake(text: str) -> None:
    """Replace ``flake.nix`` with ``text``."""
    _flake_path().write_text(text, encoding="utf-8")


def _pinned_rev() -> str:
    """Return the root rust-overlay revision when it is an exact rev pin."""
    node, follows = resolve_root_input_node(load_flake_lock(), RUST_OVERLAY_INPUT)
    locked = node.locked if node is not None else None
    original = node.original if node is not None else None
    locked_rev = locked.rev if locked is not None else None
    original_rev = original.rev if original is not None else None
    owner = locked.owner if locked is not None else None
    repo = locked.repo if locked is not None else None
    if (
        follows is not None
        or locked is None
        or original is None
        or owner != _OWNER
        or repo != _REPO
        or not isinstance(locked_rev, str)
        or not isinstance(original_rev, str)
        or locked_rev != original_rev
        or _REV.fullmatch(locked_rev) is None
    ):
        msg = "root rust-overlay is not an exact rev pin"
        raise RuntimeError(msg)
    return locked_rev


def _url_matches_pin(source: str, rev: str) -> bool:
    """Return whether the url binding is oxalica/rust-overlay at ``rev``.

    A comment that mentions the revision is not the pin. The binding itself
    has to be that exact GitHub URL.
    """
    return input_github_rev_url(source, RUST_OVERLAY_INPUT) == (
        f"github:{_OWNER}/{_REPO}/{rev}"
    )


async def _manifest_exists(
    session: aiohttp.ClientSession,
    rev: str,
    channel: str,
    *,
    config: UpdateConfig,
) -> bool:
    """Return whether ``rev`` contains the stable manifest for ``channel``.

    A 404 is absence. Any other failure, including a truncated or refused
    request, propagates so a missing manifest is never confused with a
    transport error.
    """
    url = github_raw_url(_OWNER, _REPO, rev, _MANIFEST.format(channel=channel))
    try:
        await fetch_url(
            session,
            url,
            user_agent=config.default_user_agent,
            request_timeout=config.default_timeout,
            config=config,
        )
    except RuntimeError as exc:
        if _http_status(exc) == HTTPStatus.NOT_FOUND:
            return False
        raise
    return True


def _commit_sha(item: object, *, context: str) -> str:
    """Return one GitHub commit object's revision."""
    if not isinstance(item, dict):
        msg = f"{context} is malformed"
        raise TypeError(msg)
    sha = item.get("sha")
    if not isinstance(sha, str) or _REV.fullmatch(sha) is None:
        msg = f"{context} has no revision"
        raise RuntimeError(msg)
    return sha


async def _earliest_manifest_commit(
    session: aiohttp.ClientSession,
    channel: str,
    *,
    config: UpdateConfig,
) -> str:
    """Return the oldest commit that touches the stable manifest.

    GitHub lists commits newest first. The last item of a complete history
    is the commit that introduced the file, and that commit's tree contains
    it. A full final page means the history was truncated, so there is no
    proven oldest commit to pin.
    """
    path = _MANIFEST.format(channel=channel)
    context = f"rust-overlay commit history for {path}"
    collected: list[object] = []
    for page in range(1, _MAX_PAGES + 1):
        payload = await fetch_github_api(
            session,
            _COMMITS,
            config=config,
            path=path,
            per_page=str(_PAGE_SIZE),
            page=str(page),
        )
        if not isinstance(payload, list):
            msg = f"{context} was not a list"
            raise TypeError(msg)
        collected.extend(payload)
        if len(payload) < _PAGE_SIZE:
            break
    else:
        msg = (
            f"{context} exceeded {_MAX_PAGES} pages; refusing to pin a commit "
            "that may not be the first"
        )
        raise RuntimeError(msg)
    if not collected:
        msg = f"rust-overlay has no commit for {path}"
        raise RuntimeError(msg)
    return _commit_sha(collected[-1], context=context)


async def _is_descendant(
    session: aiohttp.ClientSession,
    pinned: str,
    target: str,
    *,
    config: UpdateConfig,
) -> bool:
    """Return whether ``target`` is a strict descendant of ``pinned``."""
    payload = await fetch_github_api(
        session,
        f"repos/{_OWNER}/{_REPO}/compare/{pinned}...{target}",
        config=config,
    )
    if not isinstance(payload, dict):
        msg = "rust-overlay compare was not an object"
        raise TypeError(msg)
    ahead = payload.get("ahead_by")
    behind = payload.get("behind_by")
    if not isinstance(ahead, int) or not isinstance(behind, int):
        msg = "rust-overlay compare did not report ahead_by and behind_by"
        raise TypeError(msg)
    return ahead > 0 and behind == 0


async def _lock_rewritten_pin(
    source: str,
    target: str,
    *,
    emit: EventSink,
    config: UpdateConfig,
) -> None:
    """Write the new URL and refresh its lock, restoring the URL on failure."""
    path_text = _read_flake()
    rewritten = set_input_github_rev(path_text, RUST_OVERLAY_INPUT, target)
    if rewritten == path_text:
        msg = f"flake.nix rust-overlay pin did not move to {target}"
        raise RuntimeError(msg)
    _write_flake(rewritten)
    try:
        await update_flake_inputs(
            [RUST_OVERLAY_INPUT], source=source, emit=emit, config=config
        )
    except Exception as exc:
        try:
            _write_flake(path_text)
        except OSError as cleanup_error:
            msg = (
                "rust-overlay pin update failed and flake.nix could not be "
                f"restored: {cleanup_error}"
            )
            raise RuntimeError(msg) from exc
        invalidate_flake_lock()
        raise


async def ensure_root_rust_overlay_has_channel(
    session: aiohttp.ClientSession,
    channel: str,
    *,
    source: str,
    emit: EventSink = ignore_event,
    config: UpdateConfig,
) -> None:
    """Move the root rust-overlay pin to the first commit that ships ``channel``.

    A no-op when the current exact rev already contains the stable manifest.
    The chosen commit is the oldest one that touches that manifest, and only
    when it is a descendant of the current pin. Moving backward, or following
    a later HEAD, would drop or rebuild stables this pin already ships.
    """
    if EXACT_STABLE_CHANNEL.fullmatch(channel) is None:
        msg = f"{source} rust toolchain {channel!r} is not an exact stable release"
        raise RuntimeError(msg)
    pinned = _pinned_rev()
    if not _url_matches_pin(_read_flake(), pinned):
        msg = "root rust-overlay url is not the locked oxalica rev pin"
        raise RuntimeError(msg)
    if await _manifest_exists(session, pinned, channel, config=config):
        return
    target = await _earliest_manifest_commit(session, channel, config=config)
    if target == pinned:
        msg = f"rust-overlay {pinned} touches stable {channel} but does not contain it"
        raise RuntimeError(msg)
    if not await _is_descendant(session, pinned, target, config=config):
        msg = (
            f"stable {channel} was introduced at {target}, which is not a "
            f"descendant of rust-overlay {pinned}; refusing to move the pin"
        )
        raise RuntimeError(msg)
    await emit(
        UpdateEvent.status(
            source,
            f"Advancing rust-overlay to {target} for stable {channel}",
            operation="refresh_lock",
            status=StatusInfo(
                kind=StatusKind.UPDATING_REF,
                current=pinned,
                latest=target,
                scope="ref",
            ),
        )
    )
    await _lock_rewritten_pin(source, target, emit=emit, config=config)


__all__ = ["RUST_OVERLAY_INPUT", "ensure_root_rust_overlay_has_channel"]
