"""The GitButler frontend build must not execute a patchelf'd Node runtime."""

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import expect_binding
from lib.tests._nix_source import nix_file_binding_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell


def test_linux_frontend_build_replaces_the_downloaded_node_runtime() -> None:
    """Native addons are patched, then scripts run on the nixpkgs Node."""
    fix = expect_instance(
        nix_file_binding_expr(
            "packages/gitbutler/default.nix",
            "linuxFrontendNodeFix",
        ),
        IndentedString,
    )
    shell = parse_shell(indented_string_body(fix.rebuild()))
    commands = command_texts(shell)

    assert commands[0] == "autoPatchelf node_modules"
    assert any(
        command.startswith("find node_modules -path ")
        and "*/node@runtime+*/node_modules/node/bin/node" in command
        and "-type f" in command
        and "-type l" in command
        for command in commands
    )
    assert 'ln -s "$(type -P node)" "$node_bin"' in commands
    assert commands.index("autoPatchelf node_modules") < commands.index(
        'ln -s "$(type -P node)" "$node_bin"'
    )

    frontend = expect_instance(
        nix_file_binding_expr("packages/gitbutler/default.nix", "frontend"),
        FunctionCall,
    )
    build_phase = expect_instance(
        expect_binding(
            expect_instance(frontend.argument, AttributeSet).values,
            "buildPhase",
        ).value,
        IndentedString,
    )
    assert "linuxFrontendNodeFix" in build_phase.rebuild()
