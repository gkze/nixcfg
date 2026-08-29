"""Structural regression tests for George's SSH configuration."""

from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr


def test_george_ssh_settings_preserve_config_contract() -> None:
    """The profile should use Home Manager's current host-keyed SSH settings."""
    module = expect_instance(
        nix_file_expr("home/george/configuration.nix"),
        FunctionDefinition,
    )
    root = expect_instance(module.output, AttributeSet)
    programs = expect_instance(
        expect_binding(root.values, "programs").value,
        AttributeSet,
    )
    ssh = expect_instance(
        expect_binding(programs.values, "ssh").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        ssh,
        """
{
  enable = true;
  enableDefaultConfig = false;
  settings = {
    "github.com" = {
      Compression = true;
      ForwardAgent = true;
      HashKnownHosts = true;
      User = "git";
    };
    "*" = {
      AddKeysToAgent = "yes";
      LogLevel = "ERROR";
      StrictHostKeyChecking = "accept-new";
    };
  };
}
""",
    )
