"""Additional tests for merge-sources helper internals."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.ci import merge_sources as ms

if TYPE_CHECKING:
    import pytest


def _entry(*, version: str, hashes: list[dict[str, str]]) -> SourceEntry:
    return SourceEntry.model_validate({"version": version, "hashes": hashes})


def test_parse_root_spec_and_platform_inference() -> None:
    """Parse explicit and inferred root/platform specs."""
    assert (
        ms._infer_platform_from_root_path(Path("sources-aarch64-darwin"))
        == "aarch64-darwin"
    )
    assert ms._infer_platform_from_root_path(Path("artifacts")) is None

    platform, root = ms._parse_root_spec("x86_64-linux=/tmp/sources")
    assert platform == "x86_64-linux"
    assert root == Path("/tmp/sources")

    inferred_platform, inferred_root = ms._parse_root_spec("sources-aarch64-linux")
    assert inferred_platform == "aarch64-linux"
    assert inferred_root == Path("sources-aarch64-linux")


def test_merge_hash_entries_platform_filter_and_conflict() -> None:
    """Filter platform entries and reject conflicting global entries."""
    base = [
        HashEntry(
            hash_type="denoDepsHash",
            hash="sha256-a",
            platform="aarch64-darwin",
        )
    ]
    incoming = [
        HashEntry(
            hash_type="denoDepsHash",
            hash="sha256-b",
            platform="x86_64-linux",
        ),
        HashEntry(
            hash_type="denoDepsHash",
            hash=HashCollection.FAKE_HASH_PREFIX,
            platform="aarch64-darwin",
        ),
    ]

    merged = ms._merge_hash_entries(base, incoming, platform="aarch64-darwin")
    assert len(merged) == 1
    assert merged[0].platform == "aarch64-darwin"

    merged_without_platform = ms._merge_hash_entries(base, incoming, platform=None)
    assert any(item.platform == "x86_64-linux" for item in merged_without_platform)

    try:
        ms._merge_hash_entries(
            [HashEntry(hash_type="sha256", hash="sha256-old")],
            [HashEntry(hash_type="sha256", hash="sha256-new")],
            platform=None,
        )
    except RuntimeError as exc:
        assert "Conflicting non-platform hash entry" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    preserved = ms._merge_hash_entries(
        [HashEntry(hash_type="sha256", hash="sha256-same")],
        [HashEntry(hash_type="sha256", hash="sha256-same")],
        platform=None,
    )
    assert len(preserved) == 1

    baseline = [HashEntry(hash_type="sha256", hash="sha256-old")]
    preferred_incoming = ms._merge_hash_entries(
        [HashEntry(hash_type="sha256", hash="sha256-old")],
        [HashEntry(hash_type="sha256", hash="sha256-new")],
        platform=None,
        baseline=baseline,
    )
    assert preferred_incoming == [HashEntry(hash_type="sha256", hash="sha256-new")]

    preferred_existing = ms._merge_hash_entries(
        [HashEntry(hash_type="sha256", hash="sha256-new")],
        [HashEntry(hash_type="sha256", hash="sha256-old")],
        platform=None,
        baseline=baseline,
    )
    assert preferred_existing == [HashEntry(hash_type="sha256", hash="sha256-new")]


def test_merge_hash_mapping_filters_and_conflicts() -> None:
    """Merge hash mapping with platform filtering and conflict detection."""
    merged = ms._merge_hash_mapping(
        {"aarch64-darwin": "sha256-a"},
        {
            "aarch64-darwin": "sha256-updated",
            "x86_64-linux": f"{HashCollection.FAKE_HASH_PREFIX}skip",
        },
        platform="aarch64-darwin",
    )
    assert merged == {"aarch64-darwin": "sha256-updated"}

    merged_all = ms._merge_hash_mapping(
        {"aarch64-darwin": "sha256-a"},
        {"x86_64-linux": "sha256-b"},
        platform=None,
    )
    assert merged_all == {"aarch64-darwin": "sha256-a", "x86_64-linux": "sha256-b"}

    try:
        ms._merge_hash_mapping(
            {"x86_64-linux": "sha256-old"},
            {"x86_64-linux": "sha256-new"},
            platform=None,
        )
    except RuntimeError as exc:
        assert "Conflicting non-platform hash mapping" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    filtered = ms._merge_hash_mapping(
        {"aarch64-darwin": "sha256-a"},
        {"x86_64-linux": "sha256-b"},
        platform="aarch64-darwin",
    )
    assert filtered == {"aarch64-darwin": "sha256-a"}

    preferred_mapping = ms._merge_hash_mapping(
        {"shared": "sha256-old"},
        {"shared": "sha256-new"},
        platform=None,
        baseline={"shared": "sha256-old"},
    )
    assert preferred_mapping == {"shared": "sha256-new"}


def test_merge_optional_scalar_prefers_changed_value_over_baseline() -> None:
    """Prefer the changed scalar when the other side still matches baseline."""
    assert (
        ms._merge_optional_scalar(
            "version",
            "1.0.0",
            "2.0.0",
            baseline="1.0.0",
        )
        == "2.0.0"
    )
    assert (
        ms._merge_optional_scalar(
            "version",
            "2.0.0",
            "1.0.0",
            baseline="1.0.0",
        )
        == "2.0.0"
    )


def test_merge_urls_prefers_changed_value_over_baseline() -> None:
    """Prefer updated URLs when the other root still matches the baseline."""
    merged = ms._merge_urls(
        {"aarch64-darwin": "https://example.invalid/old"},
        {"aarch64-darwin": "https://example.invalid/new"},
        baseline={"aarch64-darwin": "https://example.invalid/old"},
    )
    assert merged == {"aarch64-darwin": "https://example.invalid/new"}

    preserved = ms._merge_urls(
        {"aarch64-darwin": "https://example.invalid/new"},
        {"aarch64-darwin": "https://example.invalid/old"},
        baseline={"aarch64-darwin": "https://example.invalid/old"},
    )
    assert preserved == {"aarch64-darwin": "https://example.invalid/new"}


def test_merge_optional_scalar_and_urls_conflicts() -> None:
    """Reject conflicting scalar and URL fields."""
    assert ms._merge_optional_scalar("version", "1.0.0", None) == "1.0.0"
    assert ms._merge_urls(None, None) is None
    assert ms._merge_urls({"linux": "a"}, {"darwin": "b"}) == {
        "linux": "a",
        "darwin": "b",
    }

    try:
        ms._merge_optional_scalar("version", "1.0.0", "2.0.0")
    except RuntimeError as exc:
        assert "Conflicting version" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    try:
        ms._merge_urls({"linux": "a"}, {"linux": "b"})
    except RuntimeError as exc:
        assert "Conflicting urls entry" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_write_merged_entries_reports_missing_destinations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Error when merged package has no output destination."""
    merged = {
        "demo": _entry(
            version="1.0.0",
            hashes=[{"hashType": "sha256", "hash": "sha256-a"}],
        )
    }

    monkeypatch.setattr(ms, "package_file_map_in", lambda *_a: {})
    monkeypatch.setattr(ms, "package_dir_for_in", lambda *_a: None)

    try:
        ms._write_merged_entries(tmp_path, merged)
    except RuntimeError as exc:
        assert "no output destination" in str(exc)
        assert "demo" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_merge_entry_hash_mapping_branch() -> None:
    """Merge entries through mapping-only hash path."""
    existing = SourceEntry.model_validate({
        "version": "1.0.0",
        "hashes": {
            "x86_64-linux": "sha256-a",
        },
    })
    incoming = SourceEntry.model_validate({
        "version": "1.0.0",
        "hashes": {
            "aarch64-darwin": "sha256-b",
        },
    })

    merged = ms._merge_entry(existing, incoming, platform=None)
    assert merged.hashes.mapping is not None
    assert merged.hashes.mapping["x86_64-linux"] == "sha256-a"
    assert merged.hashes.mapping["aarch64-darwin"] == "sha256-b"


