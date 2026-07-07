"""Regression checks for macOS application bundle management."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import threading
from contextlib import redirect_stderr
from functools import cache
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib import mac_apps_helper
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.tests._nix_eval import nix_attrset, nix_eval_raw, nix_import, nix_let, nix_list
from lib.tests._nix_source import nix_file_expr, nix_source_fragment_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression


@cache
def _module_output(relative_path: str) -> AttributeSet:
    """Parse one Nix module and return its top-level output attrset."""
    expr = expect_instance(
        parse_nix_expr(Path(REPO_ROOT / relative_path).read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    return expect_instance(expr.output, AttributeSet)


@cache
def _rsync_path() -> str:
    """Return a real rsync path for shell-script smoke tests."""
    return shutil.which("rsync") or "/usr/bin/rsync"


def _mac_apps_eval(expr: NixExpression) -> str:
    """Evaluate only the tiny mac-apps cases that require rendered shell or message output."""
    wrapped_expr = nix_let(
        {
            "context": FunctionCall(
                name=nix_import(REPO_ROOT / "tests/nix/mac-apps/eval-context.nix"),
                argument=nix_attrset({"rsyncPath": _rsync_path()}),
            ),
            "macApps": identifier_attr_path("context", "macApps"),
        },
        expr,
    )
    return nix_eval_raw(wrapped_expr)


def _fake_mac_app_package(
    pname: str,
    out_path: str,
    bundle_name: str,
    *,
    bundle_rel_path: str | None = None,
    install_mode: str | None = None,
) -> AttributeSet:
    """Build one fake package carrying ``passthru.macApp`` metadata."""
    mac_app: dict[str, str] = {
        "bundleName": bundle_name,
        "bundleRelPath": bundle_rel_path or f"Applications/{bundle_name}",
    }
    if install_mode is not None:
        mac_app["installMode"] = install_mode
    return nix_attrset({
        "pname": pname,
        "outPath": out_path,
        "passthru.macApp": mac_app,
    })


def _managed_app_overlap_assertion_result(
    package_lists: list[AttributeSet],
) -> dict[str, object]:
    """Evaluate ``managedAppsNotInPackageListsAssertion`` and decode its JSON."""
    expression = nix_let(
        {
            "managedPkg": _fake_mac_app_package(
                "cursor",
                "/nix/store/fake-cursor",
                "Cursor.app",
            )
        },
        FunctionCall(
            name=identifier_attr_path("builtins", "toJSON"),
            argument=Parenthesis(
                value=FunctionCall(
                    name=identifier_attr_path(
                        "macApps", "managedAppsNotInPackageListsAssertion"
                    ),
                    argument=nix_attrset({
                        "entries": nix_list([
                            nix_attrset({
                                "package": Identifier(name="managedPkg"),
                                "bundleName": "Cursor.app",
                                "mode": "copy",
                            })
                        ]),
                        "packageLists": nix_list(package_lists),
                    }),
                )
            ),
        ),
    )
    payload = json.loads(_mac_apps_eval(expression))
    assert isinstance(payload, dict)
    return payload


def _mac_apps_fragment_expr(start_marker: str, end_marker: str):
    return nix_source_fragment_expr("lib/mac-apps.nix", start_marker, end_marker)


def _curried_call(
    name: NixExpression,
    first_arg: NixExpression,
    second_arg: NixExpression,
) -> FunctionCall:
    """Build one curried function application with stable precedence."""
    rendered_first_arg: NixExpression = first_arg
    if not isinstance(
        first_arg,
        Identifier | NixList | Parenthesis | Primitive | Select | StringPrimitive,
    ):
        rendered_first_arg = Parenthesis(value=first_arg)
    return FunctionCall(
        name=FunctionCall(name=name, argument=rendered_first_arg),
        argument=Parenthesis(value=second_arg),
    )


def _system_applications_script_expr(
    entries: NixExpression,
    *,
    state_directory: str = "/Applications/.nixcfg-mac-apps",
    state_name: str,
    target_directory: str,
    writable: bool,
) -> FunctionCall:
    """Build the expected ``macApps.applicationsScript`` invocation."""
    return FunctionCall(
        name=identifier_attr_path("macApps", "applicationsScript"),
        argument=nix_attrset({
            "entries": entries,
            "stateDirectory": state_directory,
            "stateName": state_name,
            "targetDirectory": target_directory,
            "writable": writable,
        }),
    )


def _mac_app_metadata_attrset(
    bundle_name: object,
    bundle_rel_path: object,
    install_mode: str,
) -> AttributeSet:
    """Build the expected ``passthru.macApp`` metadata attrset."""
    return nix_attrset({
        "macApp": {
            "bundleName": bundle_name,
            "bundleRelPath": bundle_rel_path,
            "installMode": install_mode,
        }
    })


def test_managed_mac_app_routing_projection_helper_splits_exclusions_from_apps() -> (
    None
):
    """The shared helper should keep exclusion stripping as a pure structural projection."""
    projection = expect_instance(
        _mac_apps_fragment_expr(
            "  managedMacAppRoutingProjection = ",
            "\n\n  resolveApplications =",
        ),
        FunctionDefinition,
    )
    expected_projection = (
        "{\n"
        "  excludePackagesByName = unique (\n"
        "    concatLists (map entryPackageNamesForExclusion (attrValues managedMacAppRouting))\n"
        "  );\n"
        "  applications = mapAttrs' (\n"
        "    name: entry:\n"
        "    nameValuePair name (\n"
        "      builtins.removeAttrs entry [\n"
        '        "excludePackageName"\n'
        '        "excludePackageNames"\n'
        "      ]\n"
        "    )\n"
        "  ) managedMacAppRouting;\n"
        "}"
    )

    assert projection.argument_set.rebuild() == "managedMacAppRouting"
    assert_nix_ast_equal(projection.output, expected_projection)


def test_copy_mode_replaces_symlinked_application_destinations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Copy-mode installs should replace old symlink targets before rsync."""
    source_bundle = tmp_path / "source" / "Example.app"
    target_directory = tmp_path / "Applications"
    old_target = tmp_path / "old-target"
    destination = target_directory / "Example.app"
    source_bundle.mkdir(parents=True)
    target_directory.mkdir()
    old_target.mkdir()
    destination.symlink_to(old_target)

    captured: dict[str, Path | bool | str] = {}

    def _fake_rsync_copy(
        src: Path,
        dst: Path,
        *,
        rsync_path: str,
        writable: bool,
    ) -> None:
        captured["src"] = src
        captured["dst"] = dst
        captured["rsync_path"] = rsync_path
        captured["writable"] = writable
        assert dst.exists()
        assert dst.is_dir()
        assert not dst.is_symlink()

    monkeypatch.setattr(mac_apps_helper, "_rsync_copy", _fake_rsync_copy)

    mac_apps_helper._install_managed_app(
        bundle_name="Example.app",
        mode="copy",
        source_path=str(source_bundle),
        target_directory=target_directory,
        rsync_path="/usr/bin/rsync",
        writable=False,
    )

    assert captured == {
        "src": source_bundle,
        "dst": destination,
        "rsync_path": "/usr/bin/rsync",
        "writable": False,
    }
    assert destination.exists()
    assert destination.is_dir()
    assert not destination.is_symlink()


