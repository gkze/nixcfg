"""Tests for Cargo.lock git hash extraction helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lib.tests._assertions import check
from lib.update.events import GatheredValues, UpdateEvent, UpdateEventKind
from lib.update.nix_cargo import (
    _parse_cargo_lock_git_sources,
    _parse_git_source_line,
    _parse_quoted_assignment,
    _prefetch_git_hash,
    _record_git_source_match,
    _select_matching_git_dep,
    compute_import_cargo_lock_output_hashes,
)
from lib.update.updaters.base import CargoLockGitDep

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _dep(name: str, match_name: str | None = None) -> CargoLockGitDep:
    return CargoLockGitDep(
        git_dep=name,
        hash_type="sha256",
        match_name=match_name or name,
    )


def _collect(stream: AsyncIterator[UpdateEvent]) -> list[UpdateEvent]:
    async def _run() -> list[UpdateEvent]:
        items: list[UpdateEvent] = []
        async for item in stream:
            items.append(item)
        return items

    return asyncio.run(_run())


def test_cargo_lock_parsing_helpers() -> None:
    """Parse quoted assignments and git source lines."""
    check(_parse_quoted_assignment('name = "demo"', "name") == "demo")
    check(_parse_quoted_assignment("name = demo", "name") is None)

    parsed = _parse_git_source_line(
        'source = "git+https://github.com/a/b?branch=main#deadbeef"'
    )
    check(parsed == ("https://github.com/a/b", "deadbeef"))
    check(_parse_git_source_line('source = "registry+https://example.com"') is None)


def test_select_and_record_matching_git_dep() -> None:
    """Prefer direct dep keys, then unique prefix matches."""
    direct = {"dep": _dep("dep")}
    check(
        _select_matching_git_dep(direct, dep_key="dep", crate_name="dep")
        is direct["dep"]
    )

    prefix_only = {
        "dep-1": _dep("dep-1", match_name="dep"),
    }
    selected = _select_matching_git_dep(
        prefix_only,
        dep_key="other",
        crate_name="dep-crate",
    )
    check(selected is prefix_only["dep-1"])

    ambiguous = {
        "a": _dep("a", match_name="dep"),
        "b": _dep("b", match_name="dep"),
    }
    check(
        _select_matching_git_dep(ambiguous, dep_key="other", crate_name="dep-crate")
        is None
    )

    unmatched = {"dep": _dep("dep", match_name="crate")}
    result: dict[str, tuple[str, str]] = {}
    _record_git_source_match(
        current_name="crate-core",
        current_version="1.0.0",
        git_source=("https://github.com/a/b", "deadbeef"),
        unmatched=unmatched,
        result=result,
    )
    check(result == {"dep": ("https://github.com/a/b", "deadbeef")})
    check(unmatched == {})


def test_select_matching_requires_hyphenated_prefix() -> None:
    """Avoid fuzzy prefix matches that are not crate-name prefixes."""
    unmatched = {"dep": _dep("dep", match_name="dep")}
    check(
        _select_matching_git_dep(
            unmatched,
            dep_key="other",
            crate_name="depot",
        )
        is None
    )


def test_select_matching_prefers_exact_crate_name() -> None:
    """Select exact crate-name matches when dep key is different."""
    unmatched = {"dep-1.2.3": _dep("dep-1.2.3", match_name="dep")}
    selected = _select_matching_git_dep(
        unmatched,
        dep_key="other",
        crate_name="dep",
    )
    check(selected is unmatched["dep-1.2.3"])


def test_parse_cargo_lock_git_sources_success_and_error() -> None:
    """Resolve all configured git deps from lockfile content."""
    lock_content = "\n".join([
        "[[package]]",
        'name = "crate-core"',
        'version = "1.2.3"',
        'source = "git+https://github.com/a/b?branch=main#deadbeef"',
        "[[package]]",
        'name = "other"',
        'version = "2.0.0"',
        'source = "git+https://github.com/c/d?tag=v2#cafebabe"',
    ])
    deps = [
        _dep("crate-core-1.2.3", match_name="crate-core"),
        _dep("other-2.0.0", match_name="other"),
    ]
    parsed = _parse_cargo_lock_git_sources(lock_content, deps)
    check(parsed["crate-core-1.2.3"] == ("https://github.com/a/b", "deadbeef"))
    check(parsed["other-2.0.0"] == ("https://github.com/c/d", "cafebabe"))

    with pytest.raises(RuntimeError, match="Could not find git sources"):
        _parse_cargo_lock_git_sources(
            lock_content, [_dep("missing", match_name="missing")]
        )


def test_prefetch_git_hash_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return SRI hash from nix eval and reject malformed outputs."""

    async def _run_command_ok(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        from lib.update.events import CommandResult

        result = CommandResult(
            args=["nix"],
            returncode=0,
            stdout='"sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="',
            stderr="",
        )
        yield UpdateEvent(
            kind=UpdateEventKind.COMMAND_END, source="demo", payload=result
        )
        yield UpdateEvent.value("demo", result)

    monkeypatch.setattr("lib.update.nix_cargo.run_command", _run_command_ok)
    events = _collect(_prefetch_git_hash("demo", "https://github.com/a/b", "deadbeef"))
    check(events[-1].kind == UpdateEventKind.VALUE)

    async def _run_command_fail(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        from lib.update.events import CommandResult

        result = CommandResult(args=["nix"], returncode=1, stdout="", stderr="failed")
        yield UpdateEvent(
            kind=UpdateEventKind.COMMAND_END, source="demo", payload=result
        )
        yield UpdateEvent.value("demo", result)

    monkeypatch.setattr("lib.update.nix_cargo.run_command", _run_command_fail)
    with pytest.raises(RuntimeError, match="builtins.fetchGit failed"):
        _collect(_prefetch_git_hash("demo", "https://github.com/a/b", "deadbeef"))

    async def _run_command_bad_hash(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        from lib.update.events import CommandResult

        result = CommandResult(
            args=["nix"], returncode=0, stdout='"not-sri"', stderr=""
        )
        yield UpdateEvent(
            kind=UpdateEventKind.COMMAND_END, source="demo", payload=result
        )
        yield UpdateEvent.value("demo", result)

    monkeypatch.setattr("lib.update.nix_cargo.run_command", _run_command_bad_hash)
    with pytest.raises(RuntimeError, match="Unexpected hash format"):
        _collect(_prefetch_git_hash("demo", "https://github.com/a/b", "deadbeef"))


def test_compute_import_cargo_lock_output_hashes_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load lockfile, prefetch deps, and emit final git dep hash mapping."""
    lock_content = "\n".join([
        "[[package]]",
        'name = "crate-core"',
        'version = "1.2.3"',
        'source = "git+https://github.com/a/b?branch=main#deadbeef"',
    ])

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> bool:
            _ = (exc_type, exc, tb)
            return False

    monkeypatch.setattr("lib.update.nix_cargo.aiohttp.ClientSession", _Session)
    monkeypatch.setattr(
        "lib.update.nix_cargo.fetch_url",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=lock_content.encode()),
    )

    node = SimpleNamespace(
        locked=SimpleNamespace(owner="owner", repo="repo", rev="rev"),
    )
    monkeypatch.setattr("lib.update.nix_cargo.get_flake_input_node", lambda _name: node)

    async def _prefetch(
        source: str,
        url: str,
        rev: str,
        *,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        _ = (url, rev, config)
        yield UpdateEvent.value(source, f"sha256-{source}")

    monkeypatch.setattr("lib.update.nix_cargo._prefetch_git_hash", _prefetch)

    deps = [_dep("crate-core-1.2.3", match_name="crate-core")]
    events = _collect(
        compute_import_cargo_lock_output_hashes(
            "demo",
            "input",
            lockfile_path="Cargo.lock",
            git_deps=deps,
        )
    )
    check(
        any(
            (event.message or "") == "Fetching upstream Cargo.lock..."
            for event in events
        )
    )
    check(events[-1].kind == UpdateEventKind.VALUE)
    check(events[-1].payload == {"crate-core-1.2.3": "sha256-demo"})

    # locked metadata errors
    monkeypatch.setattr(
        "lib.update.nix_cargo.get_flake_input_node",
        lambda _name: SimpleNamespace(locked=None),
    )
    with pytest.raises(RuntimeError, match="has no locked info"):
        _collect(
            compute_import_cargo_lock_output_hashes(
                "demo",
                "input",
                lockfile_path="Cargo.lock",
                git_deps=deps,
            )
        )

    monkeypatch.setattr(
        "lib.update.nix_cargo.get_flake_input_node",
        lambda _name: SimpleNamespace(
            locked=SimpleNamespace(owner=None, repo="r", rev="x")
        ),
    )
    with pytest.raises(RuntimeError, match="missing owner/repo/rev"):
        _collect(
            compute_import_cargo_lock_output_hashes(
                "demo",
                "input",
                lockfile_path="Cargo.lock",
                git_deps=deps,
            )
        )


def test_compute_import_cargo_lock_output_hashes_gather_type_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject gathered values with invalid key/value types."""

    async def _fake_gather(_streams: object) -> AsyncIterator[object]:
        yield GatheredValues(values={1: "x"})

    monkeypatch.setattr("lib.update.nix_cargo.gather_event_streams", _fake_gather)
    monkeypatch.setattr(
        "lib.update.nix_cargo.get_flake_input_node",
        lambda _name: SimpleNamespace(
            locked=SimpleNamespace(owner="owner", repo="repo", rev="rev"),
        ),
    )

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr("lib.update.nix_cargo.aiohttp.ClientSession", _Session)
    monkeypatch.setattr(
        "lib.update.nix_cargo.fetch_url",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=(
                b'[[package]]\nname = "crate"\nversion = "1.0.0"\n'
                b'source = "git+https://github.com/a/b?x#deadbeef"\n'
            ),
        ),
    )

    deps = [_dep("crate-1.0.0", match_name="crate")]
    with pytest.raises(TypeError, match="Expected git dep key to be str"):
        _collect(
            compute_import_cargo_lock_output_hashes(
                "demo",
                "input",
                lockfile_path="Cargo.lock",
                git_deps=deps,
            )
        )

    async def _fake_gather_bad_value(_streams: object) -> AsyncIterator[object]:
        yield GatheredValues(values={"crate-1.0.0": ["not", "a", "hash"]})

    monkeypatch.setattr(
        "lib.update.nix_cargo.gather_event_streams", _fake_gather_bad_value
    )
    with pytest.raises(TypeError, match="Expected string payload"):
        _collect(
            compute_import_cargo_lock_output_hashes(
                "demo",
                "input",
                lockfile_path="Cargo.lock",
                git_deps=deps,
            )
        )


def test_compute_import_cargo_hashes_uses_provided_lockfile_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip lockfile fetch when content is provided by caller."""
    lock_content = "\n".join([
        "[[package]]",
        'name = "crate-core"',
        'version = "1.2.3"',
        'source = "git+https://github.com/a/b?branch=main#deadbeef"',
    ])

    def _unexpected_node(_name: str) -> object:
        msg = "get_flake_input_node should not be called"
        raise AssertionError(msg)

    monkeypatch.setattr("lib.update.nix_cargo.get_flake_input_node", _unexpected_node)

    async def _prefetch(
        source: str,
        url: str,
        rev: str,
        *,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        _ = (url, rev, config)
        yield UpdateEvent.value(source, "sha256-provided-lock")

    monkeypatch.setattr("lib.update.nix_cargo._prefetch_git_hash", _prefetch)

    deps = [_dep("crate-core-1.2.3", match_name="crate-core-1.2.3")]
    events = _collect(
        compute_import_cargo_lock_output_hashes(
            "demo",
            "input",
            lockfile_path="Cargo.lock",
            git_deps=deps,
            lockfile_content=lock_content,
        )
    )
    final = events[-1]
    check(final.kind == UpdateEventKind.VALUE)
    check(final.payload == {"crate-core-1.2.3": "sha256-provided-lock"})
