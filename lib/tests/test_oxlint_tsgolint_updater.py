"""Tests for the oxlint-tsgolint updater."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from lib.import_utils import load_module_from_path
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo


def _run[T](coro):
    return asyncio.run(coro)


async def _collect(stream):
    return [event async for event in stream]


def test_oxlint_tsgolint_is_latest_rejects_fake_and_empty_hash_mappings() -> None:
    """Placeholder and empty mapping hashes should force a refresh."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/oxlint-tsgolint/updater.py",
        "oxlint_tsgolint_latest_test",
    )
    updater = module.OxlintTsgolintUpdater()
    latest = VersionInfo("0.21.0")

    empty = SourceEntry(version="0.21.0", hashes={})
    fake = SourceEntry(
        version="0.21.0",
        hashes={"x86_64-linux": HashCollection.FAKE_HASH_PREFIX},
    )
    real = SourceEntry(
        version="0.21.0",
        hashes={"x86_64-linux": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="},
    )

    assert _run(updater._is_latest(empty, latest)) is False
    assert _run(updater._is_latest(fake, latest)) is False
    assert _run(updater._is_latest(real, latest)) is True


def test_oxlint_tsgolint_is_latest_rejects_mismatched_empty_and_missing_entries() -> (
    None
):
    """Version mismatches and missing structured hashes should not be treated as current."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/oxlint-tsgolint/updater.py",
        "oxlint_tsgolint_latest_entries_test",
    )
    updater = module.OxlintTsgolintUpdater()
    latest = VersionInfo("0.21.0")

    assert (
        _run(
            updater._is_latest(
                module.UpdateContext(
                    current=SourceEntry(
                        version="0.20.0",
                        hashes={
                            "x86_64-linux": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
                        },
                    )
                ),
                latest,
            )
        )
        is False
    )
    assert (
        _run(
            updater._is_latest(
                SimpleNamespace(
                    version="0.21.0",
                    hashes=SimpleNamespace(entries=[]),
                ),
                latest,
            )
        )
        is False
    )


def test_oxlint_tsgolint_is_latest_accepts_real_structured_entries() -> None:
    """A non-placeholder structured hash entry list should count as current."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/oxlint-tsgolint/updater.py",
        "oxlint_tsgolint_latest_real_entries_test",
    )
    updater = module.OxlintTsgolintUpdater()
    latest = VersionInfo("0.21.0")

    assert (
        _run(
            updater._is_latest(
                SimpleNamespace(
                    version="0.21.0",
                    hashes=SimpleNamespace(
                        entries=[
                            HashEntry.create(
                                "srcHash",
                                "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                            )
                        ]
                    ),
                ),
                latest,
            )
        )
        is True
    )
    assert (
        _run(
            updater._is_latest(
                SimpleNamespace(
                    version="0.21.0",
                    hashes=SimpleNamespace(entries=None, mapping=None),
                ),
                latest,
            )
        )
        is False
    )


def test_oxlint_tsgolint_fetch_hashes_computes_src_and_vendor_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater should compute srcHash first, then vendorHash with override env."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/oxlint-tsgolint/updater.py",
        "oxlint_tsgolint_hash_test",
    )
    updater = module.OxlintTsgolintUpdater()
    calls: list[dict[str, object]] = []

    async def _fixed_hash(_name: str, expr: str, **kwargs):
        calls.append({"expr": expr, **kwargs})
        yield module.UpdateEvent.value(
            "oxlint-tsgolint",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            if len(calls) == 1
            else "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
        )

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    events = _run(_collect(updater.fetch_hashes(VersionInfo("0.21.0"), object())))

    assert len(calls) == 2
    assert "fetchgit" in str(calls[0]["expr"])
    assert "fetchSubmodules" in str(calls[0]["expr"])
    assert str(calls[1]["expr"]) == module._build_overlay_expr("oxlint-tsgolint")
    assert calls[1]["env"] == updater._override_env(
        "0.21.0",
        "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        updater.config.fake_hash,
    )
    assert events[-1].payload == [
        HashEntry.create(
            "srcHash",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        ),
        HashEntry.create(
            "vendorHash",
            "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
        ),
    ]


def test_oxlint_tsgolint_fetch_hashes_requires_vendor_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing vendor hash should raise after the source hash succeeds."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/oxlint-tsgolint/updater.py",
        "oxlint_tsgolint_missing_vendor_test",
    )
    updater = module.OxlintTsgolintUpdater()

    async def _fixed_hash(_name: str, _expr: str, **_kwargs):
        if _fixed_hash.calls == 0:
            _fixed_hash.calls += 1
            yield module.UpdateEvent.value(
                "oxlint-tsgolint",
                "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            )
            return
        if False:
            yield None

    _fixed_hash.calls = 0

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    with pytest.raises(RuntimeError, match="Missing vendorHash output"):
        _run(_collect(updater.fetch_hashes(VersionInfo("0.21.0"), object())))


def test_oxlint_tsgolint_fetch_hashes_forwards_progress_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both source and vendor hash streams should preserve non-value events."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/oxlint-tsgolint/updater.py",
        "oxlint_tsgolint_progress_test",
    )
    updater = module.OxlintTsgolintUpdater()

    async def _fixed_hash(_name: str, _expr: str, **_kwargs):
        if _fixed_hash.calls == 0:
            _fixed_hash.calls += 1
            yield module.UpdateEvent.status("oxlint-tsgolint", "hashing source")
            yield module.UpdateEvent.value(
                "oxlint-tsgolint",
                "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            )
            return
        yield module.UpdateEvent.status("oxlint-tsgolint", "hashing vendor")
        yield module.UpdateEvent.value(
            "oxlint-tsgolint",
            "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
        )

    _fixed_hash.calls = 0

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    events = _run(_collect(updater.fetch_hashes(VersionInfo("0.21.0"), object())))

    assert [event.kind.value for event in events] == ["status", "status", "value"]
    assert [event.message for event in events[:-1]] == [
        "hashing source",
        "hashing vendor",
    ]
