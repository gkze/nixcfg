"""Tests for the codex-v8 updater."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from lib.import_utils import load_module_from_path
from lib.nix.models.sources import HashEntry
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo


def _run[T](coro):
    return asyncio.run(coro)


def test_codex_v8_updater_computes_recursive_src_hash(monkeypatch) -> None:
    """Compute source and Linux prebuilt hashes from the selected rusty_v8 tag."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/codex-v8/updater.py",
        "codex_v8_updater_test",
    )
    updater = module.CodexV8Updater()

    calls: list[str] = []
    url_batches: list[list[str]] = []

    async def _hash_stream(_name: str, expr: str, *, config=None):
        _ = config
        calls.append(expr)
        yield module.UpdateEvent.value(
            "codex-v8",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    async def _url_hashes(_name: str, urls):
        url_batches.append(list(urls))
        yield module.UpdateEvent.value(
            "codex-v8",
            {
                url_batches[0][
                    0
                ]: "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                url_batches[0][
                    1
                ]: "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
            },
        )

    monkeypatch.setattr(module, "compute_fixed_output_hash", _hash_stream)
    monkeypatch.setattr(module, "compute_url_hashes", _url_hashes)

    events = _run(
        _collect_events(
            updater.fetch_hashes(
                VersionInfo(version="v146.4.0"),
                object(),
            )
        )
    )

    assert "fetchgit" in calls[0]
    assert "fetchSubmodules" in calls[0]
    assert events[-1].payload == [
        HashEntry.create(
            "srcHash",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        ),
        HashEntry.create(
            "rustyV8ArchiveHash",
            "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
            platform="x86_64-linux",
            url="https://github.com/denoland/rusty_v8/releases/download/v146.4.0/librusty_v8_release_x86_64-unknown-linux-gnu.a.gz",
        ),
        HashEntry.create(
            "rustyV8BindingHash",
            "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
            platform="x86_64-linux",
            url="https://github.com/denoland/rusty_v8/releases/download/v146.4.0/src_binding_release_x86_64-unknown-linux-gnu.rs",
        ),
    ]


def test_codex_v8_is_latest_requires_all_expected_hash_entries() -> None:
    """The updater should only accept current entries with all required hashes present."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/codex-v8/updater.py",
        "codex_v8_updater_latest_test",
    )
    updater = module.CodexV8Updater()
    latest = VersionInfo(version="v146.4.0")

    incomplete = SimpleNamespace(
        version="v146.4.0",
        hashes=SimpleNamespace(
            entries=[
                HashEntry.create(
                    "srcHash",
                    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                )
            ]
        ),
    )
    complete = SimpleNamespace(
        version="v146.4.0",
        hashes=SimpleNamespace(
            entries=[
                HashEntry.create(
                    "srcHash",
                    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                ),
                HashEntry.create(
                    "rustyV8ArchiveHash",
                    "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                    platform="x86_64-linux",
                ),
                HashEntry.create(
                    "rustyV8BindingHash",
                    "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                    platform="x86_64-linux",
                ),
            ]
        ),
    )

    assert _run(updater._is_latest(None, latest)) is False
    assert (
        _run(
            updater._is_latest(
                SimpleNamespace(
                    version="v146.4.0",
                    hashes=SimpleNamespace(entries=None),
                ),
                latest,
            )
        )
        is False
    )
    assert _run(updater._is_latest(incomplete, latest)) is False
    assert _run(updater._is_latest(complete, latest)) is True


def test_codex_v8_fetch_hashes_forwards_non_value_events(monkeypatch) -> None:
    """Non-value events from both hash streams should be preserved."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/codex-v8/updater.py",
        "codex_v8_updater_forwarding_test",
    )
    updater = module.CodexV8Updater()

    async def _hash_stream(_name: str, _expr: str, *, config=None):
        _ = config
        yield module.UpdateEvent.status("codex-v8", "computing src")
        yield module.UpdateEvent.value(
            "codex-v8",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    async def _url_hashes(_name: str, urls):
        urls = list(urls)
        yield module.UpdateEvent.status("codex-v8", "computing assets")
        yield module.UpdateEvent.value(
            "codex-v8",
            {
                urls[0]: "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                urls[1]: "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
            },
        )

    monkeypatch.setattr(module, "compute_fixed_output_hash", _hash_stream)
    monkeypatch.setattr(module, "compute_url_hashes", _url_hashes)

    events = _run(
        _collect_events(
            updater.fetch_hashes(
                VersionInfo(version="v146.4.0"),
                object(),
            )
        )
    )

    assert [event.kind.value for event in events] == ["status", "status", "value"]
    assert [event.message for event in events[:-1]] == [
        "computing src",
        "computing assets",
    ]


async def _collect_events(stream):
    return [event async for event in stream]
