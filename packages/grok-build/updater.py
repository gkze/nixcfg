"""Updater for stable Grok Build desktop releases."""

from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlsplit

from lib.update.updaters import ElectronBuilderAssetURLsUpdater, register_updater

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


_ARTIFACT_ORIGIN = "storage.googleapis.com"
_ARTIFACT_DIRECTORY = "/grok-build-public-artifacts/desktop/stable"


def _is_stable_arm64_zip(version: str, url: str) -> bool:
    """Accept only xAI's immutable, versioned arm64 stable ZIP."""
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == _ARTIFACT_ORIGIN
        and parsed.path == f"{_ARTIFACT_DIRECTORY}/Grok-{version}-arm64-mac.zip"
        and not parsed.query
        and not parsed.fragment
    )


@register_updater
class GrokBuildUpdater(ElectronBuilderAssetURLsUpdater):
    """Track the official stable Grok Build arm64 ZIP."""

    name = "grok-build"
    FEED_URL: ClassVar[str] = (
        "https://storage.googleapis.com/grok-build-public-artifacts/"
        "desktop/stable/stable-mac.yml"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }
    SELECTORS: ClassVar[Mapping[str, Callable[[str, str], bool]]] = {
        "aarch64-darwin": _is_stable_arm64_zip,
    }
    DOWNLOAD_URL_TEMPLATE: ClassVar[str] = (
        "https://storage.googleapis.com/grok-build-public-artifacts/"
        "desktop/stable/Grok-{version}-arm64-mac.zip"
    )
