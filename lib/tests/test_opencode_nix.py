"""Regression checks for OpenCode profile layering."""

from __future__ import annotations

import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._nix_eval import (
    nix_attrset,
    nix_eval_json,
    nix_import,
    nix_let,
    nix_list,
)
from lib.update.flake import nixpkgs_expression
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


def _eval_opencode_json(
    profile_config: AttributeSet,
    *,
    profile_name: str,
    extra_module_paths: tuple[Path, ...] = (),
    extra_options: dict[str, object] | None = None,
    extra_config: dict[str, object] | None = None,
    special_args: AttributeSet | None = None,
    unsafe_discard_string_context: bool = False,
) -> dict[str, Any]:
    """Evaluate the module and return the parsed selected profile overlay."""
    result_config_file = identifier_attr_path("result", "config", "xdg", "configFile")
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
    if special_args is not None:
        module_args["specialArgs"] = special_args

    expression = nix_let(
        {
            "pkgs": nixpkgs_expression(),
            "lib": identifier_attr_path("pkgs", "lib"),
            "result": FunctionCall(
                name=identifier_attr_path("lib", "evalModules"),
                argument=nix_attrset(module_args),
            ),
        },
        FunctionCall(
            name=identifier_attr_path("builtins", "fromJSON"),
            argument=_profile_json_text(
                result_config_file,
                profile_name=profile_name,
                unsafe_discard_string_context=unsafe_discard_string_context,
            ),
        ),
    )
    payload = nix_eval_json(expression)
    assert isinstance(payload, dict)
    return payload


def _eval_config_file_names(
    profile_config: AttributeSet,
    *,
    extra_module_paths: tuple[Path, ...] = (),
    extra_options: dict[str, object] | None = None,
    extra_config: dict[str, object] | None = None,
    special_args: AttributeSet | None = None,
) -> list[str]:
    """Evaluate the module and return materialized OpenCode config file names."""
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
    if special_args is not None:
        module_args["specialArgs"] = special_args

    expression = nix_let(
        {
            "pkgs": nixpkgs_expression(),
            "lib": identifier_attr_path("pkgs", "lib"),
            "result": FunctionCall(
                name=identifier_attr_path("lib", "evalModules"),
                argument=nix_attrset(module_args),
            ),
        },
        FunctionCall(
            name=identifier_attr_path("builtins", "attrNames"),
            argument=identifier_attr_path("result", "config", "xdg", "configFile"),
        ),
    )
    payload = nix_eval_json(expression)
    assert isinstance(payload, list)
    assert all(isinstance(item, str) for item in payload)
    return payload


def _eval_session_variables(
    profile_config: AttributeSet,
    *,
    extra_module_paths: tuple[Path, ...] = (),
    extra_options: dict[str, object] | None = None,
    extra_config: dict[str, object] | None = None,
    special_args: AttributeSet | None = None,
) -> dict[str, Any]:
    """Evaluate the module and return the resolved home session variables."""
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
    if special_args is not None:
        module_args["specialArgs"] = special_args

    expression = nix_let(
        {
            "pkgs": nixpkgs_expression(),
            "lib": identifier_attr_path("pkgs", "lib"),
            "result": FunctionCall(
                name=identifier_attr_path("lib", "evalModules"),
                argument=nix_attrset(module_args),
            ),
        },
        identifier_attr_path("result", "config", "home", "sessionVariables"),
    )
    payload = nix_eval_json(expression)
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


