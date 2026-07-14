"""Policy audits for package and overlay source modifications."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from nix_manipulator import parse

from lib.check_python_compile import iter_target_paths
from lib.codemods.errors import CodemodError
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

    from nix_manipulator.expressions.expression import NixExpression

type NixSubstituteSite = tuple[str, int, str]
type PythonRewriteSite = tuple[str, int, int, int, int, str]

SUBSTITUTE_IN_PLACE_PATTERN: Final = re.compile(r"\bsubstituteInPlace\b")
PYTHON_AD_HOC_REWRITE_ATTRS: Final = frozenset({"replace", "sub", "subn"})
SOURCE_PATCH_SCRIPT_NAMES: Final = frozenset(
    {
        "normalize_cargo_nix.py",
        "patch_allocator_weak_linkage.py",
        "patch_node_addon_api.py",
        "patch_node_addon_api_binding_gyp.py",
        "patch_source.py",
        "patch_sources.py",
    },
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
    if current:  # pragma: no branch -- final block is always non-empty here
        blocks.append(current)

    if (
        not blocks
    ):  # pragma: no cover -- defensive; scanner only runs on non-empty blocks
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


def parse_nix_expr_for_policy(source: str, *, context: str) -> NixExpression:
    """Parse a Nix expression for source-modification policy audits."""
    parsed = parse(source)
    if parsed.contains_error:
        parsed = parse(_rewrite_function_formals_for_parser(source))
    if parsed.contains_error or parsed.expr is None:
        msg = f"Unable to parse Nix source for policy audit: {context}"
        raise CodemodError(msg)
    return parsed.expr


@dataclass(frozen=True)
class NixSubstituteAudit:
    """Audit existing Nix shell-level source rewrites."""

    allowed_sites: tuple[NixSubstituteSite, ...]
    pattern: re.Pattern[str] = SUBSTITUTE_IN_PLACE_PATTERN
    roots: tuple[Path, ...] = (REPO_ROOT / "packages", REPO_ROOT / "overlays")

    def current_sites(self) -> tuple[NixSubstituteSite, ...]:
        """Return all Nix substituteInPlace sites under the configured roots."""
        return tuple(site for path in self._files() for site in self._sites_for(path))

    def _files(self) -> tuple[Path, ...]:
        return tuple(
            sorted(
                path
                for root in self.roots
                for path in root.rglob("*.nix")
                if self.pattern.search(path.read_text(encoding="utf-8"))
            ),
        )

    def _sites_for(self, path: Path) -> tuple[NixSubstituteSite, ...]:
        expr = parse_nix_expr_for_policy(
            path.read_text(encoding="utf-8"),
            context=self._relative_path(path),
        )
        lines = expr.rebuild().splitlines()
        return tuple(
            (self._relative_path(path), index + 1, self._command_from(lines, index))
            for index, line in enumerate(lines)
            if self.pattern.search(line)
        )

    @staticmethod
    def _command_from(lines: list[str], start: int) -> str:
        command_parts: list[str] = []
        for line in lines[start:]:
            stripped = line.strip()
            command_parts.append(stripped.removesuffix("\\").strip())
            if not stripped.endswith("\\"):
                break
        return " ".join(command_parts)

    @staticmethod
    def _relative_path(path: Path) -> str:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


@dataclass(frozen=True)
class PythonRewriteAudit:
    """Audit existing Python source-mutating ad hoc rewrites."""

    allowed_sites: tuple[PythonRewriteSite, ...]
    patch_script_names: frozenset[str] = SOURCE_PATCH_SCRIPT_NAMES
    rewrite_attrs: frozenset[str] = PYTHON_AD_HOC_REWRITE_ATTRS
    target_patterns: tuple[str, ...] = ("packages/**/*.py", "overlays/**/*.py")

    def current_sites(self) -> tuple[PythonRewriteSite, ...]:
        """Return all Python ad hoc source-rewrite call sites."""
        return tuple(
            site
            for path in sorted(iter_target_paths(self.target_patterns, root=REPO_ROOT))
            for site in self._sites_for(path)
        )

    def _sites_for(self, path: Path) -> tuple[PythonRewriteSite, ...]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if path.name in self.patch_script_names:
            return self._patch_script_sites(path, tree)
        return tuple(
            sorted(
                {
                    site
                    for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                    for site in self._function_sites(path, node)
                },
            ),
        )

    def _patch_script_sites(
        self,
        path: Path,
        tree: ast.AST,
    ) -> tuple[PythonRewriteSite, ...]:
        relative_path = self._relative_path(path)
        return tuple(
            sorted(
                (relative_path, line, column, end_line, end_column, name)
                for line, column, end_line, end_column, name in self._call_sites(tree)
            ),
        )

    def _function_sites(
        self,
        path: Path,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> tuple[PythonRewriteSite, ...]:
        relative_path = self._relative_path(path)
        assigned_rewrites: dict[str, list[tuple[int, int, int, int, str]]] = {}
        inline_write_sites: list[PythonRewriteSite] = []
        written_names: set[str] = set()

        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                self._record_assignment(node.value, node.targets, assigned_rewrites)
            elif isinstance(node, ast.AnnAssign):
                self._record_assignment(node.value, (node.target,), assigned_rewrites)
            elif isinstance(node, ast.Call) and self._is_write_text_call(node):
                inline_write_sites.extend(
                    (relative_path, line, column, end_line, end_column, name)
                    for line, column, end_line, end_column, name in self._call_sites(
                        node,
                    )
                )
                if payload_name := self._write_text_payload_name(node):
                    written_names.add(payload_name)

        assigned_write_sites = [
            (relative_path, line, column, end_line, end_column, name)
            for variable in sorted(written_names & assigned_rewrites.keys())
            for line, column, end_line, end_column, name in assigned_rewrites[variable]
        ]
        return tuple(sorted({*inline_write_sites, *assigned_write_sites}))

    def _call_name(self, node: ast.Call) -> str | None:
        match node.func:
            case ast.Attribute(attr=attr) if attr in self.rewrite_attrs:
                return attr
            case _:
                return None

    def _call_sites(self, node: ast.AST) -> tuple[tuple[int, int, int, int, str], ...]:
        sites: list[tuple[int, int, int, int, str]] = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = self._call_name(child)
            if name is None:
                continue
            sites.append(
                (
                    child.lineno,
                    child.col_offset,
                    child.end_lineno or child.lineno,
                    child.end_col_offset or child.col_offset,
                    name,
                ),
            )
        return tuple(sites)

    def _record_assignment(
        self,
        value: ast.expr | None,
        targets: tuple[ast.expr, ...] | list[ast.expr],
        assigned_rewrites: dict[str, list[tuple[int, int, int, int, str]]],
    ) -> None:
        if value is None:
            return
        rewrite_sites = self._call_sites(value)
        if not rewrite_sites:
            return
        for target in targets:
            for name in self._assigned_names(target):
                assigned_rewrites.setdefault(name, []).extend(rewrite_sites)

    @staticmethod
    def _assigned_names(target: ast.expr) -> tuple[str, ...]:
        match target:
            case ast.Name(id=name):
                return (name,)
            case ast.Tuple(elts=elts) | ast.List(elts=elts):
                return tuple(
                    name
                    for element in elts
                    for name in PythonRewriteAudit._assigned_names(element)
                )
            case _:
                return ()

    @staticmethod
    def _is_write_text_call(node: ast.Call) -> bool:
        return isinstance(node.func, ast.Attribute) and node.func.attr == "write_text"

    @staticmethod
    def _relative_path(path: Path) -> str:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()

    @staticmethod
    def _write_text_payload_name(node: ast.Call) -> str | None:
        match node.func:
            case ast.Attribute(attr="write_text") if node.args:
                match node.args[0]:
                    case ast.Name(id=name):
                        return name
                    case _:
                        return None
            case _:
                return None


__all__ = [
    "NixSubstituteAudit",
    "NixSubstituteSite",
    "PythonRewriteAudit",
    "PythonRewriteSite",
    "parse_nix_expr_for_policy",
]
