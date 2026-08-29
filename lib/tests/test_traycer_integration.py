"""Semantic integration contracts for the declarative Traycer migration."""

import json
import os
import subprocess
import sys
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr, nix_source_fragment_expr
from lib.tests._package_registry import registry_override_metadata

_PREFLIGHT = (
    Path(__file__).resolve().parents[2]
    / "modules/darwin/george/traycer-host-collision-preflight.sh"
)
_NIX_CLI = "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-traycer-cli-1.1.9/bin/traycer"
_HOME_MANAGER_COMMAND = (
    f"/bin/wait4path /nix/store && exec {_NIX_CLI} "
    "host start --service-label ai.traycer.host"
)
_HOME_MANAGER_VECTOR = ["/bin/sh", "-c", _HOME_MANAGER_COMMAND]


def _select_path(expression: object) -> tuple[str, ...]:
    if isinstance(expression, Identifier):
        return (expression.name,)
    selection = expect_instance(expression, Select)
    return (*_select_path(selection.expression), selection.attribute)


def test_traycer_package_entrypoint_and_platform_route_are_exact() -> None:
    """Traycer should be discoverable only on its authenticated target platform."""
    assert_nix_ast_equal(
        nix_file_expr("packages/traycer/default.nix"),
        "import ./package.nix",
    )

    registry = expect_instance(
        nix_file_expr("packages/registry.nix"),
        FunctionDefinition,
    )
    registry_output = expect_instance(registry.output, AttributeSet)
    assert registry_override_metadata(registry_output)["traycer"] == {
        "constraint": ["aarch64-darwin"]
    }

    overlay = expect_instance(
        nix_file_expr("overlays/binary-darwin-apps.nix"),
        FunctionDefinition,
    )
    exports = expect_instance(overlay.output, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(exports.values, "traycer").value,
        'callDarwinAppPackage "traycer"',
    )


def test_traycer_app_cli_and_raw_host_agent_are_declaratively_routed() -> None:
    """The app, app-free CLI, and sole raw host supervisor should share one route."""
    routing = expect_instance(
        nix_source_fragment_expr(
            "home/george/work.nix",
            "  routing = ",
            ";\n  projection =",
        ),
        AttributeSet,
    )
    traycer_route = expect_instance(
        expect_binding(routing.values, "traycer").value,
        FunctionCall,
    )
    assert_nix_ast_equal(traycer_route.name, "systemApp")
    assert traycer_route.argument is not None
    assert_nix_ast_equal(traycer_route.argument, "pkgs.traycer")

    work = expect_instance(
        nix_file_expr("home/george/work.nix"),
        FunctionDefinition,
    )
    work_output = expect_instance(work.output, AttributeSet)
    nixcfg = expect_instance(
        expect_binding(work_output.values, "nixcfg").value,
        AttributeSet,
    )
    package_sets = expect_instance(
        expect_binding(nixcfg.values, "packageSets").value,
        AttributeSet,
    )
    extra_packages = expect_instance(
        expect_binding(package_sets.values, "extraPackages").value,
        NixList,
    )
    assert ("pkgs", "traycer.cliPackage") in {
        _select_path(package) for package in extra_packages.value
    }

    launchd = expect_instance(
        expect_binding(work_output.values, "launchd").value,
        AttributeSet,
    )
    agents = expect_instance(
        expect_binding(launchd.values, "agents").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(agents.values, '"ai.traycer.host"').value,
        """
        {
          enable = true;
          config = {
            Label = "ai.traycer.host";
            AssociatedBundleIdentifiers = [ "ai.traycer.desktop" ];
            ProgramArguments = [
              "${pkgs.traycer.cliPackage}/bin/traycer"
              "host"
              "start"
              "--service-label"
              "ai.traycer.host"
            ];
            RunAtLoad = true;
            KeepAlive = {
              SuccessfulExit = false;
              Crashed = true;
            };
            ThrottleInterval = 10;
            ProcessType = "Interactive";
            SoftResourceLimits = {
              NumberOfFiles = 8192;
            };
            EnvironmentVariables = {
              HOME = config.home.homeDirectory;
              NODE_OPTIONS = "--max-semi-space-size=16";
              PATH = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";
            };
          };
        }
        """,
    )


