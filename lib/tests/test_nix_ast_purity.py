"""Purity regressions for semantic Nix AST comparisons."""

import subprocess

import pytest
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (
            """''
  runHook preBuild
  cmake --build . --config Release
  runHook postBuild
''""",
            """''
        runHook preBuild
        cmake --build . --config Release
        runHook postBuild
      ''""",
        ),
        (
            "constraint: system: if constraint == null then true else false",
            """constraint: system:
            if constraint == null then
              true
            else
              false""",
        ),
        (
            '{ a, b }: let x = { foo = "bar"; }; in assert x.foo == "bar"; x',
            """
            {
              a,
              b,
            }:
            let
              x = { foo = "bar"; };
            in
            assert x.foo == "bar";
            x
            """,
        ),
        (
            """''
              values: ${
                lib.concatStringsSep " " values
              }
            ''""",
            """''
              values: ${lib.concatStringsSep " " values}
            ''""",
        ),
        ("''''", "''\n''"),
        ("''\n    hello\n''", "''\n      hello\n    ''"),
        ("''\n  \n''", "''\n\n''"),
        (
            """''
  foo ${
"x"
}
''""",
            """''
  foo ${
    "x"
  }
''""",
        ),
    ],
    ids=(
        "indented-string",
        "function-layout",
        "multiline-function-formals",
        "interpolation-layout",
        "empty-indented-string",
        "closing-delimiter-layout",
        "all-space-line",
        "interpolation-interior-layout",
    ),
)
def test_ast_equality_ignores_formatting_without_external_nix(
    monkeypatch: pytest.MonkeyPatch,
    actual: str,
    expected: str,
) -> None:
    """Formatting-only differences should compare without an external parser."""

    def _unexpected_subprocess(*_args: object, **_kwargs: object) -> None:
        pytest.fail("semantic AST equality invoked an external subprocess")

    monkeypatch.setattr(subprocess, "run", _unexpected_subprocess)

    assert_nix_ast_equal(actual, expected)


def test_ast_equality_resolves_unqualified_and_scoped_inherits() -> None:
    """Treat inherited attributes as their equivalent explicit bindings."""
    assert_nix_ast_equal(
        "{ inherit local; inherit (source) version; }",
        "{ local = local; version = source.version; }",
    )


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("''\n  hello\n''", "''\n  hello world\n''"),
        ("''\n    \n  hello\n''", "''\n\nhello\n''"),
        ("''''", "''\t''"),
        ("''\r\n  hello\r\n''", "''\r\n    hello\r\n''"),
        ("''\r  hello\r''", "''\r    hello\r''"),
        ("''\n  ${left}\n''", "''\n  ${right}\n''"),
    ],
    ids=(
        "literal-content",
        "blank-line-indentation",
        "literal-tab",
        "crlf-content",
        "carriage-return-content",
        "interpolation-expression",
    ),
)
def test_ast_equality_preserves_indented_string_semantics(
    actual: str,
    expected: str,
) -> None:
    """Indent normalization must not erase meaningful string differences."""
    with pytest.raises(AssertionError, match="semantically equivalent Nix ASTs"):
        assert_nix_ast_equal(actual, expected)


def test_ast_equality_ignores_redundant_nested_parentheses() -> None:
    """Any number of grouping-only parentheses should be semantically transparent."""
    assert_nix_ast_equal("((1))", "1")


def test_expect_binding_resolves_unqualified_and_scoped_inherits() -> None:
    """Canonical Nix inherits should expose the same semantic values as bindings."""
    attrs = expect_instance(
        parse_nix_expr("{ inherit local; inherit (source) version; }"),
        AttributeSet,
    )

    assert_nix_ast_equal(expect_binding(attrs.values, "local").value, "local")
    assert_nix_ast_equal(
        expect_binding(attrs.values, "version").value,
        "source.version",
    )
