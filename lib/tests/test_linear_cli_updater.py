"""Tests for the linear-cli updater."""

from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace

import pytest

from lib.import_utils import load_module_from_path
from lib.nix.models.sources import HashEntry, SourceEntry
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo


def _run[T](coro):
    return asyncio.run(coro)


async def _collect_events(stream):
    return [event async for event in stream]


def _load_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/linear-cli/updater.py",
        "linear_cli_updater_test",
    )


def _current_source_entry(*entries: HashEntry) -> SourceEntry:
    return SourceEntry.model_validate({
        "version": "1.2.3",
        "hashes": [entry.to_dict() for entry in entries],
    })


def test_linear_cli_resolve_deno_version_returns_trimmed_eval_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read the current Deno version from nix eval output."""
    module = _load_module()
    updater = module.LinearCliUpdater()

    monkeypatch.setattr(module, "get_current_nix_platform", lambda: "aarch64-darwin")

    async def _run_nix(args: list[str], *, check: bool):
        assert args == [
            "nix",
            "eval",
            "--impure",
            "--raw",
            "--expr",
            updater._deno_version_expr("aarch64-darwin"),
        ]
        assert check is False
        return SimpleNamespace(returncode=0, stdout="2.3.4\n", stderr="")

    monkeypatch.setattr(module, "run_nix", _run_nix)

    assert _run(updater._resolve_deno_version()) == "2.3.4"


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (
            SimpleNamespace(returncode=1, stdout="", stderr="deno lookup failed"),
            "Failed to evaluate deno.version for linear-cli: deno lookup failed",
        ),
        (
            SimpleNamespace(returncode=0, stdout="\n", stderr=""),
            "Failed to evaluate deno.version for linear-cli: nix eval failed",
        ),
    ],
)
def test_linear_cli_resolve_deno_version_rejects_failed_or_empty_eval(
    monkeypatch: pytest.MonkeyPatch,
    result: SimpleNamespace,
    match: str,
) -> None:
    """Raise a useful error when nix eval fails or returns no version."""
    module = _load_module()
    updater = module.LinearCliUpdater()

    monkeypatch.setattr(module, "get_current_nix_platform", lambda: "x86_64-linux")

    async def _run_nix(_args: list[str], *, check: bool):
        assert check is False
        return result

    monkeypatch.setattr(module, "run_nix", _run_nix)

    with pytest.raises(RuntimeError, match=match):
        _run(updater._resolve_deno_version())


def test_linear_cli_is_latest_requires_superclass_match(monkeypatch) -> None:
    """Skip URL comparison when the base version check already failed."""
    module = _load_module()
    updater = module.LinearCliUpdater()

    async def _not_latest(self, context, info):
        _ = (self, context, info)
        return False

    monkeypatch.setattr(module.DenoManifestUpdater, "_is_latest", _not_latest)

    assert (
        _run(
            updater._is_latest(
                None,
                VersionInfo(version="1.2.3"),
            )
        )
        is False
    )


def test_linear_cli_is_latest_compares_expected_denort_urls(monkeypatch) -> None:
    """Treat the source as current only when persisted denort URLs still match."""
    module = _load_module()
    updater = module.LinearCliUpdater()

    async def _latest(self, context, info):
        _ = (self, context, info)
        return True

    async def _resolve_deno_version() -> str:
        return "2.3.4"

    monkeypatch.setattr(module.DenoManifestUpdater, "_is_latest", _latest)
    monkeypatch.setattr(updater, "_resolve_deno_version", _resolve_deno_version)

    matching = _current_source_entry(
        *[
            HashEntry.create(
                "sha256",
                "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                platform=platform,
                url=updater._denort_url(target, "2.3.4"),
            )
            for platform, target in updater.PLATFORMS.items()
        ],
        HashEntry.create(
            "srcHash",
            "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
        ),
    )
    mismatched = _current_source_entry(
        HashEntry.create(
            "sha256",
            "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
            platform="aarch64-darwin",
            url="https://example.invalid/wrong.zip",
        )
    )

    assert _run(updater._is_latest(matching, VersionInfo(version="1.2.3"))) is True
    assert _run(updater._is_latest(mismatched, VersionInfo(version="1.2.3"))) is False


def test_linear_cli_is_latest_rejects_missing_current_hash_entries(monkeypatch) -> None:
    """Require persisted per-platform hash entries before comparing URLs."""
    module = _load_module()
    updater = module.LinearCliUpdater()

    async def _latest(self, context, info):
        _ = (self, context, info)
        return True

    async def _resolve_deno_version() -> str:
        raise AssertionError("should not resolve deno version")

    monkeypatch.setattr(module.DenoManifestUpdater, "_is_latest", _latest)
    monkeypatch.setattr(updater, "_resolve_deno_version", _resolve_deno_version)

    current = SourceEntry.model_validate({
        "version": "1.2.3",
        "hashes": {
            "x86_64-linux": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        },
    })

    assert _run(updater._is_latest(current, VersionInfo(version="1.2.3"))) is False


def test_linear_cli_fetch_hashes_forwards_events_and_emits_sorted_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward superclass/hash events and emit one sha256 entry per platform."""
    module = _load_module()
    updater = module.LinearCliUpdater()

    async def _manifest_fetch(self, info, session, *, context=None):
        _ = (self, info, session, context)
        yield UpdateEvent.status("linear-cli", "manifest ready")
        yield UpdateEvent.value("linear-cli", [])

    async def _resolve_deno_version() -> str:
        return "2.3.4"

    async def _compute_url_hashes(name: str, urls) -> object:
        url_list = list(urls)
        assert name == "linear-cli"
        assert url_list == [
            updater._denort_url(target, "2.3.4")
            for target in updater.PLATFORMS.values()
        ]
        yield UpdateEvent.status(name, "hashing denort")
        yield UpdateEvent.value(
            name,
            {
                url: f"sha256-{index:0<43}="
                for index, url in enumerate(url_list, start=1)
            },
        )

    monkeypatch.setattr(module.DenoManifestUpdater, "fetch_hashes", _manifest_fetch)
    monkeypatch.setattr(updater, "_resolve_deno_version", _resolve_deno_version)
    monkeypatch.setattr(module, "compute_url_hashes", _compute_url_hashes)

    events = _run(
        _collect_events(updater.fetch_hashes(VersionInfo(version="1.2.3"), object()))
    )

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert [event.message for event in events[:-1]] == [
        "manifest ready",
        "Fetching denort runtime hashes for Deno v2.3.4...",
        "hashing denort",
    ]
    assert [entry.platform for entry in events[-1].payload] == sorted(updater.PLATFORMS)
    assert all(entry.hash_type == "sha256" for entry in events[-1].payload)


