"""Shared helpers for repo-managed Zen script tests."""

from __future__ import annotations

import getpass
from functools import cache
from pathlib import Path
from types import ModuleType

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


@cache
def resolve_zen_script_path(script_name: str) -> Path:
    """Resolve one repo-managed Zen script under ``home/*/bin``."""
    preferred = REPO_ROOT / f"home/{getpass.getuser()}/bin/{script_name}"
    if preferred.is_file():
        return preferred

    candidates = sorted((REPO_ROOT / "home").glob(f"*/bin/{script_name}"))
    if len(candidates) == 1:
        return candidates[0]

    if candidates:
        candidate_paths = ", ".join(
            str(path.relative_to(REPO_ROOT)) for path in candidates
        )
        msg = (
            f"Unable to resolve {script_name} for user {getpass.getuser()!r}; "
            f"candidates: {candidate_paths}"
        )
        raise RuntimeError(msg)

    msg = f"Unable to locate {script_name} under home/*/bin/{script_name}"
    raise RuntimeError(msg)


def load_zen_script_module(script_name: str, module_name: str) -> ModuleType:
    """Load one repo-managed Zen script as a Python module."""
    return load_module_from_path(resolve_zen_script_path(script_name), module_name)


def load_zentool_module(module_name: str) -> ModuleType:
    """Load zentool as a Python module for direct helper tests."""
    return load_zen_script_module("zentool", module_name)


def make_session_entry(
    zentool: ModuleType,
    *,
    url: str,
    title: str = "",
    **extra: object,
) -> object:
    """Build one zentool session history entry."""
    if extra:
        return zentool.SessionEntry.model_validate({
            "url": url,
            "title": title,
            **extra,
        })
    return zentool.SessionEntry(url=url, title=title)


def make_session_tab(
    zentool: ModuleType,
    *,
    entries: list[object] | None = None,
    index: int = 1,
    sync_id: str = "sync-1",
    static_label: str | None = None,
    last_accessed: int = 123,
    pinned: bool = False,
    essential: bool = False,
    workspace: str | None = "{ws}",
    folder_id: str | None = None,
    user_context_id: int = 0,
    attributes: dict[str, object] | None = None,
    pinned_icon: str | None = None,
    has_static_icon: bool = False,
    image: str | None = None,
    hidden: bool = True,
    **extra: object,
) -> object:
    """Build one zentool session tab with the repo's runtime defaults."""
    payload: dict[str, object] = {
        "entries": list(entries or []),
        "index": index,
        "lastAccessed": last_accessed,
        "hidden": hidden,
        "pinned": pinned,
        "zenWorkspace": workspace,
        "zenSyncId": sync_id,
        "zenEssential": essential,
        "zenStaticLabel": static_label,
        "zenPinnedIcon": pinned_icon,
        "zenHasStaticIcon": has_static_icon,
        "userContextId": user_context_id,
        "groupId": folder_id,
        "attributes": dict(attributes or {}),
    }
    payload.update(extra)
    if image is not None:
        payload["image"] = image
    return zentool.SessionTab.model_validate(payload)


def make_session_folder(
    zentool: ModuleType,
    *,
    folder_id: str,
    name: str,
    workspace_id: str,
    parent_id: str | None = None,
    **extra: object,
) -> object:
    """Build one zentool session folder record."""
    return zentool.SessionFolder(
        id=folder_id,
        name=name,
        workspaceId=workspace_id,
        parentId=parent_id,
        **extra,
    )


def make_session_group(
    zentool: ModuleType,
    *,
    group_id: str,
    name: str = "",
    **extra: object,
) -> object:
    """Build one zentool session group record."""
    return zentool.SessionGroup(id=group_id, name=name or group_id, **extra)


def make_session_space(
    zentool: ModuleType,
    *,
    uuid: str,
    name: str,
    **extra: object,
) -> object:
    """Build one zentool session workspace/space record."""
    return zentool.SessionSpace(uuid=uuid, name=name, **extra)


def make_session_state(
    zentool: ModuleType,
    *,
    tabs: list[object] | None = None,
    folders: list[object] | None = None,
    groups: list[object] | None = None,
    spaces: list[object] | None = None,
) -> object:
    """Build one compact zentool session state."""
    return zentool.SessionState(
        tabs=list(tabs or []),
        folders=list(folders or []),
        groups=list(groups or []),
        spaces=list(spaces or []),
    )
