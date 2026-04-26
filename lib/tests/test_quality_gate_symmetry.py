"""Keep quality gate names aligned across hooks, flake checks, and workflows."""

from __future__ import annotations

import re
import tomllib
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import Primitive
from nix_manipulator.expressions.set import AttributeSet

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression

from lib.check_python_compile import iter_target_paths
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import parse_nix_expr
from lib.update.paths import REPO_ROOT

_FAST_HOOKS = (
    "guard-merge-conflicts",
    "fix-end-of-file",
    "fix-trailing-whitespace",
    "format-python-pyupgrade",
    "format-python-ruff",
    "lint-python-compile",
    "format-web-biome",
    "format-yaml-yamlfmt",
    "lint-editorconfig",
    "lint-pins-pinact",
    "lint-python-ruff",
    "lint-python-ty",
    "lint-workflows-actionlint",
    "lint-yaml-yamllint",
)
_MANUAL_HOOKS = ("format-repo",)
_COMMIT_MSG_HOOKS = ("commit-message-commitlint",)
_SHARED_FLAKE_CHECKS = (
    "format-repo",
    "lint-editorconfig",
    "format-yaml-yamlfmt",
    "lint-yaml-yamllint",
    "format-web-biome",
    "format-python-pyupgrade",
    "format-python-ruff",
    "lint-python-compile",
    "lint-python-ruff",
    "lint-python-ty",
    "lint-workflows-actionlint",
    "test-nix-default-api",
    "test-nix-opencode-desktop-electron",
    "test-python-pytest",
    "verify-workflow-artifacts-refresh",
    "verify-workflow-artifacts-certify",
    "verify-workflow-structure-refresh",
    "verify-workflow-structure-certify",
)
_SINGLE_LINE_MULTI_EXCEPT_PATTERN = re.compile(
    r"^\s*except \([^)\n]*,[^)\n]*\):(?:\s*#.*)?$",
    re.MULTILINE,
)