def test_launch_services_registration_ignores_non_app_bundles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only real app bundles should trigger LaunchServices registration."""

    def _unexpected_run(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError("lsregister should not run")

    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _unexpected_run)

    mac_apps_helper._refresh_launch_services_registration(
        tmp_path / "Empty.app",
        lsregister_path=tmp_path / "lsregister",
    )

    app_bundle = tmp_path / "Example.app"
    (app_bundle / "Contents").mkdir(parents=True)
    (app_bundle / "Contents" / "Info.plist").write_text("", encoding="utf-8")

    mac_apps_helper._refresh_launch_services_registration(
        app_bundle,
        lsregister_path=tmp_path / "missing-lsregister",
    )


def test_launch_services_registration_refreshes_app_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Materialized app bundles should refresh stale LaunchServices metadata."""
    app_bundle = tmp_path / "Example.app"
    (app_bundle / "Contents").mkdir(parents=True)
    (app_bundle / "Contents" / "Info.plist").write_text("", encoding="utf-8")
    lsregister = tmp_path / "lsregister"
    lsregister.write_text("", encoding="utf-8")

    calls: list[list[str]] = []

    def _run(
        command: list[str],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert check is False
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run)

    mac_apps_helper._refresh_launch_services_registration(
        app_bundle,
        lsregister_path=lsregister,
    )

    assert calls == [[str(lsregister), "-f", str(app_bundle)]]


def test_launch_services_registration_warns_when_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """LaunchServices refresh failures should warn without breaking activation."""
    app_bundle = tmp_path / "Example.app"
    (app_bundle / "Contents").mkdir(parents=True)
    (app_bundle / "Contents" / "Info.plist").write_text("", encoding="utf-8")
    lsregister = tmp_path / "lsregister"
    lsregister.write_text("", encoding="utf-8")

    def _run(
        command: list[str],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        return subprocess.CompletedProcess(command, 73)

    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run)

    mac_apps_helper._refresh_launch_services_registration(
        app_bundle,
        lsregister_path=lsregister,
    )

    assert capsys.readouterr().err == (
        "warning: could not refresh LaunchServices registration for "
        f"{app_bundle}: exit 73\n"
    )


def test_mac_app_entry_defaults_to_copy_mode() -> None:
    """Managed GUI apps should materialize as real bundles unless explicitly overridden."""
    entry_config = expect_instance(
        _mac_apps_fragment_expr("      config = ", "\n    }\n  );"),
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(entry_config.values, "mode").value,
        'mkDefault (attrByPath [ "passthru" "macApp" "installMode" ] "copy" config.package)',
    )


def test_shared_darwin_app_helpers_default_to_copy_mode_metadata() -> None:
    """Shared macOS app helpers should advertise copy mode for dockable bundles."""
    mac_app = expect_instance(
        nix_source_fragment_expr(
            "overlays/_lib/helpers/darwin-apps.nix",
            "      macApp = ",
            "\n      // macApp;",
        ),
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "installMode").value,
        StringPrimitive(value="copy"),
    )


def test_pkg_app_packages_use_shared_helper() -> None:
    """Direct macOS pkg app packages should use the shared helper."""
    cases: tuple[tuple[str, str, str, bool | None], ...] = (
        ("packages/nordvpn/default.nix", "nordvpn", "NordVPN.app", None),
        ("packages/tailscale-app/default.nix", "tailscale-app", "Tailscale.app", True),
    )

    for relative_path, pname, bundle_name, copy_contents in cases:
        package_source = Path(REPO_ROOT / relative_path).read_text(encoding="utf-8")
        package = expect_instance(parse_nix_expr(package_source), FunctionDefinition)
        derivation = expect_instance(package.output, FunctionCall)
        derivation_args = expect_instance(derivation.argument, AttributeSet)

        assert_nix_ast_equal(derivation.name, Identifier(name="mkPkgApp"))
        assert_nix_ast_equal(
            expect_binding(derivation_args.values, "pname").value,
            StringPrimitive(value=pname),
        )
        assert_nix_ast_equal(
            expect_binding(derivation_args.values, "bundleName").value,
            StringPrimitive(value=bundle_name),
        )
        if copy_contents is not None:
            assert_nix_ast_equal(
                expect_binding(derivation_args.values, "copyContents").value,
                Primitive(value=copy_contents),
            )


def test_pkg_app_helper_expands_pkg_into_fresh_destination() -> None:
    """Pkgutil --expand-full expects to create its destination directory."""
    install_phase = expect_instance(
        nix_source_fragment_expr(
            "overlays/_lib/helpers/darwin-apps.nix",
            "      installPhase = ",
            ";\n    };",
            occurrence=3,
        ),
        IndentedString,
    )
    install_shell = parse_shell(indented_string_body(install_phase.rebuild()))

    assert command_texts(install_shell, "rm") == ['rm -rf "$pkg_dir"']
    assert 'mkdir -p "$pkg_dir" "$out/Applications"' not in command_texts(
        install_shell,
        "mkdir",
    )
    assert 'mkdir -p "$out/Applications"' in command_texts(install_shell, "mkdir")
    assert command_texts(install_shell, "/usr/sbin/pkgutil") == [
        '/usr/sbin/pkgutil --expand-full "$src" "$pkg_dir"'
    ]


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_manifest_cleanup_checks_other_mac_app_managers_first(tmp_path: Path) -> None:
    """Stale cleanup logic lives in Python; keep the Nix wrapper structurally wired."""
    target_directory = tmp_path / "Applications"
    state_directory = tmp_path / ".nixcfg-mac-apps"
    stale_app = target_directory / "Cursor.app"
    fake_package = tmp_path / "fake-package"
    fake_bundle = fake_package / "Applications" / "Fake.app"

    stale_app.mkdir(parents=True)
    fake_bundle.mkdir(parents=True)
    state_directory.mkdir()
    (state_directory / "test-manager.txt").write_text("Cursor.app\n", encoding="utf-8")
    (state_directory / "other-manager.txt").write_text("Cursor.app\n", encoding="utf-8")

    system_script = expect_instance(
        _mac_apps_fragment_expr(
            "  applicationsScript =\n", "\n\n  systemApplicationsScript ="
        ),
        FunctionDefinition,
    )
    assert [argument.rebuild() for argument in system_script.argument_set] == [
        "entries",
        "stateDirectory",
        "stateName",
        "writable",
        'targetDirectory ? "/Applications"',
    ]
    system_call = expect_instance(system_script.output, FunctionCall)
    assert_nix_ast_equal(system_call.name, 'callMacAppsHelper "system-applications"')
    system_args = expect_instance(system_call.argument, AttributeSet)
    assert_nix_ast_equal(
        system_args,
        """
        {
          inherit stateDirectory stateName targetDirectory writable;
          entries = helperEntries;
          rsyncPath = getExe pkgs.rsync;
        }
        """,
    )

    stderr = StringIO()
    with redirect_stderr(stderr):
        mac_apps_helper._system_applications({
            "entries": [
                {
                    "bundleName": "Fake.app",
                    "mode": "symlink",
                    "sourcePath": str(fake_bundle),
                }
            ],
            "rsyncPath": _rsync_path(),
            "stateDirectory": str(state_directory),
            "stateName": "test-manager",
            "targetDirectory": str(target_directory),
            "writable": False,
        })

    assert stderr.getvalue() == (
        f"keeping {stale_app} because another manifest still manages it...\n"
        f"setting up {target_directory / 'Fake.app'}...\n"
    )
    assert stale_app.is_dir()
    assert (target_directory / "Fake.app").is_symlink()
    assert (target_directory / "Fake.app").resolve() == fake_bundle.resolve()
    assert (state_directory / "test-manager.txt").read_text(
        encoding="utf-8"
    ) == "Fake.app\n"
    assert (state_directory / "other-manager.txt").read_text(encoding="utf-8") == (
        "Cursor.app\n"
    )


