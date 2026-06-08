"""Updater for the pinned Tolaria macOS app archive."""

from __future__ import annotations

from lib.update.updaters.base import pinned_source_download_updater


def _download_url(version: str, _platform: str, platform_value: str) -> str:
    year, month, day = version.split(".", maxsplit=2)
    tag = f"v{year}-{int(month):02d}-{int(day):02d}"
    return (
        f"https://github.com/refactoringhq/tolaria/releases/download/{tag}/"
        f"Tolaria_{version}_macOS_{platform_value}.app.tar.gz"
    )


TolariaUpdater = pinned_source_download_updater(
    "tolaria",
    platforms={"aarch64-darwin": "Silicon"},
    download_url=_download_url,
    module=__name__,
)
