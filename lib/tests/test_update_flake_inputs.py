"""Behavior tests for locating and editing flake input declarations."""

import pytest
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.flake_inputs import has_input_ref, root_input_bindings, set_input_ref

_FLAKE = """{
  description = "demo";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    # Keep tags explicit for generic git inputs.
    github-desktop = {
      type = "git";
      url = "https://github.com/desktop/desktop.git";
      ref   =   "refs/tags/release-3.5.9-beta1";  # aligned on purpose
      submodules = true;
      flake = false;
    };
    plain = {
      url = "github:owner/plain/v1";
      flake = false;
    };
  };
  inputs.split = { type = "github"; owner = "o"; repo = "r"; ref = "v1"; };
  inputs.empty = { };

  outputs = { self, ... }: { };
}
"""


def _changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    assert len(before_lines) == len(after_lines)
    return [(a, b) for a, b in zip(before_lines, after_lines, strict=True) if a != b]


def _input(source: str, name: str) -> AttributeSet:
    root = expect_instance(parse_nix_expr(source), AttributeSet)
    nested = [
        binding.value
        for binding in root.values
        if getattr(binding, "name", None) == "inputs"
    ]
    inputs = expect_instance(nested[0], AttributeSet)
    return expect_instance(expect_binding(inputs.values, name).value, AttributeSet)


def test_set_input_ref_replaces_only_the_ref_string() -> None:
    """Comments, alignment, and sibling bindings survive a ref rewrite."""
    rewritten = set_input_ref(_FLAKE, "github-desktop", "refs/tags/release-3.5.9")

    assert _changed_lines(_FLAKE, rewritten) == [
        (
            '      ref   =   "refs/tags/release-3.5.9-beta1";  # aligned on purpose',
            '      ref   =   "refs/tags/release-3.5.9";  # aligned on purpose',
        )
    ]
    desktop = _input(rewritten, "github-desktop")
    assert_nix_ast_equal(expect_binding(desktop.values, "submodules").value, "true")


def test_set_input_ref_handles_split_root_declarations() -> None:
    """``inputs.<name> = { ... };`` is the same declaration as the nested form."""
    rewritten = set_input_ref(_FLAKE, "split", "v2")

    assert _changed_lines(_FLAKE, rewritten) == [
        (
            '  inputs.split = { type = "github"; owner = "o"; repo = "r"; ref = "v1"; };',
            '  inputs.split = { type = "github"; owner = "o"; repo = "r"; ref = "v2"; };',
        )
    ]


def test_set_input_ref_inserts_one_aligned_binding_when_absent() -> None:
    """An input without a ref gains exactly one binding at its own indentation."""
    rewritten = set_input_ref(_FLAKE, "plain", "v2")

    added = [line for line in rewritten.splitlines() if line not in _FLAKE.splitlines()]
    assert added == ['      ref = "v2";']
    assert len(rewritten.splitlines()) == len(_FLAKE.splitlines()) + 1
    assert_nix_ast_equal(
        expect_binding(_input(rewritten, "plain").values, "ref").value, '"v2"'
    )


def test_set_input_ref_inserts_into_an_empty_attrset() -> None:
    """An empty declaration still yields a parseable binding."""
    rewritten = set_input_ref(_FLAKE, "empty", "v3")

    assert '  inputs.empty = { ref = "v3"; };' in rewritten.splitlines()
    parse_nix_expr(rewritten)


def test_set_input_ref_escapes_nix_string_syntax() -> None:
    """Quotes and backslashes are rendered as a Nix string literal."""
    rewritten = set_input_ref(_FLAKE, "split", 'we"ird\\ref')

    split = expect_instance(
        expect_binding(
            expect_instance(parse_nix_expr(rewritten), AttributeSet).values,
            "inputs",
        ).value,
        AttributeSet,
    )
    assert split is not None
    assert 'ref = "we\\"ird\\\\ref";' in rewritten


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("github-desktop", True),
        ("split", True),
        ("plain", False),
        ("empty", False),
        ("nixpkgs", False),
        ("missing", False),
    ],
)
def test_has_input_ref_reports_only_attrset_declared_refs(
    name: str, expected: bool
) -> None:
    """URL-only and dotted-URL inputs have no ref binding to update."""
    assert has_input_ref(_FLAKE, name) is expected


def test_set_input_ref_rejects_inputs_without_an_attrset() -> None:
    """A dotted ``inputs.<name>.url`` or missing input cannot gain a ref here."""
    for name in ("nixpkgs", "missing"):
        with pytest.raises(RuntimeError, match="Could not find flake input attrset"):
            set_input_ref(_FLAKE, name, "v2")


def test_duplicate_input_declarations_are_ambiguous() -> None:
    """Two declarations of one input cannot be edited unambiguously."""
    source = '{ inputs = { a = { ref = "1"; }; }; inputs.a = { ref = "2"; }; }'
    with pytest.raises(ValueError, match="declared more than once"):
        set_input_ref(source, "a", "3")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("let x = 1; in { }", "literal root attribute set"),
        ("{ inherit x; }", "ambiguous"),
        ('{ ${"inputs"} = { }; }', "ambiguous"),
        ("{ inputs = { a = ; }; }", "literal root attribute set|Invalid input"),
    ],
)
def test_root_input_bindings_reject_non_literal_roots(
    source: str, message: str
) -> None:
    """Only a literal root attribute set has statically known inputs."""
    with pytest.raises(ValueError, match=message):
        root_input_bindings(source.encode())