def test_system_applications_removes_read_only_stale_copied_bundle(
    tmp_path: Path,
) -> None:
    """System cleanup should remove bundles previously copied with writable=false."""
    target_directory = tmp_path / "Applications"
    state_directory = tmp_path / ".nixcfg-mac-apps"
    stale_bundle = target_directory / "Stale.app"
    stale_contents = stale_bundle / "Contents"
    stale_info = stale_contents / "Info.plist"

    stale_contents.mkdir(parents=True)
    stale_info.write_text("old", encoding="utf-8")
    state_directory.mkdir()
    (state_directory / "test-manager.txt").write_text("Stale.app\n", encoding="utf-8")

    stale_info.chmod(stat.S_IRUSR)
    stale_contents.chmod(stat.S_IRUSR | stat.S_IXUSR)
    stale_bundle.chmod(stat.S_IRUSR | stat.S_IXUSR)

    try:
        mac_apps_helper._system_applications({
            "entries": [],
            "rsyncPath": _rsync_path(),
            "stateDirectory": str(state_directory),
            "stateName": "test-manager",
            "targetDirectory": str(target_directory),
            "writable": False,
        })
    finally:
        if stale_contents.exists():
            stale_contents.chmod(stat.S_IRWXU)
        if stale_bundle.exists():
            stale_bundle.chmod(stat.S_IRWXU)

    assert not stale_bundle.exists()
    assert (state_directory / "test-manager.txt").read_text(encoding="utf-8") == ""


def test_system_applications_installs_current_apps_concurrently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Current app setup should run independent bundle installs in parallel."""
    target_directory = tmp_path / "Applications"
    state_directory = tmp_path / ".nixcfg-mac-apps"
    barrier = threading.Barrier(2, timeout=5)
    lock = threading.Lock()
    installed: list[str] = []

    def _fake_install_managed_app(*, bundle_name: str, **_kwargs: object) -> None:
        barrier.wait()
        with lock:
            installed.append(bundle_name)

    monkeypatch.setattr(
        mac_apps_helper,
        "_install_managed_app",
        _fake_install_managed_app,
    )

    mac_apps_helper._system_applications({
        "entries": [
            {
                "bundleName": "First.app",
                "mode": "copy",
                "sourcePath": str(tmp_path / "source" / "First.app"),
            },
            {
                "bundleName": "Second.app",
                "mode": "copy",
                "sourcePath": str(tmp_path / "source" / "Second.app"),
            },
        ],
        "rsyncPath": _rsync_path(),
        "stateDirectory": str(state_directory),
        "stateName": "test-manager",
        "targetDirectory": str(target_directory),
        "writable": False,
    })

    assert sorted(installed) == ["First.app", "Second.app"]
    assert (state_directory / "test-manager.txt").read_text(
        encoding="utf-8"
    ) == "First.app\nSecond.app\n"


@pytest.mark.parametrize(
    ("payload_updates", "expected_field"),
    [
        ({"stateName": "../manager"}, "stateName"),
        (
            {
                "entries": [
                    {
                        "bundleName": "../Escape.app",
                        "mode": "symlink",
                        "sourcePath": "/nix/store/fake/Applications/Escape.app",
                    }
                ]
            },
            "entries.bundleName",
        ),
    ],
)
def test_system_applications_rejects_nested_payload_path_components(
    payload_updates: dict[str, object],
    expected_field: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """System activation payload names must not escape managed state directories."""
    payload: dict[str, object] = {
        "entries": [],
        "rsyncPath": _rsync_path(),
        "stateDirectory": str(tmp_path / ".nixcfg-mac-apps"),
        "stateName": "test-manager",
        "targetDirectory": str(tmp_path / "Applications"),
        "writable": False,
    }
    payload.update(payload_updates)

    with pytest.raises(SystemExit) as exc:
        mac_apps_helper._system_applications(payload)

    assert exc.value.code == 2
    assert f"payload field '{expected_field}' must contain only path components" in (
        capsys.readouterr().err
    )


def test_system_applications_rejects_nested_manifest_entries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Corrupted state manifests must not drive deletion outside /Applications."""
    state_directory = tmp_path / ".nixcfg-mac-apps"
    state_directory.mkdir()
    (state_directory / "test-manager.txt").write_text(
        "../Escape.app\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        mac_apps_helper._system_applications({
            "entries": [],
            "rsyncPath": _rsync_path(),
            "stateDirectory": str(state_directory),
            "stateName": "test-manager",
            "targetDirectory": str(tmp_path / "Applications"),
            "writable": False,
        })

    assert exc.value.code == 2
    assert "payload field 'manifest entry' must contain only path components" in (
        capsys.readouterr().err
    )


def test_embedded_home_manager_defers_system_app_management_to_darwin() -> None:
    """Integrated nix-darwin and Home Manager should each own their scoped app dir."""
    darwin_config = expect_instance(
        expect_binding(
            _module_output("modules/darwin/base.nix").values, "config"
        ).value,
        AttributeSet,
    )
    darwin_system = expect_instance(
        expect_binding(darwin_config.values, "system").value,
        AttributeSet,
    )
    darwin_activation_scripts = expect_instance(
        expect_binding(darwin_system.values, "activationScripts").value,
        AttributeSet,
    )
    darwin_applications = expect_instance(
        expect_binding(darwin_activation_scripts.values, "applications").value,
        AttributeSet,
    )
    darwin_text = expect_binding(darwin_applications.values, "text").value.rebuild()
    assert "macApps.applicationsScript" in darwin_text
    assert "entries = activeMacAppEntries;" in darwin_text
    assert 'stateDirectory = "/Applications/.nixcfg-mac-apps";' in darwin_text
    assert 'stateName = "darwin-system";' in darwin_text
    assert 'targetDirectory = "/Applications";' in darwin_text
    assert "writable = false;" in darwin_text

    home_config = expect_instance(
        expect_binding(
            _module_output("modules/home/darwin.nix").values, "config"
        ).value,
        AttributeSet,
    )
    targets = expect_instance(
        expect_binding(home_config.values, "targets").value,
        AttributeSet,
    )
    darwin_targets = expect_instance(
        expect_binding(targets.values, "darwin").value,
        AttributeSet,
    )
    copy_apps = expect_instance(
        expect_binding(darwin_targets.values, "copyApps").value,
        AttributeSet,
    )
    link_apps = expect_instance(
        expect_binding(darwin_targets.values, "linkApps").value,
        AttributeSet,
    )
    assert expect_binding(copy_apps.values, "enable").value.rebuild() == "false"
    assert expect_binding(link_apps.values, "enable").value.rebuild() == "false"

    home_binding = expect_instance(
        expect_binding(home_config.values, "home").value,
        AttributeSet,
    )
    home_activation = expect_instance(
        expect_binding(home_binding.values, "activation").value,
        AttributeSet,
    )
    user_activation = expect_binding(
        home_activation.values, "nixcfgUserApplications"
    ).value.rebuild()
    assert "lib.mkIf (userEntries != [ ])" in user_activation
    assert (
        'lib.hm.dag.entryAfter [ "nixcfgRemoveManagedApplicationProfileCopies" ]'
        in user_activation
    )
    assert "macApps.applicationsScript" in user_activation
    assert "entries = userEntries;" in user_activation
    assert (
        'stateDirectory = "${config.home.homeDirectory}/Applications/.nixcfg-mac-apps";'
        in user_activation
    )
    assert 'stateName = "home-manager-user";' in user_activation
    assert (
        'targetDirectory = "${config.home.homeDirectory}/Applications";'
        in user_activation
    )
    assert "writable = true;" in user_activation


