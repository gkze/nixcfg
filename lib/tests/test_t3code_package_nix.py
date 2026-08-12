"""Structural tests for the shared T3 Code workspace build."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.tests._shell_ast import (
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)


def test_t3code_workspace_pnpm_fetch_is_resilient() -> None:
    """Large workspace dependency fetches should tolerate registry instability."""
    package = expect_instance(
        nix_file_expr("packages/t3code/_shared.nix"),
        FunctionDefinition,
    )
    node_modules = expect_instance(
        expect_binding(package.output.scope, "node_modules").value,
        IfExpression,
    )
    args = expect_instance(
        expect_binding(node_modules.scope, "args").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(args.values, "pnpmInstallFlags").value,
        """[
          "--fetch-retries=5"
          "--network-concurrency=1"
        ]""",
    )


def test_t3code_workspace_uses_nix_pnpm_without_global_config() -> None:
    """Pnpm 11 should use the Nix-pinned executable without mutating global config."""
    package = expect_instance(
        nix_file_expr("packages/t3code/_shared.nix"),
        FunctionDefinition,
    )
    assert_nix_ast_equal(
        expect_binding(package.output.scope, "pnpm").value,
        "pnpm_11.override { nodejs-slim = nodejs; }",
    )
    workspace_build = expect_instance(
        expect_binding(package.output.scope, "workspaceBuild").value,
        FunctionCall,
    )
    derivation = expect_instance(workspace_build.argument, AttributeSet)
    environment = expect_instance(
        expect_binding(derivation.values, "env").value,
        AttributeSet,
    )
    build_phase = expect_instance(
        expect_binding(derivation.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))

    assert_nix_ast_equal(
        expect_binding(
            environment.values,
            "pnpm_config_pm_on_fail",
        ).value,
        StringPrimitive(value="ignore"),
    )
    assert command_texts(build_shell, "pnpm") == ["pnpm run build:desktop"]


@pytest.mark.parametrize("reject_build", [False, True])
def test_t3code_desktop_electron_builder_waits_for_completion(
    tmp_path: Path,
    reject_build: bool,
) -> None:
    """Use Node to cover promise completion, failure, and a leaked JS handle."""
    package = expect_instance(
        nix_file_expr("packages/t3code-desktop/default.nix"),
        FunctionDefinition,
    )
    assertion = expect_instance(package.output, Assertion)
    derivation_call = expect_instance(assertion.body, FunctionCall)
    derivation = expect_instance(derivation_call.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(derivation.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))
    runner_node = next(iter_nodes(build_shell.tree.root_node, "heredoc_body"))
    runner = node_text(runner_node, build_shell.sanitized)

    fake_module = tmp_path / "node_modules/electron-builder/index.js"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text(
        """
exports.Arch = { arm64: "arm64" };
exports.Platform = {
  MAC: {
    createTarget(type, arch) {
      return { platform: "mac", type, arch };
    },
  },
};
exports.build = async (options) => {
  console.log(JSON.stringify(options));
  await new Promise((resolve) => setTimeout(resolve, 20));
  if (process.env.T3CODE_TEST_REJECT === "1") {
    throw new Error("synthetic build failure");
  }
  console.log("build-complete");
  setInterval(() => {}, 1000);
};
""".lstrip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update({
        "NODE_PATH": str(tmp_path / "node_modules"),
        "T3CODE_APP_ID": "com.t3tools.t3code",
        "T3CODE_APP_NAME": "T3 Code (Alpha)",
        "T3CODE_ELECTRON_DIST": "/tmp/electron-dist",
        "T3CODE_ELECTRON_VERSION": "41.5.0",
        "T3CODE_TEST_REJECT": "1" if reject_build else "0",
    })

    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(  # noqa: S603 -- Executes the controlled runner above.
        [node, "-e", runner],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=2,
    )

    assert result.returncode == (1 if reject_build else 0)
    stdout_lines = result.stdout.splitlines()
    options = json.loads(stdout_lines[0])
    assert options["targets"] == {
        "platform": "mac",
        "type": "dir",
        "arch": "arm64",
    }
    assert options["publish"] == "never"
    assert options["config"]["electronDist"] == "/tmp/electron-dist"
    assert options["config"]["electronVersion"] == "41.5.0"
    if reject_build:
        assert stdout_lines == [stdout_lines[0]]
        assert "synthetic build failure" in result.stderr
    else:
        assert stdout_lines[1:] == ["build-complete"]
