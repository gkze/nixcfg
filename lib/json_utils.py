"""Shared JSON validation and coercion helpers."""

from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

_JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject)
_JSON_LIST_ADAPTER = TypeAdapter(list[JsonValue])
_OBJECT_LIST_ADAPTER = TypeAdapter(list[object])
_STRING_ADAPTER = TypeAdapter(str)


def as_object_dict(value: object, *, context: str) -> dict[str, object]:
    """Return ``value`` as ``dict[str, object]`` or raise ``TypeError``."""
    if not isinstance(value, dict):
        msg = f"Expected JSON object for {context}"
        raise TypeError(msg)
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            msg = f"Expected string key in {context}, got {type(key).__name__}"
            raise TypeError(msg)
        result[key] = item
    return result


def as_object_list(value: object, *, context: str) -> list[object]:
    """Return ``value`` as ``list[object]`` or raise ``TypeError``."""
    try:
        return _OBJECT_LIST_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        msg = f"Expected JSON array for {context}"
        raise TypeError(msg) from exc


def as_json_object(value: object, *, context: str) -> JsonObject:
    """Return ``value`` as :data:`JsonObject` or raise ``TypeError``."""
    try:
        return _JSON_OBJECT_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        msg = f"Expected JSON object for {context}"
        raise TypeError(msg) from exc


def as_json_list(value: object, *, context: str) -> list[JsonValue]:
    """Return ``value`` as ``list[JsonValue]`` or raise ``TypeError``."""
    try:
        return _JSON_LIST_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        msg = f"Expected JSON array for {context}"
        raise TypeError(msg) from exc


def get_required_str(mapping: dict[str, object], key: str, *, context: str) -> str:
    """Return required string field ``key`` from ``mapping``."""
    if key not in mapping:
        msg = f"Expected string field {key!r} in {context}"
        raise TypeError(msg)
    try:
        return _STRING_ADAPTER.validate_python(mapping[key], strict=True)
    except ValidationError as exc:
        msg = f"Expected string field {key!r} in {context}"
        raise TypeError(msg) from exc


def coerce_json_value(value: object, *, context: str) -> JsonValue:
    """Convert arbitrary JSON-compatible data to :data:`JsonValue`."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [coerce_json_value(item, context=f"{context}[]") for item in value]
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = f"Expected string key in {context}, got {type(key).__name__}"
                raise TypeError(msg)
            result[key] = coerce_json_value(item, context=f"{context}.{key}")
        return result
    msg = f"Unsupported JSON value in {context}: {type(value).__name__}"
    raise TypeError(msg)


def coerce_json_object(value: object, *, context: str) -> JsonObject:
    """Convert ``value`` to :data:`JsonObject` or raise ``TypeError``."""
    json_value = coerce_json_value(value, context=context)
    if not isinstance(json_value, dict):
        msg = f"Expected JSON object for {context}, got {type(json_value).__name__}"
        raise TypeError(msg)
    return json_value