def test_home_manager_mac_app_module_asserts_managed_apps_stay_out_of_home_packages() -> (
    None
):
    """Managed macOS app bundles should not also be installed via ``home.packages``."""
    home_config = expect_instance(
        expect_binding(
            _module_output("modules/home/darwin.nix").values, "config"
        ).value,
        AttributeSet,
    )
    assertions = expect_instance(
        expect_binding(home_config.values, "assertions").value,
        FunctionCall,
    )
    optionals_call = expect_instance(assertions.name, FunctionCall)
    assert_nix_ast_equal(optionals_call.name, identifier_attr_path("lib", "optionals"))
    assert optionals_call.argument.rebuild() == "(managedEntries != [ ])"

    assertion_list = expect_instance(assertions.argument, NixList).value
    assert len(assertion_list) == 2

    unique_call = expect_instance(
        expect_instance(assertion_list[0], Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        unique_call.name,
        identifier_attr_path("macApps", "uniqueBundleNamesAssertion"),
    )
    assert unique_call.argument.rebuild() == "managedEntries"

    overlap_call = expect_instance(
        expect_instance(assertion_list[1], Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        overlap_call.name,
        identifier_attr_path("macApps", "managedAppsNotInPackageListsAssertion"),
    )
    overlap_args = expect_instance(overlap_call.argument, AttributeSet)
    assert (
        expect_binding(overlap_args.values, "entries").value.rebuild()
        == "managedEntries"
    )

    package_lists = expect_instance(
        expect_binding(overlap_args.values, "packageLists").value,
        NixList,
    )
    assert len(package_lists.value) == 1
    package_list = expect_instance(package_lists.value[0], AttributeSet)
    label = expect_instance(
        expect_binding(package_list.values, "label").value,
        StringPrimitive,
    )
    assert label.value == "home.packages"
    assert_nix_ast_equal(
        package_list,
        AttributeSet(
            values=[
                Binding(name="label", value=StringPrimitive(value="home.packages")),
                Inherit(
                    from_expression=identifier_attr_path("config", "home"),
                    names=[Identifier(name="packages")],
                ),
            ]
        ),
    )


def test_home_manager_mac_app_module_removes_profile_copies_before_user_apps() -> None:
    """Stale Home Manager copies should be removed before user app installation."""
    home_config = expect_instance(
        expect_binding(
            _module_output("modules/home/darwin.nix").values, "config"
        ).value,
        AttributeSet,
    )
    home_binding = expect_instance(
        expect_binding(home_config.values, "home").value, AttributeSet
    )
    home_activation = expect_instance(
        expect_binding(home_binding.values, "activation").value,
        AttributeSet,
    )
    cleanup = expect_instance(
        expect_binding(
            home_activation.values, "nixcfgRemoveManagedApplicationProfileCopies"
        ).value,
        FunctionCall,
    )
    mk_if = expect_instance(cleanup.name, FunctionCall)
    assert_nix_ast_equal(mk_if.name, identifier_attr_path("lib", "mkIf"))
    assert mk_if.argument.rebuild() == "(managedEntries != [ ])"

    entry_after = expect_instance(
        expect_instance(cleanup.argument, Parenthesis).value, FunctionCall
    )
    entry_after_name = expect_instance(entry_after.name, FunctionCall)
    assert entry_after_name.name.rebuild() == "lib.hm.dag.entryAfter"
    assert_nix_ast_equal(entry_after_name.argument, nix_list(["installPackages"]))

    remove_copies = expect_instance(
        expect_instance(entry_after.argument, Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        remove_copies.name,
        identifier_attr_path("macApps", "removeProfileCopiesScript"),
    )
    remove_args = expect_instance(remove_copies.argument, AttributeSet)
    assert (
        expect_binding(remove_args.values, "bundleNames").value.rebuild()
        == "managedBundleNames"
    )
    assert_nix_ast_equal(
        expect_binding(remove_args.values, "targetDirectory").value,
        identifier_attr_path("config", "targets", "darwin", "copyApps", "directory"),
    )


def test_home_manager_mac_app_module_audits_profile_bundle_leaks() -> None:
    """Home Manager should audit profile package outputs for managed app bundles."""
    home_config = expect_instance(
        expect_binding(
            _module_output("modules/home/darwin.nix").values, "config"
        ).value,
        AttributeSet,
    )
    home_binding = expect_instance(
        expect_binding(home_config.values, "home").value, AttributeSet
    )
    home_activation = expect_instance(
        expect_binding(home_binding.values, "activation").value,
        AttributeSet,
    )
    audit = expect_instance(
        expect_binding(home_activation.values, "nixcfgProfileAppBundleAudit").value,
        FunctionCall,
    )
    mk_if = expect_instance(audit.name, FunctionCall)
    assert_nix_ast_equal(mk_if.name, identifier_attr_path("lib", "mkIf"))
    assert mk_if.argument.rebuild() == "(managedEntries != [ ])"

    entry_after = expect_instance(
        expect_instance(audit.argument, Parenthesis).value, FunctionCall
    )
    entry_after_name = expect_instance(entry_after.name, FunctionCall)
    assert_nix_ast_equal(
        entry_after_name.name,
        identifier_attr_path("lib", "hm", "dag", "entryAfter"),
    )
    assert_nix_ast_equal(entry_after_name.argument, nix_list(["installPackages"]))

    leak_audit = expect_instance(
        expect_instance(entry_after.argument, Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        leak_audit.name,
        identifier_attr_path("macApps", "profileBundleLeakAuditScript"),
    )
    leak_audit_args = expect_instance(leak_audit.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(leak_audit_args.values, "packagePaths").value,
        FunctionCall(
            name=FunctionCall(
                name=Identifier(name="map"),
                argument=Identifier(name="toString"),
            ),
            argument=identifier_attr_path("config", "home.packages"),
        ),
    )
    assert any(
        isinstance(expr, Inherit)
        and [name.rebuild() for name in expr.names] == ["managedBundleNames"]
        for expr in leak_audit_args.values
    )
    label = expect_instance(
        expect_binding(leak_audit_args.values, "label").value,
        StringPrimitive,
    )
    assert label.value == "home.packages"


def test_profile_bundle_leak_audit_script_reports_managed_bundle_exposure(
    tmp_path: Path,
) -> None:
    """Python leak audit behavior should stay aligned with the Nix helper wrapper."""
    managed_package = tmp_path / "cursor-package"
    (managed_package / "Applications" / "Cursor.app").mkdir(parents=True)

    leak_script = expect_instance(
        _mac_apps_fragment_expr(
            "  profileBundleLeakAuditScript =\n",
            "\n\n  applicationsScript =",
        ),
        FunctionDefinition,
    )
    assert [argument.rebuild() for argument in leak_script.argument_set] == [
        "packagePaths",
        "managedBundleNames",
        'label ? "home.packages"',
    ]
    leak_call = expect_instance(leak_script.output, FunctionCall)
    assert_nix_ast_equal(
        leak_call.name,
        'callMacAppsHelper "profile-bundle-leak-audit"',
    )
    leak_args = expect_instance(leak_call.argument, AttributeSet)
    assert_nix_ast_equal(
        leak_args,
        """
        {
          inherit label packagePaths;
          managedBundleNames = uniqueManagedBundleNames;
        }
        """,
    )

    stderr = StringIO()
    with redirect_stderr(stderr), pytest.raises(SystemExit) as exc:
        mac_apps_helper._profile_bundle_leak_audit({
            "label": "home.packages",
            "managedBundleNames": ["Cursor.app"],
            "packagePaths": [str(managed_package)],
        })

    assert exc.value.code == 1
    assert (
        "Managed macOS app bundles must not be exposed through home.packages."
        in stderr.getvalue()
    )
    assert f" - Cursor.app <= {managed_package}" in stderr.getvalue()


def test_profile_bundle_leak_audit_script_ignores_unmanaged_bundle_exposure(
    tmp_path: Path,
) -> None:
    """Unmanaged bundles should be ignored by the Python leak audit helper."""
    unrelated_package = tmp_path / "spotify-package"
    (unrelated_package / "Applications" / "Spotify.app").mkdir(parents=True)

    stderr = StringIO()
    with redirect_stderr(stderr):
        mac_apps_helper._profile_bundle_leak_audit({
            "label": "home.packages",
            "managedBundleNames": ["Cursor.app"],
            "packagePaths": [str(unrelated_package)],
        })

    assert stderr.getvalue() == ""


def test_remove_profile_copies_script_removes_read_only_stale_bundles(
    tmp_path: Path,
) -> None:
    """Profile-copy cleanup should unblock Home Manager's App Management check."""
    remove_script = expect_instance(
        _mac_apps_fragment_expr(
            "  removeProfileCopiesScript =\n",
            "\n\n  profileBundleLeakAuditScript =",
        ),
        FunctionDefinition,
    )
    assert [argument.rebuild() for argument in remove_script.argument_set] == [
        "bundleNames",
        "targetDirectory",
    ]
    remove_call = expect_instance(remove_script.output, FunctionCall)
    assert_nix_ast_equal(
        remove_call.name,
        'callMacAppsHelper "remove-profile-copies"',
    )
    remove_args = expect_instance(remove_call.argument, AttributeSet)
    assert_nix_ast_equal(
        remove_args,
        """
        {
          inherit targetDirectory;
          bundleNames = uniqueBundleNames;
        }
        """,
    )

    target_directory = tmp_path / "Home Manager Apps"
    target_directory.mkdir()
    stale_bundle = target_directory / "Emdash.app"
    stale_contents = stale_bundle / "Contents"
    stale_contents.mkdir(parents=True)
    stale_info = stale_contents / "Info.plist"
    stale_info.write_text("old", encoding="utf-8")
    stale_info.chmod(stat.S_IRUSR)
    stale_link = stale_contents / "StoreLink"
    stale_link.symlink_to(tmp_path / "missing-store-target")
    stale_contents.chmod(stat.S_IRUSR | stat.S_IXUSR)
    stale_bundle.chmod(stat.S_IRUSR | stat.S_IXUSR)

    stale_file = target_directory / "DataGrip.app"
    stale_file.write_text("not a directory", encoding="utf-8")
    stale_file.chmod(stat.S_IRUSR)

    stderr = StringIO()
    with redirect_stderr(stderr):
        mac_apps_helper._remove_profile_copies({
            "bundleNames": ["Missing.app", "Emdash.app", "DataGrip.app"],
            "targetDirectory": str(target_directory),
        })

    assert not stale_bundle.exists()
    assert not stale_file.exists()
    assert stderr.getvalue() == (
        "removing Home Manager copy of scoped managed app "
        f"{target_directory / 'Emdash.app'}...\n"
        "removing Home Manager copy of scoped managed app "
        f"{target_directory / 'DataGrip.app'}...\n"
    )

    mac_apps_helper._make_tree_user_writable(tmp_path / "missing-path")
    symlinked_directory = tmp_path / "Linked Apps"
    symlinked_directory.symlink_to(target_directory)
    mac_apps_helper._make_tree_user_writable(symlinked_directory)
    mac_apps_helper._chmod_user_writable(tmp_path / "missing-file")

    payload = tmp_path / "cleanup.json"
    payload.write_text(
        json.dumps({
            "bundleNames": [],
            "targetDirectory": str(symlinked_directory),
        }),
        encoding="utf-8",
    )
    assert mac_apps_helper.main(["prog", "remove-profile-copies", str(payload)]) == 0


def test_remove_profile_copies_script_removes_writable_apps_when_chmod_is_denied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Apple app metadata can deny chmod on bundles that are already removable."""
    target_directory = tmp_path / "Home Manager Apps"
    stale_bundle = target_directory / "AppCleaner.app"
    (stale_bundle / "Contents").mkdir(parents=True)

    def _deny_chmod(self: Path, mode: int) -> None:
        raise PermissionError(1, "Operation not permitted", str(self))

    monkeypatch.setattr(Path, "chmod", _deny_chmod)

    stderr = StringIO()
    with redirect_stderr(stderr):
        mac_apps_helper._remove_profile_copies({
            "bundleNames": ["AppCleaner.app"],
            "targetDirectory": str(target_directory),
        })

    assert not stale_bundle.exists()
    assert stderr.getvalue() == (
        "removing Home Manager copy of scoped managed app "
        f"{target_directory / 'AppCleaner.app'}...\n"
    )


def test_chmod_user_writable_skips_symlinks_and_warns_on_permission_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Writable repair should avoid symlinks and only warn when chmod is denied."""
    target = tmp_path / "target"
    target.write_text("x\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    mac_apps_helper._chmod_user_writable(link)

    locked = tmp_path / "locked"
    locked.write_text("x\n", encoding="utf-8")
    locked.chmod(stat.S_IRUSR)
    original_chmod = Path.chmod

    def _chmod(self: Path, mode: int) -> None:
        if self == locked:
            raise PermissionError("denied")
        original_chmod(self, mode)

    monkeypatch.setattr(Path, "chmod", _chmod)
    stderr = StringIO()
    with redirect_stderr(stderr):
        mac_apps_helper._chmod_user_writable(locked)

    assert "could not make" in stderr.getvalue()


@pytest.mark.parametrize("bundle_name", ["../Emdash.app", ".."])
def test_remove_profile_copies_script_rejects_nested_bundle_names(
    bundle_name: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cleanup payloads must not escape the configured profile app directory."""
    with pytest.raises(SystemExit) as exc:
        mac_apps_helper._remove_profile_copies({
            "bundleNames": [bundle_name],
            "targetDirectory": str(tmp_path),
        })

    assert exc.value.code == 2
    assert "payload field 'bundleNames' must contain only path components" in (
        capsys.readouterr().err
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_app_overlap_assertion_reports_conflicting_package_lists() -> None:
    """Managed app packages should not overlap with installed package lists."""
    result = _managed_app_overlap_assertion_result([
        nix_attrset({
            "label": "home.packages",
            "packages": nix_list([
                _fake_mac_app_package(
                    "cursor-wrapper",
                    "/nix/store/fake-wrapper",
                    "Cursor.app",
                )
            ]),
        })
    ])

    assert result == {
        "assertion": False,
        "message": (
            "nixcfg.macApps.applications packages must not also appear in other "
            "installed package lists.\n"
            "- Cursor.app (cursor) also appears in home.packages as cursor-wrapper."
        ),
    }


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_app_overlap_assertion_allows_distinct_package_lists() -> None:
    """Distinct package lists should not trip the managed app overlap assertion."""
    result = _managed_app_overlap_assertion_result([
        nix_attrset({
            "label": "home.packages",
            "packages": nix_list([
                _fake_mac_app_package(
                    "spotify",
                    "/nix/store/fake-spotify",
                    "Spotify.app",
                )
            ]),
        })
    ])

    assert result == {
        "assertion": True,
        "message": (
            "nixcfg.macApps.applications packages must not also appear in other "
            "installed package lists."
        ),
    }


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_app_overlap_assertion_ignores_unevaluable_package_outputs() -> None:
    """Platform-incompatible package outputs should not break overlap checks."""
    result = _managed_app_overlap_assertion_result([
        nix_attrset({
            "label": "home.packages",
            "packages": nix_list([
                nix_attrset({
                    "pname": "linux-only",
                    "outPath": parse_nix_expr('throw "unsupported"'),
                })
            ]),
        })
    ])

    assert result == {
        "assertion": True,
        "message": (
            "nixcfg.macApps.applications packages must not also appear in other "
            "installed package lists."
        ),
    }


def test_zoom_overlay_threads_self_source_version_and_copy_mode_mac_app_metadata() -> (
    None
):
    """The Zoom overlay should keep its local source wiring and copy-mode app contract."""
    overlay = _module_output("overlays/zoom-us/default.nix")

    zoom = expect_instance(
        expect_binding(overlay.values, "zoom-us").value, IfExpression
    )
    assert_nix_ast_equal(
        zoom.condition,
        identifier_attr_path("prev", "stdenv", "hostPlatform", "isDarwin"),
    )
    assert_nix_ast_equal(zoom.alternative, identifier_attr_path("prev", "zoom-us"))

    override_call = expect_instance(zoom.consequence, FunctionCall)
    assert_nix_ast_equal(
        override_call.name,
        identifier_attr_path("prev", "zoom-us", "overrideAttrs"),
    )
    override_fn = expect_instance(
        expect_instance(override_call.argument, Parenthesis).value,
        FunctionDefinition,
    )
    assert_nix_ast_equal(override_fn.argument_set, Identifier(name="old"))
    override_attrs = expect_instance(override_fn.output, AttributeSet)

    version_inherit = next(
        value for value in override_attrs.values if isinstance(value, Inherit)
    )
    assert_nix_ast_equal(version_inherit.from_expression, Identifier(name="selfSource"))
    assert [name.rebuild() for name in version_inherit.names] == ["version"]

    src_call = expect_instance(
        expect_binding(override_attrs.values, "src").value, FunctionCall
    )
    assert_nix_ast_equal(src_call.name, identifier_attr_path("prev", "fetchurl"))
    src_args = expect_instance(src_call.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(src_args.values, "url").value,
        identifier_attr_path("selfSource", "urls", "${system}"),
    )
    assert_nix_ast_equal(
        expect_binding(src_args.values, "hash").value,
        identifier_attr_path("selfSource", "hashes", "${system}"),
    )

    passthru = expect_instance(
        expect_binding(override_attrs.values, "passthru").value,
        BinaryExpression,
    )
    assert passthru.operator.name == "//"
    assert_nix_ast_equal(
        passthru.right,
        _mac_app_metadata_attrset(
            "zoom.us.app",
            "Applications/zoom.us.app",
            "copy",
        ),
    )


def test_netnewswire_package_exposes_copy_mode_mac_app_metadata() -> None:
    """The NetNewsWire package should expose copy-mode macApp metadata."""
    sources = json.loads(
        (REPO_ROOT / "packages/netnewswire/sources.json").read_text(encoding="utf-8")
    )
    package_source = Path(REPO_ROOT / "packages/netnewswire/default.nix").read_text(
        encoding="utf-8"
    )
    package = expect_instance(parse_nix_expr(package_source), FunctionDefinition)
    derivation = expect_instance(package.output, FunctionCall)
    derivation_args = expect_instance(derivation.argument, AttributeSet)

    assert isinstance(sources.get("version"), str)
    assert_nix_ast_equal(
        derivation.name,
        Identifier(name="mkZipApp"),
    )
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "info").value,
        Identifier(name="selfSource"),
    )
    mac_app_binding = expect_binding(derivation_args.values, "macApp").value
    assert isinstance(mac_app_binding, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(mac_app_binding.values, "installMode").value,
        StringPrimitive(value="copy"),
    )


def test_zen_twilight_package_embeds_autoconfig_and_resigns_app() -> None:
    """The Twilight package should carry nixcfg's app-bundle AutoConfig hook."""
    sources = json.loads(
        (REPO_ROOT / "packages/zen-twilight/sources.json").read_text(encoding="utf-8")
    )
    package_source = Path(REPO_ROOT / "packages/zen-twilight/default.nix").read_text(
        encoding="utf-8"
    )
    package = expect_instance(parse_nix_expr(package_source), FunctionDefinition)
    derivation = expect_instance(package.output, FunctionCall)
    derivation_args = expect_instance(derivation.argument, AttributeSet)

    assert isinstance(sources.get("version"), str)
    assert "buildID" not in sources
    assert_nix_ast_equal(derivation.name, Identifier(name="mkDmgApp"))
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "pname").value,
        StringPrimitive(value="zen-twilight"),
    )
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "appName").value,
        StringPrimitive(value="twilight"),
    )
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "executableName").value,
        StringPrimitive(value="zen"),
    )
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "codesignApp").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "macApp").value,
        '{ installMode = "copy"; }',
    )

    install_hook = expect_instance(
        expect_binding(derivation_args.values, "postInstallApp").value,
        IndentedString,
    )
    install_shell = parse_shell(indented_string_body(install_hook.rebuild()))

    assert command_texts(install_shell, "mkdir") == [
        'mkdir -p "$resources/defaults/pref"',
        'mkdir -p "$browser_resources/defaults/preferences"',
    ]
    assert command_texts(install_shell, "cp") == [
        'cp __NIX_INTERP__ "$resources/defaults/pref/autoconfig.js"',
        'cp __NIX_INTERP__ "$browser_resources/defaults/preferences/autoconfig.js"',
        'cp __NIX_INTERP__ "$resources/twilight.cfg"',
        'cp __NIX_INTERP__ "$browser_resources/twilight.cfg"',
    ]
    assert command_texts(install_shell, "zip") == []
    assert command_texts(install_shell, "unzip") == []


