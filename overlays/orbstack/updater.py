"""Updater for the pinned OrbStack macOS app archives."""

from __future__ import annotations

from lib.update.updaters.base import pinned_source_download_updater


def _download_url(version: str, _platform: str, platform_value: str) -> str:
    release, build = version.split("-", maxsplit=1)
    return (
        f"https://cdn-updates.orbstack.dev/{platform_value}/"
        f"OrbStack_v{release}_{build}_{platform_value}.dmg"
    )


OrbStackUpdater = pinned_source_download_updater(
    "orbstack",
    platforms={
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "amd64",
    },
    download_url=_download_url,
    module=__name__,
)
