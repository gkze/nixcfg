"""Audit production Python sources for direct Nix string templates."""

from __future__ import annotations

import ast
from pathlib import Path

from lib.tests._assertions import check

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXCLUDED_PARTS = {".venv", "venv", "__pycache__", "mutants", "tests"}
_FORBIDDEN_NIX_TEMPLATE_FRAGMENTS = (
    "fetchFromGitHub {",
    "fetchYarnDeps {",
    'builtins.getFlake "',
    "lib.fix (self:",
    "import flake.inputs.nixpkgs {",
    "in flake.interactivePkgs.",
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


def test_production_python_avoids_raw_nix_templates() -> None:
    """Production modules should build Nix syntax through nix-manipulator helpers."""
    violations: list[str] = []

    for path in sorted(_REPO_ROOT.rglob("*.py")):
        if any(part in _EXCLUDED_PARTS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        docstring_ids = _docstring_node_ids(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstring_ids:
                continue
            for fragment in _FORBIDDEN_NIX_TEMPLATE_FRAGMENTS:
                if fragment in node.value:
                    rel = path.relative_to(_REPO_ROOT)
                    violations.append(f"{rel}:{node.lineno}: {fragment}")

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "parse" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, (ast.Constant, ast.JoinedStr)):
                        rel = path.relative_to(_REPO_ROOT)
                        violations.append(
                            f"{rel}:{node.lineno}: parse() called on string literal",
                        )

                if isinstance(func, ast.Name) and func.id == "FunctionCall":
                    for keyword in node.keywords:
                        if keyword.arg != "name":
                            continue
                        value = keyword.value
                        if isinstance(value, ast.Constant) and isinstance(
                            value.value, str
                        ):
                            rel = path.relative_to(_REPO_ROOT)
                            violations.append(
                                f"{rel}:{node.lineno}: FunctionCall name uses raw string",
                            )

    check(violations == [], "\n".join(violations))
