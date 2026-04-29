"""Additional tests for flake/refs update helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import aiohttp
import pytest
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode, LockedRef, OriginalRef
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.config import resolve_config
from lib.update.events import CommandResult, UpdateEvent, UpdateEventKind
from lib.update.flake import (
    flake_fetch_expr,
    get_flake_input_node,
    get_flake_input_version,
    get_root_input_name,
    invalidate_flake_lock,
    load_flake_lock,
    nixpkgs_expr,
    resolve_root_input_node,
    update_flake_input,
)
from lib.update.refs import (
    FlakeInputRef,
    RefTaskOptions,
    RefUpdateResult,
    _build_version_prefixes,
    _extract_version_prefix,
    _fetch_first_matching_tag,
    _format_git_ref_update,
    _is_version_ref,
    _parse_github_git_url,
    _parse_tag_version,
    _rewrite_git_input_ref,
    _run_checked_command,
    _select_tag,
    _select_tag_from_releases,
    _select_tag_from_tags,
    _tag_matches_prefix,
    _update_git_input_ref_in_flake,
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
    assert isinstance(node, FlakeLockNode)
    assert get_root_input_name("nixpkgs") == "node-a"
    assert get_root_input_name("path-like") == "path-like"
    assert get_root_input_name("missing") == "missing"

    version_from_ref = get_flake_input_version(
        FlakeLockNode(original=OriginalRef(type="github", ref="v9.9.9"))
    )
    assert version_from_ref == "v9.9.9"

    version_from_original_rev = get_flake_input_version(
        cast(
            "FlakeLockNode",
            SimpleNamespace(
                original=SimpleNamespace(ref=None, rev="cafebabe"), locked=None
            ),
        )
    )
    assert version_from_original_rev == "cafebabe"

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
    assert version_from_rev == "deadbeef"
    assert get_flake_input_version(FlakeLockNode()) == "unknown"

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
    invalidated: list[bool] = []

    async def _fake_update(input_name: str) -> None:
        calls.append(input_name)

    monkeypatch.setattr("lib.update.flake.nix_flake_lock_update", _fake_update)
    monkeypatch.setattr(
        "lib.update.flake.invalidate_flake_lock", lambda: invalidated.append(True)
    )
    events = _run_async(update_flake_input("demo", source="demo-source"))
    event_list = cast("list[UpdateEvent]", events)
    assert len(event_list) == 2
    assert event_list[0].kind == UpdateEventKind.COMMAND_START
    assert event_list[1].kind == UpdateEventKind.COMMAND_END
    assert calls == ["demo"]
    assert invalidated == [True]


def test_resolve_root_input_node_handles_incomplete_follows_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return ``None`` when a follows chain cannot be resolved cleanly."""
    broken_start = SimpleNamespace(
        root_node=SimpleNamespace(inputs={"broken": ["missing", "nixpkgs"]}),
        nodes={},
    )
    node, follows = resolve_root_input_node(cast("FlakeLock", broken_start), "broken")
    assert node is None
    assert follows == "missing/nixpkgs"

    missing_inputs = SimpleNamespace(
        root_node=SimpleNamespace(inputs={"broken": ["wrapper", "nixpkgs"]}),
        nodes={"wrapper": SimpleNamespace(inputs=None)},
    )
    node, follows = resolve_root_input_node(cast("FlakeLock", missing_inputs), "broken")
    assert node is None
    assert follows == "wrapper/nixpkgs"

    empty_follows = SimpleNamespace(
        root_node=SimpleNamespace(inputs={"broken": []}),
        nodes={},
    )
    node, follows = resolve_root_input_node(cast("FlakeLock", empty_follows), "broken")
    assert node is None
    assert follows == ""

    malformed_start = SimpleNamespace(
        root_node=SimpleNamespace(inputs={"broken": [None, "nixpkgs"]}),
        nodes={},
    )
    node, follows = resolve_root_input_node(
        cast("FlakeLock", malformed_start), "broken"
    )
    assert node is None
    assert follows is None


