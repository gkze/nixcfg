"""Semantic package contracts for Buzz's unresolved Mesh source inputs."""

from dataclasses import fields, is_dataclass
from functools import cache
from typing import TYPE_CHECKING

from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression
    from nix_manipulator.expressions.scope import Scope

_PACKAGE_PATH = REPO_ROOT / "packages/buzz/package.nix"


@cache
def _package_scope() -> Scope:
    package = expect_instance(
        parse_nix_expr(_PACKAGE_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    asserted = expect_instance(package.output, Assertion)
    return asserted.body.scope


def _identifier_names(expression: object) -> set[str]:
    """Collect semantic identifiers without depending on rendered Nix text."""
    names: set[str] = set()
    pending = [expression]
    seen: set[int] = set()
    ignored_fields = {"after", "before", "scope", "scope_state"}
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, Identifier):
            names.add(current.name)
        if isinstance(current, (list, tuple)):
            pending.extend(current)
        elif is_dataclass(current):
            pending.extend(
                getattr(current, field.name)
                for field in fields(current)
                if field.name not in ignored_fields
            )
    return names


def _unresolved_gates() -> list[NixExpression]:
    gates: list[NixExpression] = []
    pending = [expect_binding(_package_scope(), "unresolvedBuildGates").value]
    while pending:
        gate = pending.pop(0)
        if isinstance(gate, BinaryExpression) and gate.operator.name == "++":
            pending[0:0] = [gate.left, gate.right]
        else:
            gates.append(gate)
    return gates


def _gate_for(identifier: str) -> NixExpression:
    matching = [
        gate for gate in _unresolved_gates() if identifier in _identifier_names(gate)
    ]
    assert len(matching) == 1, (
        f"expected one gate for {identifier}, got {len(matching)}"
    )
    return matching[0]


def _inherited_names(attribute_set: AttributeSet) -> set[str]:
    return {
        expect_instance(name, Identifier).name
        for value in attribute_set.values
        if isinstance(value, Inherit) and value.from_expression is None
        for name in value.names
    }


def test_mesh_source_hash_selector_is_url_qualified_and_conditional() -> None:
    """Missing Mesh URL metadata must remain a gate instead of an eval error."""
    selector = expect_binding(_package_scope(), "meshLlmSrcHashEntry").value

    assert_nix_ast_equal(
        selector,
        """if source.urls ? meshLlm then
          hashEntryFor "srcHash" source.urls.meshLlm
        else
          null""",
    )


def test_llama_source_hash_selector_is_url_qualified_and_conditional() -> None:
    """Missing llama URL metadata must remain a gate instead of an eval error."""
    selector = expect_binding(_package_scope(), "llamaCppSrcHashEntry").value

    assert_nix_ast_equal(
        selector,
        """if source.urls ? llamaCpp then
          hashEntryFor "srcHash" source.urls.llamaCpp
        else
          null""",
    )


def test_missing_mesh_source_hash_has_an_exact_unresolved_gate() -> None:
    """A missing promoted Mesh hash must keep the package fail-closed."""
    assert_nix_ast_equal(
        _gate_for("meshLlmSrcHashEntry"),
        "lib.optional (meshLlmSrcHashEntry == null) "
        '"Mesh-LLM ${expectedMeshLlmVersion} srcHash is missing"',
    )


def test_missing_llama_source_hash_has_an_exact_unresolved_gate() -> None:
    """A missing promoted llama hash must keep the package fail-closed."""
    assert_nix_ast_equal(
        _gate_for("llamaCppSrcHashEntry"),
        "lib.optional (llamaCppSrcHashEntry == null) "
        '"llama.cpp ${expectedLlamaCppCommit} srcHash is missing"',
    )


def test_mesh_source_hash_entries_are_exposed_for_future_native_builders() -> None:
    """Passthru should expose audited entries without fabricating fallback hashes."""
    passthru = expect_instance(
        expect_binding(_package_scope(), "commonPassthru").value,
        AttributeSet,
    )

    assert {
        "meshLlmSrcHashEntry",
        "llamaCppSrcHashEntry",
    } <= _inherited_names(passthru)


def test_mesh_and_llama_native_builders_are_hash_gated_and_repo_owned() -> None:
    """Only URL-qualified hashes may unlock the repo-owned source builders."""
    scope = _package_scope()
    assert_nix_ast_equal(
        expect_binding(scope, "meshLlmNative").value,
        """if meshLlmSrcHashEntry == null then
          null
        else
          import ./native/mesh-llm.nix {
            inherit fetchFromGitHub lib python3 stdenvNoCC;
            srcHash = meshLlmSrcHashEntry.hash;
          }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "llamaCppNative").value,
        """if llamaCppSrcHashEntry == null || meshLlmSrcHashEntry == null then
          null
        else
          import ./native/llama-cpp.nix {
            inherit cctools fetchFromGitHub lib stdenv;
            inherit (pkgs) cmake gitMinimal ninja;
            meshSrcHash = meshLlmSrcHashEntry.hash;
            srcHash = llamaCppSrcHashEntry.hash;
          }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "meshRuntimeBundleNative").value,
        """if meshLlmSrcHashEntry == null || llamaCppSrcHashEntry == null then
          null
        else
          import ./native/mesh-runtime-bundle.nix {
            inherit cctools fetchFromGitHub lib python3 stdenv stdenvNoCC;
            inherit (pkgs) cmake gitMinimal ninja;
            meshLlmSrcHash = meshLlmSrcHashEntry.hash;
            llamaCppSrcHash = llamaCppSrcHashEntry.hash;
          }""",
    )

    slots = expect_instance(
        expect_binding(scope, "nativeFoundationSlots").value,
        AttributeSet,
    )
    assert_nix_ast_equal(expect_binding(slots.values, "meshLlm").value, "meshLlmNative")
    assert_nix_ast_equal(
        expect_binding(slots.values, "llamaCpp").value, "llamaCppNative"
    )
    assert_nix_ast_equal(
        expect_binding(slots.values, "meshRuntimeBundle").value,
        "meshRuntimeBundleNative",
    )
    assert_nix_ast_equal(
        expect_binding(slots.values, "patchedBuzzSource").value,
        "buzzRuntimePolicySource",
    )
