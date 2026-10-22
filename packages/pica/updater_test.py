"""Contracts for Pica's independently changing short version and build."""

import pytest

from lib.tests._updater_helpers import load_repo_module
from lib.update.updaters.vendor_feeds import SparkleAppcastItem


def test_pica_preserves_the_build_in_the_version_identity() -> None:
    """A build-only release must not appear current because its short version matches."""
    module = load_repo_module("packages/pica/updater.py", "pica_updater_test")
    updater = module.PicaUpdater()
    assert (
        updater.item_version(
            SparkleAppcastItem("15", "1.9", "https://example.invalid/app.dmg")
        )
        == "1.9.15"
    )
    assert (
        updater.item_version(
            SparkleAppcastItem("16", "1.9", "https://example.invalid/app.dmg")
        )
        == "1.9.16"
    )
    with pytest.raises(RuntimeError, match="Missing version"):
        updater.item_version(
            SparkleAppcastItem(None, "1.9", "https://example.invalid/app.dmg")
        )
