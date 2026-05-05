"""Helpers for building and evaluating Nix expressions in tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import Primitive
from nix_manipulator.expressions.set import AttributeSet

from lib.update.paths import REPO_ROOT

_NIX_EVAL_TIMEOUT_SECONDS = 30


def nix_value(value: object) -> NixExpression:
    """Convert a Python value into a nix-manipulator expression."""
    if isinstance(value, NixExpression):
        return value
    if isinstance(value, Mapping):
        return AttributeSet(
            values=[
                Binding(name=str(name), value=nix_value(item))
                for name, item in value.items()
            ],
            multiline=len(value) != 1,
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return NixList(value=[nix_value(item) for item in value])
    return Primitive(value=value)


def nix_attrset(values: Mapping[str, object]) -> AttributeSet:
    """Build an attribute set from Python data."""
    expr = nix_value(dict(values))
    assert isinstance(expr, AttributeSet)
    return expr


def nix_list(values: Sequence[object]) -> NixList:
    """Build a Nix list from Python data."""
    return NixList(value=[nix_value(item) for item in values])


def nix_import(path: Path | str) -> FunctionCall:
    """Build an ``import /path`` expression for *path*."""
    return FunctionCall(
        name=Identifier(name="import"),
        argument=NixPath(path=str(Path(path).resolve())),
    )


def nix_let(bindings: Mapping[str, object], value: object) -> LetExpression:
    """Build a ``let ... in ...`` expression from Python data."""
    return LetExpression(
        local_variables=[
            Binding(name=name, value=nix_value(binding))
            for name, binding in bindings.items()
        ],
        value=nix_value(value),
    )


def _run_nix_eval(expression: NixExpression, *, raw: bool) -> str:
    nix = shutil.which("nix")
    assert nix is not None
    command = [nix, "eval", "--impure"]
    command.append("--raw" if raw else "--json")
    command.extend(["--expr", expression.rebuild()])
    result = subprocess.run(  # noqa: S603
        command,
        check=True,
        capture_output=True,
        cwd=REPO_ROOT,
        text=True,
        timeout=_NIX_EVAL_TIMEOUT_SECONDS,
    )
    return result.stdout


def nix_eval_raw(expression: NixExpression) -> str:
    """Evaluate a Nix expression and return raw stdout."""
    return _run_nix_eval(expression, raw=True)


def nix_eval_json(expression: NixExpression) -> object:
    """Evaluate a Nix expression and decode its JSON output."""
    return json.loads(_run_nix_eval(expression, raw=False))