def test_george_config_manages_mutable_gui_apps_via_scoped_applications() -> None:
    """George's config should single-source managed macOS app routing."""
    root = _module_output("home/george/configuration.nix")
    nixcfg = expect_instance(expect_binding(root.values, "nixcfg").value, AttributeSet)

    def entry_for(table: AttributeSet, name: str) -> AttributeSet:
        try:
            entry_binding = expect_binding(table.values, name)
        except AssertionError:
            entry_binding = expect_binding(table.values, f'"{name}"')
        return expect_instance(entry_binding.value, AttributeSet)

    def package_for(table: AttributeSet, name: str) -> str:
        return expect_binding(entry_for(table, name).values, "package").value.rebuild()

    def scope_for(table: AttributeSet, name: str) -> str:
        return expect_binding(entry_for(table, name).values, "scope").value.rebuild()

    def has_scope(table: AttributeSet, name: str) -> bool:
        return any(
            isinstance(binding, Binding) and binding.name == "scope"
            for binding in entry_for(table, name).values
        )

    assert_nix_ast_equal(
        expect_scope_binding(nixcfg, "macAppHelpers").value,
        "import ../../lib/mac-apps.nix { inherit lib pkgs; }",
    )
    routing = expect_instance(
        expect_scope_binding(nixcfg, "managedMacAppRouting").value,
        BinaryExpression,
    )
    assert routing.operator.name == "//"
    base_routing = expect_instance(routing.left, AttributeSet)
    assert package_for(base_routing, "slack") == "pkgs.slack"
    assert package_for(base_routing, "ghostty") == "pkgs.ghostty-tip"
    assert package_for(base_routing, "zed") == "pkgs.zed-editor-nightly"
    assert package_for(base_routing, "zen-twilight") == "pkgs.zen-twilight"
    assert package_for(base_routing, "code-cursor") == "pkgs.code-cursor"
    assert package_for(base_routing, "vscode-insiders") == "pkgs.vscode-insiders"
    assert package_for(base_routing, "superset") == "pkgs.superset"
    assert package_for(base_routing, "goose") == "pkgs.goose-desktop"
    assert package_for(base_routing, "nordvpn") == "pkgs.nordvpn"
    assert scope_for(base_routing, "nordvpn") == '"system"'
    assert package_for(base_routing, "zoom") == "pkgs.zoom-us"
    assert not has_scope(base_routing, "zoom")

    work_call = expect_instance(routing.right, FunctionCall)
    assert work_call.name.rebuild() == "lib.optionalAttrs config.profiles.work.enable"
    work_routing = expect_instance(work_call.argument, AttributeSet)
    assert package_for(work_routing, "onepassword") == "pkgs.onepassword"
    assert scope_for(work_routing, "onepassword") == '"system"'
    tailscale = expect_instance(
        expect_binding(work_routing.values, "tailscale").value, AttributeSet
    )
    assert expect_binding(tailscale.values, "package").value.rebuild() == (
        "pkgs.tailscale-app"
    )
    assert all(
        not (isinstance(binding, Binding) and binding.name == "scope")
        for binding in tailscale.values
    )
    assert_nix_ast_equal(
        expect_scope_binding(nixcfg, "managedMacAppProjection").value,
        "macAppHelpers.managedMacAppRoutingProjection managedMacAppRouting",
    )

    package_sets = expect_instance(
        expect_binding(nixcfg.values, "packageSets").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(package_sets.values, "heavyOptional").value,
        "{ enable = lib.mkDefault false; }",
    )
    assert_nix_ast_equal(
        expect_binding(package_sets.values, "cloud").value,
        "{ enable = lib.mkDefault false; }",
    )
    exclude_packages_binding = next(
        (
            binding
            for binding in package_sets.values
            if isinstance(binding, Binding) and binding.name == "excludePackagesByName"
        ),
        None,
    )
    if exclude_packages_binding is not None:
        assert_nix_ast_equal(
            exclude_packages_binding.value,
            "managedMacAppProjection.excludePackagesByName",
        )
    else:
        exclude_packages_inherit = next(
            (
                inherit_expr
                for inherit_expr in package_sets.values
                if isinstance(inherit_expr, Inherit)
                and inherit_expr.from_expression is not None
                and inherit_expr.from_expression.rebuild() == "managedMacAppProjection"
                and [name.rebuild() for name in inherit_expr.names]
                == ["excludePackagesByName"]
            ),
            None,
        )
        assert exclude_packages_inherit is not None

    mac_apps = expect_instance(
        expect_binding(nixcfg.values, "macApps").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(mac_apps.values, "applications").value,
        "managedMacAppProjection.applications",
    )

    programs = expect_instance(
        expect_binding(root.values, "programs").value, AttributeSet
    )
    assert_nix_ast_equal(
        expect_binding(programs.values, "vscode").value,
        """
{
  enable = true;
  package = null;
}
// lib.optionalAttrs (options.programs.vscode ? nameShort) {
  pname = "vscode-insiders";
}
""",
    )


