"""Track Slack's official stable Apple Silicon desktop release."""

from typing import ClassVar

from lib.update.updaters import JsonFieldDownloadUpdater, register_updater


@register_updater
class SlackUpdater(JsonFieldDownloadUpdater):
    """Resolve the same public release API used by the nixpkgs updater."""

    name = "slack"
    JSON_URL = "https://slack.com/api/desktop.latestRelease?arch=arm64&variant=dmg"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    DOWNLOAD_URL_TEMPLATE = (
        "https://downloads.slack-edge.com/desktop-releases/mac/{platform_value}/"
        "{version}/Slack-{version}-macOS.dmg"
    )
