"""Updater registry and built-in definitions.

Imports are explicit so registration order and side effects remain easy to audit.
"""

from update.updaters.base import UPDATERS, Updater

from . import (  # noqa: F401
    chatgpt,
    chrome,
    conductor,
    cursor,
    datagrip,
    droid,
    github_raw_file,
    opencode_desktop,
    registry,
    sculptor,
    sentry_cli,
    vscode_insiders,
)

__all__ = ["UPDATERS", "Updater"]