def test_managed_gui_app_tiny_overlays_keep_copy_mode_metadata_contracts() -> None:
    """The shared overlay should keep copy-mode macApp metadata on the targeted apps."""
    overlay_root = _module_output("overlays/default.nix")
    default_overlay = expect_instance(
        expect_binding(overlay_root.values, "default").value,
        FunctionDefinition,
    )
    overlay_fn = expect_instance(default_overlay.output, FunctionDefinition)
    tiny_overlays = expect_instance(
        expect_scope_binding(overlay_fn.output, "tinyOverlays").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(tiny_overlays.values, "chatgpt").value,
        _curried_call(
            Identifier(name="withManagedMacApp"),
            FunctionCall(
                name=FunctionCall(
                    name=identifier_attr_path("final", "mkSourceOverride"),
                    argument=StringPrimitive(value="chatgpt", raw_string=True),
                ),
                argument=identifier_attr_path("prev", "chatgpt"),
            ),
            StringPrimitive(value="ChatGPT.app", raw_string=True),
        ),
    )
    assert_nix_ast_equal(
        expect_binding(tiny_overlays.values, "code-cursor").value,
        _curried_call(
            Identifier(name="withManagedMacApp"),
            FunctionCall(
                name=FunctionCall(
                    name=identifier_attr_path("final", "mkSourceOverride"),
                    argument=StringPrimitive(value="code-cursor", raw_string=True),
                ),
                argument=identifier_attr_path("prev", "code-cursor"),
            ),
            StringPrimitive(value="Cursor.app", raw_string=True),
        ),
    )
    jetbrains = expect_instance(
        expect_binding(tiny_overlays.values, "jetbrains").value,
        BinaryExpression,
    )
    assert_nix_ast_equal(jetbrains.left, identifier_attr_path("prev", "jetbrains"))
    assert jetbrains.operator.name == "//"
    jetbrains_overrides = expect_instance(jetbrains.right, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(jetbrains_overrides.values, "datagrip").value,
        _curried_call(
            Identifier(name="withManagedMacApp"),
            FunctionCall(
                name=FunctionCall(
                    name=identifier_attr_path("final", "mkSourceOverride"),
                    argument=StringPrimitive(value="datagrip", raw_string=True),
                ),
                argument=identifier_attr_path("prev", "jetbrains", "datagrip"),
            ),
            StringPrimitive(value="DataGrip.app", raw_string=True),
        ),
    )