def test_load_flake_lock_cache_can_be_invalidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return fresh lock data after explicit cache invalidation."""
    invalidate_flake_lock()
    lock_a = SimpleNamespace(nodes={"demo": "a"})
    lock_b = SimpleNamespace(nodes={"demo": "b"})
    locks = [lock_a, lock_b]

    monkeypatch.setattr(
        "lib.update.flake.FlakeLock.from_file", lambda _path: locks.pop(0)
    )

    first = load_flake_lock()
    second = load_flake_lock()
    assert first is second
    assert first.nodes["demo"] == "a"

    invalidate_flake_lock()
    third = load_flake_lock()
    assert third.nodes["demo"] == "b"
    invalidate_flake_lock()


def test_refs_version_parsing_and_selection_helpers() -> None:
    """Select latest stable tags while respecting prefix semantics."""
    assert _is_version_ref("v1.2.3") is True
    assert _is_version_ref("master") is False
    assert _is_version_ref("nixpkgs-unstable") is False
    assert _is_version_ref("nixos-24.11") is False
    assert _is_version_ref("deadbeef") is False
    assert _is_version_ref("feature") is False

    assert _extract_version_prefix("release-v2.3.4") == "release-v"
    assert _extract_version_prefix("stable") == ""
    assert _build_version_prefixes("release-v") == ["release-v", "v"]
    assert _build_version_prefixes("v") == ["v", ""]
    assert _build_version_prefixes("refs/tags/v") == ["refs/tags/v", "v", ""]
    assert _build_version_prefixes("rel-") == ["rel-"]
    assert _build_version_prefixes("refs/tags/release-") == [
        "refs/tags/release-",
        "release-",
    ]
    assert _parse_github_git_url("https://github.com/desktop/desktop.git") == (
        "desktop",
        "desktop",
    )
    assert _parse_github_git_url("git@github.com:desktop/desktop.git") == (
        "desktop",
        "desktop",
    )
    assert _parse_github_git_url("https://gitlab.com/desktop/desktop.git") is None
    assert _format_git_ref_update("release-1", "release-2") == "release-2"

    assert _tag_matches_prefix("release-v1.2.3", "release-v") is True
    assert _tag_matches_prefix("1.2.3", "") is True
    assert _tag_matches_prefix("v1.2.3", "") is False

    assert str(_parse_tag_version("release-v1.2.3", "release-v")) == "1.2.3"
    assert str(_parse_tag_version("release-v1.2.3", "release-")) == "1.2.3"
    assert _parse_tag_version("release-v1.2.3-rc1", "release-v") is None
    assert (
        str(
            _parse_tag_version(
                "release-v1.2.3-rc1",
                "release-v",
                allow_prerelease=True,
            )
        )
        == "1.2.3rc1"
    )
    assert _parse_tag_version("bad-tag", "release-v") is None

    assert (
        _select_tag(["release-v1.2.0", "release-v1.10.0"], "release-v")
        == "release-v1.10.0"
    )
    assert (
        _select_tag(
            ["release-v1.2.0", "release-v1.11.0-beta1"],
            "release-v",
            allow_prerelease=True,
        )
        == "release-v1.11.0-beta1"
    )
    assert _select_tag(["release-vnext"], "release-v") == "release-vnext"
    assert _select_tag(["x"], "release-v") is None

    releases = [
        {"tag_name": "v1.2.3", "draft": False, "prerelease": False},
        {"tag_name": "v9.9.9-rc1", "draft": False, "prerelease": True},
        {"tag_name": "v3.0.0", "draft": True, "prerelease": False},
    ]
    assert _select_tag_from_releases(releases, "v") == "v1.2.3"
    assert (
        _select_tag_from_releases(releases, "v", allow_prerelease=True) == "v9.9.9-rc1"
    )

    tags = [{"name": "v1.0.0"}, {"name": "v9.9.9"}, {"name": 1}]
    assert _select_tag_from_tags(tags, "v") == "v9.9.9"
    assert (
        _select_tag_from_tags(
            [{"name": "v1.0.0"}, {"name": "v2.0.0-beta1"}],
            "v",
            allow_prerelease=True,
        )
        == "v2.0.0-beta1"
    )


def test_get_flake_inputs_with_refs_filters_expected_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collect only version-like GitHub/GitLab/Git root inputs."""
    valid_node = FlakeLockNode(
        original=OriginalRef(type="github", owner="o", repo="r", ref="v1.0.0")
    )
    gitlab_node = FlakeLockNode(
        original=OriginalRef(type="gitlab", owner="o2", repo="r2", ref="1.2.3")
    )
    git_node = FlakeLockNode(
        original=OriginalRef.model_validate({
            "type": "git",
            "url": "https://github.com/desktop/desktop.git",
            "ref": "refs/tags/release-3.5.9-beta2",
            "submodules": True,
        })
    )
    git_unparsed_node = FlakeLockNode(
        original=OriginalRef.model_validate({
            "type": "git",
            "url": "https://example.com/desktop/desktop.git",
            "ref": "refs/tags/release-3.5.9-beta2",
        })
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
                "git": "git-node",
                "git-unparsed": "git-unparsed-node",
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
            "git-node": git_node,
            "git-unparsed-node": git_unparsed_node,
            "branch-node": branch_node,
            "unsupported-node": unsupported_node,
            "missing-owner": missing_owner,
        },
    )
    monkeypatch.setattr("lib.update.refs.load_flake_lock", lambda: model)

    items = get_flake_inputs_with_refs()
    assert [item.name for item in items] == ["git", "gitlab", "valid"]
    assert items[0].input_type == "git"
    assert items[0].owner == "desktop"
    assert items[0].repo == "desktop"
    assert items[0].submodules is True
    assert items[1].input_type == "gitlab"


