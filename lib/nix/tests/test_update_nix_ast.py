"""Tests for nix-manipulator-backed Nix expression builders."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nix_manipulator import parse

if TYPE_CHECKING:
    import pytest

from lib.nix.models.flake_lock import FlakeLockNode, LockedRef
from lib.update.flake import flake_fetch_expr
from lib.update.nix import _build_nix_expr, _build_overlay_expr
from lib.update.sources import nix_source_names


def test_flake_fetch_expr_builds_parseable_fetch_tree() -> None:
    """flake_fetch_expr should emit valid fetchTree Nix."""
    node = FlakeLockNode(
        locked=LockedRef(
            type="github",
            owner="NixOS",
            repo="nixpkgs",
            rev="abc123",
            narHash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        ),
    )

    expr = flake_fetch_expr(node)

    parse(expr)
    assert "builtins.fetchTree" in expr  # noqa: S101
    assert 'owner = "NixOS";' in expr  # noqa: S101
    assert 'repo = "nixpkgs";' in expr  # noqa: S101


def test_build_nix_expr_wraps_body_with_pkgs_binding() -> None:
    """_build_nix_expr should construct a parseable let-expression."""
    expr = _build_nix_expr("pkgs.hello")

    parse(expr)
    assert expr.startswith("let pkgs = ")  # noqa: S101
    assert expr.endswith("in pkgs.hello")  # noqa: S101


def test_build_overlay_expr_supports_explicit_system() -> None:
    """_build_overlay_expr should produce parseable Nix for explicit systems."""
    expr = _build_overlay_expr("chatgpt", system="x86_64-linux")

    parse(expr)
    assert 'system = "x86_64-linux";' in expr  # noqa: S101
    assert 'in pkgs."chatgpt"' in expr  # noqa: S101


def test_nix_source_names_uses_parseable_ast_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nix_source_names should evaluate a valid expression generated via AST."""
    captured: dict[str, str] = {}

    class _Result:
        returncode = 0
        stdout = '["foo", "bar"]'
        stderr = ""

    def _fake_run(args: list[str], **_: object) -> _Result:
        captured["expr"] = args[-1]
        return _Result()

    monkeypatch.setattr("lib.update.sources.shutil.which", lambda _tool: "/usr/bin/nix")
    monkeypatch.setattr("lib.update.sources.subprocess.run", _fake_run)

    names = nix_source_names()

    assert names == {"foo", "bar"}  # noqa: S101
    parse(captured["expr"])
    assert "builtins.getFlake" in captured["expr"]  # noqa: S101