def test_argus_runs_the_traycer_collision_preflight_before_app_replacement() -> None:
    """Argus should gate the shared Applications activation on a read-only probe."""
    argus = expect_instance(nix_file_expr("darwin/argus.nix"), FunctionDefinition)
    host = expect_instance(argus.output, FunctionCall)
    arguments = expect_instance(host.argument, AttributeSet)
    modules = expect_instance(
        expect_binding(arguments.values, "extraSystemModules").value,
        NixList,
    )
    assert "${lib.modulesPath}/darwin/george/traycer-host.nix" in {
        module.value for module in modules.value if isinstance(module, StringPrimitive)
    }

    assert_nix_ast_equal(
        nix_file_expr("modules/darwin/george/traycer-host.nix"),
        """
        {
          config,
          lib,
          primaryUser,
          ...
        }:
        {
          system.activationScripts.applications.text = lib.mkBefore ''
            /bin/bash ${./traycer-host-collision-preflight.sh} \\
              ${lib.escapeShellArg (toString config.users.users.${primaryUser}.uid)} \\
              ${lib.escapeShellArg "/Users/${primaryUser}"} \\
              ${lib.escapeShellArg "/Applications"} \\
              /bin/launchctl \\
              /usr/bin/plutil
          '';
        }
        """,
    )


def _write_fake_plutil(path: Path) -> None:
    path.write_text(
        f"""#!{sys.executable}
import json
import os
import sys

with open(os.environ["FAKE_PLUTIL_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")

args = sys.argv[1:]
try:
    if len(args) == 2 and args[0] == "-lint":
        with open(args[1], encoding="utf-8") as stream:
            json.load(stream)
        raise SystemExit(0)
    if len(args) == 6 and args[0] == "-extract" and args[2:5] == ["raw", "-o", "-"]:
        with open(args[5], encoding="utf-8") as stream:
            value = json.load(stream)
        for component in args[1].split("."):
            value = value[int(component)] if isinstance(value, list) else value[component]
        print(str(value).lower() if isinstance(value, bool) else value)
        raise SystemExit(0)
except (FileNotFoundError, IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
raise SystemExit(64)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_fake_launchctl(path: Path) -> None:
    path.write_text(
        """#!/bin/bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_LAUNCHCTL_LOG"
if [ "$#" -ne 2 ] || [ "$1" != print ]; then
  exit 64
fi
label="${2##*/}"
if [ "$label" = ai.traycer.host.agent ]; then
  mode="$FAKE_AGENT_MODE"
else
  mode="$FAKE_BASE_MODE"
fi
case "$mode" in
  not-found)
    printf 'Bad request.\\nCould not find service "%s" in domain for user gui: %s\\n' "$label" "$FAKE_UID" >&2
    exit 113
    ;;
  not-found-specified)
    printf 'Could not find specified service "%s" in domain for user gui: %s\\n' "$label" "$FAKE_UID" >&2
    exit 113
    ;;
  not-found-specified-wrong-label)
    printf 'Could not find specified service "ai.traycer.other" in domain for user gui: %s\\n' "$FAKE_UID" >&2
    exit 113
    ;;
  not-found-specified-wrong-status)
    printf 'Could not find specified service "%s" in domain for user gui: %s\\n' "$label" "$FAKE_UID" >&2
    exit 1
    ;;
  exact)
    cat <<EOF
gui/$FAKE_UID/$label = {
\tactive count = 1
\tpath = $FAKE_PRIOR_PLIST
\ttype = LaunchAgent
\tstate = running
\tprogram = /bin/sh
\targuments = {
\t\t/bin/sh
\t\t-c
\t\t$FAKE_HOME_MANAGER_COMMAND
\t}
}
EOF
    ;;
  mismatched)
    cat <<EOF
gui/$FAKE_UID/$label = {
\tpath = $FAKE_PRIOR_PLIST
\ttype = LaunchAgent
\tprogram = $FAKE_PROGRAM
\targuments = {
\t\t$FAKE_PROGRAM
\t\thost
\t\tstart
\t}
}
EOF
    ;;
  nested-spoof)
    cat <<EOF
gui/$FAKE_UID/$label = {
\tpath = /tmp/foreign.plist
\ttype = LaunchAgent
\tprogram = /tmp/foreign
\tendpoints = {
\t\tpath = $FAKE_PRIOR_PLIST
\t\ttype = LaunchAgent
\t\tprogram = /bin/sh
\t\targuments = {
\t\t\t/bin/sh
\t\t\t-c
\t\t\t$FAKE_HOME_MANAGER_COMMAND
\t\t}
\t}
\targuments = {
\t\t/tmp/foreign
\t}
}
EOF
    ;;
  duplicate-field-spoof)
    cat <<EOF
