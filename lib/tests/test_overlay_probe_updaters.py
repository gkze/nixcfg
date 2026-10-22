"""Updaters that hash through the overlay probe need an overlay attribute."""

import re

from lib.update.paths import REPO_ROOT
from lib.update.updaters import ensure_updaters_loaded
from lib.update.updaters.core import SourceThenOverlayHashMixin


def _overlay_bindings() -> set[str]:
    binding = re.compile(r'^\s*"?([A-Za-z0-9_-]+)"?\s*=', re.MULTILINE)
    return {
        match.group(1)
        for path in (REPO_ROOT / "overlays").rglob("*.nix")
        for match in binding.finditer(path.read_text(encoding="utf-8"))
    }


def test_overlay_probe_updaters_are_overlay_attributes() -> None:
    """The dependency hash probe selects ``applied.<name>`` (Update run 39760093470)."""
    probe_updaters = sorted(
        name
        for name, updater in ensure_updaters_loaded().items()
        if issubclass(updater, SourceThenOverlayHashMixin)
    )
    assert probe_updaters
    assert [name for name in probe_updaters if name not in _overlay_bindings()] == []
