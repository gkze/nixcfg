"""Contracts for the OpenAI CLI package."""

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr


def _override_attrs() -> AttributeSet:
    """Return the `overrideAttrs` payload that carries the package policy."""
    package = expect_instance(
        nix_file_expr("packages/openai-cli/default.nix"),
        FunctionDefinition,
    )
    override_call = expect_instance(package.output, FunctionCall)
    argument = override_call.argument
    override = expect_instance(
        argument.value if isinstance(argument, Parenthesis) else argument,
        FunctionDefinition,
    )
    return expect_instance(override.output, AttributeSet)


def test_openai_cli_skips_upstream_checks_only_for_pinned_versions() -> None:
    """v1.38.0 and v1.39.0 checkPhase fail in upstream tests after the build succeeds."""
    assert_nix_ast_equal(
        expect_binding(_override_attrs().values, "doCheck").value,
        'old.version != "1.38.0" && old.version != "1.39.0"',
    )


def test_openai_cli_check_support_covers_native_darwin_tests() -> None:
    """The PTY and metadata tests need the tools and patches the sandbox lacks."""
    attrs = _override_attrs()
    assert_nix_ast_equal(
        expect_binding(attrs.values, "nativeCheckInputs").value,
        """
        (old.nativeCheckInputs or [ ]) ++ [
          python3
          bashInteractive
          procps
        ]
        """,
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "patches").value,
        """
        (old.patches or [ ]) ++ [
          ./darwin-provenance.patch
          ./darwin-native-test-tools.patch
        ]
        """,
    )


def test_openai_cli_runs_the_full_autocomplete_suite_off_darwin() -> None:
    """Only Darwin narrows the autocomplete suite to the sandbox-safe checks."""
    assert_nix_ast_equal(
        expect_binding(_override_attrs().values, "postCheck").value,
        """
        (old.postCheck or "")
        + (
          if stdenv.hostPlatform.isDarwin then
            ''
              go test -p "$NIX_BUILD_CORES" -v ./internal/autocomplete -run '^TestPicker(Darwin.*|InstallPreservesDarwinFileMetadata|InstallRejectsInheritedScriptACL|InstallRejectsWritableScriptDirectoryACL|InstallAllowsBenignProfileParentACL|InstallRejectsWritableDirectoryACL)$'
            ''
          else
            ''
              go test -p "$NIX_BUILD_CORES" -v ./internal/autocomplete
            ''
        )
        """,
    )
