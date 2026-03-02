"""Additional tests for flake/refs update helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import aiohttp
import pytest

from lib.nix.models.flake_lock import FlakeLockNode, LockedRef, OriginalRef
from lib.tests._assertions import check
from lib.update.config import resolve_config
from lib.update.events import CommandResult, UpdateEvent, UpdateEventKind
from lib.update.flake import (
    flake_fetch_expr,
    get_flake_input_node,
    get_flake_input_version,
    get_root_input_name,
    update_flake_input,
)
from lib.update.refs import (
    FlakeInputRef,
    RefTaskOptions,
    RefUpdateResult,
    _build_version_prefixes,
    _extract_version_prefix,
    _fetch_first_matching_tag,
    _is_version_ref,
    _parse_tag_version,
    _run_checked_command,
    _select_tag,
    _select_tag_from_releases,
    _select_tag_from_tags,
    _tag_matches_prefix,
    check_flake_ref_update,
    fetch_github_latest_version_ref,
    get_flake_inputs_with_refs,
    update_flake_ref,
    update_refs_task,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _run_async[T](
    value: AsyncIterator[T] | asyncio.Future[T] | asyncio.Task[T],
) -> list[T] | T:
    async def _collect_stream(stream: AsyncIterator[T]) -> list[T]:
        items: list[T] = []
        async for item in stream:
            items.append(item)
        return items

    if hasattr(value, "__aiter__"):
        return asyncio.run(_collect_stream(value))
    return asyncio.run(value)  # type: ignore[arg-type]


def test_flake_helpers_and_fetch_expr_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover root/input/version helpers and flake expression validation."""
    lock = SimpleNamespace(
        root_node=SimpleNamespace(inputs={"nixpkgs": "node-a", "path-like": ["x"]}),
        nodes={
            "node-a": FlakeLockNode(
                original=OriginalRef(
                    type="github", owner="nixos", repo="nixpkgs", ref="v1.2.3"
                ),
                locked=LockedRef(
                    type="github",
                    owner="nixos",
                    repo="nixpkgs",
                    rev="abc",
                    narHash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                ),
            )
        },
    )
    monkeypatch.setattr("lib.update.flake.load_flake_lock", lambda: lock)

    node = get_flake_input_node("node-a")
    check(isinstance(node, FlakeLockNode))
    check(get_root_input_name("nixpkgs") == "node-a")
    check(get_root_input_name("path-like") == "path-like")
    check(get_root_input_name("missing") == "missing")

    version_from_ref = get_flake_input_version(
        FlakeLockNode(original=OriginalRef(type="github", ref="v2.0.0"))
    )
    check(version_from_ref == "v2.0.0")

    version_from_rev = get_flake_input_version(
        FlakeLockNode(
            original=OriginalRef(
                type="github", ref=None, owner="a", repo="b", url=None, path=None
            ),
            locked=LockedRef(
                type="github",
                owner="a",
                repo="b",
                rev="deadbeef",
                narHash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            ),
        )
    )
    check(version_from_rev == "deadbeef")
    check(get_flake_input_version(FlakeLockNode()) == "unknown")

    with pytest.raises(KeyError, match="not found"):
        get_flake_input_node("missing")

    with pytest.raises(ValueError, match="no locked ref"):
        flake_fetch_expr(FlakeLockNode())
    with pytest.raises(ValueError, match="Unsupported flake input type"):
        flake_fetch_expr(
            FlakeLockNode(
                locked=LockedRef(
                    type="path",
                    owner="x",
                    repo="y",
                    rev="z",
                    narHash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                )
            )
        )
    with pytest.raises(ValueError, match="Incomplete locked ref"):
        flake_fetch_expr(
            FlakeLockNode(
                locked=LockedRef(
                    type="github",
                    owner=None,
                    repo="repo",
                    rev="rev",
                    narHash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                )
            )
        )


