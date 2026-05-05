"""Audit tests for source-text substring assertions."""

from __future__ import annotations

import ast
from pathlib import Path

from lib.update.paths import REPO_ROOT

_REPO_ROOT = Path(REPO_ROOT)
_SELF_PATH = Path(__file__).resolve()
_EXCLUDED_PARTS = {".venv", "__pycache__", "mutants", "node_modules"}
_EVAL_JUSTIFICATION_TERMS = (
    "ast",
    "evaluate only",
    "semantics",
    "semantic",
)


def _receiver_is_repo_source_path(
    source: str,
    receiver: ast.expr,
    aliases: set[str] | None = None,
) -> bool:
    text = ast.get_source_segment(source, receiver) or ""
    if "REPO_ROOT" in text:
        return True
    if isinstance(receiver, ast.Name):
        return receiver.id.endswith("_PATH") or receiver.id in (aliases or set())
    return False


def _is_repo_source_read(
    source: str,
    node: ast.AST | None,
    aliases: set[str] | None = None,
) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_text"
        and _receiver_is_repo_source_path(source, node.func.value, aliases)
    )


class _RepoSourceReaderVisitor(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.path_names: set[str] = set()
        self.returns_repo_source_text = False

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._is_repo_source_path(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.path_names.add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._is_repo_source_path(node.value) and isinstance(node.target, ast.Name):
            self.path_names.add(node.target.id)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if _is_repo_source_read(self.source, node.value, self.path_names):
            self.returns_repo_source_text = True
        self.generic_visit(node)

    def _is_repo_source_path(self, node: ast.AST | None) -> bool:
        if node is None:
            return False
        text = ast.get_source_segment(self.source, node) or ""
        if "REPO_ROOT" in text:
            return True
        return any(
            isinstance(item, ast.Name) and item.id in self.path_names
            for item in ast.walk(node)
        )


def _repo_source_reader_names(source: str, tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        visitor = _RepoSourceReaderVisitor(source)
        visitor.visit(node)
        if visitor.returns_repo_source_text:
            names.add(node.name)
    return names


class _SourceSubstringVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str, reader_names: set[str]) -> None:
        self.path = path
        self.source = source
        self.reader_names = reader_names
        self.source_path_names: set[str] = set()
        self.source_text_names: set[str] = set()
        self.violations: list[str] = []

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._is_repo_source_path(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.source_path_names.add(target.id)
        if self._is_source_text(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.source_text_names.add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._is_repo_source_path(node.value) and isinstance(node.target, ast.Name):
            self.source_path_names.add(node.target.id)
        if self._is_source_text(node.value) and isinstance(node.target, ast.Name):
            self.source_text_names.add(node.target.id)
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        for compare in ast.walk(node.test):
            if isinstance(compare, ast.Compare):
                self._visit_compare(node, compare)
        self.generic_visit(node)

    def _visit_compare(self, assert_node: ast.Assert, compare: ast.Compare) -> None:
        operands = [compare.left, *compare.comparators]
        for index, operator in enumerate(compare.ops):
            if not isinstance(operator, (ast.In, ast.NotIn)):
                continue
            left = operands[index]
            right = operands[index + 1]
            if _is_string_literal(left) and self._uses_source_text(right):
                self._record_violation(assert_node)
            if _is_string_literal(right) and self._uses_source_text(left):
                self._record_violation(assert_node)

    def _record_violation(self, node: ast.Assert) -> None:
        rel = self.path.relative_to(_REPO_ROOT)
        statement = ast.get_source_segment(self.source, node) or "assert ..."
        self.violations.append(f"{rel}:{node.lineno}: {statement.strip()}")

    def _is_source_text(self, node: ast.AST | None) -> bool:
        if _is_repo_source_read(self.source, node, self.source_path_names):
            return True
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.reader_names
        )

    def _is_repo_source_path(self, node: ast.AST | None) -> bool:
        if node is None:
            return False
        text = ast.get_source_segment(self.source, node) or ""
        if "REPO_ROOT" in text:
            return True
        return any(
            isinstance(item, ast.Name) and item.id in self.source_path_names
            for item in ast.walk(node)
        )

    def _uses_source_text(self, node: ast.AST) -> bool:
        if self._is_source_text(node):
            return True
        return any(
            isinstance(item, ast.Name) and item.id in self.source_text_names
            for item in ast.walk(node)
        )


class _DirectReadTextSubstringVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self.violations: list[str] = []

    def visit_Assert(self, node: ast.Assert) -> None:
        for compare in ast.walk(node.test):
            if isinstance(compare, ast.Compare):
                self._visit_compare(node, compare)
        self.generic_visit(node)

    def _visit_compare(self, assert_node: ast.Assert, compare: ast.Compare) -> None:
        operands = [compare.left, *compare.comparators]
        for index, operator in enumerate(compare.ops):
            if not isinstance(operator, (ast.In, ast.NotIn)):
                continue
            left = operands[index]
            right = operands[index + 1]
            if _is_string_literal(left) and _calls_read_text(right):
                self._record_violation(assert_node)
            if _is_string_literal(right) and _calls_read_text(left):
                self._record_violation(assert_node)

    def _record_violation(self, node: ast.Assert) -> None:
        rel = self.path.relative_to(_REPO_ROOT)
        statement = ast.get_source_segment(self.source, node) or "assert ..."
        self.violations.append(f"{rel}:{node.lineno}: {statement.strip()}")


class _NixExprSubstringVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self.nix_expr_names: set[str] = set()
        self.violations: list[str] = []

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._is_nix_expr_assignment(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.nix_expr_names.add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._is_nix_expr_assignment(node.value) and isinstance(
            node.target, ast.Name
        ):
            self.nix_expr_names.add(node.target.id)
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        for compare in ast.walk(node.test):
            if isinstance(compare, ast.Compare):
                self._visit_compare(node, compare)
        self.generic_visit(node)

    def _visit_compare(self, assert_node: ast.Assert, compare: ast.Compare) -> None:
        operands = [compare.left, *compare.comparators]
        for index, operator in enumerate(compare.ops):
            if not isinstance(operator, (ast.In, ast.NotIn)):
                continue
            left = operands[index]
            right = operands[index + 1]
            if _is_string_literal(left) and self._uses_nix_expr_text(right):
                self._record_violation(assert_node)
            if _is_string_literal(right) and self._uses_nix_expr_text(left):
                self._record_violation(assert_node)

    def _is_nix_expr_assignment(self, node: ast.AST | None) -> bool:
        call_name = _call_name(node) if isinstance(node, ast.Call) else ""
        return (
            isinstance(node, ast.Call)
            and call_name.endswith(("_expr", "_expression"))
            and call_name != "parse_nix_expr"
        )

    def _uses_nix_expr_text(self, node: ast.AST) -> bool:
        return any(
            isinstance(item, ast.Name) and item.id in self.nix_expr_names
            for item in ast.walk(node)
        )

    def _record_violation(self, node: ast.Assert) -> None:
        rel = self.path.relative_to(_REPO_ROOT)
        statement = ast.get_source_segment(self.source, node) or "assert ..."
        self.violations.append(f"{rel}:{node.lineno}: {statement.strip()}")


class _NixEvalJustificationVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str, eval_names: set[str]) -> None:
        self.path = path
        self.source = source
        self.eval_names = eval_names
        self.function_stack: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        self.violations: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if (
            self._calls_real_nix_eval(node)
            and not self._current_docstring_justifies_eval()
        ):
            self._record_violation(node)
        self.generic_visit(node)

    def _calls_real_nix_eval(self, node: ast.Call) -> bool:
        return isinstance(node.func, ast.Name) and node.func.id in self.eval_names

    def _current_docstring_justifies_eval(self) -> bool:
        if not self.function_stack:
            return False
        docstring = ast.get_docstring(self.function_stack[-1]) or ""
        normalized = docstring.lower()
        return any(term in normalized for term in _EVAL_JUSTIFICATION_TERMS)

    def _record_violation(self, node: ast.Call) -> None:
        rel = self.path.relative_to(_REPO_ROOT)
        statement = ast.get_source_segment(self.source, node) or "nix_eval(...)"
        self.violations.append(f"{rel}:{node.lineno}: {statement.strip()}")


def _calls_read_text(node: ast.AST) -> bool:
    return any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr == "read_text"
        for item in ast.walk(node)
    )


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _nix_eval_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "lib.tests._nix_eval":
            continue
        for alias in node.names:
            if alias.name in {"nix_eval_json", "nix_eval_raw"}:
                names.add(alias.asname or alias.name)
    return names


def _is_string_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def test_source_substring_visitor_tracks_repo_path_aliases() -> None:
    """Repo-source substring audits should catch path aliases before read_text."""
    source = """
from lib.update.paths import REPO_ROOT

path = REPO_ROOT / "packages/demo/default.nix"
source_text = path.read_text(encoding="utf-8")
assert "demo" in source_text
"""
    tree = ast.parse(source)
    visitor = _SourceSubstringVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
        _repo_source_reader_names(source, tree),
    )

    visitor.visit(tree)

    assert visitor.violations == [
        'lib/tests/test_demo.py:6: assert "demo" in source_text'
    ]


def test_repo_source_reader_names_tracks_function_path_aliases() -> None:
    """Reader helper detection should handle aliases inside helper functions."""
    source = """
from lib.update.paths import REPO_ROOT

def read_source():
    path = REPO_ROOT / "packages/demo/default.nix"
    return path.read_text(encoding="utf-8")
"""

    assert _repo_source_reader_names(source, ast.parse(source)) == {"read_source"}


def test_tests_do_not_assert_substrings_in_repo_source_text() -> None:
    """Source-file tests should assert parsed behavior or AST structure instead."""
    violations: list[str] = []
    for path in sorted((_REPO_ROOT / "lib/tests").glob("test*.py")):
        if path.resolve() == _SELF_PATH:
            continue
        if any(part in _EXCLUDED_PARTS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        visitor = _SourceSubstringVisitor(
            path,
            source,
            _repo_source_reader_names(source, tree),
        )
        visitor.visit(tree)
        violations.extend(visitor.violations)

    assert violations == [], "\n".join(violations)


def test_tests_do_not_assert_substrings_directly_against_file_text() -> None:
    """Structured file outputs should be parsed before behavior assertions."""
    violations: list[str] = []
    for path in sorted((_REPO_ROOT / "lib/tests").glob("test*.py")):
        if path.resolve() == _SELF_PATH:
            continue
        if any(part in _EXCLUDED_PARTS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        visitor = _DirectReadTextSubstringVisitor(path, source)
        visitor.visit(ast.parse(source))
        violations.extend(visitor.violations)

    assert violations == [], "\n".join(violations)


def test_tests_assert_nix_expr_builders_with_ast_checks() -> None:
    """Python-to-Nix boundary tests should compare parsed Nix structure."""
    violations: list[str] = []
    for path in sorted((_REPO_ROOT / "lib/tests").glob("test*.py")):
        if path.resolve() == _SELF_PATH:
            continue
        if any(part in _EXCLUDED_PARTS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        visitor = _NixExprSubstringVisitor(path, source)
        visitor.visit(ast.parse(source))
        violations.extend(visitor.violations)

    assert violations == [], "\n".join(violations)


def test_real_nix_eval_tests_explain_why_ast_checks_are_insufficient() -> None:
    """Eval-based tests should remain sparse and justify the semantic boundary."""
    violations: list[str] = []
    for path in sorted((_REPO_ROOT / "lib/tests").glob("test*.py")):
        if path.resolve() == _SELF_PATH:
            continue
        if any(part in _EXCLUDED_PARTS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        eval_names = _nix_eval_names(tree)
        if not eval_names:
            continue
        visitor = _NixEvalJustificationVisitor(path, source, eval_names)
        visitor.visit(tree)
        violations.extend(visitor.violations)

    assert violations == [], "\n".join(violations)
