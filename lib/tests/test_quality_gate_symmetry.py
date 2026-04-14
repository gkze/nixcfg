"""Keep quality gate names aligned across hooks, flake checks, and workflows."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from lib.update.paths import REPO_ROOT

_FAST_HOOKS = (
    "guard-merge-conflicts",
    "fix-end-of-file",
    "fix-trailing-whitespace",
    "format-python-ruff",
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
    "format-python-ruff",
    "lint-python-ruff",
    "lint-python-ty",
    "lint-workflows-actionlint",
    "test-python-pytest",
    "verify-workflow-artifacts-refresh",
    "verify-workflow-artifacts-certify",
    "verify-workflow-structure-refresh",
    "verify-workflow-structure-certify",
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


def _workflow_body(path: str) -> str:
    return _read(REPO_ROOT / path)


def test_flake_quality_check_names_match_shared_surface_names() -> None:
    """Expose the shared flake-friendly quality/test gate names directly as checks."""
    assert _flake_check_names() == tuple(sorted(_SHARED_FLAKE_CHECKS))


def test_ci_quality_matrix_matches_shared_check_names() -> None:
    """Keep CI quality matrix names identical to the shared flake check IDs."""
    assert _ci_matrix_checks() == _SHARED_FLAKE_CHECKS


def test_update_certify_quality_job_matches_shared_check_names() -> None:
    """Keep certification flake-check gates aligned with the CI matrix and flake checks."""
    assert _certify_quality_checks() == _SHARED_FLAKE_CHECKS


def test_ci_runs_runner_only_pinact_and_crate2nix_verification_explicitly() -> None:
    """Keep pinact and crate2nix verification outside sandboxed flake checks in CI."""
    workflow = _workflow_body(".github/workflows/ci.yml")
    assert "nix run --inputs-from . nixpkgs#pinact -- run --check" in workflow
    assert "nix run .#nixcfg -- ci pipeline crate2nix" in workflow


def test_update_certify_runs_runner_only_pinact_and_crate2nix_verification_explicitly() -> (
    None
):
    """Keep certification pinact and crate2nix verification outside flake checks."""
    workflow = _workflow_body(".github/workflows/update-certify.yml")
    assert "nix run --inputs-from . nixpkgs#pinact -- run --check" in workflow
    assert "nix run .#nixcfg -- ci pipeline crate2nix" in workflow
