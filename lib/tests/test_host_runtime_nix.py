"""Semantic checks for low-overhead Darwin host runtime defaults."""

from functools import cache
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, binding_map, expect_binding
from lib.tests._nix_eval import nix_attrset, nix_eval_json, nix_import
from lib.tests._nix_source import nix_file_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.update.paths import REPO_ROOT

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


@cache
def _host_arguments(host: str) -> dict[str, object]:
    """Return the ``mkDarwinHost`` arguments that *host* composes from profiles.

    Callers carry the ``nix_eval`` marker: the profile fold has no AST
    representation, and the stub constructor keeps the evaluation tiny.
    """
    arguments = nix_eval_json(
        FunctionCall(
            name=nix_import(REPO_ROOT / "tests/nix/darwin-host-arguments.nix"),
            argument=nix_attrset({"host": host}),
        )
    )
    return expect_instance(arguments, dict)


def _host_defers_zsh_completion(host: str) -> bool:
    modules = expect_instance(_host_arguments(host)["extraSystemModules"], list)
    return any(
        isinstance(module, dict)
        and module
        .get("darwinDefaults", {})
        .get("zsh", {})
        .get("deferCompletionInitToHomeManager")
        is True
        for module in modules
    )


@pytest.mark.nix_eval(
    reason="Host files fold profiles into constructor arguments; only evaluation "
    "shows the composed result."
)
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
    assert _host_defers_zsh_completion("argus")
    assert _host_defers_zsh_completion("rocinante")
    assert _host_defers_zsh_completion("zeus")

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


_GEORGE_BASE_SYSTEM_MODULES = [
    "./darwin/george/caches.nix",
    {
        "darwinDefaults": {
            "appearance": "dark",
            "zsh": {"deferCompletionInitToHomeManager": True},
        },
        "home-manager": {"backupFileExtension": "backup"},
    },
]
_TOWN_HOME_MODULES = [
    "<modulesPath>/home/darwin-closure-priority.nix",
    "./home/george/work.nix",
    "./darwin/george/town-dock-apps.nix",
    "<function>",
]


@pytest.mark.nix_eval(
    reason="Host files fold profiles into constructor arguments; only evaluation "
    "shows the composed result."
)
def test_zeus_preserves_the_town_workstation_contract() -> None:
    """Zeus should keep the complete work policy and its user-facing identity."""
    assert _host_arguments("zeus") == {
        "user": "george",
        "rosettaBuilderMemory": "16GiB",
        "rosettaBuilderLingerMinutes": 30,
        "brewAppsModule": "./darwin/george/brew-apps.nix",
        "extraHomeModules": _TOWN_HOME_MODULES,
        "extraSystemModules": [
            *_GEORGE_BASE_SYSTEM_MODULES,
            "./darwin/george/work.nix",
            {"networking": {"computerName": "Zeus"}},
        ],
    }


@pytest.mark.nix_eval(
    reason="Host files fold profiles into constructor arguments; only evaluation "
    "shows the composed result."
)
def test_argus_is_a_town_workstation_with_the_rocinante_builder() -> None:
    """Argus adds rocinante as substituter and build machine, and drops Spotlight."""
    assert _host_arguments("argus") == {
        **_host_arguments("zeus"),
        "extraSystemModules": [
            *_GEORGE_BASE_SYSTEM_MODULES,
            "./darwin/george/work.nix",
            "<function>",
            {
                "nix": {
                    "buildMachines": [
                        {
                            "hostName": "rocinante",
                            "maxJobs": 8,
                            "protocol": "ssh-ng",
                            "speedFactor": 2,
                            "sshKey": "/Users/george/.ssh/rocinante-nix-substituter",
                            "sshUser": "george",
                            "supportedFeatures": ["benchmark", "big-parallel"],
                            "systems": ["aarch64-darwin"],
                        }
                    ],
                    "settings": {"max-jobs": 0, "trusted-users": ["george"]},
                }
            },
            {"darwinDefaults": {"spotlight": {"indexing": False}}},
        ],
    }


@pytest.mark.nix_eval(
    reason="Host files fold profiles into constructor arguments; only evaluation "
    "shows the composed result."
)
def test_rocinante_uses_only_the_george_base_profile() -> None:
    """Rocinante should get the personal Dock and none of the Town policy."""
    assert _host_arguments("rocinante") == {
        "user": "george",
        "rosettaBuilderLingerMinutes": 30,
        "brewAppsModule": "./darwin/george/brew-apps.nix",
        "extraHomeModules": [
            "<modulesPath>/home/darwin-closure-priority.nix",
            "./darwin/george/dock-apps.nix",
        ],
        "extraSystemModules": _GEORGE_BASE_SYSTEM_MODULES,
    }


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
