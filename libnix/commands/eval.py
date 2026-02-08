"""Typed wrappers around ``nix eval`` for JSON, Pydantic, and raw output."""

import json
from typing import Any

from pydantic import TypeAdapter

from .base import run_nix


async def nix_eval_json(expr: str, *, timeout: float = 60.0) -> Any:
    """Evaluate a Nix expression and return the parsed JSON value.

    Runs ``nix eval --json --expr <expr>`` and decodes stdout as JSON.
    The return type is whatever ``json.loads`` produces (dict, list, str,
    int, bool, or None).
    """
    result = await run_nix(
        ["nix", "eval", "--json", "--expr", expr],
        timeout=timeout,
    )
    return json.loads(result.stdout)


async def nix_eval_typed[T](expr: str, model: type[T], *, timeout: float = 60.0) -> T:
    """Evaluate a Nix expression and validate the result against a Pydantic model.

    Calls :func:`nix_eval_json` then validates the parsed data with
    ``TypeAdapter(model).validate_python(data)``.
    """
    data = await nix_eval_json(expr, timeout=timeout)
    return TypeAdapter(model).validate_python(data)


async def nix_eval_raw(expr: str, *, timeout: float = 60.0) -> str:
    """Evaluate a Nix expression and return the raw string output.

    Runs ``nix eval --raw --expr <expr>``, which prints the value without
    JSON quoting.  Useful for expressions that evaluate to a string.
    """
    result = await run_nix(
        ["nix", "eval", "--raw", "--expr", expr],
        timeout=timeout,
    )
    return result.stdout
