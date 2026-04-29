"""Tests for the hwatch updater overlay surface."""

from __future__ import annotations

from collections.abc import AsyncIterator

from lib.nix.models.sources import HashEntry
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.updaters.base import VersionInfo

CARGO_HASH = "sha256-dyugbY7v1GX1IoErX3lOOKycRWDy+9kZxMLRZScz7TQ="


def _load_module():
    return load_repo_module("overlays/hwatch/updater.py", "hwatch_updater_test")


def test_hwatch_updater_tracks_flake_input_and_cargo_hash() -> None:
    """Persist only the cargo vendor hash for the hwatch flake input."""
    module = _load_module()
    updater = module.HwatchUpdater()

    assert updater.name == "hwatch"
    assert updater.input_name == "hwatch"
    assert updater.hash_type == "cargoHash"

    result = updater.build_result(
        VersionInfo(version="0.4.1", metadata={}),
        [HashEntry.create("cargoHash", CARGO_HASH)],
    )

    assert result.version == "0.4.1"
    assert result.input == "hwatch"
    assert result.hashes.entries == [HashEntry.create("cargoHash", CARGO_HASH)]


def test_hwatch_updater_fetch_hashes_uses_overlay_hash(monkeypatch) -> None:
    """Compute cargoHash by evaluating the hwatch overlay package."""
    module = _load_module()
    updater = module.HwatchUpdater()
    info = VersionInfo(version="0.4.1", metadata={})
    calls: list[dict[str, object]] = []

    async def _compute_overlay_hash(
        source_name: str,
        *,
        system: str | None = None,
        config: object = None,
    ) -> AsyncIterator[UpdateEvent]:
        calls.append({"source_name": source_name, "system": system, "config": config})
        yield UpdateEvent.status(source_name, "building cargo")
        yield UpdateEvent.value(source_name, CARGO_HASH)

    monkeypatch.setattr(
        "lib.update.updaters.base.compute_overlay_hash",
        _compute_overlay_hash,
    )

    events = _run(_collect_events(updater.fetch_hashes(info, object())))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert events[0].message == "building cargo"
    assert events[-1].payload == [HashEntry.create("cargoHash", CARGO_HASH)]
    assert calls == [
        {"source_name": "hwatch", "system": None, "config": updater.config}
    ]
