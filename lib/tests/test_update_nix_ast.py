"""Tests for nix-manipulator-backed Nix expression builders."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nix_manipulator import parse

from lib.tests._assertions import check

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
    check("builtins.fetchTree" in expr)
    check('owner = "NixOS";' in expr)
    check('repo = "nixpkgs";' in expr)


def test_build_nix_expr_wraps_body_with_pkgs_binding() -> None:
    """_build_nix_expr should construct a parseable let-expression."""
    expr = _build_nix_expr("pkgs.hello")

    parse(expr)
    check(expr.startswith("let pkgs = "))
    check(expr.endswith("in pkgs.hello"))


def test_build_overlay_expr_supports_explicit_system() -> None:
    """_build_overlay_expr should produce parseable Nix for explicit systems."""
    expr = _build_overlay_expr("chatgpt", system="x86_64-linux")

    parse(expr)
    check('system = "x86_64-linux";' in expr)
    check('in applied."chatgpt"' in expr)


def test_nix_source_names_uses_parseable_ast_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nix_source_names should evaluate a valid expression generated via AST."""
    captured: dict[str, str] = {}

    async def _fake_run_nix(args: list[str], **_: object) -> object:
        captured["expr"] = args[-1]

        class _Result:
            returncode = 0
            stdout = '["foo", "bar"]'
            stderr = ""

        return _Result()

    monkeypatch.setattr("lib.update.sources.shutil.which", lambda _tool: "/usr/bin/nix")
    monkeypatch.setattr("lib.update.sources.run_nix", _fake_run_nix)

    names = nix_source_names()

    check(names == {"foo", "bar"})
    parse(captured["expr"])
    check("builtins.getFlake" in captured["expr"])
