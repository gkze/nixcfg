"""Updater for the pinned Superconductor nightly macOS app archive."""

from __future__ import annotations

from lib.update.updaters.base import pinned_source_download_updater


def _download_url(version: str, _platform: str, platform_value: str) -> str:
    commit = version.rsplit("-", maxsplit=1)[-1]
    return (
        "https://releases.superconductor.so/nightly/"
        f"Superconductor-nightly-{commit}-{platform_value}.dmg"
    )


SuperconductorUpdater = pinned_source_download_updater(
    "superconductor",
    platforms={"aarch64-darwin": "arm64"},
    download_url=_download_url,
    module=__name__,
)
