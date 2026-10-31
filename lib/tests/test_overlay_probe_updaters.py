"""Updaters that hash through the overlay probe need a reachable overlay attribute.

Both properties are structural. The previous version scraped overlay sources
with regular expressions, which is forbidden by the repository's own rule and
silently depended on formatting.
"""

from collections.abc import Iterator
from dataclasses import fields, is_dataclass
from pathlib import Path

from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import binding_map, parse_nix_expr
from lib.tests._package_registry import is_darwin_overlay_export
from lib.update.paths import REPO_ROOT
from lib.update.planner import source_prerequisites
from lib.update.updaters import ensure_updaters_loaded
from lib.update.updaters.core import SourceThenOverlayHashMixin

# Fields that hold parser bookkeeping rather than program structure.
_NON_SEMANTIC_FIELDS = frozenset({"scope", "scope_state"})


def _walk(node: object) -> Iterator[object]:
    """Yield every expression node reachable from *node*."""
    stack: list[object] = [node]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, NixExpression):
            for field in fields(current):
                if field.name in _NON_SEMANTIC_FIELDS:
                    continue
                value = getattr(current, field.name)
                if is_dataclass(value):
                    stack.append(value)
                elif isinstance(value, (list, tuple)):
                    stack.extend(item for item in value if is_dataclass(item))
        elif isinstance(current, (list, tuple)):
            stack.extend(item for item in current if is_dataclass(item))


def _probe_updater_names() -> list[str]:
    return sorted(
        name
        for name, updater in ensure_updaters_loaded().items()
        if issubclass(updater, SourceThenOverlayHashMixin)
    )


def _overlay_path(name: str) -> Path:
    return REPO_ROOT / "overlays" / name / "default.nix"


def _overlay_attributes(name: str) -> set[str]:
    """Return every attribute name bound anywhere in the updater's overlay.

    Overlays export their package either as a top-level attrset or as an
    argument to a helper such as ``optionalAttrs``, so the whole file is
    examined rather than only the function body.
    """
    path = _overlay_path(name)
    if not path.is_file():
        return set()
    expression = parse_nix_expr(path.read_text(encoding="utf-8"))
    return {
        attribute
        for node in _walk(expression)
        if isinstance(node, AttributeSet)
        for attribute in binding_map(node.values)
    }


def _referenced_sources(name: str) -> set[str]:
    """Return the ``sources.<x>`` keys the updater's overlay reads."""
    path = _overlay_path(name)
    if not path.is_file():
        return set()
    expression = parse_nix_expr(path.read_text(encoding="utf-8"))
    return {
        node.attribute.strip('"')
        for node in _walk(expression)
        if isinstance(node, Select)
        and isinstance(node.expression, Identifier)
        and node.expression.name == "sources"
    }


def test_overlay_probe_updaters_resolve_to_an_overlay_attribute() -> None:
    """The dependency hash probe selects ``applied.<name>`` (Update run 39760093470).

    A probe updater is reachable either from its own overlay or from the
    registry-driven Darwin overlay. Any other updater would probe an attribute
    that does not exist.
    """
    probe_updaters = _probe_updater_names()
    assert probe_updaters

    registry = expect_instance(
        expect_instance(
            parse_nix_expr(
                (REPO_ROOT / "packages/registry.nix").read_text(encoding="utf-8")
            ),
            FunctionDefinition,
        ).output,
        AttributeSet,
    )

    assert [
        name
        for name in probe_updaters
        if name not in _overlay_attributes(name)
        and not is_darwin_overlay_export(registry, name)
    ] == []


def test_overlay_probe_sources_are_prerequisites() -> None:
    """A probed overlay that reads another source must run after that source.

    Until the other source's update finishes, its sources.json can be unhashed
    and the probe fails to evaluate (Update run 39774011035).
    """
    updaters = ensure_updaters_loaded()
    unordered = [
        (name, source)
        for name in _probe_updater_names()
        for source in sorted(_referenced_sources(name))
        if source != name
        and source in updaters
        and source not in set(source_prerequisites(updaters, name))
    ]
    assert unordered == []
