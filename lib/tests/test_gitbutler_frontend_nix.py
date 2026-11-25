"""The GitButler frontend build must keep Nix's Node and pnpm."""

import json
from pathlib import Path

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import expect_binding
from lib.tests._nix_source import nix_file_binding_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import load_repo_module_for_test


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
    phase = build_phase.rebuild()
    assert "linuxFrontendNodeFix" in phase
    passthrough = phase.index("${python3}/bin/python3 ${./turbo_pnpm_passthrough.py}")
    assert passthrough < phase.index("pnpm build:desktop -- --mode production")

    env = expect_instance(
        expect_binding(
            expect_instance(frontend.argument, AttributeSet).values,
            "env",
        ).value,
        AttributeSet,
    )
    for name in (
        "npm_config_manage_package_manager_versions",
        "pnpm_config_manage_package_manager_versions",
    ):
        assert expect_binding(env.values, name).value.rebuild() == '"false"'


def _passthrough_module():
    return load_repo_module_for_test(
        "packages/gitbutler/turbo_pnpm_passthrough.py",
        prefix="gitbutler_turbo_pnpm",
    )


def test_turbo_passthrough_records_the_nix_pnpm_switch_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turbo must forward the variables that keep the Nix pnpm."""
    module = _passthrough_module()
    config = {
        "tasks": {
            "package": {"dependsOn": ["^package"], "outputs": ["dist/**"]},
        },
        "globalPassThroughEnv": ["SENTRY_AUTH_TOKEN", module.PASSTHROUGH_ENV[0]],
    }

    module.align_turbo_package_manager_env(config)

    assert config["globalPassThroughEnv"] == [
        "SENTRY_AUTH_TOKEN",
        *module.PASSTHROUGH_ENV,
    ]
    assert config["tasks"]["package"]["outputs"] == ["dist/**"]

    missing = {"tasks": {"package": {}}}
    module.align_turbo_package_manager_env(missing)
    assert missing["globalPassThroughEnv"] == list(module.PASSTHROUGH_ENV)

    module.align_turbo_package_manager_env(missing)
    assert missing["globalPassThroughEnv"] == list(module.PASSTHROUGH_ENV)

    with pytest.raises(ValueError, match="globalPassThroughEnv"):
        module.align_turbo_package_manager_env({"globalPassThroughEnv": "npm_config"})

    path = tmp_path / "turbo.json"
    path.write_text(
        json.dumps({"globalPassThroughEnv": None, "tasks": {}}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    module.main()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["globalPassThroughEnv"] == list(module.PASSTHROUGH_ENV)
    assert written["tasks"] == {}
    module.main()
    assert json.loads(path.read_text(encoding="utf-8")) == written


def test_turbo_passthrough_rejects_a_non_object_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turbo.json that is not an object cannot name the pass-through env."""
    module = _passthrough_module()
    path = tmp_path / "turbo.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="JSON object"):
        module.main()
    assert path.read_text(encoding="utf-8") == "[]"
