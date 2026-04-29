"""Flake input ref discovery and version-tag update helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Iterable, Mapping

    import aiohttp

from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.events import (
    CommandResult,
    EventStream,
    RefUpdatePayload,
    UpdateEvent,
    UpdateEventKind,
)
from lib.update.flake import load_flake_lock
from lib.update.net import fetch_github_api_paginated
from lib.update.paths import get_repo_root
from lib.update.process import StreamCommandOptions, run_queue_task, stream_command

_BRANCH_REF_PATTERNS = {
    "master",
    "main",
    "nixos-unstable",
    "nixos-stable",
    "nixpkgs-unstable",
}

_MIN_COMMIT_HEX_LEN = 7
_GIT_TAG_REF_PREFIX = "refs/tags/"
_GITHUB_GIT_URL_RE = re.compile(
    r"^(?:https://|ssh://git@)github\.com[:/](?P<owner>[^/]+)/"
    r"(?P<repo>[^/]+?)(?:\.git)?/?$"
)
_GITHUB_GIT_SCP_URL_RE = re.compile(
    r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
_INPUT_ATTRSET_START_RE_TEMPLATE = r"^(?P<indent>\s*){name}\s*=\s*\{{\s*$"
_REF_BINDING_RE = re.compile(
    r"^(?P<indent>\s*)ref\s*=\s*\"(?P<value>(?:\\.|[^\"])*)\"\s*;"
    r"(?P<suffix>\s*(?:#.*)?)$"
)


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
    """A flake root input pinned to a version-like ref."""

    name: str
    owner: str
    repo: str
    ref: str
    input_type: str  # "github", "gitlab", "git"
    submodules: bool = False


def _parse_github_git_url(url: str) -> tuple[str, str] | None:
    for pattern in (_GITHUB_GIT_URL_RE, _GITHUB_GIT_SCP_URL_RE):
        match = pattern.match(url)
        if match:
            return match.group("owner"), match.group("repo")
    return None


def get_flake_inputs_with_refs() -> list[FlakeInputRef]:
    """Return root flake inputs whose refs look like version tags."""
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
        submodules = bool(getattr(node.original, "submodules", False))
        if owner and repo and input_type in ("github", "gitlab"):
            result.append(
                FlakeInputRef(
                    name=input_name,
                    owner=owner,
                    repo=repo,
                    ref=ref,
                    input_type=input_type,
                    submodules=submodules,
                ),
            )
            continue
        if input_type == "git" and node.original.url:
            parsed = _parse_github_git_url(node.original.url)
            if parsed:
                owner, repo = parsed
                result.append(
                    FlakeInputRef(
                        name=input_name,
                        owner=owner,
                        repo=repo,
                        ref=ref,
                        input_type=input_type,
                        submodules=submodules,
                    ),
                )
    return result


def _extract_version_prefix(ref: str) -> str:
    match = re.match(r"^(.*?)\d", ref)
    if match:
        return match.group(1)
    return ""


def _build_version_prefixes(prefix: str) -> list[str]:
    prefixes: list[str] = []

    def add(candidate: str) -> None:
        if candidate not in prefixes:
            prefixes.append(candidate)

    add(prefix)
    if prefix.startswith(_GIT_TAG_REF_PREFIX):
        add(prefix.removeprefix(_GIT_TAG_REF_PREFIX))

    initial_prefixes = tuple(prefixes)
    for candidate in initial_prefixes:
        lowered = candidate.lower()
        if lowered.endswith("v") and lowered != "v":
            add("v")
        if lowered == "v":
            add("")
    return list(dict.fromkeys(prefixes))


def _tag_matches_prefix(tag: str, prefix: str) -> bool:
    if prefix:
        return tag.startswith(prefix)
    return bool(re.match(r"\d", tag))


def _parse_tag_version(
    tag: str,
    prefix: str,
    *,
    allow_prerelease: bool = False,
) -> Version | None:
    if not _tag_matches_prefix(tag, prefix):
        return None
    suffix = tag[len(prefix) :] if prefix else tag
    suffix = suffix.lstrip("-_.")
    if suffix.lower().startswith("v"):
        suffix = suffix[1:]
    try:
        parsed = Version(suffix)
    except InvalidVersion:
        return None
    if parsed.is_prerelease and not allow_prerelease:
        return None
    return parsed


def _select_tag(
    tags: Iterable[str],
    prefix: str,
    *,
    allow_prerelease: bool = False,
) -> str | None:
    matches = [tag for tag in tags if _tag_matches_prefix(tag, prefix)]
    if not matches:
        return None

    parsed_versions = [
        (tag, parsed)
        for tag in matches
        if (
            parsed := _parse_tag_version(
                tag,
                prefix,
                allow_prerelease=allow_prerelease,
            )
        )
        is not None
    ]
    if parsed_versions:
        return max(parsed_versions, key=lambda item: item[1])[0]

    return matches[0]


def _select_tag_from_releases(
    releases: Iterable[Mapping[str, object]],
    prefix: str,
    *,
    allow_prerelease: bool = False,
) -> str | None:
    return _select_tag(
        (
            tag_name
            for release in releases
            if isinstance(tag_name := release.get("tag_name"), str)
            if not release.get("draft")
            if allow_prerelease or not release.get("prerelease")
        ),
        prefix,
        allow_prerelease=allow_prerelease,
    )


def _select_tag_from_tags(
    tags: Iterable[Mapping[str, object]],
    prefix: str,
    *,
    allow_prerelease: bool = False,
) -> str | None:
    return _select_tag(
        (name for tag in tags if isinstance(name := tag.get("name"), str)),
        prefix,
        allow_prerelease=allow_prerelease,
    )


async def _fetch_first_matching_tag(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    prefix: str,
    *,
    config: UpdateConfig,
    allow_prerelease: bool = False,
) -> str | None:
    max_pages = 5
    last_error: RuntimeError | None = None
    saw_tags_fetch = False
    candidates = (
        (
            f"repos/{owner}/{repo}/releases",
            50,
            _select_tag_from_releases,
        ),
        (
            f"repos/{owner}/{repo}/tags",
            100,
            _select_tag_from_tags,
        ),
    )
    for path, per_page, selector in candidates:
        try:
            payload_raw = await fetch_github_api_paginated(
                session,
                path,
                config=config,
                per_page=per_page,
                max_pages=max_pages,
                item_limit=per_page * max_pages,
            )
        except RuntimeError as exc:
            last_error = exc
            continue
        if path.endswith("/tags"):
            saw_tags_fetch = True
        payload = [item for item in payload_raw if isinstance(item, dict)]
        tag = selector(payload, prefix, allow_prerelease=allow_prerelease)
        if tag:
            return tag
    if not saw_tags_fetch and last_error is not None:
        msg = f"GitHub API lookup failed for {owner}/{repo}: {last_error}"
        raise RuntimeError(msg) from last_error
    return None


async def fetch_github_latest_version_ref(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    prefix: str,
    *,
    config: UpdateConfig | None = None,
    allow_prerelease: bool = False,
) -> str | None:
    config = resolve_active_config(config)
    for candidate_prefix in _build_version_prefixes(prefix):
        tag = await _fetch_first_matching_tag(
            session,
            owner,
            repo,
            candidate_prefix,
            config=config,
            allow_prerelease=allow_prerelease,
        )
        if tag:
            return tag

    return None


@dataclass(frozen=True)
class RefUpdateResult:
    """Result of checking whether a flake input ref has a newer tag."""

    name: str
    current_ref: str
    latest_ref: str | None
    error: str | None = None


def _format_git_ref_update(current_ref: str, new_ref: str) -> str:
    """Preserve explicit tag refs when updating generic git flake inputs."""
    if current_ref.startswith(_GIT_TAG_REF_PREFIX) and not new_ref.startswith("refs/"):
        return f"{_GIT_TAG_REF_PREFIX}{new_ref}"
    return new_ref


def _quote_nix_string_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _rewrite_git_input_ref(text: str, input_name: str, new_ref: str) -> str:
    start_re = re.compile(
        _INPUT_ATTRSET_START_RE_TEMPLATE.format(name=re.escape(input_name))
    )
    lines = text.splitlines()
    keep_trailing_newline = text.endswith("\n")
    escaped_ref = _quote_nix_string_value(new_ref)
    inside = False
    updated = False
    input_indent = ""

    for index, line in enumerate(lines):
        if not inside:
            match = start_re.match(line)
            if match:
                inside = True
                input_indent = match.group("indent")
            continue

        ref_match = _REF_BINDING_RE.match(line)
        if ref_match:
            lines[index] = (
                f'{ref_match.group("indent")}ref = "{escaped_ref}";'
                f"{ref_match.group('suffix')}"
            )
            updated = True
            break

        if re.match(rf"^{re.escape(input_indent)}\}};\s*$", line):
            lines.insert(index, f'{input_indent}  ref = "{escaped_ref}";')
            updated = True
            break

    if not updated:
        msg = f"Could not find git flake input attrset for {input_name!r}"
        raise RuntimeError(msg)

    rewritten = "\n".join(lines)
    return f"{rewritten}\n" if keep_trailing_newline else rewritten


def _update_git_input_ref_in_flake(input_name: str, new_ref: str) -> None:
    flake_path = Path(get_repo_root()) / "flake.nix"
    text = flake_path.read_text(encoding="utf-8")
    rewritten = _rewrite_git_input_ref(text, input_name, new_ref)
    if rewritten != text:
        flake_path.write_text(rewritten, encoding="utf-8")


@dataclass(frozen=True)
class RefTaskOptions:
    """Options controlling ``update_refs_task`` behavior."""

    dry_run: bool = False
    flake_edit_lock: asyncio.Lock | None = None
    config: UpdateConfig | None = None


async def check_flake_ref_update(
    input_ref: FlakeInputRef,
    session: aiohttp.ClientSession,
    *,
    config: UpdateConfig | None = None,
) -> RefUpdateResult:
    """Check a flake input ref against the latest matching upstream tag."""
    config = resolve_active_config(config)
    prefix = _extract_version_prefix(input_ref.ref)
    current_version = _parse_tag_version(
        input_ref.ref,
        prefix,
        allow_prerelease=True,
    )
    allow_prerelease = current_version is not None and current_version.is_prerelease

    if input_ref.input_type in {"github", "git"}:
        try:
            latest = await fetch_github_latest_version_ref(
                session,
                input_ref.owner,
                input_ref.repo,
                prefix,
                config=config,
                allow_prerelease=allow_prerelease,
            )
        except RuntimeError as exc:
            return RefUpdateResult(
                name=input_ref.name,
                current_ref=input_ref.ref,
                latest_ref=None,
                error=str(exc),
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
    if input_ref.input_type == "git":
        latest = _format_git_ref_update(input_ref.ref, latest)

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
    yield UpdateEvent.status(
        source,
        f"Updating ref: {input_ref.ref} -> {new_ref}",
        operation="update_ref",
        status="updating_ref",
        detail={"current": input_ref.ref, "latest": new_ref},
    )

    if input_ref.input_type == "github":
        new_url = f"github:{input_ref.owner}/{input_ref.repo}/{new_ref}"
    elif input_ref.input_type == "gitlab":
        new_url = f"gitlab:{input_ref.owner}/{input_ref.repo}/{new_ref}"
    elif input_ref.input_type == "git":
        ref = _format_git_ref_update(input_ref.ref, new_ref)
        _update_git_input_ref_in_flake(input_ref.name, ref)
        yield UpdateEvent.status(
            source,
            f"Updated flake.nix input ref: {input_ref.name} -> {ref}",
            operation="update_ref",
            status="updating_ref",
            detail={"input": input_ref.name, "latest": ref},
        )
        new_url = None
    else:
        msg = f"Unsupported input type: {input_ref.input_type}"
        raise RuntimeError(msg)
    if new_url is not None:
        async for event in _run_checked_command(
            ["flake-edit", "change", input_ref.name, new_url],
            source=source,
            error_prefix="flake-edit change failed",
        ):
            yield event

    async for event in _run_checked_command(
        ["nix", "flake", "lock", "--update-input", input_ref.name],
        source=source,
        error_prefix="nix flake lock failed",
    ):
        yield event


async def _run_checked_command(
    args: list[str],
    *,
    source: str,
    error_prefix: str,
) -> EventStream:
    result: CommandResult | None = None
    async for event in stream_command(
        args,
        options=StreamCommandOptions(source=source),
    ):
        if event.kind == UpdateEventKind.COMMAND_END and isinstance(
            event.payload,
            CommandResult,
        ):
            result = event.payload
        yield event
    if result is None or result.returncode == 0:
        return
    stderr = result.stderr.strip()
    msg = (
        f"{error_prefix} (exit {result.returncode})"
        if not stderr
        else f"{error_prefix} (exit {result.returncode}): {stderr}"
    )
    raise RuntimeError(msg)


async def update_refs_task(
    input_ref: FlakeInputRef,
    session: aiohttp.ClientSession,
    queue: asyncio.Queue[UpdateEvent | None],
    *,
    options: RefTaskOptions | None = None,
) -> None:
    """Run a single flake-ref update, emitting events to *queue*."""
    task_options = options or RefTaskOptions()

    async def _run() -> None:
        resolved_config = resolve_active_config(task_options.config)
        source = input_ref.name
        put = queue.put

        await put(
            UpdateEvent.status(
                source,
                f"Checking {input_ref.owner}/{input_ref.repo} (current: {input_ref.ref})",
                operation="check_version",
                status="checking_current",
                detail=input_ref.ref,
            ),
        )
        result = await check_flake_ref_update(
            input_ref,
            session,
            config=resolved_config,
        )

        if result.error:
            await put(UpdateEvent.error(source, result.error))
            return

        if result.latest_ref == result.current_ref:
            await put(
                UpdateEvent.status(
                    source,
                    f"Up to date (ref: {result.current_ref})",
                    operation="check_version",
                    status="up_to_date",
                    detail={"scope": "ref", "value": result.current_ref},
                ),
            )
            await put(UpdateEvent.result(source))
            return

        latest_ref = result.latest_ref
        if latest_ref is None:
            await put(UpdateEvent.error(source, "Missing latest ref"))
            return
        update_payload: RefUpdatePayload = {
            "current": result.current_ref,
            "latest": latest_ref,
        }
        if task_options.dry_run:
            await put(
                UpdateEvent.status(
                    source,
                    f"Update available: {result.current_ref} -> {result.latest_ref}",
                    operation="check_version",
                    status="update_available",
                    detail={"current": result.current_ref, "latest": result.latest_ref},
                ),
            )
            await put(UpdateEvent.result(source, update_payload))
            return

        async def do_update() -> None:
            async for event in update_flake_ref(input_ref, latest_ref, source=source):
                await put(event)

        if task_options.flake_edit_lock:
            async with task_options.flake_edit_lock:
                await do_update()
        else:
            await do_update()

        await put(UpdateEvent.result(source, update_payload))

    await run_queue_task(source=input_ref.name, queue=queue, task=_run)


__all__ = [
    "FlakeInputRef",
    "RefTaskOptions",
    "RefUpdateResult",
    "check_flake_ref_update",
    "get_flake_inputs_with_refs",
    "update_refs_task",
]
