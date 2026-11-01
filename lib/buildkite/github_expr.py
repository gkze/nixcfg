"""The subset of GitHub Actions expressions these workflows actually use.

Evaluation follows Actions truthiness and the value-returning ``&&`` / ``||``
operators. An expression this module cannot parse raises
:class:`GitHubExprError` so a new workflow form fails the parity test instead
of being ignored.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_INTERPOLATION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)


class GitHubExprError(ValueError):
    """A workflow expression is outside the supported subset."""


@dataclass(frozen=True)
class Token:
    """One lexical token. ``value`` is the raw spelling."""

    kind: str
    value: str


@dataclass(frozen=True)
class Lit:
    """A literal boolean, number, string, or null."""

    value: object


@dataclass(frozen=True)
class Name:
    """A context path such as ``needs.prepare-pins.result`` or ``needs.*.result``."""

    path: tuple[str, ...]


@dataclass(frozen=True)
class Call:
    """A function call. ``name`` is ``format``, ``contains``, ``always``, and so on."""

    name: str
    args: tuple[Expr, ...]


@dataclass(frozen=True)
class Unary:
    """``!`` applied to one expression."""

    item: Expr


@dataclass(frozen=True)
class Binary:
    """``&&``, ``||``, ``==``, or ``!=``."""

    op: str
    left: Expr
    right: Expr


type Expr = Lit | Name | Call | Unary | Binary


def tokenize(source: str) -> list[Token]:
    """Return tokens for one expression body, excluding the ``${{ }}`` wrapper."""
    tokens: list[Token] = []
    index = 0
    while index < len(source):
        char = source[index]
        if char.isspace():
            index += 1
            continue
        for spelling, kind in (("&&", "and"), ("||", "or"), ("==", "eq"), ("!=", "ne")):
            if source.startswith(spelling, index):
                tokens.append(Token(kind, spelling))
                index += len(spelling)
                break
        else:
            if char in "().,!":
                tokens.append(Token(char, char))
                index += 1
                continue
            if char in {"'", '"'}:
                tokens.append(Token("string", _read_string(source, index)))
                index += len(tokens[-1].value) + 2
                continue
            if char == "*":
                tokens.append(Token("star", "*"))
                index += 1
                continue
            if char.isalpha() or char == "_":
                end = index + 1
                while end < len(source) and (
                    source[end].isalnum() or source[end] in "_-"
                ):
                    end += 1
                tokens.append(Token("ident", source[index:end]))
                index = end
                continue
            if char.isdigit():
                end = index + 1
                while end < len(source) and source[end].isdigit():
                    end += 1
                tokens.append(Token("number", source[index:end]))
                index = end
                continue
            message = f"unexpected {char!r} in expression"
            raise GitHubExprError(message)
    return tokens


def _read_string(source: str, index: int) -> str:
    quote = source[index]
    end = source.find(quote, index + 1)
    if end < 0:
        message = "unterminated string"
        raise GitHubExprError(message)
    return source[index + 1 : end]


def parse(source: str) -> Expr:
    """Parse one expression body."""
    parser = _Parser(tokenize(source))
    expr = parser.parse_or()
    if parser.pending():
        message = "trailing tokens in expression"
        raise GitHubExprError(message)
    return expr


def truthy(value: object) -> bool:
    """Return Actions truthiness. A non-empty string is true, including ``'false'``."""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, int) and not isinstance(value, bool):
        return value != 0
    return bool(value)


def stringify(value: object) -> str:
    """Render a value the way ``${{ }}`` interpolation does."""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def interpolate(text: str, context: ExprContext) -> str:
    """Replace every ``${{ }}`` in *text*."""

    def replace(match: re.Match[str]) -> str:
        return stringify(context.eval(parse(match.group(1))))

    return _INTERPOLATION.sub(replace, text)


def expression_text(value: str) -> str | None:
    """Return the body when *value* is exactly one ``${{ }}``, else ``None``."""
    stripped = value.strip()
    match = _INTERPOLATION.fullmatch(stripped)
    if match is None:
        return None
    return match.group(1)


@dataclass
class ExprContext:
    """Values an expression can read while a step is running."""

    inputs: Mapping[str, object]
    needs: Mapping[str, Mapping[str, object]]
    steps: Mapping[str, Mapping[str, object]]
    github: Mapping[str, object]
    runner: Mapping[str, object]
    secrets: Mapping[str, object]
    matrix: Mapping[str, object]
    success: bool = True
    failure: bool = False
    cancelled: bool = False

    def eval(self, expr: Expr) -> object:
        """Evaluate *expr* in this context."""
        if isinstance(expr, Lit):
            return expr.value
        if isinstance(expr, Name):
            return self._name(expr.path)
        if isinstance(expr, Call):
            return self._call(expr)
        if isinstance(expr, Unary):
            return not truthy(self.eval(expr.item))
        return self._binary(expr)

    def _binary(self, expr: Binary) -> object:
        left = self.eval(expr.left)
        if expr.op == "and":
            return left if not truthy(left) else self.eval(expr.right)
        if expr.op == "or":
            return left if truthy(left) else self.eval(expr.right)
        right = self.eval(expr.right)
        same = left == right
        return same if expr.op == "eq" else not same

    def _name(self, path: tuple[str, ...]) -> object:
        if path == ("true",):
            return True
        if path == ("false",):
            return False
        if path == ("null",):
            return None
        if "*" in path:
            return self._star(path)
        head, *rest = path
        try:
            current: object = {
                "inputs": self.inputs,
                "needs": self.needs,
                "steps": self.steps,
                "github": self.github,
                "runner": self.runner,
                "secrets": self.secrets,
                "matrix": self.matrix,
            }[head]
        except KeyError:
            message = f"unknown context {head}"
            raise GitHubExprError(message) from None
        for part in rest:
            current = _index(current, part)
        return current

    def _star(self, path: tuple[str, ...]) -> list[object]:
        if path[:2] != ("needs", "*"):
            message = f"unsupported wildcard {'.'.join(path)}"
            raise GitHubExprError(message)
        values: list[object] = []
        for payload in self.needs.values():
            current: object = payload
            for part in path[2:]:
                current = _index(current, part)
            values.append(current)
        return values

    def _call(self, expr: Call) -> object:
        args = [self.eval(arg) for arg in expr.args]
        if expr.name in {"always", "cancelled", "success", "failure"}:
            return self._status(expr, args)
        return _invoke(expr, args)

    def _status(self, expr: Call, args: list[object]) -> object:
        _require_arity(expr, args, 0)
        if expr.name == "always":
            return True
        if expr.name == "cancelled":
            return self.cancelled
        if expr.name == "success":
            return self.success
        return self.failure


def _invoke(expr: Call, args: list[object]) -> object:
    if expr.name == "contains":
        _require_arity(expr, args, 2)
        return _contains(args[0], args[1])
    if expr.name == "startsWith":
        _require_arity(expr, args, 2)
        return stringify(args[0]).startswith(stringify(args[1]))
    if expr.name == "format":
        if not args:
            message = "format() requires a template"
            raise GitHubExprError(message)
        return _format(stringify(args[0]), args[1:])
    if expr.name == "fromJSON":
        _require_arity(expr, args, 1)
        try:
            return json.loads(stringify(args[0]))
        except json.JSONDecodeError as error:
            message = "fromJSON() received invalid JSON"
            raise GitHubExprError(message) from error
    message = f"unsupported function {expr.name}"
    raise GitHubExprError(message)


def references_needs(expr: Expr) -> bool:
    """Return whether *expr* reads the ``needs`` context."""
    if isinstance(expr, Name):
        return expr.path[0] == "needs"
    if isinstance(expr, Call):
        return any(references_needs(arg) for arg in expr.args)
    if isinstance(expr, Unary):
        return references_needs(expr.item)
    if isinstance(expr, Binary):
        return references_needs(expr.left) or references_needs(expr.right)
    return False


def _contains(haystack: object, needle: object) -> bool:
    if isinstance(haystack, str):
        return stringify(needle) in haystack
    if isinstance(haystack, Sequence) and not isinstance(haystack, (str, bytes)):
        return needle in haystack
    message = "contains() requires a string or a list"
    raise GitHubExprError(message)


def _format(template: str, args: Sequence[object]) -> str:
    rendered = template
    for index, arg in enumerate(args):
        rendered = rendered.replace(f"{{{index}}}", stringify(arg))
    return rendered


def _require_arity(expr: Call, args: Sequence[object], arity: int) -> None:
    if len(args) != arity:
        message = f"{expr.name}() expects {arity} arguments"
        raise GitHubExprError(message)


def _index(current: object, part: str) -> object:
    """Return a missing field as an empty string, matching an unset Actions output."""
    if current is None or current == "":
        return ""
    if isinstance(current, Mapping):
        return current.get(part, "")
    message = f"cannot read {part}"
    raise GitHubExprError(message)


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._index = 0

    def pending(self) -> bool:
        return self._index < len(self._tokens)

    def parse_or(self) -> Expr:
        left = self.parse_and()
        while self._accept("or"):
            left = Binary("or", left, self.parse_and())
        return left

    def parse_and(self) -> Expr:
        left = self.parse_eq()
        while self._accept("and"):
            left = Binary("and", left, self.parse_eq())
        return left

    def parse_eq(self) -> Expr:
        left = self.parse_unary()
        if self._accept("eq"):
            return Binary("eq", left, self.parse_unary())
        if self._accept("ne"):
            return Binary("ne", left, self.parse_unary())
        return left

    def parse_unary(self) -> Expr:
        if self._accept("!"):
            return Unary(self.parse_unary())
        return self.parse_primary()

    def parse_primary(self) -> Expr:
        if self._accept("("):
            expr = self.parse_or()
            self._expect(")")
            return expr
        token = self._pop()
        if token.kind == "string":
            return Lit(token.value)
        if token.kind == "number":
            return Lit(int(token.value))
        if token.kind != "ident":
            message = f"expected a value, found {token.value!r}"
            raise GitHubExprError(message)
        if self._accept("("):
            args: list[Expr] = []
            if not self._peek(")"):
                args.append(self.parse_or())
                while self._accept(","):
                    args.append(self.parse_or())
            self._expect(")")
            return Call(token.value, tuple(args))
        path = [token.value]
        while self._accept("."):
            if self._accept("star"):
                path.append("*")
                continue
            path.append(self._expect("ident").value)
        return Name(tuple(path))

    def _accept(self, kind: str) -> bool:
        if self._peek(kind):
            self._index += 1
            return True
        return False

    def _peek(self, kind: str) -> bool:
        return self.pending() and self._tokens[self._index].kind == kind

    def _pop(self) -> Token:
        if not self.pending():
            message = "unexpected end of expression"
            raise GitHubExprError(message)
        token = self._tokens[self._index]
        self._index += 1
        return token

    def _expect(self, kind: str) -> Token:
        token = self._pop()
        if token.kind != kind:
            message = f"expected {kind}, found {token.value!r}"
            raise GitHubExprError(message)
        return token