def _eval_work_config_file_names(
    *,
    profile_config: AttributeSet | None = None,
    work_config: dict[str, object] | None = None,
) -> list[str]:
    """Evaluate and return the materialized config files for the work profile."""
    return _eval_config_file_names(
        **_work_profile_eval_kwargs(
            profile_config=profile_config,
            work_config=work_config,
        )
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


def test_default_chrome_devtools_mcp_command_uses_bunx() -> None:
    """The shared Chrome DevTools MCP definition should launch through bunx."""
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
            "bunx",
            "--bun",
            "chrome-devtools-mcp@latest",
            "--autoConnect",
            "--channel=stable",
        ]),
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_selected_profile_materializes_only_one_overlay_file() -> None:
    """Only the active profile overlay should be materialized under ``xdg.configFile``."""
    config_files = _eval_config_file_names(
        nix_attrset({
            "activeProfile": "personal",
            "profiles.personal": {},
        })
    )

    assert config_files == ["opencode/personal.json"]


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_work_profile_materializes_only_one_overlay_file() -> None:
    """The work profile should materialize exactly one selected overlay file."""
    config_files = _eval_work_config_file_names()

    assert config_files == ["opencode/work.json"]


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_selected_profile_sets_shell_env_var_to_the_same_profile_path() -> None:
    """Shell sessions should export the selected profile path directly."""
    session_variables = _eval_session_variables(
        nix_attrset({
            "activeProfile": "personal",
            "profiles.personal": {},
        })
    )

    assert (
        session_variables["OPENCODE_CONFIG"]
        == "/Users/test/.config/opencode/personal.json"
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_selected_profile_json_is_a_thin_overlay_when_no_overrides_exist() -> None:
    """An empty profile should materialize to a schema-only overlay."""
    profile = _eval_opencode_json(
        nix_attrset({
            "activeProfile": "personal",
            "profiles.personal": {},
        }),
        profile_name="personal",
    )

    assert profile == {"$schema": "https://opencode.ai/config.json"}


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
    """Darwin Keychain-backed MCP wrappers should stay behind a Darwin guard."""
    profiles_module = (REPO_ROOT / "modules/home/profiles.nix").read_text(
        encoding="utf-8"
    )

    assert "} // lib.optionalAttrs pkgs.stdenv.isDarwin (" in profiles_module
    guarded_section = profiles_module.split(
        "} // lib.optionalAttrs pkgs.stdenv.isDarwin (", 1
    )[1]
    assert "slack = {" in guarded_section
    assert "render = {" in guarded_section
    assert "security find-generic-password" in guarded_section
    assert "security find-internet-password" in guarded_section


def test_darwin_hosts_select_the_materialized_opencode_profile_files() -> None:
    """Host launchd wiring should point at the profile files the module materializes."""
    argus = (REPO_ROOT / "darwin/argus.nix").read_text(encoding="utf-8")
    rocinante = (REPO_ROOT / "darwin/rocinante.nix").read_text(encoding="utf-8")

    assert '(lib.mkSetOpencodeEnvModule "work.json")' in argus
    assert '(lib.mkSetOpencodeEnvModule "personal.json")' in rocinante
    assert 'mkSetOpencodeEnvModule "active.json"' not in argus
    assert 'mkSetOpencodeEnvModule "active.json"' not in rocinante


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_work_profile_overlay_keeps_shared_defaults_in_global_config() -> None:
    """The work overlay should contain work additions without duplicating shared base MCPs."""
    profile = _eval_work_profile_json()

    assert profile["mcp"]["axiom"] == {
        "enabled": False,
        "type": "remote",
        "url": "https://mcp.axiom.co/mcp",
    }
    assert profile["mcp"]["convex"] == {
        "command": ["bunx", "--bun", "convex@latest", "mcp", "start"],
        "enabled": False,
        "type": "local",
    }
    assert "chrome-devtools" not in profile["mcp"]


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_work_profile_sparse_mcp_overrides_stay_sparse() -> None:
    """Work MCP overrides should only emit the fields that differ from global config."""
    profile = _eval_work_profile_json(
        work_config={"opencodeMcp.chrome-devtools.enable": True}
    )

    assert profile["mcp"]["chrome-devtools"] == {"enabled": True}


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_profile_mcp_override_invalid_shape_is_rejected() -> None:
    """Profile MCP overrides should reject invalid typed sparse override shapes."""
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
    """Work MCP overrides should reuse the same sparse override validation."""
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        _eval_work_profile_json(
            work_config={"opencodeMcp.chrome-devtools.command": "bunx"}
        )

    assert excinfo.value.stderr is not None
    assert "profiles.work.opencodeMcp.chrome-devtools" in excinfo.value.stderr
