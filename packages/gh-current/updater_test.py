"""GitHub CLI source and vendor identity remain one update transaction."""

from unittest.mock import AsyncMock

import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext, VersionInfo, core

MODULE = load_repo_module("packages/gh-current/updater.py", "gh_current_tests")


def test_source_and_vendor_hashes_share_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "b" * 40
    hash_value = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    hashes = [
        HashEntry.create("srcHash", hash_value),
        HashEntry.create("vendorHash", hash_value),
    ]
    stream = AsyncMock(return_value=hashes)
    monkeypatch.setattr(core, "stream_source_then_overlay_hashes", stream)
    updater = MODULE.GhCurrentUpdater()
    info = VersionInfo(
        version="2.103.0", metadata={"commit": commit, "sourceDateEpoch": "1792595308"}
    )
    actual = run_async(
        updater.fetch_hashes(info, None, context=UpdateContext(current=None))
    )
    assert actual == hashes
    assert stream.await_args.args == ("gh-current",)
    assert stream.await_args.kwargs["commit"] == commit
    assert stream.await_args.kwargs["version"] == "2.103.0"
    assert stream.await_args.kwargs["dependency_hash_type"] == "vendorHash"
    result = updater.build_result(info, actual)
    assert result.version == "2.103.0"
    assert result.commit == commit
    assert result.hashes.entries == hashes
    assert result.pins == {"sourceDateEpoch": "1792595308"}
    assert stream.await_args.kwargs["source_pins"] == result.pins
    assert stream.await_args.kwargs["prerequisite_sources"] is None


def test_vendor_probe_reads_this_runs_go_toolchain_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The workspace still holds go-toolchain's emptied pin-stage hashes."""
    stream = AsyncMock(return_value=[])
    monkeypatch.setattr(core, "stream_source_then_overlay_hashes", stream)
    go_toolchain = SourceEntry(
        version="1.27.3",
        hashes=HashCollection.from_value([
            HashEntry.create(
                "sha256",
                "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                url="https://go.dev/dl/go1.27.3.src.tar.gz",
            )
        ]),
    )
    context = UpdateContext(
        current=None,
        effective_sources={"go-toolchain": go_toolchain, "unrelated": go_toolchain},
    )
    info = VersionInfo(
        version="2.103.0",
        metadata={"commit": "b" * 40, "sourceDateEpoch": "1792595308"},
    )

    run_async(MODULE.GhCurrentUpdater().fetch_hashes(info, None, context=context))

    assert stream.await_args.kwargs["prerequisite_sources"] == {
        "go-toolchain": go_toolchain
    }


def test_fetch_latest_pins_exact_commit_time(monkeypatch: pytest.MonkeyPatch) -> None:
    commit = "b" * 40
    latest = AsyncMock(
        return_value=VersionInfo(
            version="2.103.0", metadata={"commit": commit, "tag": "v2.103.0"}
        )
    )
    fetch = AsyncMock(
        return_value={
            "sha": commit,
            "commit": {"committer": {"date": "2026-10-21T15:08:28Z"}},
        }
    )
    monkeypatch.setattr(MODULE.GitHubReleaseUpdater, "fetch_latest", latest)
    monkeypatch.setattr(MODULE, "fetch_github_api", fetch)
    updater = MODULE.GhCurrentUpdater()
    info = run_async(updater.fetch_latest(None, context=UpdateContext(current=None)))
    assert info.version == "2.103.0"
    assert info.metadata == {
        "commit": commit,
        "tag": "v2.103.0",
        "sourceDateEpoch": "1792595308",
    }
    assert fetch.await_args.args == (None, f"repos/cli/cli/commits/{commit}")


@pytest.mark.parametrize("payload", [None, [], {}, {"sha": "c" * 40}])
def test_commit_timestamp_rejects_wrong_identity(payload: object) -> None:
    with pytest.raises(ValueError, match="wrong SHA"):
        MODULE._commit_source_epoch(payload, "b" * 40)


@pytest.mark.parametrize(
    "details",
    [None, {}, {"committer": None}, {"committer": {}}, {"committer": {"date": 123}}],
)
def test_commit_timestamp_requires_committer_date(details: object) -> None:
    with pytest.raises(TypeError, match="missing"):
        MODULE._commit_source_epoch({"sha": "b" * 40, "commit": details}, "b" * 40)


@pytest.mark.parametrize(
    "date",
    [
        "invalid",
        "2026-10-21T15:08:28",
        "2026-10-21T15:08:28+01:00",
        "2026-02-30T15:08:28Z",
        "2026-10-21T15:08:28.5Z",
    ],
)
def test_commit_timestamp_requires_utc_seconds(date: str) -> None:
    with pytest.raises(ValueError, match="UTC seconds"):
        MODULE._commit_source_epoch(
            {"sha": "b" * 40, "commit": {"committer": {"date": date}}}, "b" * 40
        )


@pytest.mark.parametrize("date", ["1970-01-01T00:00:00Z", "1969-12-31T23:59:59Z"])
def test_commit_timestamp_requires_positive_epoch(date: str) -> None:
    with pytest.raises(ValueError, match="positive"):
        MODULE._commit_source_epoch(
            {"sha": "b" * 40, "commit": {"committer": {"date": date}}}, "b" * 40
        )


@pytest.mark.parametrize("value", ["0", "-1", "001", "1.5", "abc", "\u0661"])
def test_source_pin_requires_canonical_positive_seconds(value: str) -> None:
    updater = MODULE.GhCurrentUpdater()
    with pytest.raises(ValueError, match="canonical positive"):
        updater.source_pins_for(
            VersionInfo(version="2.103.0", metadata={"sourceDateEpoch": value})
        )


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"sourceDateEpoch": None},
        {"sourceDateEpoch": 1},
        {"sourceDateEpoch": ""},
    ],
)
def test_source_pin_requires_timestamp_metadata(metadata: object) -> None:
    with pytest.raises(TypeError, match="sourceDateEpoch"):
        MODULE.GhCurrentUpdater().source_pins_for(
            VersionInfo(version="2.103.0", metadata=metadata)
        )


def test_missing_source_commit_stops_timestamp_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = AsyncMock(return_value=VersionInfo(version="2.103.0", metadata={}))
    fetch = AsyncMock()
    monkeypatch.setattr(MODULE.GitHubReleaseUpdater, "fetch_latest", latest)
    monkeypatch.setattr(MODULE, "fetch_github_api", fetch)
    with pytest.raises(RuntimeError, match="immutable source commit"):
        run_async(
            MODULE.GhCurrentUpdater().fetch_latest(
                None, context=UpdateContext(current=None)
            )
        )
    fetch.assert_not_awaited()
