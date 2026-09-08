"""Capy release selection must keep architectures and archive formats distinct."""

import pytest

from lib.tests._updater_helpers import load_repo_module
from lib.update.updaters import VersionInfo


@pytest.mark.parametrize(
    ("platform", "architecture"),
    [("aarch64-darwin", "arm64"), ("x86_64-darwin", "x64")],
)
def test_capy_selects_only_the_matching_release_zip(
    platform: str,
    architecture: str,
) -> None:
    """A mixed feed must not choose a DMG, stale release, or other architecture."""
    updater = load_repo_module(
        "packages/capy/updater.py",
        "capy_updater_test_module",
    ).CapyUpdater()
    version = "0.2.0"
    selector = updater.SELECTORS[platform]
    expected = f"https://d1lfowv2t69uz0.cloudfront.net/stable/Capy-{version}-{architecture}.zip"
    candidates = [
        f"https://d1lfowv2t69uz0.cloudfront.net/stable/Capy-{release}-{arch}.{extension}"
        for release in ("0.1.0", version)
        for arch in ("arm64", "x64")
        for extension in ("zip", "dmg")
    ]

    assert [url for url in candidates if selector(version, url)] == [expected]
    assert updater.get_download_url(platform, VersionInfo(version)) == expected