def test_vscode_insiders_overlay_keeps_copy_mode_mac_app_metadata_contract() -> None:
    """The VS Code Insiders overlay should keep its copy-mode app metadata contract."""
    overlay = _module_output("overlays/vscode-insiders/default.nix")

    vscode_overlay = expect_instance(
        expect_binding(overlay.values, "vscode-insiders").value,
        FunctionCall,
    )

    assert_nix_ast_equal(
        expect_scope_binding(vscode_overlay, "info").value,
        Identifier(name="selfSource"),
    )
    version_inherit = next(
        value for value in vscode_overlay.scope if isinstance(value, Inherit)
    )
    assert_nix_ast_equal(version_inherit.from_expression, Identifier(name="info"))
    assert [name.rebuild() for name in version_inherit.names] == ["version"]
    assert_nix_ast_equal(
        expect_scope_binding(vscode_overlay, "hash").value,
        identifier_attr_path("info", "hashes", "${system}"),
    )
    assert_nix_ast_equal(
        expect_scope_binding(vscode_overlay, "plat").value,
        Select(
            expression=nix_attrset({
                "aarch64-darwin": "darwin-arm64",
                "x86_64-darwin": "darwin",
                "aarch64-linux": "linux-arm64",
                "x86_64-linux": "linux-x64",
            }),
            attribute="${system}",
        ),
    )
    assert_nix_ast_equal(
        expect_scope_binding(vscode_overlay, "archive_fmt").value,
        IfExpression(
            condition=identifier_attr_path(
                "prev", "stdenv", "hostPlatform", "isDarwin"
            ),
            consequence=StringPrimitive(value="zip"),
            alternative=StringPrimitive(value="tar.gz"),
        ),
    )

    assert_nix_ast_equal(
        vscode_overlay.name,
        Select(
            expression=Parenthesis(
                value=FunctionCall(
                    name=identifier_attr_path("prev", "vscode", "override"),
                    argument=nix_attrset({"isInsiders": True}),
                )
            ),
            attribute="overrideAttrs",
        ),
    )
    override_fn = expect_instance(
        expect_instance(vscode_overlay.argument, Parenthesis).value,
        FunctionDefinition,
    )
    assert_nix_ast_equal(override_fn.argument_set, Identifier(name="old"))
    override_attrs = expect_instance(override_fn.output, AttributeSet)

    version_inherit = next(
        value for value in override_attrs.values if isinstance(value, Inherit)
    )
    assert version_inherit.from_expression is None
    assert [name.rebuild() for name in version_inherit.names] == ["version"]

    src_call = expect_instance(
        expect_binding(override_attrs.values, "src").value, FunctionCall
    )
    assert_nix_ast_equal(src_call.name, identifier_attr_path("prev", "fetchurl"))
    src_args = expect_instance(src_call.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(src_args.values, "name").value,
        StringPrimitive(
            value="VSCode-insiders-${version}-${plat}.${archive_fmt}",
            raw_string=True,
        ),
    )
    assert_nix_ast_equal(
        expect_binding(src_args.values, "url").value,
        identifier_attr_path("info", "urls", "${system}"),
    )
    src_hash_inherit = next(
        value for value in src_args.values if isinstance(value, Inherit)
    )
    assert src_hash_inherit.from_expression is None
    assert [name.rebuild() for name in src_hash_inherit.names] == ["hash"]

    meta = expect_instance(
        expect_binding(override_attrs.values, "meta").value, BinaryExpression
    )
    assert meta.operator.name == "//"
    assert_nix_ast_equal(
        meta.right,
        nix_attrset({
            "platforms": FunctionCall(
                name=identifier_attr_path("builtins", "attrNames"),
                argument=identifier_attr_path("info", "urls"),
            )
        }),
    )

    passthru = expect_instance(
        expect_binding(override_attrs.values, "passthru").value,
        BinaryExpression,
    )
    assert passthru.operator.name == "//"
    assert_nix_ast_equal(
        passthru.right,
        _mac_app_metadata_attrset(
            "Visual Studio Code - Insiders.app",
            "Applications/Visual Studio Code - Insiders.app",
            "copy",
        ),
    )