def test_update_flake_input_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emit command lifecycle events around flake lock update."""
    calls: list[str] = []

    async def _fake_update(input_name: str) -> None:
        calls.append(input_name)

    monkeypatch.setattr("lib.update.flake.nix_flake_lock_update", _fake_update)
    events = _run_async(update_flake_input("demo", source="demo-source"))
    check(isinstance(events, list))
    event_list = events
    check(len(event_list) == 2)
    check(event_list[0].kind == UpdateEventKind.COMMAND_START)
    check(event_list[1].kind == UpdateEventKind.COMMAND_END)
    check(calls == ["demo"])


def test_refs_version_parsing_and_selection_helpers() -> None:
    """Select latest stable tags while respecting prefix semantics."""
    check(_is_version_ref("v1.2.3") is True)
    check(_is_version_ref("master") is False)
    check(_is_version_ref("nixpkgs-unstable") is False)
    check(_is_version_ref("nixos-24.11") is False)
    check(_is_version_ref("deadbeef") is False)
    check(_is_version_ref("feature") is False)

    check(_extract_version_prefix("release-v2.3.4") == "release-v")
    check(_extract_version_prefix("stable") == "")
    check(_build_version_prefixes("release-v") == ["release-v", "v"])
    check(_build_version_prefixes("v") == ["v", ""])
    check(_build_version_prefixes("rel-") == ["rel-"])

    check(_tag_matches_prefix("release-v1.2.3", "release-v") is True)
    check(_tag_matches_prefix("1.2.3", "") is True)
    check(_tag_matches_prefix("v1.2.3", "") is False)

    check(str(_parse_tag_version("release-v1.2.3", "release-v")) == "1.2.3")
    check(str(_parse_tag_version("release-v1.2.3", "release-")) == "1.2.3")
    check(_parse_tag_version("release-v1.2.3-rc1", "release-v") is None)
    check(_parse_tag_version("bad-tag", "release-v") is None)

    check(
        _select_tag(["release-v1.2.0", "release-v1.10.0"], "release-v")
        == "release-v1.10.0"
    )
    check(_select_tag(["release-vnext"], "release-v") == "release-vnext")
    check(_select_tag(["x"], "release-v") is None)

    releases = [
        {"tag_name": "v1.2.3", "draft": False, "prerelease": False},
        {"tag_name": "v2.0.0-rc1", "draft": False, "prerelease": True},
        {"tag_name": "v3.0.0", "draft": True, "prerelease": False},
    ]
    check(_select_tag_from_releases(releases, "v") == "v1.2.3")

    tags = [{"name": "v1.0.0"}, {"name": "v2.0.0"}, {"name": 1}]
    check(_select_tag_from_tags(tags, "v") == "v2.0.0")


def test_get_flake_inputs_with_refs_filters_expected_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collect only version-like github/gitlab root inputs."""
    valid_node = FlakeLockNode(
        original=OriginalRef(type="github", owner="o", repo="r", ref="v1.0.0")
    )
    gitlab_node = FlakeLockNode(
        original=OriginalRef(type="gitlab", owner="o2", repo="r2", ref="1.2.3")
    )
    branch_node = FlakeLockNode(
        original=OriginalRef(type="github", owner="o", repo="r", ref="main")
    )
    unsupported_node = FlakeLockNode(
        original=OriginalRef(type="path", owner="o", repo="r", ref="v1.0.0")
    )
    missing_owner = FlakeLockNode(
        original=OriginalRef(type="github", owner=None, repo="r", ref="v1.0.0")
    )

    model = SimpleNamespace(
        root_node=SimpleNamespace(
            inputs={
                "valid": "valid-node",
                "gitlab": "gitlab-node",
                "branch": "branch-node",
                "unsupported": "unsupported-node",
                "missing-owner": "missing-owner",
                "list-path": ["nested", "node"],
                "unknown": "unknown-node",
            }
        ),
        nodes={
            "valid-node": valid_node,
            "gitlab-node": gitlab_node,
            "branch-node": branch_node,
            "unsupported-node": unsupported_node,
            "missing-owner": missing_owner,
        },
    )
    monkeypatch.setattr("lib.update.refs.load_flake_lock", lambda: model)

    items = get_flake_inputs_with_refs()
    check([item.name for item in items] == ["gitlab", "valid"])
    check(items[0].input_type == "gitlab")


def test_get_flake_inputs_with_refs_empty_root_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return an empty list when root inputs are absent."""
    model = SimpleNamespace(root_node=SimpleNamespace(inputs=None), nodes={})
    monkeypatch.setattr("lib.update.refs.load_flake_lock", lambda: model)
    check(get_flake_inputs_with_refs() == [])


def test_fetch_first_matching_tag_falls_back_to_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle release API failure and select from tags endpoint."""
    calls: list[str] = []

    async def _fetch_paginated(
        _session: aiohttp.ClientSession,
        path: str,
        *,
        config: object,
        per_page: int,
        max_pages: int,
    ) -> list[object]:
        _ = (config, per_page, max_pages)
        calls.append(path)
        if path.endswith("/releases"):
            msg = "release API down"
            raise RuntimeError(msg)
        return [{"name": "v1.2.3"}, {"name": "v1.3.0"}]

    monkeypatch.setattr("lib.update.refs.fetch_github_api_paginated", _fetch_paginated)

    async def _run() -> str | None:
        async with aiohttp.ClientSession() as session:
            return await _fetch_first_matching_tag(
                session,
                "owner",
                "repo",
                "v",
                config=resolve_config(),
            )

    latest = _run_async(_run())
    check(latest == "v1.3.0")
    check(calls == ["repos/owner/repo/releases", "repos/owner/repo/tags"])


