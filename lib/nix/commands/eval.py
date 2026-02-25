"""Typed wrappers around ``nix eval`` for JSON, Pydantic, and raw output."""

from pydantic import TypeAdapter

from ._json import JsonValue, run_nix_json
from .base import _resolve_timeout_alias, run_nix


async def nix_eval_json(
    expr: str,
    *,
    command_timeout: float = 60.0,
    **kwargs: object,
) -> JsonValue:
    """Evaluate a Nix expression and return the parsed JSON value.

    Runs ``nix eval --json --expr <expr>`` and decodes stdout as JSON.
    The return type is whatever ``json.loads`` produces (dict, list, str,
    int, bool, or None).
    """
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    return await run_nix_json(
        ["nix", "eval", "--json", "--expr", expr],
        timeout=timeout_seconds,
    )


async def nix_eval_typed[T](
    expr: str,
    model: type[T],
    *,
    command_timeout: float = 60.0,
    **kwargs: object,
) -> T:
    """Evaluate a Nix expression and validate the result against a Pydantic model.

    Calls :func:`nix_eval_json` then validates the parsed data with
    ``TypeAdapter(model).validate_python(data)``.
    """
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    data = await nix_eval_json(expr, timeout=timeout_seconds)
    return TypeAdapter(model).validate_python(data)


async def nix_eval_raw(
    expr: str,
    *,
    command_timeout: float = 60.0,
    **kwargs: object,
) -> str:
    """Evaluate a Nix expression and return the raw string output.

    Runs ``nix eval --raw --expr <expr>``, which prints the value without
    JSON quoting.  Useful for expressions that evaluate to a string.
    """
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    result = await run_nix(
        ["nix", "eval", "--raw", "--expr", expr],
        timeout=timeout_seconds,
    )
    return result.stdout
