"""Semantic checks for low-overhead Darwin host runtime defaults."""

import os
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, binding_map, expect_binding
from lib.tests._nix_eval import nix_attrset, nix_eval_json, nix_eval_raw, nix_import
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


def test_spotlight_activation_preserves_global_policy_and_scopes_exclusions() -> None:
    """Global policy stays explicit, and only listed mounted volumes drop out."""
    module = _module_output("modules/darwin/base.nix")
    spotlight_options = _nested_attrset(
        module,
        "options",
        "darwinDefaults",
        "spotlight",
    )
    assert_nix_ast_equal(_option_default(spotlight_options, "indexing"), "true")
    assert_nix_ast_equal(
        _option_default(spotlight_options, "unindexedVolumes"),
        "[ ]",
    )

    post_activation = _nested_attrset(
        module,
        "config",
        "system",
        "activationScripts",
        "postActivation",
    )
    merge = expect_instance(
        expect_binding(post_activation.values, "text").value,
        FunctionCall,
    )
    assert_nix_ast_equal(merge.name, "lib.mkMerge")
    branches = expect_instance(merge.argument, NixList).value
    assert len(branches) == 2

    global_call = expect_instance(
        expect_instance(branches[0], Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(global_call.name, "lib.mkIf (!cfg.spotlight.indexing)")
    global_commands = command_texts(
        parse_shell(indented_string_body(global_call.argument.rebuild())),
    )
    assert any(
        command.startswith("/usr/bin/mdutil -a -i off") for command in global_commands
    )

    scoped_call = expect_instance(
        expect_instance(branches[1], Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        scoped_call.name,
        "lib.mkIf (cfg.spotlight.indexing && cfg.spotlight.unindexedVolumes != [ ])",
    )
    template_call = expect_instance(
        expect_instance(scoped_call.argument, Parenthesis).value,
        FunctionCall,
    )
    # The rendered template is applied to the configured volume names, so the
    # script cannot drift from the option.
    assert_nix_ast_equal(template_call.argument, "cfg.spotlight.unindexedVolumes")
    render_call = expect_instance(template_call.name, FunctionCall)
    assert_nix_ast_equal(render_call.name, 'lib.concatMapStringsSep "\\n"')
    template_function = expect_instance(
        expect_instance(render_call.argument, Parenthesis).value,
        FunctionDefinition,
    )
    template = expect_instance(template_function.output, IndentedString)
    scoped = parse_shell(indented_string_body(template.rebuild()))
    scoped_commands = command_texts(scoped)
    assert any(
        command.startswith('/usr/bin/mdutil -i off -d "$volumePath"')
        for command in scoped_commands
    )
    assert any(
        command.startswith('/usr/sbin/diskutil info -plist "$volumePath"')
        for command in scoped_commands
    )
    assert any(
        command.startswith("/usr/bin/plutil -extract MountPoint raw")
        for command in scoped_commands
    )
    assert any(
        command.startswith('[ "$currentMountPoint" = "$volumePath" ]')
        for command in scoped_commands
    )
    assert not any(
        command.startswith('/usr/bin/mdutil -s "$volumePath"')
        for command in scoped_commands
    )
    assert "volumePath=__NIX_INTERP__" in scoped.sanitized


@cache
def _spotlight_fixture() -> dict[str, object]:
    """Render real Spotlight fragments with only generic Nix helpers stubbed."""
    rendered = nix_eval_json(
        FunctionCall(
            name=nix_import(REPO_ROOT / "tests/nix/spotlight-activation.nix"),
            argument=nix_attrset({
                "volumes": [
                    "Build",
                    "Build-disabled",
                    "Build Volume",
                    "O'Brien",
                    "Stale",
                    "Missing",
                ],
                "namesToCheck": [
                    "Build",
                    "",
                    ".",
                    "..",
                    "../",
                    "a/b",
                    "Build Volume",
                    "O'Brien",
                ],
            }),
        )
    )
    return expect_instance(rendered, dict)


def _run_owned_shell(
    script: str,
    *,
    env: dict[str, str],
    check: bool = True,
    errexit: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute owned rendered shell with platform commands replaced by functions."""
    bash = shutil.which("bash")
    assert bash is not None
    command = [bash, "-o", "pipefail"]
    if errexit:
        command.append("-e")
    command.extend(["-c", script])
    return subprocess.run(  # noqa: S603 -- Execute owned activation against local stubs.
        command,
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.parametrize(
    ("source_name", "source_populated", "target_state", "expected_returncode"),
    [
        (".bun", True, "populated", 0),
        (".bun", True, "empty", 1),
        (".bun.backup", True, "missing", 0),
        (".bun", False, "empty", 0),
        (None, False, "missing", 0),
    ],
)
def test_bun_migration_guard_requires_nonempty_target_for_existing_data(
    tmp_path: Path,
    source_name: str | None,
    source_populated: bool,
    target_state: str,
    expected_returncode: int,
) -> None:
    """The pre-link guard preserves data and explains a non-overwriting recovery."""
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "target"
    if target_state != "missing":
        target.mkdir()
    if target_state == "populated":
        (target / "existing").write_text("target", encoding="utf-8")
    source: Path | None = None
    if source_name is not None:
        source = home / source_name
        source.mkdir()
        if source_populated:
            (source / "global-cli").write_text("source", encoding="utf-8")
        if source_name == ".bun.backup":
            (home / ".bun").symlink_to(target, target_is_directory=True)

    script = (REPO_ROOT / "darwin/george/build-volume-bun-migration.sh").read_text(
        encoding="utf-8"
    )
    result = _run_owned_shell(
        script,
        env=os.environ
        | {
            "HOME": str(home),
            "buildVolumeBunInstall": str(target),
        },
        check=False,
    )

    assert result.returncode == expected_returncode
    if expected_returncode == 0:
        assert result.stderr == ""
    else:
        assert source is not None
        assert str(source) in result.stderr
        assert str(target) in result.stderr
        assert "/usr/bin/ditto" in result.stderr
        assert (source / "global-cli").read_text(encoding="utf-8") == "source"


@pytest.mark.parametrize("target_exists", [False, True])
def test_bun_migration_guard_fails_when_directory_inspection_fails(
    tmp_path: Path, target_exists: bool
) -> None:
    """Unreadable directories are errors, never empty directories."""
    home = tmp_path / "home"
    (home / ".bun").mkdir(parents=True)
    (home / ".bun/global-cli").write_text("source", encoding="utf-8")
    target = tmp_path / "target"
    if target_exists:
        target.mkdir()
    script = (REPO_ROOT / "darwin/george/build-volume-bun-migration.sh").read_text(
        encoding="utf-8"
    )
    result = _run_owned_shell(
        "/bin/ls() { return 23; }\n" + script,
        env=os.environ
        | {
            "HOME": str(home),
            "buildVolumeBunInstall": str(target),
        },
        check=False,
    )

    assert result.returncode == 1
    expected = "Build Bun target" if target_exists else "existing Bun directory"
    assert expected in result.stderr
    assert (home / ".bun/global-cli").read_text(encoding="utf-8") == "source"


@pytest.mark.nix_eval(
    reason="Render the actual login setter; Bash must verify its own fail-fast behavior"
)
def test_build_volume_login_setter_stops_after_failed_setenv(tmp_path: Path) -> None:
    """A middle launchctl failure cannot leave a successful partial environment."""
    script = nix_eval_raw(nix_import(REPO_ROOT / "tests/nix/build-volume-login.nix"))
    call_log = tmp_path / "calls"
    stubs = r"""
/bin/launchctl() {
  printf '%s\n' "$*" >> "$CALL_LOG"
  if [ "$2" = "MISE_CACHE_DIR" ]; then
    return 23
  fi
}
"""
    result = _run_owned_shell(
        stubs + "\n" + script,
        env=os.environ | {"CALL_LOG": str(call_log)},
        check=False,
        errexit=False,
    )

    assert result.returncode == 23
    assert call_log.read_text(encoding="utf-8").splitlines() == [
        "setenv BUN_INSTALL /Volumes/Build/bun",
        "setenv DENO_DIR /Volumes/Build/deno",
        "setenv GOCACHE /Volumes/Build/go/build",
        "setenv GOMODCACHE /Volumes/Build/go/mod",
        "setenv HOMEBREW_CACHE /Volumes/Build/cache/Homebrew",
        "setenv MISE_CACHE_DIR /Volumes/Build/mise",
    ]


@pytest.mark.nix_eval(
    reason="Evaluate the actual volume-name predicate instead of copying its logic"
)
def test_spotlight_volume_names_are_single_basenames() -> None:
    """Only one mounted-volume path component can enter the activation template."""
    assert _spotlight_fixture()["checks"] == [
        True,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
    ]


@pytest.mark.nix_eval(
    reason="Render actual Nix interpolation; Bash must verify quoting and mount transitions"
)
def test_spotlight_exclusions_require_exact_mounts_and_preserve_quoting(
    tmp_path: Path,
) -> None:
    """Quoted names converge, stale directories skip, and failures stay observable."""
    exclusions = expect_instance(_spotlight_fixture()["exclusions"], str)
    call_log = tmp_path / "calls"
    stubs = r"""
/usr/sbin/diskutil() {
  case "$3" in
    /Volumes/Stale) printf '%s' '/' ;;
    /Volumes/Missing) return 1 ;;
    *) printf '%s' "$3" ;;
  esac
}
/usr/bin/plutil() {
  /bin/cat
}
/usr/bin/mdutil() {
  printf 'mdutil' >> "$CALL_LOG"
  for argument in "$@"; do
    printf ' <%s>' "$argument" >> "$CALL_LOG"
  done
  printf '\n' >> "$CALL_LOG"
  if [ "$4" = "$FAIL_VOLUME" ]; then
    return 1
  fi
}
"""
    result = _run_owned_shell(
        stubs + "\n" + exclusions,
        env=os.environ
        | {
            "CALL_LOG": str(call_log),
            "FAIL_VOLUME": "/Volumes/Build Volume",
        },
    )

    assert call_log.read_text(encoding="utf-8").splitlines() == [
        "mdutil <-i> <off> <-d> </Volumes/Build>",
        "mdutil <-i> <off> <-d> </Volumes/Build-disabled>",
        "mdutil <-i> <off> <-d> </Volumes/Build Volume>",
        "mdutil <-i> <off> <-d> </Volumes/O'Brien>",
    ]
    assert (
        "warning: mdutil could not disable Spotlight indexing for /Volumes/Build Volume"
        in result.stderr
    )
    assert result.stderr.count("skipping Spotlight exclusion") == 2
    assert "/Volumes/Stale" in result.stderr
    assert "/Volumes/Missing" in result.stderr


@pytest.mark.nix_eval(
    reason="Render the actual Argus preActivation fragment for native shell state tests"
)
@pytest.mark.parametrize(
    ("root_status", "data_status", "expected_paths"),
    [
        ("Indexing enabled.", "Indexing enabled.", []),
        ("Indexing disabled.", "Indexing enabled.", ["/"]),
        (
            "Indexing enabled.",
            "Indexing and searching disabled.",
            ["/System/Volumes/Data"],
        ),
        (
            "Indexing and searching disabled.",
            "Indexing disabled.",
            ["/", "/System/Volumes/Data"],
        ),
    ],
)
def test_argus_restores_only_required_spotlight_stores(
    tmp_path: Path,
    root_status: str,
    data_status: str,
    expected_paths: list[str],
) -> None:
    """Old global disablement is reversed before mas without touching Build."""
    restoration = expect_instance(_spotlight_fixture()["restoration"], str)
    call_log = tmp_path / "calls"
    stubs = r"""
/usr/bin/mdutil() {
  if [ "$1" = "-s" ]; then
    if [ "$2" = "/" ]; then
      printf '/:\n\t%s\n' "$ROOT_STATUS"
    else
      printf '/System/Volumes/Data:\n\t%s\n' "$DATA_STATUS"
    fi
    return 0
  fi
  printf 'mdutil' >> "$CALL_LOG"
  for argument in "$@"; do
    printf ' <%s>' "$argument" >> "$CALL_LOG"
  done
  printf '\n' >> "$CALL_LOG"
}
"""
    _run_owned_shell(
        stubs + "\n" + restoration,
        env=os.environ
        | {
            "CALL_LOG": str(call_log),
            "ROOT_STATUS": root_status,
            "DATA_STATUS": data_status,
        },
    )

    actual = (
        call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []
    )
    assert actual == [f"mdutil <-i> <on> <{path}>" for path in expected_paths]


@pytest.mark.nix_eval(
    reason="Render the actual Argus fragment to verify fatal restoration semantics"
)
def test_argus_spotlight_restore_failure_stops_activation(tmp_path: Path) -> None:
    """A failed required-store restore must not reach the Homebrew consumer."""
    restoration = expect_instance(_spotlight_fixture()["restoration"], str)
    call_log = tmp_path / "calls"
    stubs = r"""
/usr/bin/mdutil() {
  if [ "$1" = "-s" ]; then
    printf '/:\n\tIndexing disabled.\n'
    return 0
  fi
  printf 'mdutil' >> "$CALL_LOG"
  for argument in "$@"; do
    printf ' <%s>' "$argument" >> "$CALL_LOG"
  done
  printf '\n' >> "$CALL_LOG"
  return 23
}
"""
    result = _run_owned_shell(
        stubs + "\n" + restoration + "\nprintf 'downstream reached\\n'\n",
        env=os.environ | {"CALL_LOG": str(call_log)},
        check=False,
    )

    assert result.returncode == 23
    assert result.stdout == ""
    assert call_log.read_text(encoding="utf-8").splitlines() == ["mdutil <-i> <on> </>"]
