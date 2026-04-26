"""Audit tests for source-text substring assertions."""

from __future__ import annotations

import ast
from pathlib import Path

from lib.update.paths import REPO_ROOT

_REPO_ROOT = Path(REPO_ROOT)
_SELF_PATH = Path(__file__).resolve()
_EXCLUDED_PARTS = {".venv", "__pycache__", "mutants", "node_modules"}


def _receiver_is_repo_source_path(source: str, receiver: ast.expr) -> bool:
    text = ast.get_source_segment(source, receiver) or ""
    if "REPO_ROOT" in text:
        return True
    if isinstance(receiver, ast.Name):
        return receiver.id.endswith("_PATH")
    return False


def _is_repo_source_read(source: str, node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_text"
        and _receiver_is_repo_source_path(source, node.func.value)
    )


def _repo_source_reader_names(source: str, tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(
            isinstance(item, ast.Return) and _is_repo_source_read(source, item.value)
            for item in ast.walk(node)
        ):
            names.add(node.name)
    return names


class _SourceSubstringVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str, reader_names: set[str]) -> None:
        self.path = path
        self.source = source
        self.reader_names = reader_names
        self.source_text_names: set[str] = set()
        self.violations: list[str] = []

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._is_source_text(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.source_text_names.add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
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
        if _is_repo_source_read(self.source, node):
            return True
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.reader_names
        )

    def _uses_source_text(self, node: ast.AST) -> bool:
        if self._is_source_text(node):
            return True
        return any(
            isinstance(item, ast.Name) and item.id in self.source_text_names
            for item in ast.walk(node)
        )


def _is_string_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


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
