"""Shared helpers for JSON-producing Nix command wrappers."""

from __future__ import annotations

import json
from typing import Protocol, cast

from .base import run_nix


class _ModelValidator[T](Protocol):
    @classmethod
    def model_validate(cls, obj: object) -> T: ...


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


async def run_nix_json(
    args: list[str],
    *,
    timeout: float,  # noqa: ASYNC109
) -> JsonValue:
    """Run a Nix command and parse stdout as JSON."""
    result = await run_nix(args, timeout=timeout)
    return json.loads(result.stdout)


def as_model_mapping[T](raw: object, model: _ModelValidator[T]) -> dict[str, T]:
    """Validate a dict payload as ``dict[str, T]`` via ``model_validate``."""
    if not isinstance(raw, dict):
        msg = f"Expected JSON object, got {type(raw)}"
        raise TypeError(msg)
    if not all(isinstance(key, str) for key in raw):
        msg = "Expected JSON object with string keys"
        raise TypeError(msg)
    raw_mapping = cast("dict[str, object]", raw)
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
