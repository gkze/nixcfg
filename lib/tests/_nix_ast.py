"""Helpers for asserting on Nix ASTs with nix-manipulator."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import fields, is_dataclass
from functools import lru_cache
from pathlib import Path

from nix_manipulator import parse
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.source_code import NixSourceCode

from lib.tests._assertions import check, expect_not_none

_NON_SEMANTIC_FIELD_NAMES = {
    "after",
    "argument_set_is_multiline",
    "attrpath_order",
    "before",
    "leading_blank_line",
    "multiline",
    "named_attribute_set_before_formals",
    "scope_state",
    "source_path",
    "trailing_blank_line",
}
_NON_SEMANTIC_FIELD_FRAGMENTS = (
    "comment",
    "comments",
    "gap",
    "indent",
    "newline",
    "trivia",
    "_lines",
)


def _rewrite_function_formals_for_parser(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "{":
        return text

    try:
        end_index = next(
            index for index, line in enumerate(lines) if line.strip() == "}:"
        )
    except StopIteration:
        return text

    header_lines = lines[1:end_index]
    if not header_lines:
        return text

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in header_lines:
        if line.startswith("  ") and not line.startswith("    "):
            if current:
                blocks.append(current)
            current = [line]
            continue
        if not current:
            return text
        current.append(line)
    if current:
        blocks.append(current)

    if not blocks:
        return text

    rewritten = ["{ " + blocks[0][0].strip().removesuffix(",")]
    for block_index, block in enumerate(blocks):
        if block_index > 0:
            rewritten.append(", " + block[0].strip().removesuffix(","))
        for index, line in enumerate(block[1:], start=1):
            rewritten.append(
                line.removesuffix(",") if index == len(block) - 1 else line
            )

    rewritten.append(lines[end_index])
    rewritten.extend(lines[end_index + 1 :])
    rebuilt = "\n".join(rewritten)
    return f"{rebuilt}\n" if text.endswith("\n") else rebuilt


def _parse_nix_source(value: str | NixExpression | NixSourceCode) -> NixSourceCode:
    if isinstance(value, str):
        parsed = parse(value)
        if parsed.contains_error:
            parsed = parse(_rewrite_function_formals_for_parser(value))
    elif isinstance(value, NixSourceCode):
        parsed = value
    else:
        rebuilt = value.rebuild()
        parsed = parse(rebuilt)
        if parsed.contains_error:
            parsed = parse(_rewrite_function_formals_for_parser(rebuilt))

    check(parsed.contains_error is False, "expected parseable Nix expression")
    return parsed


def parse_nix_expr(value: str | NixExpression | NixSourceCode) -> NixExpression:
    """Parse *value* and return its root expression."""
    return expect_not_none(
        _parse_nix_source(value).expr,
        "expected nix-manipulator to return a root expression",
    )


def _source_text(value: str | NixExpression | NixSourceCode) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, NixSourceCode):
        return value.rebuild()
    return value.rebuild()


@lru_cache(maxsize=128)
def _canonical_nix_parse(text: str) -> str:
    nix_instantiate = expect_not_none(
        shutil.which("nix-instantiate"),
        "nix-instantiate command not available",
    )
    result = subprocess.run(  # noqa: S603
        [nix_instantiate, "--parse", "-"],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
    )
    check(
        result.returncode == 0,
        result.stderr.strip() or "nix-instantiate --parse failed",
    )
    return result.stdout.strip()


def _is_non_semantic_field(name: str) -> bool:
    if name in _NON_SEMANTIC_FIELD_NAMES:
        return True
    return any(fragment in name for fragment in _NON_SEMANTIC_FIELD_FRAGMENTS)


def _semantic_tree(value: object) -> object:
    if isinstance(value, Parenthesis):
        return _semantic_tree(value.value)
    if isinstance(value, NixExpression):
        return {
            "type": value.__class__.__name__,
            "fields": {
                field.name: _semantic_tree(getattr(value, field.name))
                for field in fields(value)
                if not _is_non_semantic_field(field.name)
            },
        }
    if isinstance(value, list):
        return [_semantic_tree(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_semantic_tree(item) for item in value)
    if isinstance(value, dict):
        return {key: _semantic_tree(item) for key, item in value.items()}
    if is_dataclass(value):
        return {
            "type": value.__class__.__name__,
            "fields": {
                field.name: _semantic_tree(getattr(value, field.name))
                for field in fields(value)
                if not _is_non_semantic_field(field.name)
            },
        }
    return value


def assert_nix_ast_equal(
    actual: str | NixExpression | NixSourceCode,
    expected: str | NixExpression | NixSourceCode,
) -> None:
    """Assert that two Nix expressions are semantically equivalent."""
    try:
        actual_expr = parse_nix_expr(actual)
        expected_expr = parse_nix_expr(expected)
    except AssertionError:
        check(
            _canonical_nix_parse(_source_text(actual))
            == _canonical_nix_parse(_source_text(expected)),
            "expected semantically equivalent Nix ASTs",
        )
        return

    if _semantic_tree(actual_expr) == _semantic_tree(expected_expr):
        return

    check(
        _canonical_nix_parse(_source_text(actual))
        == _canonical_nix_parse(_source_text(expected)),
        "expected semantically equivalent Nix ASTs",
    )


def binding_map(bindings: Iterable[Binding | Inherit]) -> dict[str, Binding]:
    """Return the named bindings from *bindings* keyed by binding name."""
    return {
        binding.name: binding for binding in bindings if isinstance(binding, Binding)
    }


def expect_binding(bindings: Iterable[Binding | Inherit], name: str) -> Binding:
    """Return the binding named *name* from *bindings*."""
    binding = binding_map(bindings).get(name)
    return expect_not_none(binding, f"missing binding {name}")


def expect_scope_binding(expr: NixExpression, name: str) -> Binding:
    """Return the scoped let-binding named *name* from *expr*."""
    return expect_binding(expr.scope, name)
