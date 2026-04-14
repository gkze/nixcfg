"""Source-level guardrails for standalone Home Manager flake outputs."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from lib.update.paths import REPO_ROOT


@cache
def _flake_text() -> str:
    """Return the flake source text."""
    return Path(REPO_ROOT / "flake.nix").read_text(encoding="utf-8")


def test_standalone_home_output_is_exported_outside_flakelight_home_checks() -> None:
    """Avoid wiring the standalone home activation package into shared flake checks."""
    source = _flake_text()

    assert (
        'homeConfigurations.george = mkStandaloneHomeConfiguration "george" (' in source
    )
    assert (
        "homeConfigurations.george = import ./home/george { outputs = self; };"
        not in source
    )
