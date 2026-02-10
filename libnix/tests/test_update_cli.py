"""Unit tests for source-update merge behavior in the update CLI."""

from libnix.models.sources import HashCollection, HashEntry, SourceEntry
from update.cli import _merge_source_updates


def _entry_with_hashes(*entries: HashEntry) -> SourceEntry:
    return SourceEntry(hashes=HashCollection(entries=list(entries)))


def test_merge_source_updates_native_only_preserves_other_platform_hashes() -> None:
    """Merge native updates while preserving non-native existing hashes."""
    existing = {
        "opencode": _entry_with_hashes(
            HashEntry.create(
                hash_type="nodeModulesHash",
                hash_value="sha256-JnkqDwuC7lNsjafV+jOGfvs8K1xC8rk5CTOW+spjiCA=",
                platform="aarch64-darwin",
            ),
            HashEntry.create(
                hash_type="nodeModulesHash",
                hash_value="sha256-cvRBvHRuunNjF07c4GVHl5rRgoTn1qfI/HdJWtOV63M=",
                platform="x86_64-linux",
            ),
        ),
    }
    updates = {
        "opencode": _entry_with_hashes(
            HashEntry.create(
                hash_type="nodeModulesHash",
                hash_value="sha256-DJUI4pMZ7wQTnyOiuDHALmZz7FZtrTbzRzCuNOShmWE=",
                platform="aarch64-darwin",
            ),
        ),
    }

    merged = _merge_source_updates(existing, updates, native_only=True)

    result_entries = merged["opencode"].hashes.entries
    assert result_entries is not None  # noqa: S101
    values_by_platform = {entry.platform: entry.hash for entry in result_entries}
    assert values_by_platform == {  # noqa: S101
        "aarch64-darwin": "sha256-DJUI4pMZ7wQTnyOiuDHALmZz7FZtrTbzRzCuNOShmWE=",
        "x86_64-linux": "sha256-cvRBvHRuunNjF07c4GVHl5rRgoTn1qfI/HdJWtOV63M=",
    }


def test_merge_source_updates_non_native_returns_updates_unchanged() -> None:
    """Return updates unchanged when native-only merge mode is disabled."""
    updates = {
        "demo": _entry_with_hashes(
            HashEntry.create(
                hash_type="sha256",
                hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
            ),
        ),
    }

    merged = _merge_source_updates({}, updates, native_only=False)

    assert merged is updates  # noqa: S101
