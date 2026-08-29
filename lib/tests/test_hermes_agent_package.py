"""Semantic checks for the Darwin Hermes Agent package adaptation."""

from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import StringPrimitive

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr


def test_hermes_darwin_extras_match_the_supported_upstream_groups() -> None:
    """Request only the current non-voice integration groups on Darwin."""
    package = expect_instance(
        nix_file_expr("packages/hermes-agent/default.nix"),
        FunctionDefinition,
    )
    platform_choice = expect_instance(package.output, IfExpression)
    darwin_package = platform_choice.alternative
    extras = expect_instance(
        expect_binding(darwin_package.scope, "darwinExtras").value,
        NixList,
    )

    assert [expect_instance(item, StringPrimitive).value for item in extras.value] == [
        "acp",
        "bedrock",
        "daytona",
        "dingtalk",
        "feishu",
        "google",
        "homeassistant",
        "honcho",
        "mcp",
        "modal",
        "slack",
        "sms",
        "tts-premium",
        "web",
        "youtube",
    ]


def test_hermes_darwin_venv_stamps_authoritative_nix_provenance() -> None:
    """Legacy user-home stamps must not override the immutable code owner."""
    package = expect_instance(
        nix_file_expr("packages/hermes-agent/default.nix"),
        FunctionDefinition,
    )
    platform_choice = expect_instance(package.output, IfExpression)
    scope = platform_choice.alternative.scope

    assert_nix_ast_equal(
        expect_binding(scope, "hermesPythonPackage").value,
        """lib.findFirst
          (dependency:
            lib.hasSuffix
              "-hermes-agent-${upstreamPackage.version}"
              (baseNameOf dependency))
          (throw "Hermes Python package missing from the generated virtualenv dependency set")
          (lib.splitString ":" hermesVenvBase.NIX_PYPROJECT_DEPS)""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "nixManagedHermesPythonPackage").value,
        """symlinkJoin {
          name = "hermes-agent-nix-managed-python";
          paths = [ hermesPythonPackage ];
          postBuild = ''
            printf '%s\\n' nix > "$out/${python312ForHermes.sitePackages}/.install_method"
            for entrypoint in "$out"/bin/*; do
              if test -L "$entrypoint"; then
                entrypointTarget=$(readlink "$entrypoint")
                unlink "$entrypoint"
                cp "$entrypointTarget" "$entrypoint"
              fi
            done
          '';
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "hermesVenv").value,
        """hermesVenvBase.overrideAttrs (old: {
          NIX_PYPROJECT_DEPS = lib.concatStringsSep ":" (
            map
              (dependency:
                if dependency == hermesPythonPackage then
                  toString nixManagedHermesPythonPackage
                else
                  dependency)
              (lib.splitString ":" old.NIX_PYPROJECT_DEPS)
          );
        })""",
    )
