"""Updaters that hash through the overlay probe need an overlay attribute."""

import re

from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_source import nix_file_expr
from lib.tests._package_registry import is_darwin_overlay_export
from lib.update.paths import REPO_ROOT
from lib.update.planner import source_prerequisites
from lib.update.updaters import ensure_updaters_loaded
from lib.update.updaters.core import SourceThenOverlayHashMixin


def _overlay_bindings() -> set[str]:
    binding = re.compile(r'^\s*"?([A-Za-z0-9_-]+)"?\s*=', re.MULTILINE)
    return {
        match.group(1)
        for path in (REPO_ROOT / "overlays").rglob("*.nix")
        for match in binding.finditer(path.read_text(encoding="utf-8"))
    }


def _probe_updater_names() -> list[str]:
    return sorted(
        name
        for name, updater in ensure_updaters_loaded().items()
        if issubclass(updater, SourceThenOverlayHashMixin)
    )


def test_overlay_probe_updaters_are_overlay_attributes() -> None:
    """The dependency hash probe selects ``applied.<name>`` (Update run 39760093470)."""
    probe_updaters = sorted(
        name
        for name, updater in ensure_updaters_loaded().items()
        if issubclass(updater, SourceThenOverlayHashMixin)
    )
    assert probe_updaters
    registry = expect_instance(
        expect_instance(
            nix_file_expr("packages/registry.nix"), FunctionDefinition
        ).output,
        AttributeSet,
    )
    bindings = _overlay_bindings()
    assert [
        name
        for name in probe_updaters
        if name not in bindings and not is_darwin_overlay_export(registry, name)
    ] == []


def test_overlay_probe_sources_are_prerequisites() -> None:
    """A probed overlay that reads another source must run after that source.

    Until the other source's update finishes, its sources.json can be unhashed
    and the probe fails to evaluate (Update run 39774011035).
    """
    updaters = ensure_updaters_loaded()
    reference = re.compile(r'\bsources\.(?:"([^"]+)"|([A-Za-z0-9_-]+))')
    unordered = []
    for name in _probe_updater_names():
        overlay = REPO_ROOT / "overlays" / name / "default.nix"
        if not overlay.is_file():
            continue
        referenced = {
            quoted or bare
            for quoted, bare in reference.findall(overlay.read_text(encoding="utf-8"))
        }
        prerequisites = set(source_prerequisites(updaters, name))
        unordered.extend(
            (name, source)
            for source in sorted(referenced)
            if source != name and source in updaters and source not in prerequisites
        )
    assert unordered == []
