"""Tests for the OpenCode Desktop updater."""

from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace

import pytest

from lib.import_utils import load_module_from_path
from lib.nix.models.sources import HashCollection, HashEntry
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo

HASH_A = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
HASH_B = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
HASH_C = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="

LOCKFILE = """
version = 4

[[package]]
name = "specta"
version = "1.0.0"
source = "git+https://example.invalid/specta"

[[package]]
name = "tauri"
version = "2.0.0"
source = "git+https://example.invalid/tauri"

[[package]]
name = "tauri-specta"
version = "3.0.0"
source = "git+https://example.invalid/tauri-specta"
"""


def _run[T](coro):
    return asyncio.run(coro)


async def _collect_events(stream):
    return [event async for event in stream]


def _load_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/opencode-desktop/updater.py",
        "opencode_desktop_updater_test",
    )


def test_resolve_git_dep_keys_returns_expected_keys() -> None:
    """Match each target git dependency by its name and version."""
    module = _load_module()

    keys = module.OpencodeDesktopUpdater._resolve_git_dep_keys(LOCKFILE)

    assert keys == {
        "specta": "specta-1.0.0",
        "tauri": "tauri-2.0.0",
        "tauri-specta": "tauri-specta-3.0.0",
    }


def test_resolve_git_dep_keys_ignores_non_matching_entries_without_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignore non-dict, malformed, non-git, and duplicate-identical package entries."""
    module = _load_module()
    monkeypatch.setattr(
        module.tomllib,
        "loads",
        lambda _text: {
            "package": [
                "not-a-package",
                {
                    "name": 1,
                    "version": "1.0.0",
                    "source": "git+https://example.invalid/ignored",
                },
                {
                    "name": "ignored",
                    "version": "1.0.0",
                    "source": "registry+https://example.invalid",
                },
                {
                    "name": "specta",
                    "version": "1.0.0",
                    "source": "git+https://example.invalid/specta",
                },
                {
                    "name": "specta",
                    "version": "1.0.0",
                    "source": "git+https://example.invalid/specta",
                },
                {
                    "name": "tauri",
                    "version": "2.0.0",
                    "source": "git+https://example.invalid/tauri",
                },
                {
                    "name": "tauri-specta",
                    "version": "3.0.0",
                    "source": "git+https://example.invalid/tauri-specta",
                },
            ]
        },
    )

    assert module.OpencodeDesktopUpdater._resolve_git_dep_keys("ignored") == {
        "specta": "specta-1.0.0",
        "tauri": "tauri-2.0.0",
        "tauri-specta": "tauri-specta-3.0.0",
    }


@pytest.mark.parametrize(
    ("lockfile", "match"),
    [
        ("version = 4\n", "missing a top-level package array"),
        (
            """
version = 4

[[package]]
name = "specta"
version = "1.0.0"
source = "git+https://example.invalid/specta"

[[package]]
name = "tauri"
version = "2.0.0"
source = "git+https://example.invalid/tauri"
""",
            "Missing git dependencies in Cargo.lock",
        ),
        (
            """
version = 4

[[package]]
name = "specta"
version = "1.0.0"
source = "git+https://example.invalid/specta"

[[package]]
name = "specta"
version = "1.1.0"
source = "git+https://example.invalid/specta-next"

[[package]]
name = "tauri"
version = "2.0.0"
source = "git+https://example.invalid/tauri"

[[package]]
name = "tauri-specta"
version = "3.0.0"
source = "git+https://example.invalid/tauri-specta"
""",
            "Multiple git dependency keys matched 'specta'",
        ),
    ],
)
def test_resolve_git_dep_keys_rejects_invalid_lockfiles(
    lockfile: str,
    match: str,
) -> None:
    """Raise clear errors when the upstream Cargo.lock shape is invalid."""
    module = _load_module()

    with pytest.raises((RuntimeError, TypeError), match=match):
        module.OpencodeDesktopUpdater._resolve_git_dep_keys(lockfile)


def test_fetch_lockfile_content_reads_raw_github_lockfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build the raw GitHub URL from flake metadata and decode bytes safely."""
    module = _load_module()
    updater = module.OpencodeDesktopUpdater()
    info = VersionInfo(version="1.2.3")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        updater,
        "_resolve_flake_node",
        lambda _info: SimpleNamespace(
            locked=SimpleNamespace(owner="sst", repo="opencode", rev="deadbeef")
        ),
    )

    async def _fetch_url(session, url: str, **kwargs):
        captured.update({"session": session, "url": url, **kwargs})
        return b"lockfile\xff"

    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    session = object()
    content = _run(updater._fetch_lockfile_content(info, session))

    assert content == "lockfile\ufffd"
    assert captured == {
        "session": session,
        "url": (
            "https://raw.githubusercontent.com/"
            "sst/opencode/deadbeef/packages/desktop/src-tauri/Cargo.lock"
        ),
        "request_timeout": updater.config.default_timeout,
        "config": updater.config,
        "user_agent": updater.config.default_user_agent,
    }


def test_fetch_lockfile_content_requires_complete_flake_metadata() -> None:
    """Refuse to fetch when the flake input lacks owner, repo, or rev."""
    module = _load_module()
    updater = module.OpencodeDesktopUpdater()
    info = VersionInfo(version="1.2.3")

    updater._resolve_flake_node = lambda _info: SimpleNamespace(  # type: ignore[method-assign]
        locked=SimpleNamespace(owner="sst", repo=None, rev="deadbeef")
    )

    with pytest.raises(
        RuntimeError,
        match="missing owner/repo/rev metadata",
    ):
        _run(updater._fetch_lockfile_content(info, object()))