def _read(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _hooks_body() -> str:
    text = _read(REPO_ROOT / "lib/dev-shell.nix")
    match = re.search(r"hooks = \{(?P<body>.*?)\n    \};\n  \};", text, re.DOTALL)
    if match is None:
        msg = "Could not isolate hooks block in lib/dev-shell.nix"
        raise AssertionError(msg)
    return match.group("body")


def _hook_block(attr_name: str) -> str:
    body = _hooks_body()
    pattern = re.compile(
        rf"^\s*{re.escape(attr_name)} = \{{(?P<body>.*?)^\s*\}};",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(body)
    if match is None:
        msg = f"Could not find hook block {attr_name!r}"
        raise AssertionError(msg)
    return match.group("body")


def _hook_names() -> tuple[str, ...]:
    return tuple(
        sorted(set(re.findall(r'^\s*name = "([^"]+)";$', _hooks_body(), re.MULTILINE)))
    )


def _flake_check_names() -> tuple[str, ...]:
    text = _read(REPO_ROOT / "flake.nix")
    return tuple(sorted(set(re.findall(r'checks\."([^"]+)"\s*=', text))))


def _flake_check_block(attr_name: str) -> str:
    text = _read(REPO_ROOT / "flake.nix")
    pattern = re.compile(
        rf'^\s*checks\."{re.escape(attr_name)}"\s*=\s*mkRepoCheck\s*\{{(?P<body>.*?)^\s*\}};',
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(text)
    if match is None:
        msg = f"Could not find flake check block {attr_name!r}"
        raise AssertionError(msg)
    return match.group("body")


def _ci_matrix_checks() -> tuple[str, ...]:
    payload = yaml.safe_load(_read(REPO_ROOT / ".github/workflows/ci.yml"))
    return tuple(payload["jobs"]["quality"]["strategy"]["matrix"]["check"])


def _certify_quality_checks() -> tuple[str, ...]:
    payload = yaml.safe_load(_read(REPO_ROOT / ".github/workflows/update-certify.yml"))
    runs = [
        step["run"]
        for step in payload["jobs"]["quality-gates"]["steps"]
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    ]
    joined = "\n".join(runs)
    return tuple(re.findall(r"\.\#checks\.x86_64-linux\.([A-Za-z0-9-]+)", joined))


def test_dev_shell_hook_names_are_grouped_and_complete() -> None:
    """Keep dev-shell hook IDs aligned with the declared grouped naming scheme."""
    assert _hook_names() == tuple(
        sorted(_FAST_HOOKS + _MANUAL_HOOKS + _COMMIT_MSG_HOOKS)
    )


def test_dev_shell_manual_and_commit_msg_hooks_stay_stage_scoped() -> None:
    """Keep the aggregate formatter manual-only and commitlint commit-msg-only."""
    assert 'stages = [ "manual" ];' in _hook_block("format-repo")
    assert 'stages = [ "commit-msg" ];' in _hook_block("commit-message-commitlint")


@cache
def _lint_files_expr() -> AttributeSet:
    """Parse ``lib/lint-files.nix`` once for literal-data extraction."""
    return expect_instance(
        parse_nix_expr(_read(REPO_ROOT / "lib/lint-files.nix")), AttributeSet
    )


@cache
def _lint_files() -> dict[str, object]:
    """Decode the simple let-bound literal surface without invoking Nix."""
    expr = _lint_files_expr()
    scope = {
        binding.name: binding.value
        for binding in expr.scope
        if isinstance(binding, Binding)
    }
    payload = _eval_lint_files_expr(expr, scope)
    assert isinstance(payload, dict)
    return payload


def _eval_lint_files_expr(
    expr: NixExpression, scope: dict[str, NixExpression]
) -> object:
    if isinstance(expr, Parenthesis):
        return _eval_lint_files_expr(expr.value, scope)
    if isinstance(expr, Primitive):
        return expr.value
    if isinstance(expr, Identifier):
        try:
            return _eval_lint_files_expr(scope[expr.name], scope)
        except KeyError as exc:
            msg = f"Unsupported identifier {expr.name!r} in lib/lint-files.nix"
            raise AssertionError(msg) from exc
    if isinstance(expr, NixList):
        return [_eval_lint_files_expr(item, scope) for item in expr.value]
    if isinstance(expr, BinaryExpression):
        assert expr.operator.name == "++"
        left = _eval_lint_files_expr(expr.left, scope)
        right = _eval_lint_files_expr(expr.right, scope)
        assert isinstance(left, list)
        assert isinstance(right, list)
        return [*left, *right]
    if isinstance(expr, AttributeSet):
        result: dict[str, object] = {}
        for value in expr.values:
            if isinstance(value, Inherit):
                inherited: dict[str, object] = {}
                if value.from_expression is not None:
                    source = _eval_lint_files_expr(value.from_expression, scope)
                    assert isinstance(source, dict)
                    inherited = source
                for name in value.names:
                    if value.from_expression is None:
                        result[name.name] = _eval_lint_files_expr(
                            scope[name.name], scope
                        )
                    else:
                        result[name.name] = inherited[name.name]
                continue
            binding = expect_instance(value, Binding)
            result[binding.name] = _eval_lint_files_expr(binding.value, scope)
        return result

    msg = f"Unsupported expression {type(expr).__name__} in lib/lint-files.nix"
    raise AssertionError(msg)


def _lint_files_python() -> dict[str, object]:
    python = _lint_files().get("python")
    assert isinstance(python, dict)
    return python


@cache
def _pyproject() -> dict[str, object]:
    payload = tomllib.loads(_read(REPO_ROOT / "pyproject.toml"))
    assert isinstance(payload, dict)
    return payload


def _relative_repo_path(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


@cache
def _repo_python_paths() -> tuple[Path, ...]:
    python = _lint_files_python()
    script_paths = python["pythonScriptPaths"]
    assert isinstance(script_paths, list)

    patterns = ("**/*.py", "**/*.pyi", *script_paths)
    return tuple(
        sorted(REPO_ROOT / path for path in iter_target_paths(patterns, root=REPO_ROOT))
    )


@cache
def _single_line_multi_except_files() -> set[str]:
    return {
        _relative_repo_path(path)
        for path in _repo_python_paths()
        if _SINGLE_LINE_MULTI_EXCEPT_PATTERN.search(_read(path))
    }


@cache
def _expand_python_paths(patterns: tuple[str, ...]) -> set[str]:
    return {path.as_posix() for path in iter_target_paths(patterns, root=REPO_ROOT)}


def test_flake_quality_check_names_match_shared_surface_names() -> None:
    """Expose the shared flake-friendly quality/test gate names directly as checks."""
    assert _flake_check_names() == tuple(sorted(_SHARED_FLAKE_CHECKS))


def test_ci_quality_matrix_matches_shared_check_names() -> None:
    """Keep CI quality matrix names identical to the shared flake check IDs."""
    assert _ci_matrix_checks() == _SHARED_FLAKE_CHECKS


def test_update_certify_quality_job_matches_shared_check_names() -> None:
    """Keep certification flake-check gates aligned with the CI matrix and flake checks."""
    assert _certify_quality_checks() == _SHARED_FLAKE_CHECKS


def test_pyproject_ruff_format_excludes_match_shared_python_runtime_sensitive_paths() -> (
    None
):
    """Keep direct Ruff runs aligned with the shared Python helper exclusions."""
    pyproject = _pyproject()
    excludes = pyproject["tool"]["ruff"]["format"]["exclude"]
    assert excludes == _lint_files_python()["ruffMutationExcludes"]


def test_pyproject_ruff_force_exclude_stays_enabled() -> None:
    """Keep direct file-based Ruff invocations from bypassing excluded helpers."""
    pyproject = _pyproject()
    assert pyproject["tool"]["ruff"]["force-exclude"] is True


def test_python_quality_surfaces_use_multi_except_normalizer() -> None:
    """Keep the pyupgrade surfaces aligned with the multi-except repair shim."""
    dev_shell = _read(REPO_ROOT / "lib/dev-shell.nix")
    flake_check = _flake_check_block("format-python-pyupgrade")
    assert "lib.fix_python_multi_except" in dev_shell
    assert "lib.fix_python_multi_except" in flake_check
    assert "--pyupgrade-exe ${lib.getExe pkgs.pyupgrade}" in flake_check
    assert "--pyupgrade-arg=--py313-plus" in flake_check
    assert (
        "| ${pkgs.findutils}/bin/xargs -0 -r ${lib.getExe pkgs.pyupgrade}"
        not in flake_check
    )


def test_python_compile_surfaces_use_shared_compile_helper() -> None:
    """Keep compile smoke checks routed through the shared helper script."""
    dev_shell = _read(REPO_ROOT / "lib/dev-shell.nix")
    flake = _read(REPO_ROOT / "flake.nix")
    assert "check_python_compile.py" in dev_shell
    assert "check_python_compile.py" in flake


def test_ruff_format_excludes_cover_vulnerable_single_line_multi_except_files() -> None:
    """Exclude every file Ruff can misformat into invalid multi-exception syntax."""
    python = _lint_files_python()
    excludes = python["ruffMutationExcludes"]
    assert isinstance(excludes, list)
    assert _single_line_multi_except_files() <= set(excludes)


def test_compile_paths_cover_vulnerable_single_line_multi_except_files() -> None:
    """Keep compile checks broad enough to catch invalid nested Python rewrites."""
    python = _lint_files_python()
    compile_paths = python["compilePaths"]
    assert isinstance(compile_paths, list)
    assert _single_line_multi_except_files() <= _expand_python_paths(
        tuple(pattern for pattern in compile_paths if isinstance(pattern, str))
    )
