"""Structured JSON file helpers for packaging codemods."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from lib.codemods.errors import CodemodError
from lib.json_utils import as_object_dict

if TYPE_CHECKING:
    from pathlib import Path

type JsonObject = dict[str, object]
type JsonObjectUpdater = Callable[[JsonObject], bool | None]


def _context(path: Path, context: str | None) -> str:
    return str(path) if context is None else context


def read_json_object(path: Path, *, context: str | None = None) -> JsonObject:
    """Read path as a JSON object."""
    label = _context(path, context)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"invalid JSON in {label}: {exc}"
        raise CodemodError(msg) from exc

    try:
        return as_object_dict(payload, context=label)
    except TypeError as exc:
        raise CodemodError(str(exc)) from exc


def render_json_object(payload: JsonObject, *, sort_keys: bool = False) -> str:
    """Render a JSON object with package-helper formatting."""
    return json.dumps(payload, indent=2, sort_keys=sort_keys) + "\n"


def write_json_object(
    path: Path,
    payload: JsonObject,
    *,
    sort_keys: bool = False,
) -> None:
    """Write payload to path as formatted JSON."""
    path.write_text(render_json_object(payload, sort_keys=sort_keys), encoding="utf-8")


def update_json_object(
    path: Path,
    updater: JsonObjectUpdater,
    *,
    sort_keys: bool = False,
    context: str | None = None,
) -> bool:
    """Mutate a JSON object through updater and write it when changed."""
    original_text = path.read_text(encoding="utf-8")
    payload = read_json_object(path, context=context)
    changed_hint = updater(payload)
    rendered = render_json_object(payload, sort_keys=sort_keys)
    should_write = (
        changed_hint if changed_hint is not None else rendered != original_text
    )
    if not should_write:
        return False
    path.write_text(rendered, encoding="utf-8")
    return rendered != original_text


def required_object(
    mapping: JsonObject,
    key: str,
    *,
    context: str,
) -> JsonObject:
    """Return a required nested JSON object from mapping."""
    value = mapping.get(key)
    try:
        return as_object_dict(value, context=f"{context}.{key}")
    except TypeError as exc:
        raise CodemodError(str(exc)) from exc


def required_string(
    mapping: JsonObject,
    key: str,
    *,
    context: str,
) -> str:
    """Return a required string field from mapping."""
    value = mapping.get(key)
    if isinstance(value, str):
        return value
    msg = f"Expected string field {key!r} in {context}"
    raise CodemodError(msg)