def test_fetch_hashes_streams_materialization_and_builds_hash_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward materialization and cargo events before emitting HashEntry values."""
    module = _load_module()
    updater = module.OpencodeDesktopUpdater()
    info = VersionInfo(version="1.2.3")
    git_dep_keys = {
        "specta": "specta-1.0.0",
        "tauri": "tauri-2.0.0",
        "tauri-specta": "tauri-specta-3.0.0",
    }

    async def _materialized():
        yield UpdateEvent.status(updater.name, "materializing")

    async def _hashes(name: str, input_name: str, **kwargs):
        assert name == updater.name
        assert input_name == updater._input
        assert kwargs["lockfile_path"] == updater._LOCKFILE_PATH
        assert kwargs["lockfile_content"] == LOCKFILE
        assert kwargs["config"] == updater.config
        assert [dep.git_dep for dep in kwargs["git_deps"]] == [
            "specta-1.0.0",
            "tauri-2.0.0",
            "tauri-specta-3.0.0",
        ]
        yield UpdateEvent.status(name, "computing cargo hashes")
        yield UpdateEvent.value(
            name,
            {
                "specta-1.0.0": HASH_A,
                "tauri-2.0.0": HASH_B,
                "tauri-specta-3.0.0": HASH_C,
            },
        )

    monkeypatch.setattr(updater, "stream_materialized_artifacts", _materialized)
    monkeypatch.setattr(
        updater,
        "_fetch_lockfile_content",
        lambda *_args: asyncio.sleep(0, result=LOCKFILE),
    )
    monkeypatch.setattr(updater, "_resolve_git_dep_keys", lambda _content: git_dep_keys)
    monkeypatch.setattr(module, "compute_import_cargo_lock_output_hashes", _hashes)

    events = _run(_collect_events(updater.fetch_hashes(info, object())))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert [event.message for event in events[:-1]] == [
        "materializing",
        "computing cargo hashes",
    ]
    assert events[-1].payload == [
        HashEntry.create("spectaOutputHash", HASH_A, git_dep="specta-1.0.0"),
        HashEntry.create("tauriOutputHash", HASH_B, git_dep="tauri-2.0.0"),
        HashEntry.create(
            "tauriSpectaOutputHash",
            HASH_C,
            git_dep="tauri-specta-3.0.0",
        ),
    ]


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (["not", "a", "mapping"], "Expected hash mapping from cargo updater"),
        ({"specta-1.0.0": 1}, "Expected string key/value hash mapping"),
        ({1: HASH_A}, "Expected string key/value hash mapping"),
    ],
)
def test_fetch_hashes_rejects_invalid_cargo_value_payloads(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    match: str,
) -> None:
    """Type-check cargo updater VALUE payloads before building HashEntry output."""
    module = _load_module()
    updater = module.OpencodeDesktopUpdater()

    async def _materialized():
        if False:
            yield UpdateEvent.status(updater.name, "never")

    async def _hashes(*_args, **_kwargs):
        yield UpdateEvent.value(updater.name, payload)

    monkeypatch.setattr(updater, "stream_materialized_artifacts", _materialized)
    monkeypatch.setattr(
        updater,
        "_fetch_lockfile_content",
        lambda *_args: asyncio.sleep(0, result=LOCKFILE),
    )
    monkeypatch.setattr(
        updater,
        "_resolve_git_dep_keys",
        lambda _content: {
            "specta": "specta-1.0.0",
            "tauri": "tauri-2.0.0",
            "tauri-specta": "tauri-specta-3.0.0",
        },
    )
    monkeypatch.setattr(module, "compute_import_cargo_lock_output_hashes", _hashes)

    with pytest.raises(TypeError, match=match):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.2.3"), object())
            )
        )


def test_fetch_hashes_requires_cargo_hash_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise when the cargo hash helper emits no VALUE payload at all."""
    module = _load_module()
    updater = module.OpencodeDesktopUpdater()

    async def _materialized():
        if False:
            yield UpdateEvent.status(updater.name, "never")

    async def _hashes(*_args, **_kwargs):
        yield UpdateEvent.status(updater.name, "still working")

    monkeypatch.setattr(updater, "stream_materialized_artifacts", _materialized)
    monkeypatch.setattr(
        updater,
        "_fetch_lockfile_content",
        lambda *_args: asyncio.sleep(0, result=LOCKFILE),
    )
    monkeypatch.setattr(
        updater,
        "_resolve_git_dep_keys",
        lambda _content: {
            "specta": "specta-1.0.0",
            "tauri": "tauri-2.0.0",
            "tauri-specta": "tauri-specta-3.0.0",
        },
    )
    monkeypatch.setattr(module, "compute_import_cargo_lock_output_hashes", _hashes)

    with pytest.raises(
        RuntimeError, match="Missing opencode-desktop cargo hash output"
    ):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.2.3"), object())
            )
        )


def test_build_result_preserves_version_input_commit_and_hashes() -> None:
    """Build a SourceEntry with the current flake input and commit metadata."""
    module = _load_module()
    updater = module.OpencodeDesktopUpdater()
    info = VersionInfo(version="1.2.3", metadata={"commit": "a" * 40})

    result = updater.build_result(info, [HashEntry.create("spectaOutputHash", HASH_A)])

    assert result.version == "1.2.3"
    assert result.input == "opencode"
    assert result.commit == "a" * 40
    assert result.hashes == HashCollection.from_value([
        HashEntry.create("spectaOutputHash", HASH_A)
    ])