def test_get_flake_inputs_with_refs_empty_root_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return an empty list when root inputs are absent."""
    model = SimpleNamespace(root_node=SimpleNamespace(inputs=None), nodes={})
    monkeypatch.setattr("lib.update.refs.load_flake_lock", lambda: model)
    assert get_flake_inputs_with_refs() == []


def test_rewrite_git_input_ref_preserves_submodule_attrset() -> None:
    """Update only the generic git input ref while preserving input metadata."""
    source = """{
  inputs = {
    github-desktop = {
      type = "git";
      url = "https://github.com/desktop/desktop.git";
      ref = "refs/tags/release-3.5.9-beta1";
      submodules = true;
      flake = false;
    };
  };
  outputs = { self, ... }: {};
}
"""

    rewritten = _rewrite_git_input_ref(
        source,
        "github-desktop",
        "refs/tags/release-3.5.9-beta2",
    )

    root = expect_instance(parse_nix_expr(rewritten), AttributeSet)
    inputs = expect_instance(expect_binding(root.values, "inputs").value, AttributeSet)
    github_desktop = expect_instance(
        expect_binding(inputs.values, "github-desktop").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(github_desktop.values, "ref").value,
        '"refs/tags/release-3.5.9-beta2"',
    )
    assert_nix_ast_equal(
        expect_binding(github_desktop.values, "submodules").value, "true"
    )
    assert_nix_ast_equal(expect_binding(github_desktop.values, "flake").value, "false")


def test_rewrite_git_input_ref_inserts_missing_ref_and_reports_absent_input() -> None:
    """Insert refs into generic git inputs that only declare a URL."""
    source = """{
  inputs = {
    github-desktop = {
      type = "git";
      url = "https://github.com/desktop/desktop.git";
      submodules = true;
    };
  };
  outputs = { self, ... }: {};
}"""

    rewritten = _rewrite_git_input_ref(
        source,
        "github-desktop",
        "refs/tags/release-3.5.9-beta2",
    )

    root = expect_instance(parse_nix_expr(rewritten), AttributeSet)
    inputs = expect_instance(expect_binding(root.values, "inputs").value, AttributeSet)
    github_desktop = expect_instance(
        expect_binding(inputs.values, "github-desktop").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(github_desktop.values, "ref").value,
        '"refs/tags/release-3.5.9-beta2"',
    )

    with pytest.raises(RuntimeError, match="Could not find git flake input attrset"):
        _rewrite_git_input_ref(source, "missing", "v2")


def test_update_git_input_ref_in_flake_writes_changed_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Update the repository flake file only when the rendered text changes."""
    flake_path = tmp_path / "flake.nix"
    flake_path.write_text(
        """{
  inputs = {
    github-desktop = {
      type = "git";
      url = "https://github.com/desktop/desktop.git";
    };
  };
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("lib.update.refs.get_repo_root", lambda: tmp_path)

    _update_git_input_ref_in_flake("github-desktop", "refs/tags/release-3.5.9-beta2")

    root = expect_instance(parse_nix_expr(flake_path.read_text()), AttributeSet)
    inputs = expect_instance(expect_binding(root.values, "inputs").value, AttributeSet)
    github_desktop = expect_instance(
        expect_binding(inputs.values, "github-desktop").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(github_desktop.values, "ref").value,
        '"refs/tags/release-3.5.9-beta2"',
    )

    updated_text = flake_path.read_text(encoding="utf-8")
    _update_git_input_ref_in_flake("github-desktop", "refs/tags/release-3.5.9-beta2")
    assert flake_path.read_text(encoding="utf-8") == updated_text


def test_get_root_input_name_without_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the requested name when the root node exposes no inputs."""
    model = SimpleNamespace(root_node=SimpleNamespace(inputs=None), nodes={})
    monkeypatch.setattr("lib.update.flake.load_flake_lock", lambda: model)
    assert get_root_input_name("nixpkgs") == "nixpkgs"


def test_nixpkgs_expr_compacts_rebuilt_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build the nixpkgs expression through the compaction helper."""
    monkeypatch.setattr(
        "lib.update.flake.nixpkgs_expression",
        lambda: SimpleNamespace(rebuild=lambda: "raw-expr"),
    )
    monkeypatch.setattr("lib.update.flake.compact_nix_expr", lambda expr: f"<{expr}>")
    assert nixpkgs_expr() == "<raw-expr>"


def test_fetch_first_matching_tag_falls_back_to_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle release API failure and select from tags endpoint."""
    calls: list[tuple[str, int, int, int | None]] = []

    async def _fetch_paginated(
        _session: aiohttp.ClientSession,
        path: str,
        *,
        config: object,
        per_page: int,
        max_pages: int,
        item_limit: int | None = None,
    ) -> list[object]:
        _ = config
        calls.append((path, per_page, max_pages, item_limit))
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
    assert latest == "v1.3.0"
    assert calls == [
        ("repos/owner/repo/releases", 50, 5, 250),
        ("repos/owner/repo/tags", 100, 5, 500),
    ]


def test_fetch_first_matching_tag_returns_none_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Continue through both candidates and return ``None`` if nothing matches."""

    async def _fetch_paginated(
        *_args: object,
        item_limit: int | None = None,
        **_kwargs: object,
    ) -> list[object]:
        assert item_limit in {250, 500}
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

    assert _run_async(_run()) is None


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
        allow_prerelease: bool = False,
    ) -> str | None:
        _ = (config, allow_prerelease)
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
    assert result == "v1.2.3"
    assert calls == ["release-v", "v"]


def test_check_flake_ref_update_follows_prerelease_when_current_ref_is_prerelease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prerelease-pinned inputs should keep following prerelease tags."""
    calls: list[bool] = []

    async def _latest(
        *_args: object,
        allow_prerelease: bool = False,
        **_kwargs: object,
    ) -> str | None:
        calls.append(allow_prerelease)
        return "release-3.5.9-beta2"

    monkeypatch.setattr("lib.update.refs.fetch_github_latest_version_ref", _latest)

    async def _run(ref: str, input_type: str = "github") -> RefUpdateResult:
        async with aiohttp.ClientSession() as session:
            return await check_flake_ref_update(
                FlakeInputRef("demo", "desktop", "desktop", ref, input_type),
                session,
            )

    beta = _run_async(_run("release-3.5.9-beta1"))
    stable = _run_async(_run("release-3.5.8"))
    git_beta = _run_async(_run("refs/tags/release-3.5.9-beta1", "git"))

    assert beta.latest_ref == "release-3.5.9-beta2"
    assert stable.latest_ref == "release-3.5.9-beta2"
    assert git_beta.latest_ref == "refs/tags/release-3.5.9-beta2"
    assert calls == [True, False, True]


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

    assert _run_async(_run()) is None


def test_fetch_first_matching_tag_falls_back_to_tags_after_release_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep searching when releases fail but tags can still answer the query."""
    calls: list[str] = []

    async def _fake_fetch(
        _session: aiohttp.ClientSession,
        path: str,
        *,
        config: object,
        per_page: int,
        max_pages: int,
        item_limit: int,
    ) -> list[dict[str, object]]:
        del config, per_page, max_pages, item_limit
        calls.append(path)
        if path.endswith("/releases"):
            msg = "rate limited"
            raise RuntimeError(msg)
        return [{"name": "v1.2.3"}]

    monkeypatch.setattr("lib.update.refs.fetch_github_api_paginated", _fake_fetch)

    async def _run() -> str | None:
        async with aiohttp.ClientSession() as session:
            return await _fetch_first_matching_tag(
                session,
                "owner",
                "repo",
                "v",
                config=resolve_config(),
            )

    assert _run_async(_run()) == "v1.2.3"
    assert calls == ["repos/owner/repo/releases", "repos/owner/repo/tags"]


def test_fetch_first_matching_tag_raises_when_tags_lookup_fails_after_no_release_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat a failed tags lookup as authoritative lookup failure."""

    async def _fake_fetch(
        _session: aiohttp.ClientSession,
        path: str,
        *,
        config: object,
        per_page: int,
        max_pages: int,
        item_limit: int,
    ) -> list[dict[str, object]]:
        del config, per_page, max_pages, item_limit
        if path.endswith("/releases"):
            return []
        msg = "bad gateway"
        raise RuntimeError(msg)

    monkeypatch.setattr("lib.update.refs.fetch_github_api_paginated", _fake_fetch)

    async def _run() -> str | None:
        async with aiohttp.ClientSession() as session:
            return await _fetch_first_matching_tag(
                session,
                "owner",
                "repo",
                "v",
                config=resolve_config(),
            )

    with pytest.raises(RuntimeError, match="GitHub API lookup failed for owner/repo"):
        _run_async(_run())


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
    assert isinstance(no_latest, RefUpdateResult)
    assert no_latest.error == "Could not determine latest version"

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
    assert latest.latest_ref == "v2"
    unsupported = _run_async(
        check_flake_ref_update(
            FlakeInputRef("demo", "o", "r", "v1", "gitlab"),
            SimpleNamespace(),
        )
    )
    assert isinstance(unsupported, RefUpdateResult)
    assert "Unsupported input type" in (unsupported.error or "")


def test_check_flake_ref_update_preserves_lookup_failure_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report upstream lookup failures instead of collapsing them into no-update."""

    async def _fail(*_args: object, **_kwargs: object) -> str | None:
        msg = "GitHub API lookup failed for owner/repo: rate limited"
        raise RuntimeError(msg)

    monkeypatch.setattr("lib.update.refs.fetch_github_latest_version_ref", _fail)

    async def _run() -> RefUpdateResult:
        async with aiohttp.ClientSession() as session:
            return await check_flake_ref_update(
                FlakeInputRef("demo", "owner", "repo", "v1", "github"),
                session,
            )

    result = _run_async(_run())
    assert isinstance(result, RefUpdateResult)
    assert result.error == "GitHub API lookup failed for owner/repo: rate limited"


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
    assert isinstance(ok_events, list)
    assert len(ok_events) == 1

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
    assert isinstance(non_result_events, list)
    assert len(non_result_events) == 2

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
    rewritten_refs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "lib.update.refs._update_git_input_ref_in_flake",
        lambda input_name, new_ref: rewritten_refs.append((input_name, new_ref)),
    )
    github_ref = FlakeInputRef("demo", "owner", "repo", "v1", "github")
    _run_async(update_flake_ref(github_ref, "v2", source="demo"))
    assert called_args[0][:3] == ["flake-edit", "change", "demo"]
    assert called_args[1][:4] == ["nix", "flake", "lock", "--update-input"]

    called_args.clear()
    gitlab_ref = FlakeInputRef("demo", "owner", "repo", "v1", "gitlab")
    _run_async(update_flake_ref(gitlab_ref, "v2", source="demo"))
    assert "gitlab:owner/repo/v2" in " ".join(called_args[0])

    called_args.clear()
    git_ref = FlakeInputRef(
        "demo",
        "desktop",
        "desktop",
        "refs/tags/release-3.5.9-beta1",
        "git",
        submodules=True,
    )
    _run_async(update_flake_ref(git_ref, "release-3.5.9-beta2", source="demo"))
    assert rewritten_refs == [("demo", "refs/tags/release-3.5.9-beta2")]
    assert called_args[0][:4] == ["nix", "flake", "lock", "--update-input"]

    with pytest.raises(RuntimeError, match="Unsupported input type"):
        _run_async(
            update_flake_ref(
                FlakeInputRef("demo", "o", "r", "v1", "path"), "v2", source="demo"
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
    assert any(event.kind == UpdateEventKind.ERROR for event in events_error)

    events_uptodate = _run_async(
        _run_case(
            RefUpdateResult(name="demo", current_ref="v1", latest_ref="v1"),
            options=RefTaskOptions(),
        )
    )
    assert any(
        (event.message or "").startswith("Up to date") for event in events_uptodate
    )
    assert any(event.kind == UpdateEventKind.RESULT for event in events_uptodate)

    events_missing_latest = _run_async(
        _run_case(
            RefUpdateResult(name="demo", current_ref="v1", latest_ref=None),
            options=RefTaskOptions(),
        )
    )
    assert any(event.kind == UpdateEventKind.ERROR for event in events_missing_latest)

    events_dry_run = _run_async(
        _run_case(
            RefUpdateResult(name="demo", current_ref="v1", latest_ref="v2"),
            options=RefTaskOptions(dry_run=True),
        )
    )
    assert any("Update available" in (event.message or "") for event in events_dry_run)

    lock = asyncio.Lock()
    events_real = _run_async(
        _run_case(
            RefUpdateResult(name="demo", current_ref="v1", latest_ref="v2"),
            options=RefTaskOptions(
                dry_run=False, flake_edit_lock=lock, config=resolve_config()
            ),
        )
    )
    assert any((event.message or "") == "updated" for event in events_real)
    assert any(event.kind == UpdateEventKind.RESULT for event in events_real)

    events_real_no_lock = _run_async(
        _run_case(
            RefUpdateResult(name="demo", current_ref="v1", latest_ref="v2"),
            options=RefTaskOptions(dry_run=False, config=resolve_config()),
        )
    )
    assert any((event.message or "") == "updated" for event in events_real_no_lock)