gui/$FAKE_UID/$label = {
\tpath = $FAKE_PRIOR_PLIST
\ttype = LaunchAgent
\tprogram = /bin/sh
\tprogram = /bin/sh
\targuments = {
\t\t/bin/sh
\t\t-c
\t\t$FAKE_HOME_MANAGER_COMMAND
\t}
}
EOF
    ;;
  duplicate-arguments-spoof)
    cat <<EOF
gui/$FAKE_UID/$label = {
\tpath = $FAKE_PRIOR_PLIST
\ttype = LaunchAgent
\tprogram = /bin/sh
\targuments = {
\t\t/bin/sh
\t\t-c
\t\t$FAKE_HOME_MANAGER_COMMAND
\t}
\targuments = {
\t\t/bin/sh
\t\t-c
\t\t$FAKE_HOME_MANAGER_COMMAND
\t}
}
EOF
    ;;
  smappservice)
    cat <<EOF
gui/$FAKE_UID/$label = {
\tpath = (submitted by smd.93299)
\ttype = Submitted
\tmanaged_by = com.apple.xpc.ServiceManagement
\tprogram identifier = Contents/Library/LaunchAgents/Traycer Host.app/Contents/MacOS/traycer (mode: 2)
\tparent bundle identifier = ai.traycer.desktop
\tBTM uuid = 2E0FA2B2-5AED-4B9D-9D29-518D9AF8A49F
}
EOF
    ;;
  indeterminate)
    printf 'Bad request.\\nCould not find domain for user gui: %s\\n' "$FAKE_UID" >&2
    exit 1
    ;;
  *) exit 64 ;;
esac
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _preflight_fixture(
    tmp_path: Path, *, with_prior: bool
) -> tuple[list[str], dict[str, str]]:
    uid = "501"
    home = tmp_path / "home"
    applications = tmp_path / "Applications"
    prior_plist = home / "Library/LaunchAgents/ai.traycer.host.plist"
    legacy_agents = applications / "Traycer.app/Contents/Library/LaunchAgents"
    legacy_program = (
        "Contents/Library/LaunchAgents/Traycer Host.app/Contents/MacOS/Traycer Host"
    )
    for label in ("ai.traycer.host", "ai.traycer.host.agent"):
        _write_json(
            legacy_agents / f"{label}.plist",
            {
                "Label": label,
                "BundleProgram": legacy_program,
                "ProgramArguments": [legacy_program, label],
            },
        )
    if with_prior:
        _write_json(
            prior_plist,
            {
                "Label": "ai.traycer.host",
                "ProgramArguments": _HOME_MANAGER_VECTOR,
            },
        )

    fake_launchctl = tmp_path / "fake-launchctl"
    fake_plutil = tmp_path / "fake-plutil"
    _write_fake_launchctl(fake_launchctl)
    _write_fake_plutil(fake_plutil)
    launchctl_log = tmp_path / "launchctl.log"
    plutil_log = tmp_path / "plutil.log"
    env = os.environ.copy()
    env.update({
        "FAKE_AGENT_MODE": "not-found",
        "FAKE_BASE_MODE": "not-found",
        "FAKE_HOME_MANAGER_COMMAND": _HOME_MANAGER_COMMAND,
        "FAKE_LAUNCHCTL_LOG": str(launchctl_log),
        "FAKE_PLUTIL_LOG": str(plutil_log),
        "FAKE_PRIOR_PLIST": str(prior_plist),
        "FAKE_PROGRAM": _NIX_CLI,
        "FAKE_UID": uid,
    })
    arguments = [
        "/bin/bash",
        str(_PREFLIGHT),
        uid,
        str(home),
        str(applications),
        str(fake_launchctl),
        str(fake_plutil),
    ]
    return arguments, env


def test_traycer_preflight_accepts_only_the_exact_prior_nix_supervisor(
    tmp_path: Path,
) -> None:
    """A previous declarative generation may remain loaded during replacement."""
    arguments, env = _preflight_fixture(tmp_path, with_prior=True)
    env["FAKE_BASE_MODE"] = "exact"

    result = subprocess.run(  # noqa: S603
        arguments,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "launchctl.log").read_text(encoding="utf-8").splitlines() == [
        "print gui/501/ai.traycer.host",
        "print gui/501/ai.traycer.host.agent",
    ]
    plutil_calls = (tmp_path / "plutil.log").read_text(encoding="utf-8")
    assert (
        "Traycer.app/Contents/Library/LaunchAgents/ai.traycer.host.plist"
        in plutil_calls
    )
    assert (
        "Traycer.app/Contents/Library/LaunchAgents/ai.traycer.host.agent.plist"
        in plutil_calls
    )


