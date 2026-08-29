"""Helpers for asserting on Nix ASTs with nix-manipulator."""

from collections.abc import Iterable
from dataclasses import fields, is_dataclass
from textwrap import dedent

from nix_manipulator import parse
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.expressions.source_code import NixSourceCode

from lib.tests._assertions import expect_not_none

_NON_SEMANTIC_FIELD_NAMES = {
    "after",
    "argument_set_is_multiline",
    "attrpath_order",
    "before",
    "breaks_after_semicolon",
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
    normalized = dedent(text).strip()
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "{":
        return normalized

    try:
        end_index = next(
            index for index, line in enumerate(lines) if line.strip() == "}:"
        )
    except StopIteration:
        return normalized

    header_lines = lines[1:end_index]
    if not header_lines:
        return normalized

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in header_lines:
        if line.startswith("  ") and not line.startswith("    "):
            if current:
                blocks.append(current)
            current = [line]
            continue
        if not current:
            return normalized
        current.append(line)
    if current:
        blocks.append(current)

    if not blocks:
        return normalized

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
    return "\n".join(rewritten)


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

    assert parsed.contains_error is False, "expected parseable Nix expression"
    return parsed


def parse_nix_expr(value: str | NixExpression | NixSourceCode) -> NixExpression:
    """Parse *value* and return its root expression."""
    return expect_not_none(
        _parse_nix_source(value).expr,
        "expected nix-manipulator to return a root expression",
    )


def nix_attrset_call(
    function: NixExpression,
    /,
    **attributes: NixExpression | str | bool,
) -> FunctionCall:
    """Build a Nix function call whose argument is an attribute set."""
    return FunctionCall(
        name=function,
        argument=AttributeSet(
            values=[
                Binding(name=name, value=value) for name, value in attributes.items()
            ]
        ),
    )


def nix_apply(
    function: NixExpression,
    argument: NixExpression,
    /,
    *arguments: NixExpression,
) -> FunctionCall:
    """Build a left-associated Nix function application."""
    call = FunctionCall(name=function, argument=argument)
    for next_argument in arguments:
        call = FunctionCall(name=call, argument=next_argument)
    return call


def _is_non_semantic_field(name: str) -> bool:
    if name in _NON_SEMANTIC_FIELD_NAMES:
        return True
    return any(fragment in name for fragment in _NON_SEMANTIC_FIELD_FRAGMENTS)


def _normalize_indented_string_body(value: str) -> str:
    """Apply Nix's common-indentation rules without evaluating the string."""
    if not value:
        return ""
    fragments = value.split("\n")
    lines = [fragment + "\n" for fragment in fragments[:-1]]
    lines.append(fragments[-1])

    def _without_line_ending(line: str) -> str:
        # Nix's indented-string lexer recognizes LF here.  A carriage return,
        # including the CR in CRLF, remains semantic string content.
        if line.endswith("\n"):
            return line[:-1]
        return line

    def _contains_only_spaces(line: str) -> bool:
        return not _without_line_ending(line).strip(" ")

    # Spaces on the opening-delimiter line and the unterminated whitespace
    # before the closing delimiter are layout, not string content.
    # Tabs are string content in Nix, so do not use ``str.strip`` here.
    if lines and lines[0].endswith("\n") and _contains_only_spaces(lines[0]):
        lines.pop(0)
    if lines and not lines[-1].endswith("\n") and _contains_only_spaces(lines[-1]):
        lines.pop()

    content_lines = [line for line in lines if not _contains_only_spaces(line)]
    if not content_lines:
        return "".join(line.lstrip(" ") for line in lines)

    common_indent = min(len(line) - len(line.lstrip(" ")) for line in content_lines)
    normalized_lines: list[str] = []
    for line in lines:
        removable_indent = min(
            common_indent,
            len(line) - len(line.lstrip(" ")),
        )
        normalized_lines.append(line[removable_indent:])
    return "".join(normalized_lines)


def _semantic_indented_string(value: IndentedString) -> object:
    parsed = _parse_nix_source(value)
    string_node = expect_not_none(
        next(iter(parsed.node.named_children), None),
        "expected an indented-string syntax node",
    )

    skeleton_parts: list[str] = []
    interpolations: list[object] = []
    markers: list[str] = []
    for child in string_node.named_children:
        child_text = expect_not_none(
            child.text,
            "expected indented-string syntax text",
        ).decode()
        if child.type != "interpolation":
            assert "\0" not in child_text
            skeleton_parts.append(child_text)
            continue

        interpolation = expect_not_none(
            next(iter(child.named_children), None),
            "expected an interpolation expression",
        )
        interpolation_text = expect_not_none(
            interpolation.text,
            "expected interpolation syntax text",
        ).decode()
        marker = f"\0{len(markers)}\0"
        markers.append(marker)
        skeleton_parts.append(marker)
        interpolations.append({
            "interpolation": _semantic_tree(parse_nix_expr(interpolation_text))
        })

    remaining = _normalize_indented_string_body("".join(skeleton_parts))
    parts: list[object] = []
    for marker, interpolation in zip(markers, interpolations, strict=True):
        literal, separator, remaining = remaining.partition(marker)
        assert separator == marker, "expected interpolation marker after normalization"
        if literal:
            parts.append(literal)
        parts.append(interpolation)
    if remaining:
        parts.append(remaining)
    return {"type": "IndentedString", "parts": parts}


def _semantic_tree(value: object) -> object:
    while isinstance(value, Parenthesis):
        value = value.value
    if isinstance(value, IndentedString):
        return _semantic_indented_string(value)
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
        if value and all(isinstance(item, Binding | Inherit) for item in value):
            value = list(binding_map(value).values())
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
    assert _semantic_tree(parse_nix_expr(actual)) == _semantic_tree(
        parse_nix_expr(expected)
    ), "expected semantically equivalent Nix ASTs"


def binding_map(bindings: Iterable[Binding | Inherit]) -> dict[str, Binding]:
    """Return explicit and inherited bindings keyed by semantic attribute name."""
    result: dict[str, Binding] = {}
    for binding in bindings:
        if isinstance(binding, Binding):
            result[binding.name] = binding
            continue
        for inherited_name in binding.names:
            value = (
                inherited_name
                if binding.from_expression is None
                else Select(
                    expression=binding.from_expression,
                    attribute=inherited_name.name,
                )
            )
            result[inherited_name.name] = Binding(
                name=inherited_name.name,
                value=value,
            )
    return result


def expect_binding(bindings: Iterable[Binding | Inherit], name: str) -> Binding:
    """Return the binding named *name* from *bindings*."""
    binding = binding_map(bindings).get(name)
    return expect_not_none(binding, f"missing binding {name}")


def expect_scope_binding(expr: NixExpression, name: str) -> Binding:
    """Return the scoped let-binding named *name* from *expr*."""
    return expect_binding(expr.scope, name)
