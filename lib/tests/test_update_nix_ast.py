"""Tests for nix-manipulator-backed Nix expression builders."""

from __future__ import annotations

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.flake_lock import FlakeLockNode, LockedRef
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.update.flake import (
    flake_fetch_expr,
    flake_fetch_expression,
    nixpkgs_expression,
)
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_fetch_from_github_expr,
    _build_fetch_yarn_deps_expr,
    _build_flake_attr_expr,
    _build_nix_expr,
    _build_overlay_attr_expr,
    _build_overlay_expr,
    _build_overlay_expression,
)
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT
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

    assert_nix_ast_equal(expr, flake_fetch_expression(node))


def test_build_nix_expr_wraps_body_with_pkgs_binding() -> None:
    """_build_nix_expr should construct a parseable let-expression."""
    expr = _build_nix_expr("pkgs.hello")

    assert_nix_ast_equal(
        expr,
        LetExpression(
            local_variables=[Binding(name="pkgs", value=nixpkgs_expression())],
            value=identifier_attr_path("pkgs", "hello"),
        ),
    )


def test_build_overlay_expr_supports_explicit_system() -> None:
    """_build_overlay_expr should produce parseable Nix for explicit systems."""
    expr = _build_overlay_expr("chatgpt", system="x86_64-linux")

    assert_nix_ast_equal(
        expr, _build_overlay_expression("chatgpt", system="x86_64-linux")
    )


def test_build_fetch_from_github_expr_is_parseable() -> None:
    """FetchFromGitHub helper should emit valid Nix via nix-manipulator."""
    expr = _build_fetch_from_github_expr(
        "element-hq",
        "element-desktop",
        rev="v1.11.0",
    )

    assert_nix_ast_equal(
        expr,
        _build_fetch_from_github_call(
            "element-hq",
            "element-desktop",
            rev="v1.11.0",
        ),
    )


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

    assert_nix_ast_equal(
        expr,
        _build_fetch_from_github_call(
            "getsentry",
            "sentry-cli",
            tag="v2.0.0",
            hash_value=StringPrimitive(
                value="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            ),
            post_fetch="rm -rf $out/*.xcarchive",
        ),
    )


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

    assert_nix_ast_equal(
        expr,
        LetExpression(
            local_variables=[
                Binding(
                    name="flake",
                    value=FunctionCall(
                        name=identifier_attr_path("builtins", "getFlake"),
                        argument=StringPrimitive(value="path:/tmp/repo"),
                    ),
                ),
            ],
            value=identifier_attr_path(
                "flake",
                "pkgs",
                '"x86_64-linux"',
                "deno",
                "version",
            ),
        ),
    )


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

    assert_nix_ast_equal(
        expr,
        LetExpression(
            local_variables=[
                Binding(
                    name="src",
                    value=_build_fetch_from_github_call(
                        "element-hq",
                        "element-desktop",
                        rev="v1.11.0",
                        hash_value="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    ),
                ),
            ],
            value=FunctionCall(
                name=identifier_attr_path("pkgs", "fetchYarnDeps"),
                argument=AttributeSet(
                    values=[
                        Binding(
                            name="yarnLock",
                            value=BinaryExpression(
                                left=identifier_attr_path("src"),
                                operator=Operator(name="+"),
                                right=StringPrimitive(value="/yarn.lock"),
                            ),
                        ),
                        Binding(
                            name="hash",
                            value=identifier_attr_path("pkgs", "lib", "fakeHash"),
                        ),
                    ],
                ),
            ),
        ),
    )


def test_build_overlay_attr_expr_wraps_selection_target() -> None:
    """Overlay attribute path helper should select attrs via the parsed AST."""
    expr = _build_overlay_attr_expr(
        "gemini-cli",
        ".node_modules",
        system="x86_64-linux",
    )

    assert_nix_ast_equal(
        expr,
        Select(
            expression=Parenthesis(
                value=_build_overlay_expression("gemini-cli", system="x86_64-linux"),
            ),
            attribute="node_modules",
        ),
    )


def test_build_overlay_attr_expr_skips_empty_attr_segments() -> None:
    """Overlay attr helper should tolerate redundant dots in attribute paths."""
    expr = _build_overlay_attr_expr(
        "gemini-cli",
        ".passthru..denoDeps",
        system="x86_64-linux",
    )

    assert_nix_ast_equal(
        expr,
        Select(
            expression=Select(
                expression=Parenthesis(
                    value=_build_overlay_expression(
                        "gemini-cli",
                        system="x86_64-linux",
                    ),
                ),
                attribute="passthru",
            ),
            attribute="denoDeps",
        ),
    )


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

    assert names == {"foo", "bar"}
    assert_nix_ast_equal(
        captured["expr"],
        LetExpression(
            local_variables=[
                Binding(
                    name="flake",
                    value=FunctionCall(
                        name=identifier_attr_path("builtins", "getFlake"),
                        argument=StringPrimitive(
                            value=f"git+file://{REPO_ROOT}?dirty=1"
                        ),
                    ),
                ),
            ],
            value=FunctionCall(
                name=identifier_attr_path("builtins", "attrNames"),
                argument=identifier_attr_path("flake", "outputs", "lib", "sources"),
            ),
        ),
    )