def test_fetch_first_matching_tag_returns_none_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Continue through both candidates and return ``None`` if nothing matches."""

    async def _fetch_paginated(*_args: object, **_kwargs: object) -> list[object]:
        return [{"name": "stable"}, {"tag_name": "preview"}]

    monkeypatch.setattr("lib.update.refs.fetch_github_api_paginated", _fetch_paginated)

    async def _run() -> str | None:
        async with aiohttp.ClientSession() as session:
            return await _fetch_first_matching_tag(
                session,
                "owner",
                "repo",
                "v",
                config=resolve_config(),
            )

    check(_run_async(_run()) is None)


def test_fetch_github_latest_version_ref_tries_prefix_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Try release prefix candidates until one returns a tag."""
    calls: list[str] = []

    async def _fake_fetch(
        _session: aiohttp.ClientSession,
        _owner: str,
        _repo: str,
        prefix: str,
        *,
        config: object,
    ) -> str | None:
        _ = config
        calls.append(prefix)
        return "v1.2.3" if prefix == "v" else None

    monkeypatch.setattr("lib.update.refs._fetch_first_matching_tag", _fake_fetch)

    async def _run() -> str | None:
        async with aiohttp.ClientSession() as session:
            return await fetch_github_latest_version_ref(
                session,
                "owner",
                "repo",
                "release-v",
            )

    result = _run_async(_run())
    check(result == "v1.2.3")
    check(calls == ["release-v", "v"])


def test_fetch_github_latest_version_ref_returns_none_when_unmatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return ``None`` when no prefix candidate yields a tag."""

    async def _always_none(*_args: object, **_kwargs: object) -> str | None:
        return None

    monkeypatch.setattr("lib.update.refs._fetch_first_matching_tag", _always_none)

    async def _run() -> str | None:
        async with aiohttp.ClientSession() as session:
            return await fetch_github_latest_version_ref(
                session,
                "owner",
                "repo",
                "v",
            )

    check(_run_async(_run()) is None)


def test_check_flake_ref_update_supported_and_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle unsupported inputs and missing/latest tag cases."""

    async def _latest_none(*_args: object, **_kwargs: object) -> str | None:
        return None

    monkeypatch.setattr("lib.update.refs.fetch_github_latest_version_ref", _latest_none)

    async def _run_none() -> RefUpdateResult:
        async with aiohttp.ClientSession() as session:
            return await check_flake_ref_update(
                FlakeInputRef("demo", "o", "r", "v1", "github"),
                session,
            )

    no_latest = _run_async(_run_none())
    check(isinstance(no_latest, RefUpdateResult))
    check(no_latest.error == "Could not determine latest version")

    async def _latest(*_args: object, **_kwargs: object) -> str | None:
        return "v2"

    monkeypatch.setattr("lib.update.refs.fetch_github_latest_version_ref", _latest)

    async def _run_latest() -> RefUpdateResult:
        async with aiohttp.ClientSession() as session:
            return await check_flake_ref_update(
                FlakeInputRef("demo", "o", "r", "v1", "github"),
                session,
            )

    latest = _run_async(_run_latest())
    check(latest.latest_ref == "v2")
    unsupported = _run_async(
        check_flake_ref_update(
            FlakeInputRef("demo", "o", "r", "v1", "git"),
            SimpleNamespace(),
        )
    )
    check(isinstance(unsupported, RefUpdateResult))
    check("Unsupported input type" in (unsupported.error or ""))