def test_linear_cli_fetch_hashes_requires_manifest_value(monkeypatch) -> None:
    """Raise when the superclass manifest stream never yields a VALUE event."""
    module = _load_module()
    updater = module.LinearCliUpdater()

    async def _manifest_fetch(self, info, session, *, context=None):
        _ = (self, info, session, context)
        yield UpdateEvent.status("linear-cli", "manifest pending")

    monkeypatch.setattr(module.DenoManifestUpdater, "fetch_hashes", _manifest_fetch)

    with pytest.raises(RuntimeError, match="Missing deno manifest output"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.2.3"), object())
            )
        )


def test_linear_cli_fetch_hashes_requires_denort_hash_mapping(monkeypatch) -> None:
    """Raise when denort hash computation emits no VALUE mapping."""
    module = _load_module()
    updater = module.LinearCliUpdater()

    async def _manifest_fetch(self, info, session, *, context=None):
        _ = (self, info, session, context)
        yield UpdateEvent.value("linear-cli", [])

    async def _resolve_deno_version() -> str:
        return "2.3.4"

    async def _compute_url_hashes(name: str, urls) -> object:
        _ = (name, list(urls))
        yield UpdateEvent.status("linear-cli", "hashing denort")

    monkeypatch.setattr(module.DenoManifestUpdater, "fetch_hashes", _manifest_fetch)
    monkeypatch.setattr(updater, "_resolve_deno_version", _resolve_deno_version)
    monkeypatch.setattr(module, "compute_url_hashes", _compute_url_hashes)

    with pytest.raises(RuntimeError, match="Missing denort hash output"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.2.3"), object())
            )
        )


def test_linear_cli_fetch_hashes_rejects_non_mapping_hash_payload(monkeypatch) -> None:
    """Surface parse failures when the denort hash stream yields the wrong type."""
    module = _load_module()
    updater = module.LinearCliUpdater()

    async def _manifest_fetch(self, info, session, *, context=None):
        _ = (self, info, session, context)
        yield UpdateEvent.value("linear-cli", [])

    async def _resolve_deno_version() -> str:
        return "2.3.4"

    async def _compute_url_hashes(name: str, urls) -> object:
        _ = (name, list(urls))
        yield UpdateEvent.value("linear-cli", "not-a-mapping")

    monkeypatch.setattr(module.DenoManifestUpdater, "fetch_hashes", _manifest_fetch)
    monkeypatch.setattr(updater, "_resolve_deno_version", _resolve_deno_version)
    monkeypatch.setattr(module, "compute_url_hashes", _compute_url_hashes)

    with pytest.raises(TypeError, match="Expected hash mapping payload"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.2.3"), object())
            )
        )
