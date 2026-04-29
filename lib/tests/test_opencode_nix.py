"""Regression checks for OpenCode profile layering."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.tests._nix_eval import (
    nix_attrset,
    nix_eval_json,
    nix_import,
    nix_let,
    nix_list,
)
from lib.tests._shell_ast import (
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
from lib.update.flake import nixpkgs_lib_expression
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT


@cache
def _opencode_module_output() -> AttributeSet:
    """Parse the OpenCode home-manager module once."""
    expr = expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "modules/home/opencode.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(expr.output, AttributeSet)


@cache
def _opencode_config_attrset() -> AttributeSet:
    """Return the ``config = mkIf ...`` attribute set for the OpenCode module."""
    config_call = expect_instance(
        expect_binding(_opencode_module_output().values, "config").value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        config_call.name,
        FunctionCall(
            name=Identifier(name="mkIf"),
            argument=identifier_attr_path("cfg", "enable"),
        ),
    )
    return expect_instance(config_call.argument, AttributeSet)


@cache
def _opencode_options_attrset() -> AttributeSet:
    """Return the ``options.nixcfg.opencode`` attrset for the OpenCode module."""
    options = expect_instance(
        expect_binding(_opencode_module_output().values, "options").value,
        AttributeSet,
    )
    nixcfg = expect_instance(
        expect_binding(options.values, "nixcfg").value,
        AttributeSet,
    )
    return expect_instance(
        expect_binding(nixcfg.values, "opencode").value, AttributeSet
    )


@cache
def _profiles_module_source() -> str:
    """Read the work profiles module once for targeted fragment parsing."""
    return Path(REPO_ROOT / "modules/home/profiles.nix").read_text(encoding="utf-8")


def _profiles_fragment_expr(start_marker: str, end_marker: str):
    source = _profiles_module_source()
    start = source.index(start_marker) + len(start_marker)
    end = source.index(end_marker, start)
    fragment = textwrap.dedent(source[start:end]).rstrip().removesuffix(";")
    return parse_nix_expr(fragment)


@cache
def _mcp_remote_wrapper_module() -> FunctionDefinition:
    """Return the shared MCP remote-wrapper helper function."""
    return expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "lib/mcp-remote-wrapper.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )


@cache
def _mk_mcp_remote_wrapper_expr() -> FunctionDefinition:
    return expect_instance(
        expect_scope_binding(
            _mcp_remote_wrapper_module().output, "mkMcpRemoteWrapper"
        ).value,
        FunctionDefinition,
    )


@cache
def _slack_mcp_wrapper_expr() -> FunctionCall:
    script = expect_instance(
        _profiles_fragment_expr(
            '      slack-mcp-wrapper = pkgs.writeShellScript "slack-mcp" ',
            "\n    in",
        ),
        IndentedString,
    )
    return FunctionCall(
        name=FunctionCall(
            name=identifier_attr_path("pkgs", "writeShellScript"),
            argument=StringPrimitive(value="slack-mcp"),
        ),
        argument=script,
    )


@cache
def _default_opencode_mcp_expr() -> BinaryExpression:
    source = _profiles_module_source()
    start = source.index("  defaultOpencodeMcp = ") + len("  defaultOpencodeMcp = ")
    end = source.index("in\n{", start)
    fragment = source[start:end].rstrip().removesuffix(";")
    return expect_instance(parse_nix_expr(fragment), BinaryExpression)


@cache
def _mk_mcp_remote_wrapper_shell():
    call = expect_instance(_mk_mcp_remote_wrapper_expr().output, FunctionCall)
    script = expect_instance(call.argument, IndentedString)
    return parse_shell(indented_string_body(script.rebuild()))


@cache
def _slack_mcp_wrapper_shell():
    script = expect_instance(_slack_mcp_wrapper_expr().argument, IndentedString)
    return parse_shell(indented_string_body(script.rebuild()))


def _mk_option(
    type_expr: FunctionCall | AttributeSet | Select, default: object
) -> FunctionCall:
    """Build one ``lib.mkOption`` call for the test eval harness."""
    return FunctionCall(
        name=identifier_attr_path("lib", "mkOption"),
        argument=nix_attrset({"type": type_expr, "default": default}),
    )


def _opencode_eval_options(
    *,
    extra_options: dict[str, object] | None = None,
) -> AttributeSet:
    """Build the shared evalModules option harness for OpenCode materialization."""
    anything_type = identifier_attr_path("lib", "types", "anything")
    return nix_attrset({
        "assertions": _mk_option(
            FunctionCall(
                name=identifier_attr_path("lib", "types", "listOf"),
                argument=anything_type,
            ),
            [],
        ),
        "theme.slug": _mk_option(
            identifier_attr_path("lib", "types", "str"),
            "catppuccin-frappe",
        ),
        "home.activation": _mk_option(
            FunctionCall(
                name=identifier_attr_path("lib", "types", "attrsOf"),
                argument=anything_type,
            ),
            {},
        ),
        "home.homeDirectory": _mk_option(
            identifier_attr_path("lib", "types", "str"),
            "/Users/test",
        ),
        "home.sessionVariables": _mk_option(
            FunctionCall(
                name=identifier_attr_path("lib", "types", "attrsOf"),
                argument=anything_type,
            ),
            {},
        ),
        "programs.opencode": _mk_option(
            FunctionCall(
                name=identifier_attr_path("lib", "types", "attrsOf"),
                argument=anything_type,
            ),
            {},
        ),
        "xdg.configFile": _mk_option(
            FunctionCall(
                name=identifier_attr_path("lib", "types", "attrsOf"),
                argument=anything_type,
            ),
            {},
        ),
        **(extra_options or {}),
    })


def _profile_json_text(
    result_config_file: Select,
    *,
    profile_name: str,
    unsafe_discard_string_context: bool,
):
    """Select the generated ``opencode/<profile>.json`` text payload."""
    text = Select(
        expression=Select(
            expression=result_config_file,
            attribute=f'"opencode/{profile_name}.json"',
        ),
        attribute="text",
    )
    if not unsafe_discard_string_context:
        return text
    return Parenthesis(
        value=FunctionCall(
            name=identifier_attr_path("builtins", "unsafeDiscardStringContext"),
            argument=text,
        )
    )


def _curried_lambda(arguments: tuple[str, ...], output: object) -> FunctionDefinition:
    """Build a curried Nix lambda from simple positional argument names."""
    expression = output
    for argument in reversed(arguments):
        expression = FunctionDefinition(
            argument_set=Identifier(name=argument),
            output=expression,
        )
    assert isinstance(expression, FunctionDefinition)
    return expression


@cache
def _stub_work_profile_pkgs(*, is_darwin: bool = True) -> AttributeSet:
    """Return the lightweight ``pkgs`` surface needed by ``modules/home/profiles.nix``."""
    return nix_attrset({
        "lib": {
            "getExe'": _curried_lambda(
                ("pkg", "exeName"),
                BinaryExpression(
                    left=BinaryExpression(
                        left=FunctionCall(
                            name=identifier_attr_path("builtins", "toString"),
                            argument=Identifier(name="pkg"),
                        ),
                        operator=Operator(name="+"),
                        right=StringPrimitive(value="/"),
                    ),
                    operator=Operator(name="+"),
                    right=Identifier(name="exeName"),
                ),
            )
        },
        "bun": "/nix/store/fake-bun",
        "stdenv.isDarwin": is_darwin,
        "writeShellScript": _curried_lambda(
            ("name", "text"),
            BinaryExpression(
                left=StringPrimitive(value="/nix/store/"),
                operator=Operator(name="+"),
                right=Identifier(name="name"),
            ),
        ),
        "_1password-cli": "/nix/store/1password-cli",
        "google-cloud-sdk": "/nix/store/google-cloud-sdk",
        "linear-cli": "/nix/store/linear-cli",
        "linearis": "/nix/store/linearis",
        "runCommand": _curried_lambda(
            ("name", "attrs", "script"),
            BinaryExpression(
                left=StringPrimitive(value="/nix/store/"),
                operator=Operator(name="+"),
                right=Identifier(name="name"),
            ),
        ),
    })


def _eval_opencode_value(
    profile_config: AttributeSet,
    value: object,
    *,
    extra_module_paths: tuple[Path, ...] = (),
    extra_options: dict[str, object] | None = None,
    extra_config: dict[str, object] | None = None,
    special_args: AttributeSet | None = None,
    pkgs_expr: AttributeSet | None = None,
) -> object:
    """Evaluate only the module semantics that AST checks cannot prove.

    This helper is intentionally reserved for sparse override merging and module
    type-validation behavior that depends on ``lib.evalModules`` rather than the
    source AST alone.
    """
    module_args: dict[str, object] = {
        "modules": nix_list([
            Parenthesis(value=nix_import(REPO_ROOT / "modules/home/opencode.nix")),
            *[Parenthesis(value=nix_import(path)) for path in extra_module_paths],
            nix_attrset({
                "options": _opencode_eval_options(extra_options=extra_options),
                "config.nixcfg.opencode": profile_config,
                **(extra_config or {}),
            }),
        ])
    }
    module_args["specialArgs"] = (
        special_args
        if special_args is not None
        else nix_attrset({"pkgs": Identifier(name="pkgs")})
    )

    bindings: dict[str, object] = {
        "lib": nixpkgs_lib_expression(),
        "result": FunctionCall(
            name=identifier_attr_path("lib", "evalModules"),
            argument=nix_attrset(module_args),
        ),
    }
    bindings["pkgs"] = pkgs_expr if pkgs_expr is not None else _stub_work_profile_pkgs()

    return nix_eval_json(nix_let(bindings, value))


def _eval_opencode_json(
    profile_config: AttributeSet,
    *,
    profile_name: str,
    extra_module_paths: tuple[Path, ...] = (),
    extra_options: dict[str, object] | None = None,
    extra_config: dict[str, object] | None = None,
    special_args: AttributeSet | None = None,
    pkgs_expr: AttributeSet | None = None,
    unsafe_discard_string_context: bool = False,
) -> dict[str, Any]:
    """Evaluate the module and return the parsed selected profile overlay."""
    result_config_file = identifier_attr_path("result", "config", "xdg", "configFile")
    payload = _eval_opencode_value(
        profile_config,
        FunctionCall(
            name=identifier_attr_path("builtins", "fromJSON"),
            argument=_profile_json_text(
                result_config_file,
                profile_name=profile_name,
                unsafe_discard_string_context=unsafe_discard_string_context,
            ),
        ),
        extra_module_paths=extra_module_paths,
        extra_options=extra_options,
        extra_config=extra_config,
        special_args=special_args,
        pkgs_expr=pkgs_expr,
    )
    assert isinstance(payload, dict)
    return payload


def _work_profile_eval_kwargs(
    *,
    profile_config: AttributeSet | None = None,
    work_config: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return the shared evalModules harness for the work OpenCode profile."""
    return {
        "profile_config": (
            profile_config
            if profile_config is not None
            else nix_attrset({"activeProfile": "work"})
        ),
        "extra_module_paths": (REPO_ROOT / "modules/home/profiles.nix",),
        "extra_options": {
            "home.homeDirectory": _mk_option(
                identifier_attr_path("lib", "types", "str"),
                "/Users/test",
            ),
            "home.packages": _mk_option(
                FunctionCall(
                    name=identifier_attr_path("lib", "types", "listOf"),
                    argument=identifier_attr_path("lib", "types", "anything"),
                ),
                [],
            ),
            "programs.topgrade.settings.misc.disable": _mk_option(
                FunctionCall(
                    name=identifier_attr_path("lib", "types", "listOf"),
                    argument=identifier_attr_path("lib", "types", "str"),
                ),
                [],
            ),
            "programs.zsh.plugins": _mk_option(
                FunctionCall(
                    name=identifier_attr_path("lib", "types", "listOf"),
                    argument=identifier_attr_path("lib", "types", "anything"),
                ),
                [],
            ),
            "sops.secrets.github_pat.path": _mk_option(
                identifier_attr_path("lib", "types", "str"),
                "/tmp/github_pat",
            ),
        },
        "extra_config": {
            "config.profiles.work": {
                "enable": True,
                "enableOnePasswordZshPlugin": False,
                "packages": [],
                **(work_config or {}),
            }
        },
        "special_args": nix_attrset({"pkgs": Identifier(name="pkgs")}),
        "pkgs_expr": _stub_work_profile_pkgs(),
    }


