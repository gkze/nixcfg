"""Regression checks for Twilight AutoConfig glue used by the Zen theme."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from lib.update.paths import REPO_ROOT


def test_twilight_autoconfig_bootstrap_points_at_repo_managed_cfg() -> None:
    """The app-bundle prefs shim should load the repo-managed Twilight config."""
    actual = Path(REPO_ROOT / "home/george/zen/autoconfig/autoconfig.js").read_text(
        encoding="utf-8"
    )

    assert (
        actual
        == dedent(
            """
        // Managed by nixcfg nixcfg.zen.
        pref("general.config.filename", "twilight.cfg");
        pref("general.config.obscure_value", 0);
        pref("general.config.sandbox_enabled", false);
        """
        ).lstrip()
    )


def test_twilight_cfg_targets_common_dialog_accept_button_inside_shadow_root() -> None:
    """The Twilight AutoConfig script should patch the internal accept button."""
    actual = Path(REPO_ROOT / "home/george/zen/autoconfig/twilight.cfg").read_text(
        encoding="utf-8"
    )

    assert "chrome://global/content/commonDialog.xhtml" in actual
    assert 'button[dlgtype="accept"]' in actual
    assert "const shadowRoot = dialog && dialog.shadowRoot;" in actual
    assert "shadowRoot.appendChild(style)" in actual
    assert 'acceptButton.style.removeProperty("background");' in actual
    assert 'acceptButton.style.removeProperty("background-color");' in actual
    assert 'acceptButton.style.removeProperty("border");' in actual
    assert (
        'Services.obs.addObserver(observer, "chrome-document-global-created");'
        in actual
    )
    assert (
        'subject.addEventListener("DOMContentLoaded", this, { once: true });' in actual
    )
    assert 'pref("nixcfg.zen.autoconfig.phase", "bootstrap");' not in actual
    assert (
        'Services.prefs.setStringPref("nixcfg.zen.autoconfig.phase", phase);'
        not in actual
    )