def test_merge_entry_fallback_to_hash_collection_merge() -> None:
    """Use fallback HashCollection.merge path when hash storage differs."""
    existing = SourceEntry.model_validate({
        "version": "1.0.0",
        "hashes": {"x86_64-linux": "sha256-a"},
    })
    incoming = SourceEntry.model_validate({
        "version": "1.0.0",
        "hashes": [{"hashType": "sha256", "hash": "sha256-b"}],
    })
    try:
        ms._merge_entry(existing, incoming, platform=None)
    except ValueError as exc:
        assert "Cannot merge hash mapping with hash entries" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_collect_and_run_exit_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cover run() early return and validation failure paths."""
    monkeypatch.setattr(
        ms,
        "_collect_merged_entries",
        lambda _roots, *, baseline=None: ({}, 0, [], []),
    )
    assert ms.run(roots=["x"], output_root=tmp_path) == 1

    monkeypatch.setattr(
        ms,
        "_collect_merged_entries",
        lambda _roots, *, baseline=None: ({}, 1, ["missing"], ["empty"]),
    )
    try:
        ms.run(roots=["x"], output_root=tmp_path)
    except RuntimeError as exc:
        assert "Invalid merge input roots" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_validate_input_roots_individual_lists() -> None:
    """Validate message construction for missing-only and empty-only roots."""
    try:
        ms._validate_input_roots(["missing-a"], [])
    except RuntimeError as exc:
        assert "missing roots" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    try:
        ms._validate_input_roots([], ["empty-a"])
    except RuntimeError as exc:
        assert "roots with no sources.json files" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