def _eval_work_profile_json(
    *,
    profile_config: AttributeSet | None = None,
    work_config: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Evaluate the selected ``work.json`` overlay with the work profile module enabled."""
    return _eval_opencode_json(
        profile_name="work",
        unsafe_discard_string_context=True,
        **_work_profile_eval_kwargs(
            profile_config=profile_config,
            work_config=work_config,
        ),
    )


def test_program_settings_keep_shared_base_config_in_the_module_ast() -> None:
    """The base ``programs.opencode.settings`` binding should stay shared."""
    config = _opencode_config_attrset()
    programs = expect_instance(
        expect_binding(config.values, "programs").value,
        AttributeSet,
    )
    opencode = expect_instance(
        expect_binding(programs.values, "opencode").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(opencode.values, "settings").value,
        BinaryExpression(
            left=Identifier(name="baseOpencodeSettings"),
            operator=Operator(name="//"),
            right=nix_attrset({
                "mcp": FunctionCall(
                    name=Identifier(name="renderMcpServers"),
                    argument=identifier_attr_path("cfg", "mcpServers"),
                )
            }),
        ),
    )


def test_default_chrome_devtools_mcp_command_uses_npx() -> None:
    """The shared Chrome DevTools MCP definition should launch through npx."""
    options = _opencode_options_attrset()
    mcp_servers = expect_instance(
        expect_binding(options.values, "mcpServers").value,
        FunctionCall,
    )
    defaults = expect_instance(mcp_servers.argument, AttributeSet)
    default_servers = expect_instance(
        expect_binding(defaults.values, "default").value,
        AttributeSet,
    )
    chrome_devtools = expect_instance(
        expect_binding(default_servers.values, "chrome-devtools").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(chrome_devtools.values, "command").value,
        nix_list([
            "npx",
            "-y",
            "chrome-devtools-mcp@latest",
            "--autoConnect",
            "--channel=stable",
        ]),
    )


def test_selected_profile_materializes_only_one_overlay_file() -> None:
    """The module should materialize exactly ``opencode/${cfg.activeProfile}.json``."""
    xdg = expect_instance(
        expect_binding(_opencode_config_attrset().values, "xdg").value,
        AttributeSet,
    )
    config_file = expect_instance(
        expect_binding(xdg.values, "configFile").value,
        AttributeSet,
    )
    profile_json = expect_instance(
        expect_binding(
            config_file.values, '"opencode/${cfg.activeProfile}.json"'
        ).value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(profile_json.values, "text").value,
        "builtins.toJSON (mkProfileOverlayConfig selectedProfileConfig)",
    )


def test_work_profile_materializes_only_one_overlay_file() -> None:
    """The work profile module should select the shared dynamic profile path."""
    source = _profiles_module_source()
    start = source.index("    nixcfg.opencode = {\n") + len("    nixcfg.opencode = ")
    end = source.index("    };\n", start) + len("    }")
    opencode = expect_instance(parse_nix_expr(source[start:end]), AttributeSet)

    assert_nix_ast_equal(
        expect_binding(opencode.values, "activeProfile").value,
        'lib.mkDefault "work"',
    )
    profiles = expect_instance(
        expect_binding(opencode.values, "profiles").value, AttributeSet
    )
    work = expect_instance(expect_binding(profiles.values, "work").value, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(work.values, "mcpServers").value,
        "cfg.opencodeMcp",
    )


def test_selected_profile_sets_shell_env_var_to_the_same_profile_path() -> None:
    """Shell sessions should export the shared selected profile path binding."""
    home = expect_instance(
        expect_binding(_opencode_config_attrset().values, "home").value,
        AttributeSet,
    )
    session_variables = expect_instance(
        expect_binding(home.values, "sessionVariables").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_scope_binding(_opencode_module_output(), "selectedProfilePath").value,
        '"${config.home.homeDirectory}/.config/opencode/${cfg.activeProfile}.json"',
    )
    assert_nix_ast_equal(
        expect_binding(session_variables.values, "OPENCODE_CONFIG").value,
        "selectedProfilePath",
    )


def test_selected_profile_json_is_a_thin_overlay_when_no_overrides_exist() -> None:
    """An empty profile should still render only the schema and optional MCP overrides."""
    assert_nix_ast_equal(
        expect_scope_binding(_opencode_module_output(), "emptyProfile").value,
        """
        {
          settings = { };
          mcpServers = { };
        }
        """,
    )

    assert (
        str(
            expect_scope_binding(
                _opencode_module_output(), "mkProfileOverlayConfig"
            ).value
        )
        == """profile:
profile.settings
// {
  \"$schema\" = profile.settings.\"$schema\" or \"https://opencode.ai/config.json\";
}
// optionalAttrs (profile.mcpServers != { }) {
  mcp = renderSparseMcpServerOverrides profile.mcpServers;
}"""
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_selected_profile_json_contains_only_profile_overrides() -> None:
    """Profile overlays should omit shared base config and keep only overrides."""
    profile = _eval_opencode_json(
        nix_attrset({
            "activeProfile": "personal",
            "profiles.personal": {
                "settings.model": "anthropic/claude-sonnet-4-5",
                "mcpServers.macos-automator.enable": True,
            },
        }),
        profile_name="personal",
    )

    assert profile["$schema"] == "https://opencode.ai/config.json"
    assert profile["model"] == "anthropic/claude-sonnet-4-5"
    assert "tui" not in profile
    assert "plugin" not in profile
    assert profile["mcp"] == {"macos-automator": {"enabled": True}}


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_selected_profile_json_preserves_freeform_mcp_extras() -> None:
    """Profile-only MCP additions should keep extras and stay disabled by default."""
    profile = _eval_opencode_json(
        nix_attrset({
            "activeProfile": "personal",
            "profiles.personal.mcpServers.notion": {
                "type": "remote",
                "url": "https://mcp.notion.com/mcp",
                "oauth": {},
            },
        }),
        profile_name="personal",
    )

    assert profile["mcp"]["notion"] == {
        "enabled": False,
        "oauth": {},
        "type": "remote",
        "url": "https://mcp.notion.com/mcp",
    }


def test_work_profile_darwin_only_wrappers_stay_platform_guarded() -> None:
    """Darwin Keychain-backed wrappers should stay behind ``pkgs.stdenv.isDarwin``."""
    optional_attrs = expect_instance(_default_opencode_mcp_expr().right, FunctionCall)
    assert_nix_ast_equal(optional_attrs.name, "lib.optionalAttrs pkgs.stdenv.isDarwin")

    remote_wrapper_commands = command_texts(_mk_mcp_remote_wrapper_shell())
    assert "set -euo pipefail" in remote_wrapper_commands
    assert '[ -z "$token" ]' in command_texts(_mk_mcp_remote_wrapper_shell())
    assert any("mcp-remote@latest" in text for text in remote_wrapper_commands)

    slack_wrapper_commands = command_texts(_slack_mcp_wrapper_shell())
    assert "set -euo pipefail" in slack_wrapper_commands
    assert 'export SLACK_MCP_XOXP_TOKEN="$token"' in slack_wrapper_commands
    assert "export SLACK_MCP_ADD_MESSAGE_TOOL=true" in slack_wrapper_commands
    assert any(
        "slack-mcp-server@latest --transport stdio" in text
        for text in slack_wrapper_commands
    )


def test_work_profile_wrapper_scripts_fail_fast_on_missing_tokens() -> None:
    """Remote MCP wrappers should stop immediately when credential lookup fails."""
    remote_shell = _mk_mcp_remote_wrapper_shell()
    slack_shell = _slack_mcp_wrapper_shell()
    remote_wrapper_commands = command_texts(remote_shell)
    slack_wrapper_commands = command_texts(slack_shell)
    remote_assignments = [
        node_text(node, remote_shell.sanitized)
        for node in iter_nodes(remote_shell.tree.root_node, "variable_assignment")
    ]
    slack_assignments = [
        node_text(node, slack_shell.sanitized)
        for node in iter_nodes(slack_shell.tree.root_node, "variable_assignment")
    ]
    remote_redirects = [
        node_text(node, remote_shell.sanitized)
        for node in iter_nodes(remote_shell.tree.root_node, "redirected_statement")
    ]
    slack_redirects = [
        node_text(node, slack_shell.sanitized)
        for node in iter_nodes(slack_shell.tree.root_node, "redirected_statement")
    ]

    assert 'token="$(__NIX_INTERP__)"' in remote_assignments
    assert '[ -z "$token" ]' in remote_wrapper_commands
    assert "echo __NIX_INTERP__ >&2" in remote_redirects
    assert "exit 1" in remote_wrapper_commands

    assert (
        'token="$(security find-generic-password -s "slack-mcp-token" -a "$USER" -w)"'
        in slack_assignments
    )
    assert '[ -z "$token" ]' in slack_wrapper_commands
    assert 'echo "slack-mcp: token lookup returned empty output" >&2' in slack_redirects
    assert "exit 1" in slack_wrapper_commands


def test_darwin_hosts_select_the_materialized_opencode_profile_files() -> None:
    """Host launchd wiring should point at the profile files the module materializes."""
    argus = expect_instance(
        parse_nix_expr((REPO_ROOT / "darwin/argus.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    rocinante = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "darwin/rocinante.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )

    assert_nix_ast_equal(
        expect_instance(argus.output, FunctionCall).argument,
        """
        {
          user = "george";
          work = true;
          rosettaBuilderMemory = "16GiB";
          brewAppsModule = "${lib.modulesPath}/darwin/george/brew-apps.nix";
          extraHomeModules = [
            "${lib.modulesPath}/home/darwin-closure-priority.nix"
            (
              { pkgs, ... }:
              {
                nixcfg.packageSets.extraPackages = [
                  pkgs.goose-cli
                  pkgs.gws
                ];
              }
            )
          ];
          extraSystemModules = [
            {
              home-manager.backupFileExtension = "backup";
            }
            "${lib.modulesPath}/darwin/george/town-dock-apps.nix"
            (lib.mkSetOpencodeEnvModule "work.json")
          ];
        }
        """,
    )
    assert_nix_ast_equal(
        expect_instance(rocinante.output, FunctionCall).argument,
        """
        {
          user = "george";
          brewAppsModule = "${lib.modulesPath}/darwin/george/brew-apps.nix";
          extraHomeModules = [
            "${lib.modulesPath}/home/darwin-closure-priority.nix"
          ];
          extraSystemModules = [
            {
              home-manager.backupFileExtension = "backup";
            }
            "${lib.modulesPath}/darwin/george/dock-apps.nix"
            (lib.mkSetOpencodeEnvModule "personal.json")
          ];
        }
        """,
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_work_profile_overlay_keeps_shared_defaults_in_global_config() -> None:
    """The work defaults should add work MCPs without shadowing shared global ones."""
    defaults = expect_instance(_default_opencode_mcp_expr().left, AttributeSet)
    axiom = expect_instance(
        expect_binding(defaults.values, "axiom").value, AttributeSet
    )
    convex = expect_instance(
        expect_binding(defaults.values, "convex").value, AttributeSet
    )

    assert_nix_ast_equal(
        axiom,
        '{ type = "remote"; url = "https://mcp.axiom.co/mcp"; }',
    )
    assert_nix_ast_equal(
        convex,
        """
        {
          type = "local";
          command = [
            "bunx"
            "--bun"
            "convex@latest"
            "mcp"
            "start"
          ];
        }
        """,
    )

    assert "chrome-devtools" not in {
        binding.name for binding in defaults.values if isinstance(binding, Binding)
    }


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_work_profile_sparse_mcp_overrides_stay_sparse() -> None:
    """Work MCP override sparsity depends on module merge semantics, so keep a tiny eval."""
    profile = _eval_work_profile_json(
        work_config={"opencodeMcp.chrome-devtools.enable": True}
    )

    assert profile["mcp"]["chrome-devtools"] == {"enabled": True}


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_profile_mcp_override_invalid_shape_is_rejected() -> None:
    """Type errors come from ``evalModules`` validation, so this stays as a minimal eval."""
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        _eval_opencode_json(
            nix_attrset({
                "activeProfile": "personal",
                "profiles.personal.mcpServers.chrome-devtools.command": "bunx",
            }),
            profile_name="personal",
        )

    assert excinfo.value.stderr is not None
    assert (
        "nixcfg.opencode.profiles.personal.mcpServers.chrome-devtools"
        in excinfo.value.stderr
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_work_profile_mcp_override_invalid_shape_is_rejected() -> None:
    """Type errors come from ``evalModules`` validation, so this stays as a minimal eval."""
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        _eval_work_profile_json(
            work_config={"opencodeMcp.chrome-devtools.command": "bunx"}
        )

    assert excinfo.value.stderr is not None
    assert "profiles.work.opencodeMcp.chrome-devtools" in excinfo.value.stderr
