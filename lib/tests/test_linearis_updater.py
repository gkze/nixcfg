"""Tests for the linearis updater."""

from __future__ import annotations

from types import ModuleType

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.metadata import DownloadUrlMetadata


def _load_module() -> ModuleType:
    return load_repo_module("packages/linearis/updater.py", "linearis_updater_test")


def test_linearis_fetch_latest_reads_version_and_tarball(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve the latest npm version and published tarball URL."""
    module = _load_module()
    updater = module.LinearisUpdater()

    async def _fetch_json(*_args, **_kwargs):
        return {
            "version": "1.2.3",
            "dist": {
                "tarball": "https://registry.npmjs.org/linearis/-/linearis-1.2.3.tgz"
            },
        }

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    info = _run(updater.fetch_latest(object()))

    assert info == VersionInfo(
        version="1.2.3",
        metadata=DownloadUrlMetadata(
            url="https://registry.npmjs.org/linearis/-/linearis-1.2.3.tgz"
        ),
    )


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (
            {
                "dist": {
                    "tarball": "https://registry.npmjs.org/linearis/-/linearis-1.2.3.tgz"
                }
            },
            "Missing version",
        ),
        (
            {
                "version": "",
                "dist": {
                    "tarball": "https://registry.npmjs.org/linearis/-/linearis-1.2.3.tgz"
                },
            },
            "Missing version",
        ),
        ({"version": "1.2.3"}, r"Missing dist\.tarball"),
        ({"version": "1.2.3", "dist": {"tarball": ""}}, r"Missing dist\.tarball"),
    ],
)
def test_linearis_fetch_latest_rejects_missing_required_npm_fields(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    match: str,
) -> None:
    """Fail cleanly when npm metadata is missing required fields."""
    module = _load_module()
    updater = module.LinearisUpdater()

    async def _fetch_json(*_args, **_kwargs):
        return payload

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    with pytest.raises(RuntimeError, match=match):
        _run(updater.fetch_latest(object()))


def test_linearis_fetch_hashes_emits_single_tarball_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward non-value events and emit one sha256 HashEntry for the tarball."""
    module = _load_module()
    updater = module.LinearisUpdater()
    tarball = "https://registry.npmjs.org/linearis/-/linearis-1.2.3.tgz"
    hash_value = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    async def _compute_url_hashes(name: str, urls: list[str]):
        assert name == "linearis"
        assert urls == [tarball]
        yield UpdateEvent.status(name, "hashing tarball")
        yield UpdateEvent.value(name, {tarball: hash_value})

    monkeypatch.setattr(
        "lib.update.updaters.base.compute_url_hashes", _compute_url_hashes
    )

    events = _run(
        _collect_events(
            updater.fetch_hashes(
                VersionInfo(
                    version="1.2.3",
                    metadata=DownloadUrlMetadata(url=tarball),
                ),
                object(),
            )
        )
    )

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert events[0].message == "hashing tarball"
    assert events[1].payload == [
        HashEntry.create("sha256", hash_value, url=tarball),
    ]


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"tarball": ""},
    ],
)
def test_linearis_fetch_hashes_rejects_missing_tarball_metadata(
    metadata: object,
) -> None:
    """Require tarball metadata before attempting hash computation."""
    module = _load_module()
    updater = module.LinearisUpdater()

    with pytest.raises(RuntimeError, match="Missing tarball metadata"):
        _run(
            _collect_events(
                updater.fetch_hashes(
                    VersionInfo(version="1.2.3", metadata=metadata),
                    object(),
                )
            )
        )
