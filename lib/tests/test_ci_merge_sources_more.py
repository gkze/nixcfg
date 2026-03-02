"""Additional tests for merge-sources helper internals."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import check
from lib.update.ci import merge_sources as ms

if TYPE_CHECKING:
    import pytest


def _entry(*, version: str, hashes: list[dict[str, str]]) -> SourceEntry:
    return SourceEntry.model_validate({"version": version, "hashes": hashes})


def test_parse_root_spec_and_platform_inference() -> None:
    """Parse explicit and inferred root/platform specs."""
    check(
        ms._infer_platform_from_root_path(Path("sources-aarch64-darwin"))
        == "aarch64-darwin"
    )
    check(ms._infer_platform_from_root_path(Path("artifacts")) is None)

    platform, root = ms._parse_root_spec("x86_64-linux=/tmp/sources")
    check(platform == "x86_64-linux")
    check(root == Path("/tmp/sources"))

    inferred_platform, inferred_root = ms._parse_root_spec("sources-aarch64-linux")
    check(inferred_platform == "aarch64-linux")
    check(inferred_root == Path("sources-aarch64-linux"))


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
    check(len(merged) == 1)
    check(merged[0].platform == "aarch64-darwin")

    merged_without_platform = ms._merge_hash_entries(base, incoming, platform=None)
    check(any(item.platform == "x86_64-linux" for item in merged_without_platform))

    try:
        ms._merge_hash_entries(
            [HashEntry(hash_type="sha256", hash="sha256-old")],
            [HashEntry(hash_type="sha256", hash="sha256-new")],
            platform=None,
        )
    except RuntimeError as exc:
        check("Conflicting non-platform hash entry" in str(exc))
    else:
        raise AssertionError("expected RuntimeError")

    preserved = ms._merge_hash_entries(
        [HashEntry(hash_type="sha256", hash="sha256-same")],
        [HashEntry(hash_type="sha256", hash="sha256-same")],
        platform=None,
    )
    check(len(preserved) == 1)


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
    check(merged == {"aarch64-darwin": "sha256-updated"})

    merged_all = ms._merge_hash_mapping(
        {"aarch64-darwin": "sha256-a"},
        {"x86_64-linux": "sha256-b"},
        platform=None,
    )
    check(merged_all == {"aarch64-darwin": "sha256-a", "x86_64-linux": "sha256-b"})

    try:
        ms._merge_hash_mapping(
            {"x86_64-linux": "sha256-old"},
            {"x86_64-linux": "sha256-new"},
            platform=None,
        )
    except RuntimeError as exc:
        check("Conflicting non-platform hash mapping" in str(exc))
    else:
        raise AssertionError("expected RuntimeError")

    filtered = ms._merge_hash_mapping(
        {"aarch64-darwin": "sha256-a"},
        {"x86_64-linux": "sha256-b"},
        platform="aarch64-darwin",
    )
    check(filtered == {"aarch64-darwin": "sha256-a"})


def test_merge_optional_scalar_and_urls_conflicts() -> None:
    """Reject conflicting scalar and URL fields."""
    check(ms._merge_optional_scalar("version", "1.0.0", None) == "1.0.0")
    check(ms._merge_urls(None, None) is None)
    check(
        ms._merge_urls({"linux": "a"}, {"darwin": "b"}) == {"linux": "a", "darwin": "b"}
    )

    try:
        ms._merge_optional_scalar("version", "1.0.0", "2.0.0")
    except RuntimeError as exc:
        check("Conflicting version" in str(exc))
    else:
        raise AssertionError("expected RuntimeError")

    try:
        ms._merge_urls({"linux": "a"}, {"linux": "b"})
    except RuntimeError as exc:
        check("Conflicting urls entry" in str(exc))
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
        check("no output destination" in str(exc))
        check("demo" in str(exc))
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
    check(merged.hashes.mapping is not None)
    check(merged.hashes.mapping["x86_64-linux"] == "sha256-a")
    check(merged.hashes.mapping["aarch64-darwin"] == "sha256-b")


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
        check("Cannot merge hash mapping with hash entries" in str(exc))
    else:
        raise AssertionError("expected ValueError")


def test_collect_and_run_exit_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cover run() early return and validation failure paths."""
    monkeypatch.setattr(
        ms,
        "_collect_merged_entries",
        lambda _roots: ({}, 0, [], []),
    )
    check(ms.run(roots=["x"], output_root=tmp_path) == 1)

    monkeypatch.setattr(
        ms,
        "_collect_merged_entries",
        lambda _roots: ({}, 1, ["missing"], ["empty"]),
    )
    try:
        ms.run(roots=["x"], output_root=tmp_path)
    except RuntimeError as exc:
        check("Invalid merge input roots" in str(exc))
    else:
        raise AssertionError("expected RuntimeError")


def test_validate_input_roots_individual_lists() -> None:
    """Validate message construction for missing-only and empty-only roots."""
    try:
        ms._validate_input_roots(["missing-a"], [])
    except RuntimeError as exc:
        check("missing roots" in str(exc))
    else:
        raise AssertionError("expected RuntimeError")

    try:
        ms._validate_input_roots([], ["empty-a"])
    except RuntimeError as exc:
        check("roots with no sources.json files" in str(exc))
    else:
        raise AssertionError("expected RuntimeError")
