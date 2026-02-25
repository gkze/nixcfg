"""Shared helpers for JSON-producing Nix command wrappers."""

from __future__ import annotations

import json
from typing import Protocol

from .base import _resolve_timeout_alias, run_nix


class _ModelValidator[T](Protocol):
    @classmethod
    def model_validate(cls, obj: object) -> T: ...


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


async def run_nix_json(
    args: list[str],
    *,
    command_timeout: float | None = None,
    **kwargs: object,
) -> JsonValue:
    """Run a Nix command and parse stdout as JSON."""
    if command_timeout is None and "timeout" not in kwargs:
        msg = "command_timeout is required"
        raise TypeError(msg)
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout or 0.0,
        kwargs=kwargs,
    )
    result = await run_nix(args, timeout=timeout_seconds)
    return json.loads(result.stdout)


def as_model_mapping[T](raw: object, model: _ModelValidator[T]) -> dict[str, T]:
    """Validate a dict payload as ``dict[str, T]`` via ``model_validate``."""
    if not isinstance(raw, dict):
        msg = f"Expected JSON object, got {type(raw)}"
        raise TypeError(msg)
    raw_mapping: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            msg = "Expected JSON object with string keys"
            raise TypeError(msg)
        raw_mapping[key] = value
    return {key: model.model_validate(value) for key, value in raw_mapping.items()}


def as_model_list[T](raw: object, model: _ModelValidator[T]) -> list[T]:
    """Validate a list-like payload into ``list[T]``.

    Some Nix commands return either ``[{...}]`` or ``{"path": {...}}``.
    Dict payloads are converted by taking ``values()``.
    """
    if isinstance(raw, list):
        return [model.model_validate(item) for item in raw]
    if isinstance(raw, dict):
        return [model.model_validate(item) for item in raw.values()]
    msg = f"Expected JSON list or object, got {type(raw)}"
    raise TypeError(msg)