def test_dock_configs_keep_the_targeted_gc_mitigation_scope_explicit() -> None:
    """Dock modules should consume resolved app paths instead of hard-coded app dirs."""

    def dock_items(
        relative_path: str,
    ) -> tuple[str, list[str], list[str], list[str], FunctionCall, list[Inherit]]:
        expr = expect_instance(
            nix_file_expr(relative_path),
            FunctionDefinition,
        )
        call = expect_instance(expr.output, FunctionCall)
        assert call.name.rebuild() == "dock.mkDockModule"
        args = expect_instance(call.argument, AttributeSet)
        activation = expect_instance(
            expect_binding(args.values, "activationName").value, StringPrimitive
        ).value
        apps = [
            item.rebuild()
            for item in expect_instance(
                expect_binding(args.values, "apps").value, NixList
            ).value
        ]
        others = [
            item.rebuild()
            for item in expect_instance(
                expect_binding(args.values, "others").value, NixList
            ).value
        ]
        remove_others = [
            item.rebuild()
            for item in expect_instance(
                expect_binding(args.values, "removeOthers").value, NixList
            ).value
        ]
        dock_context = expect_instance(
            expect_scope_binding(call, "dockContext").value, FunctionCall
        )
        context_inherits = [value for value in call.scope if isinstance(value, Inherit)]
        return activation, apps, others, remove_others, dock_context, context_inherits

    (
        george_activation,
        george_dock,
        george_others,
        george_remove_others,
        george_context,
        george_context_inherits,
    ) = dock_items("modules/darwin/george/dock-apps.nix")
    (
        town_activation,
        town_dock,
        town_others,
        town_remove_others,
        town_context,
        town_context_inherits,
    ) = dock_items("modules/darwin/george/town-dock-apps.nix")

    assert george_activation == "nixcfgPersonalDock"
    assert town_activation == "nixcfgTownDock"
    for context in (george_context, town_context):
        assert_nix_ast_equal(context.name, "dock.mkDockContext")
        assert_nix_ast_equal(
            context.argument,
            """
            {
              inherit config primaryUser username;
            }
            """,
        )
    for context_inherits in (george_context_inherits, town_context_inherits):
        assert any(
            inherit_expr.from_expression is not None
            and inherit_expr.from_expression.rebuild() == "dockContext"
            and [name.rebuild() for name in inherit_expr.names]
            == ["appPath", "homeDirectory"]
            for inherit_expr in context_inherits
        )

    for apps in (george_dock, town_dock):
        assert '(appPath "claude" "Claude.app")' in apps
        assert '(appPath "ghostty" "Ghostty.app")' in apps
        assert '(appPath "spotify" "Spotify.app")' in apps
        assert '"/Applications/Claude.app"' not in apps
        assert '"/Applications/Ghostty.app"' not in apps
        assert '"/Applications/Spotify.app"' not in apps

    assert '(appPath "datagrip" "DataGrip.app")' in george_dock

    assert '(appPath "onepassword" "1Password.app")' in town_dock
    assert '(appPath "code-cursor" "Cursor.app")' in town_dock
    assert (
        '(appPath "vscode-insiders" "Visual Studio Code - Insiders.app")' in town_dock
    )
    assert '(appPath "datagrip" "DataGrip.app")' in town_dock
    assert '(appPath "figma" "Figma.app")' in town_dock
    assert '(appPath "linear" "Linear.app")' in town_dock
    assert '(appPath "opencode" "OpenCode Desktop Dev.app")' in town_dock
    assert '"/Applications/OpenCode Desktop Dev.app"' not in town_dock
    assert '"/Applications/Cursor.app"' not in town_dock
    assert '"/Applications/Visual Studio Code - Insiders.app"' not in town_dock

    for others in (george_others, town_others):
        assert '"${homeDirectory}/Applications"' not in others
        assert '"/Applications"' in others
        assert '"/Applications/Utilities"' in others
        assert '"${homeDirectory}/Downloads"' in others

    for remove_others in (george_remove_others, town_remove_others):
        assert '"${homeDirectory}/Applications"' in remove_others
        assert '"/Applications"' not in remove_others


def test_dock_activation_updates_items_without_clearing_the_dock() -> None:
    """Dock activation should avoid leaving the Dock empty after partial failures."""
    mk_dock_module = expect_instance(
        nix_source_fragment_expr(
            "modules/darwin/george/dock-lib.nix",
            "  mkDockModule =\n",
            "\n}",
        ),
        FunctionDefinition,
    )
    dock_label = expect_scope_binding(mk_dock_module.output, "dockLabel").value
    add_apps = expect_scope_binding(mk_dock_module.output, "addAppCommands").value
    add_others = expect_scope_binding(mk_dock_module.output, "addOtherCommands").value
    remove_others = expect_scope_binding(
        mk_dock_module.output, "removeOtherCommands"
    ).value

    assert_nix_ast_equal(
        dock_label,
        'path: lib.removeSuffix ".app" (builtins.baseNameOf path)',
    )
    for expression in (add_apps, add_others, remove_others):
        shell_text = expression.rebuild()
        assert "--remove all" not in shell_text

    assert "--replacing ${escapeShellArg (dockLabel app)}" in add_apps.rebuild()
    assert "--replacing ${escapeShellArg (dockLabel other)}" in add_others.rebuild()
    assert '"$dockutil" --find ${escapeShellArg other} --section others' in (
        remove_others.rebuild()
    )
    assert '"$dockutil" --remove ${escapeShellArg other} --section others' in (
        remove_others.rebuild()
    )
    assert 'echo "warning: failed to remove stale Dock item ${other}" >&2' in (
        remove_others.rebuild()
    )
    assert 'if ! "$dockutil" --add ${escapeShellArg app}' in add_apps.rebuild()
    assert 'echo "warning: failed to add Dock app ${app}" >&2' in add_apps.rebuild()
    assert 'if ! "$dockutil" --add ${escapeShellArg other}' in add_others.rebuild()
    assert 'echo "warning: failed to add Dock item ${other}" >&2' in (
        add_others.rebuild()
    )


def test_george_config_does_not_install_repo_managed_editor_cli_wrappers() -> None:
    """Editor app copies should no longer be accompanied by repo-managed CLI wrappers."""
    assert not (REPO_ROOT / "home/george/bin/_managed-app-cli-wrapper").exists()
    assert not (REPO_ROOT / "home/george/bin/code-insiders").exists()
    assert not (REPO_ROOT / "home/george/bin/cursor").exists()
