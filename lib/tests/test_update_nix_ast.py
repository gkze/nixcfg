"""Tests for nix-manipulator-backed Nix expression builders."""

from __future__ import annotations

import pytest
from nix_manipulator import parse
from nix_manipulator.expressions.primitive import StringPrimitive

from lib.nix.models.flake_lock import FlakeLockNode, LockedRef
from lib.tests._assertions import check
from lib.update.flake import flake_fetch_expr
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_fetch_from_github_expr,
    _build_fetch_yarn_deps_expr,
    _build_flake_attr_expr,
    _build_nix_expr,
    _build_overlay_attr_expr,
    _build_overlay_expr,
)
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


def test_build_fetch_from_github_expr_is_parseable() -> None:
    """FetchFromGitHub helper should emit valid Nix via nix-manipulator."""
    expr = _build_fetch_from_github_expr(
        "element-hq",
        "element-desktop",
        rev="v1.11.0",
    )

    parse(expr)
    check("pkgs.fetchFromGitHub" in expr)
    check('repo = "element-desktop";' in expr)
    check('rev = "v1.11.0";' in expr)


def test_build_fetch_from_github_expr_supports_tag_post_fetch_and_expr_hash() -> None:
    """FetchFromGitHub helper should handle non-default optional fields."""
    expr = _build_fetch_from_github_expr(
        "getsentry",
        "sentry-cli",
        tag="v2.0.0",
        hash_value=StringPrimitive(
            value="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        ),
        post_fetch="rm -rf $out/*.xcarchive",
    )

    parse(expr)
    check('tag = "v2.0.0";' in expr)
    check('hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";' in expr)
    check('postFetch = "rm -rf $out/*.xcarchive";' in expr)


def test_build_fetch_from_github_call_requires_exactly_one_selector() -> None:
    """Exactly one of rev or tag must be provided."""
    with pytest.raises(ValueError, match="Expected exactly one of rev or tag"):
        _build_fetch_from_github_call("element-hq", "element-desktop")

    with pytest.raises(ValueError, match="Expected exactly one of rev or tag"):
        _build_fetch_from_github_call(
            "element-hq",
            "element-desktop",
            rev="v1.11.0",
            tag="v1.11.0",
        )


def test_build_flake_attr_expr_quotes_dynamic_segments() -> None:
    """Quoted attribute selections should remain parseable for hyphenated keys."""
    expr = _build_flake_attr_expr(
        "path:/tmp/repo",
        "pkgs",
        "x86_64-linux",
        "deno",
        "version",
        quoted_indices=(1,),
    )

    parse(expr)
    check('flake.pkgs."x86_64-linux".deno.version' in expr)


def test_build_fetch_yarn_deps_expr_is_parseable() -> None:
    """FetchYarnDeps helper should build the yarnLock path via the Nix AST."""
    expr = _build_fetch_yarn_deps_expr(
        _build_fetch_from_github_call(
            "element-hq",
            "element-desktop",
            rev="v1.11.0",
            hash_value="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )
    )

    parse(expr)
    check("pkgs.fetchYarnDeps" in expr)
    check('yarnLock = src + "/yarn.lock";' in expr)


def test_build_overlay_attr_expr_wraps_selection_target() -> None:
    """Overlay attribute path helper should select attrs via the parsed AST."""
    expr = _build_overlay_attr_expr(
        "gemini-cli",
        ".node_modules",
        system="x86_64-linux",
    )

    parse(expr)
    check('system = "x86_64-linux";' in expr)
    check('in applied."gemini-cli").node_modules' in expr)


def test_build_overlay_attr_expr_skips_empty_attr_segments() -> None:
    """Overlay attr helper should tolerate redundant dots in attribute paths."""
    expr = _build_overlay_attr_expr(
        "gemini-cli",
        ".passthru..denoDeps",
        system="x86_64-linux",
    )

    parse(expr)
    check(".passthru.denoDeps" in expr)


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
