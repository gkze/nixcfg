"""Semantic assertions for updater-owned ``sources.json`` metadata."""

import base64
import re
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry

_RELEASE_VERSION = re.compile(
    r"^[0-9]+(?:\.[0-9]+)*"
    r"(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?$"
)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def assert_release_version(version: str | None) -> str:
    """Require an exact, non-prefixed release version without freezing its value."""
    assert version is not None
    assert _RELEASE_VERSION.fullmatch(version) is not None
    return version


def assert_immutable_commit(commit: str | None) -> str:
    """Require a lowercase immutable Git commit without freezing its value."""
    assert commit is not None
    assert _COMMIT.fullmatch(commit) is not None
    return commit


def assert_sha256_sri(value: str) -> bytes:
    """Require a valid SHA-256 SRI value with a 32-byte digest."""
    algorithm, separator, encoded = value.partition("-")
    assert (algorithm, separator) == ("sha256", "-")
    padded = encoded + "=" * (-len(encoded) % 4)
    digest = base64.b64decode(padded, validate=True)
    assert len(digest) == 32
    return digest


def assert_https_url(url: str, *, host: str | None = None) -> None:
    """Require an HTTPS artifact URL, optionally from one authoritative host."""
    parsed = urlsplit(url)
    assert parsed.scheme == "https"
    assert parsed.username is None
    assert parsed.password is None
    if host is not None:
        assert parsed.hostname == host


def assert_url_contains_version(url: str, version: str) -> None:
    """Require the immutable artifact path to carry its selected release version."""
    assert version in unquote(urlsplit(url).path)


def assert_platform_source_entry(
    source: SourceEntry,
    *,
    platforms: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Require complete, aligned platform hash and URL mappings."""
    hashes = source.hashes.mapping
    urls = source.urls
    assert hashes is not None
    assert urls is not None
    assert set(hashes) == platforms
    assert set(urls) == platforms
    for value in hashes.values():
        assert_sha256_sri(value)
    for url in urls.values():
        assert_https_url(url)
    return hashes, urls


def assert_structured_source_hashes(
    source: SourceEntry,
    *,
    hash_types: set[str],
) -> None:
    """Require one complete structured hash entry for each expected closure."""
    entries = source.hashes.entries
    assert entries is not None
    assert len(entries) == len(hash_types)
    assert {entry.hash_type for entry in entries} == hash_types
    for entry in entries:
        assert_sha256_sri(entry.hash)
