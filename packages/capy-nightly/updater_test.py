"""Capy Nightly selection must preserve channel, architecture, and URL encoding."""

import pytest

from lib.tests._updater_helpers import load_repo_module
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata


@pytest.mark.parametrize(
    ("platform", "architecture"),
    [("aarch64-darwin", "arm64"), ("x86_64-darwin", "x64")],
)
@pytest.mark.parametrize("separator", [" ", "%20"])
def test_capy_nightly_selects_and_encodes_release_zip(
    platform: str,
    architecture: str,
    separator: str,
) -> None:
    """Reject stable apps, old releases, DMGs, and the other architecture."""
    updater = load_repo_module(
        "packages/capy-nightly/updater.py",
        "capy_nightly_updater_test_module",
    ).CapyNightlyUpdater()
    version = "0.2.1-nightly.20260915.155"
    base_url = "https://d1lfowv2t69uz0.cloudfront.net/nightly"
    expected = f"{base_url}/Capy%20Nightly-{version}-{architecture}.zip"
    selected = f"{base_url}/Capy{separator}Nightly-{version}-{architecture}.zip"
    candidates = [
        f"{base_url}/{app}-{release}-{arch}.{extension}"
        for app in ("Capy", f"Capy{separator}Nightly")
        for release in ("0.2.1-nightly.20260914.150", version)
        for arch in ("arm64", "x64")
        for extension in ("zip", "dmg")
    ]

    assert [url for url in candidates if updater.SELECTORS[platform](version, url)] == [
        selected
    ]
    assert updater.get_download_url(platform, VersionInfo(version)) == expected
    info = VersionInfo(
        version,
        metadata=AssetURLsMetadata({platform: selected}),
    )
    assert updater.get_download_url(platform, info) == expected
    assert (
        updater.build_result(info, {platform: "sha256-test"}).urls[platform] == expected
    )
