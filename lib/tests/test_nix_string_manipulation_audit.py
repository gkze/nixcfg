"""Audit Python sources for direct Nix string templates."""

from __future__ import annotations

import ast
from pathlib import Path

from lib.update.paths import REPO_ROOT

_REPO_ROOT = Path(REPO_ROOT)
_EXCLUDED_PARTS = {".claude", ".venv", "venv", "__pycache__", "mutants"}
_SELF_PATH = Path(__file__).resolve()
_FORBIDDEN_NIX_TEMPLATE_FRAGMENTS = (
    "fetchFromGitHub {",
    "fetchYarnDeps {",
    'builtins.getFlake "',
    "lib.fix (self:",
    "import flake.inputs.nixpkgs {",
    "in flake.pkgs.",
)
_AST_SCAN_MARKERS = (
    *_FORBIDDEN_NIX_TEMPLATE_FRAGMENTS,
    "FunctionCall(",
    "nix_manipulator",
)


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    if isinstance(tree, ast.Module):
        bodies: list[list[ast.stmt]] = [tree.body]
    else:
        bodies = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bodies.append(node.body)
    for body in bodies:
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            ids.add(id(value))
    return ids


def _nix_parse_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "nix_manipulator":
            continue
        for alias in node.names:
            if alias.name == "parse":
                names.add(alias.asname or alias.name)
    return names


def _might_need_ast_scan(source: str) -> bool:
    return any(marker in source for marker in _AST_SCAN_MARKERS)


def test_python_sources_avoid_raw_nix_templates() -> None:
    """Python sources should build Nix syntax through nix-manipulator helpers."""
    violations: list[str] = []

    for path in sorted(_REPO_ROOT.rglob("*.py")):
        if path.resolve() == _SELF_PATH:
            continue
        if any(part in _EXCLUDED_PARTS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        if not _might_need_ast_scan(source):
            continue

        tree = ast.parse(source)
        docstring_ids = _docstring_node_ids(tree)
        nix_parse_names = _nix_parse_names(tree)
        rel = path.relative_to(_REPO_ROOT)

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstring_ids:
                    continue
                for fragment in _FORBIDDEN_NIX_TEMPLATE_FRAGMENTS:
                    if fragment in node.value:
                        violations.append(f"{rel}:{node.lineno}: {fragment}")

            if not isinstance(node, ast.Call):
                continue

            func = node.func
            if isinstance(func, ast.Name) and func.id in nix_parse_names and node.args:
                arg = node.args[0]
                if isinstance(arg, (ast.Constant, ast.JoinedStr)):
                    violations.append(
                        f"{rel}:{node.lineno}: parse() called on string literal",
                    )

            if isinstance(func, ast.Name) and func.id == "FunctionCall":
                for keyword in node.keywords:
                    if keyword.arg != "name":
                        continue
                    value = keyword.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        violations.append(
                            f"{rel}:{node.lineno}: FunctionCall name uses raw string",
                        )

    assert violations == [], "\n".join(violations)