def test_run_checked_command_and_update_flake_ref_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise descriptive errors for failed commands and emit events for updates."""

    async def _stream_ok(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(args=["x"], returncode=0, stdout="", stderr=""),
        )

    monkeypatch.setattr("lib.update.refs.stream_command", _stream_ok)
    ok_events = _run_async(
        _run_checked_command(["x"], source="demo", error_prefix="failed")
    )
    check(isinstance(ok_events, list))
    check(len(ok_events) == 1)

    async def _stream_non_result_end(
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("demo", "running")
        yield UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload="done",
        )

    monkeypatch.setattr("lib.update.refs.stream_command", _stream_non_result_end)
    non_result_events = _run_async(
        _run_checked_command(["x"], source="demo", error_prefix="failed")
    )
    check(isinstance(non_result_events, list))
    check(len(non_result_events) == 2)

    async def _stream_fail_empty(
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(args=["x"], returncode=2, stdout="", stderr=""),
        )

    monkeypatch.setattr("lib.update.refs.stream_command", _stream_fail_empty)
    with pytest.raises(RuntimeError, match=r"failed \(exit 2\)"):
        _run_async(_run_checked_command(["x"], source="demo", error_prefix="failed"))

    async def _stream_fail_stderr(
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(args=["x"], returncode=2, stdout="", stderr="oops"),
        )

    monkeypatch.setattr("lib.update.refs.stream_command", _stream_fail_stderr)
    with pytest.raises(RuntimeError, match="oops"):
        _run_async(_run_checked_command(["x"], source="demo", error_prefix="failed"))

    called_args: list[list[str]] = []

    async def _record_run_checked(
        args: list[str],
        *,
        source: str,
        error_prefix: str,
    ) -> AsyncIterator[UpdateEvent]:
        _ = (source, error_prefix)
        called_args.append(args)
        yield UpdateEvent.status("demo", "ok")

    monkeypatch.setattr("lib.update.refs._run_checked_command", _record_run_checked)
    github_ref = FlakeInputRef("demo", "owner", "repo", "v1", "github")
    _run_async(update_flake_ref(github_ref, "v2", source="demo"))
    check(called_args[0][:3] == ["flake-edit", "change", "demo"])
    check(called_args[1][:4] == ["nix", "flake", "lock", "--update-input"])

    called_args.clear()
    gitlab_ref = FlakeInputRef("demo", "owner", "repo", "v1", "gitlab")
    _run_async(update_flake_ref(gitlab_ref, "v2", source="demo"))
    check("gitlab:owner/repo/v2" in " ".join(called_args[0]))

    with pytest.raises(RuntimeError, match="Unsupported input type"):
        _run_async(
            update_flake_ref(
                FlakeInputRef("demo", "o", "r", "v1", "git"), "v2", source="demo"
            )
        )


def test_update_refs_task_flow_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise all queue update branches in refs task orchestration."""

    async def _run_queue_task(
        *,
        source: str,
        queue: asyncio.Queue[UpdateEvent | None],
        task,
    ) -> None:
        _ = (source, queue)
        await task()

    monkeypatch.setattr("lib.update.refs.run_queue_task", _run_queue_task)

    async def _fake_update_flake_ref(
        _input_ref: FlakeInputRef,
        _new_ref: str,
        *,
        source: str,
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status(source, "updated")

    monkeypatch.setattr("lib.update.refs.update_flake_ref", _fake_update_flake_ref)

    def _drain_queue(q: asyncio.Queue[UpdateEvent | None]) -> list[UpdateEvent]:
        items: list[UpdateEvent] = []
        while not q.empty():
            item = q.get_nowait()
            if isinstance(item, UpdateEvent):
                items.append(item)
        return items

    async def _run_case(
        result: RefUpdateResult, *, options: RefTaskOptions
    ) -> list[UpdateEvent]:
        async def _check(*_args: object, **_kwargs: object) -> RefUpdateResult:
            return result

        monkeypatch.setattr("lib.update.refs.check_flake_ref_update", _check)
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        async with aiohttp.ClientSession() as session:
            await update_refs_task(
                FlakeInputRef("demo", "owner", "repo", "v1", "github"),
                session,
                queue,
                options=options,
            )
        return _drain_queue(queue)

    events_error = _run_async(
        _run_case(
            RefUpdateResult(
                name="demo", current_ref="v1", latest_ref=None, error="boom"
            ),
            options=RefTaskOptions(),
        )
    )
    check(any(event.kind == UpdateEventKind.ERROR for event in events_error))

    events_uptodate = _run_async(
        _run_case(
            RefUpdateResult(name="demo", current_ref="v1", latest_ref="v1"),
            options=RefTaskOptions(),
        )
    )
    check(
        any((event.message or "").startswith("Up to date") for event in events_uptodate)
    )
    check(any(event.kind == UpdateEventKind.RESULT for event in events_uptodate))

    events_missing_latest = _run_async(
        _run_case(
            RefUpdateResult(name="demo", current_ref="v1", latest_ref=None),
            options=RefTaskOptions(),
        )
    )
    check(any(event.kind == UpdateEventKind.ERROR for event in events_missing_latest))

    events_dry_run = _run_async(
        _run_case(
            RefUpdateResult(name="demo", current_ref="v1", latest_ref="v2"),
            options=RefTaskOptions(dry_run=True),
        )
    )
    check(any("Update available" in (event.message or "") for event in events_dry_run))

    lock = asyncio.Lock()
    events_real = _run_async(
        _run_case(
            RefUpdateResult(name="demo", current_ref="v1", latest_ref="v2"),
            options=RefTaskOptions(
                dry_run=False, flake_edit_lock=lock, config=resolve_config()
            ),
        )
    )
    check(any((event.message or "") == "updated" for event in events_real))
    check(any(event.kind == UpdateEventKind.RESULT for event in events_real))

    events_real_no_lock = _run_async(
        _run_case(
            RefUpdateResult(name="demo", current_ref="v1", latest_ref="v2"),
            options=RefTaskOptions(dry_run=False, config=resolve_config()),
        )
    )
    check(any((event.message or "") == "updated" for event in events_real_no_lock))