def test_traycer_preflight_rejects_nested_or_duplicate_identity_spoofs(
    tmp_path: Path,
) -> None:
    """Only one exact top-level launchd identity may attest the supervisor."""
    for index, mode in enumerate((
        "nested-spoof",
        "duplicate-field-spoof",
        "duplicate-arguments-spoof",
    )):
        arguments, env = _preflight_fixture(tmp_path / str(index), with_prior=True)
        env["FAKE_BASE_MODE"] = mode

        result = subprocess.run(  # noqa: S603
            arguments,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0, mode
        assert "does not match" in result.stderr


def test_traycer_preflight_rejects_shell_syntax_disguised_as_a_store_name(
    tmp_path: Path,
) -> None:
    """The Home Manager shell wrapper may contain only a shell-safe Nix path."""
    arguments, env = _preflight_fixture(tmp_path, with_prior=True)
    injected_cli = (
        "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-traycer-cli-$(id)/bin/traycer"
    )
    injected_command = (
        f"/bin/wait4path /nix/store && exec {injected_cli} "
        "host start --service-label ai.traycer.host"
    )
    _write_json(
        tmp_path / "home/Library/LaunchAgents/ai.traycer.host.plist",
        {
            "Label": "ai.traycer.host",
            "ProgramArguments": ["/bin/sh", "-c", injected_command],
        },
    )
    env["FAKE_BASE_MODE"] = "exact"
    env["FAKE_HOME_MANAGER_COMMAND"] = injected_command

    result = subprocess.run(  # noqa: S603
        arguments,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "exact Traycer CLI Nix store path" in result.stderr


def test_traycer_preflight_rejects_unexpected_raw_user_launchagents(
    tmp_path: Path,
) -> None:
    """A dormant raw plist must not be able to claim either Traycer label."""
    for index, label in enumerate(("ai.traycer.host", "ai.traycer.host.agent")):
        case_root = tmp_path / str(index)
        arguments, env = _preflight_fixture(case_root, with_prior=True)
        _write_json(
            case_root / "home/Library/LaunchAgents/unexpected-name.plist",
            {"Label": label, "ProgramArguments": ["/tmp/foreign"]},
        )

        result = subprocess.run(  # noqa: S603
            arguments,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0, label
        assert "unexpected raw user LaunchAgent" in result.stderr


def test_traycer_preflight_accepts_an_empty_runtime_slot(tmp_path: Path) -> None:
    """A first declarative activation may proceed when neither label is loaded."""
    arguments, env = _preflight_fixture(tmp_path, with_prior=False)

    result = subprocess.run(  # noqa: S603
        arguments,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_traycer_preflight_accepts_the_label_bound_not_found_variant(
    tmp_path: Path,
) -> None:
    """A known launchctl wording remains absent when it names the exact target."""
    arguments, env = _preflight_fixture(tmp_path, with_prior=False)
    env["FAKE_BASE_MODE"] = "not-found-specified"
    env["FAKE_AGENT_MODE"] = "not-found-specified"

    result = subprocess.run(  # noqa: S603
        arguments,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_traycer_preflight_rejects_smappservice_and_indeterminate_states(
    tmp_path: Path,
) -> None:
    """Vendor-managed, mismatched, and unreadable jobs must block app replacement."""
    cases = (
        ("smappservice", "not-found", True, "SMAppService"),
        ("not-found", "smappservice", False, "SMAppService"),
        ("mismatched", "not-found", True, "does not match"),
        ("indeterminate", "not-found", False, "could not determine"),
        ("not-found", "indeterminate", False, "could not determine"),
        (
            "not-found-specified-wrong-label",
            "not-found",
            False,
            "could not determine",
        ),
        (
            "not-found-specified-wrong-status",
            "not-found",
            False,
            "could not determine",
        ),
        ("exact", "not-found", False, "no exact prior Nix"),
    )
    for index, (base_mode, agent_mode, with_prior, error) in enumerate(cases):
        case_root = tmp_path / str(index)
        arguments, env = _preflight_fixture(case_root, with_prior=with_prior)
        env["FAKE_BASE_MODE"] = base_mode
        env["FAKE_AGENT_MODE"] = agent_mode

        result = subprocess.run(  # noqa: S603
            arguments,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0, (base_mode, agent_mode)
        assert error in result.stderr, result.stderr
