"""Shared test isolation for the update tooling."""

from os import terminal_size

import pytest


@pytest.fixture(autouse=True)
def _no_persistent_run_log(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Keep every test's update run out of the user's real state directory.

    Detailed diagnostics are opt-in in tests. Durable execution always persists
    its SQLite state, so the default run directory must also be temporary.
    """
    monkeypatch.setenv("UPDATE_RUN_LOG", "0")
    monkeypatch.setenv("UPDATE_RUN_LOG_DIR", str(tmp_path / "update-runs"))


@pytest.fixture(autouse=True)
def _isolated_crate2nix_cargo_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Keep crate2nix's Cargo home inside the test's temporary directory.

    A real target refresh creates this directory eagerly
    (``crate2nix._crate2nix_cargo_home``), so without a redirect the suite
    writes into the developer's ``~/.cache/nixcfg`` and fails on any read-only
    home. Tests that assert the default/override resolution set these variables
    themselves and still win, because monkeypatch unwinds in reverse order.
    """
    monkeypatch.setenv(
        "NIXCFG_CRATE2NIX_CARGO_HOME", str(tmp_path / "crate2nix-cargo-home")
    )


@pytest.fixture(autouse=True)
def _stable_cli_help_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hosted macos-15 quality sees a degenerate TTY; Rich then omits flags."""
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("LINES", "50")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
    monkeypatch.setattr(
        "shutil.get_terminal_size",
        lambda fallback=(200, 50): terminal_size((200, 50)),
    )
