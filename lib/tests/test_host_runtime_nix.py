"""Semantic checks for low-overhead Darwin host runtime defaults."""

from functools import cache
from typing import TYPE_CHECKING

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import BooleanPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, binding_map, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression


@cache
def _module_output(relative_path: str) -> AttributeSet:
    module = expect_instance(nix_file_expr(relative_path), FunctionDefinition)
    return expect_instance(module.output, AttributeSet)


def _nested_attrset(root: AttributeSet, *names: str) -> AttributeSet:
    current = root
    for name in names:
        current = expect_instance(
            expect_binding(current.values, name).value,
            AttributeSet,
        )
    return current


def _option_default(options: AttributeSet, name: str) -> NixExpression:
    option = expect_instance(
        expect_binding(options.values, name).value,
        FunctionCall,
    )
    arguments = expect_instance(option.argument, AttributeSet)
    return expect_binding(arguments.values, "default").value


def _host_extra_system_modules(relative_path: str) -> NixList:
    host = expect_instance(nix_file_expr(relative_path), FunctionDefinition)
    constructor = expect_instance(host.output, FunctionCall)
    arguments = expect_instance(constructor.argument, AttributeSet)
    return expect_instance(
        expect_binding(arguments.values, "extraSystemModules").value,
        NixList,
    )


def _host_defers_zsh_completion(relative_path: str) -> bool:
    for item in _host_extra_system_modules(relative_path).value:
        if not isinstance(item, AttributeSet):
            continue
        darwin_defaults = binding_map(item.values).get("darwinDefaults")
        if darwin_defaults is None or not isinstance(
            darwin_defaults.value,
            AttributeSet,
        ):
            continue
        zsh = binding_map(darwin_defaults.value.values).get("zsh")
        if zsh is None or not isinstance(zsh.value, AttributeSet):
            continue
        deferral = binding_map(zsh.value.values).get("deferCompletionInitToHomeManager")
        if deferral is not None:
            return expect_instance(deferral.value, BooleanPrimitive).value
    return False


def test_zsh_completion_deferral_is_conservative_and_host_scoped() -> None:
    """Only hosts with the matching Home Manager module should defer compinit."""
    common_config = _nested_attrset(
        _module_output("modules/common.nix"),
        "config",
    )
    common_zsh = _nested_attrset(common_config, "programs", "zsh")
    common_bindings = binding_map(common_zsh.values)

    assert_nix_ast_equal(common_bindings["enable"].value, "true")
    assert "enableGlobalCompInit" not in common_bindings
    assert "enableBashCompletion" not in common_bindings

    darwin_module = _module_output("modules/darwin/base.nix")
    zsh_options = _nested_attrset(
        darwin_module,
        "options",
        "darwinDefaults",
        "zsh",
    )
    assert_nix_ast_equal(
        _option_default(zsh_options, "deferCompletionInitToHomeManager"),
        "false",
    )

    darwin_config = _nested_attrset(darwin_module, "config")
    deferral = expect_instance(
        expect_binding(
            _nested_attrset(darwin_config, "programs").values,
            "zsh",
        ).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        deferral.name,
        "lib.mkIf cfg.zsh.deferCompletionInitToHomeManager",
    )
    deferred_zsh = expect_instance(deferral.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(deferred_zsh.values, "enableGlobalCompInit").value,
        "false",
    )
    assert_nix_ast_equal(
        expect_binding(deferred_zsh.values, "enableBashCompletion").value,
        "false",
    )
    assert _host_defers_zsh_completion("darwin/argus.nix")
    assert _host_defers_zsh_completion("darwin/rocinante.nix")

    home_manager_module = _module_output("modules/home/zsh.nix")
    guarded_config = expect_instance(
        expect_binding(home_manager_module.values, "config").value,
        FunctionCall,
    )
    home_manager_zsh = _nested_attrset(
        expect_instance(guarded_config.argument, AttributeSet),
        "programs",
        "zsh",
    )
    completion_init = expect_binding(
        home_manager_zsh.values,
        "completionInit",
    ).value.rebuild()
    commands = command_texts(parse_shell(indented_string_body(completion_init)))

    assert commands[:3] == [
        "autoload -U compinit bashcompinit",
        "compinit",
        "bashcompinit",
    ]
    assert commands.count("compinit") == 1
    assert commands.count("bashcompinit") == 1


def test_homebrew_activation_defaults_are_idempotent() -> None:
    """Activation must reconcile declarations without refreshing or upgrading them."""
    module = _module_output("modules/darwin/base.nix")
    homebrew_options = _nested_attrset(
        module,
        "options",
        "darwinDefaults",
        "homebrew",
    )
    homebrew_config = _nested_attrset(module, "config", "homebrew")
    global_config = _nested_attrset(homebrew_config, "global")
    activation = _nested_attrset(homebrew_config, "onActivation")

    assert_nix_ast_equal(_option_default(homebrew_options, "autoUpdate"), "false")
    assert_nix_ast_equal(_option_default(homebrew_options, "upgrade"), "false")
    assert_nix_ast_equal(
        expect_binding(global_config.values, "autoUpdate").value,
        "cfg.homebrew.autoUpdate",
    )
    activation_inherit = expect_instance(activation.values[0], Inherit)
    assert activation_inherit.from_expression is not None
    assert_nix_ast_equal(activation_inherit.from_expression, "cfg.homebrew")
    assert {name.name for name in activation_inherit.names} == {
        "autoUpdate",
        "cleanup",
    }
    assert_nix_ast_equal(
        expect_binding(activation.values, "upgrade").value,
        "cfg.homebrew.upgrade",
    )
