"""ChatGPT Linux launch-check argv: Chromium sandbox cannot nest in nix-daemon."""

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import expect_binding, expect_platform_branch, parse_nix_expr
from lib.tests._shell_ast import (
    ParsedShell,
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
from lib.update.paths import get_repo_root


def _launch_check_source() -> str:
    text = (get_repo_root() / "tests/nix/chatgpt-linux-launch.nix").read_text(
        encoding="utf-8"
    )
    return text[text.index("{") :]


def _launch_script_shell() -> ParsedShell:
    root = expect_instance(parse_nix_expr(_launch_check_source()), FunctionDefinition)
    builder_call = expect_instance(root.output, FunctionCall)
    builder = expect_instance(builder_call.argument, IndentedString)
    outer = parse_shell(indented_string_body(builder.rebuild()))
    heredocs = list(iter_nodes(outer.tree.root_node, "heredoc_body"))
    assert len(heredocs) == 1
    return parse_shell(node_text(heredocs[0], outer.sanitized))


def test_chatgpt_linux_launch_check_disables_nested_chromium_sandbox() -> None:
    """Hosted Linux validate died on zygote 'No usable sandbox!' inside nix-daemon.

    38553752589 validate-arm built chatgpt-linux-launch-check and the Electron
    app SIGTRAPed before presenting a window. The user wrapper stays unchanged;
    only this Xvfb check may add the nested-sandbox flags.
    """
    shell = _launch_script_shell()
    launches = [
        command
        for command in command_texts(shell)
        if command.startswith(('"$CHATGPT_APP"', "$CHATGPT_APP"))
    ]
    assert len(launches) == 1
    argv = launches[0].split()
    assert "--no-sandbox" in argv
    assert "--disable-setuid-sandbox" in argv
    assert "--disable-gpu" in argv
    assert "--disable-dev-shm-usage" in argv


def test_chatgpt_user_wrapper_keeps_chromium_sandbox() -> None:
    """The user-facing Linux wrapper must not inherit the check-only flags."""
    source = (get_repo_root() / "packages/chatgpt/default.nix").read_text(
        encoding="utf-8"
    )
    package = expect_instance(parse_nix_expr(source), FunctionDefinition)
    platform_branch = expect_platform_branch(package.output)
    linux_call = expect_instance(platform_branch.alternative, FunctionCall)
    linux_args = expect_instance(linux_call.argument, AttributeSet)
    install = expect_instance(
        expect_binding(linux_args.values, "installPhase").value,
        IndentedString,
    )
    wrappers = [
        command
        for command in command_texts(
            parse_shell(indented_string_body(install.rebuild()))
        )
        if command.startswith(("makeWrapper", "wrapProgram"))
    ]
    assert len(wrappers) == 1
    argv = wrappers[0].split()
    assert "--no-sandbox" not in argv
    assert "--disable-setuid-sandbox" not in argv
