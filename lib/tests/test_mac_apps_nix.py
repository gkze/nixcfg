"""Regression checks for macOS application bundle management."""

import json
import os
import plistlib
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
from nix_manipulator.expressions.assertion import Assertion
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
from nix_manipulator.expressions.with_statement import WithStatement

from lib import mac_apps_helper
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.tests._nix_eval import (
    nix_attrset,
    nix_eval_raw,
    nix_eval_result,
    nix_import,
    nix_let,
    nix_list,
)
from lib.tests._nix_source import nix_file_expr, nix_source_fragment_expr
from lib.tests._shell_ast import (
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
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


def _routing_entry(table: AttributeSet, name: str) -> NixExpression:
    try:
        entry_binding = expect_binding(table.values, name)
    except AssertionError:
        entry_binding = expect_binding(table.values, f'"{name}"')
    return entry_binding.value


def _route_package(table: AttributeSet, name: str) -> NixExpression:
    entry = _routing_entry(table, name)
    if isinstance(entry, FunctionCall):
        assert_nix_ast_equal(entry.name, Identifier(name="systemApp"))
        assert entry.argument is not None
        return entry.argument
    attrs = expect_instance(entry, AttributeSet)
    return expect_binding(attrs.values, "package").value


def _route_scope(table: AttributeSet, name: str) -> NixExpression:
    entry = _routing_entry(table, name)
    if isinstance(entry, FunctionCall):
        assert_nix_ast_equal(entry.name, Identifier(name="systemApp"))
        return StringPrimitive(value="system")
    attrs = expect_instance(entry, AttributeSet)
    return expect_binding(attrs.values, "scope").value


def _route_has_scope(table: AttributeSet, name: str) -> bool:
    entry = _routing_entry(table, name)
    if isinstance(entry, FunctionCall):
        assert_nix_ast_equal(entry.name, Identifier(name="systemApp"))
        return True
    attrs = expect_instance(entry, AttributeSet)
    return any(
        isinstance(binding, Binding) and binding.name == "scope"
        for binding in attrs.values
    )


@cache
def _darwin_app_builder(name: str, next_name: str) -> FunctionDefinition:
    """Parse one exported shared Darwin app builder."""
    return expect_instance(
        nix_source_fragment_expr(
            "overlays/_lib/helpers/darwin-apps.nix",
            f"  {name} =\n",
            f";\n\n  {next_name} =",
        ),
        FunctionDefinition,
    )


@cache
def _rsync_path() -> str:
    """Return a real rsync path for shell-script smoke tests."""
    return shutil.which("rsync") or "/usr/bin/rsync"


def _fake_app_bundle(
    root: Path,
    name: str = "Focus.app",
    *,
    version: str = "1.0",
) -> Path:
    app_bundle = root / name
    (app_bundle / "Contents").mkdir(parents=True)
    with (app_bundle / "Contents" / "Info.plist").open("wb") as plist_file:
        plistlib.dump(
            {
                "CFBundleIdentifier": f"com.example.{app_bundle.stem}",
                "CFBundleShortVersionString": version,
            },
            plist_file,
        )
    return app_bundle


def _write_system_applications_payload(
    tmp_path: Path,
    *,
    source_bundle: Path,
    mode: str,
    prevent_downgrade: bool = False,
) -> tuple[Path, Path]:
    target_bundle = tmp_path / "Applications" / source_bundle.name
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({
            "entries": [
                {
                    "bundleName": source_bundle.name,
                    "mode": mode,
                    "preventDowngrade": prevent_downgrade,
                    "sourcePath": str(source_bundle),
                }
            ],
            "rsyncPath": _rsync_path(),
            "stateDirectory": str(tmp_path / ".nixcfg-mac-apps"),
            "stateName": "test-manager",
            "targetDirectory": str(target_bundle.parent),
            "writable": False,
        }),
        encoding="utf-8",
    )
    return payload_path, target_bundle


class _FakeMacAppDiscoveryCommands:
    def __init__(self, metadata_results: list[tuple[int, str]]) -> None:
        if not metadata_results:
            raise ValueError("metadata_results must not be empty")
        self.imported_apps: list[Path] = []
        self.import_batches: list[list[Path]] = []
        self.inspected_apps: list[Path] = []
        self._metadata_results = metadata_results
        self._real_is_file = Path.is_file

    def is_file(self, path: Path) -> bool:
        if path in {
            mac_apps_helper.LSREGISTER_PATH,
            mac_apps_helper.MDIMPORT_PATH,
            mac_apps_helper.MDLS_PATH,
        }:
            return True
        return self._real_is_file(path)

    def run(
        self,
        command: list[str],
        *,
        check: bool,
        capture_output: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        assert not check
        assert timeout is None or timeout > 0
        if command[0] == _rsync_path():
            shutil.copytree(
                Path(command[-2].removesuffix("/")),
                Path(command[-1]),
                dirs_exist_ok=True,
            )
            return subprocess.CompletedProcess(command, 0)
        if command[0] == str(mac_apps_helper.LSREGISTER_PATH):
            return subprocess.CompletedProcess(command, 0)
        if command[:2] == [str(mac_apps_helper.MDIMPORT_PATH), "-i"]:
            app_paths = [Path(raw_path) for raw_path in command[2:]]
            assert all(
                (app_path / "Contents" / "Info.plist").is_file()
                for app_path in app_paths
            )
            self.import_batches.append(app_paths)
            self.imported_apps.extend(app_paths)
            return subprocess.CompletedProcess(command, 0)
        assert command[:3] == [
            str(mac_apps_helper.MDLS_PATH),
            "-plist",
            "-",
        ]
        assert capture_output
        app_path = Path(command[3])
        self.inspected_apps.append(app_path)
        result_index = min(
            len(self.inspected_apps) - 1,
            len(self._metadata_results) - 1,
        )
        return_code, marker = self._metadata_results[result_index]
        if marker == "com.apple.application-bundle\n":
            stdout = plistlib.dumps({
                "kMDItemCFBundleIdentifier": f"com.example.{app_path.stem}",
                "kMDItemContentType": "com.apple.application-bundle",
                "kMDItemVersion": "1.0",
            })
        elif marker == "(null)\n":
            stdout = plistlib.dumps({})
        else:
            stdout = marker.encode()
        return subprocess.CompletedProcess(command, return_code, stdout=stdout)


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
    *,
    entries: list[AttributeSet] | None = None,
) -> dict[str, object]:
    """Evaluate ``managedAppsNotInPackageListsAssertion`` and decode its JSON."""
    managed_entries = (
        entries
        if entries is not None
        else [
            nix_attrset({
                "package": Identifier(name="managedPkg"),
                "bundleName": "Cursor.app",
                "mode": "copy",
            })
        ]
    )
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
                        "entries": nix_list(managed_entries),
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


def _concat_terms(expression: NixExpression) -> list[NixExpression]:
    """Flatten one left-associative Nix list concatenation."""
    if isinstance(expression, BinaryExpression) and expression.operator.name == "++":
        return [*_concat_terms(expression.left), *_concat_terms(expression.right)]
    return [expression]


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

    assert_nix_ast_equal(
        projection.argument_set,
        Identifier(name="managedMacAppRouting"),
    )
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


def test_spotlight_refresh_ignores_non_app_bundles_and_missing_importer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Spotlight refresh should not invoke tools without an app or importer."""

    def _unexpected_run(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError("Spotlight tools should not run")

    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _unexpected_run)

    mdimport = tmp_path / "mdimport"
    mdimport.write_text("", encoding="utf-8")
    mac_apps_helper._refresh_spotlight_metadata(
        [tmp_path / "Empty.app"],
        mdimport_path=mdimport,
        mdls_path=tmp_path / "mdls",
    )

    app_bundle = tmp_path / "Example.app"
    (app_bundle / "Contents").mkdir(parents=True)
    (app_bundle / "Contents" / "Info.plist").write_text("", encoding="utf-8")
    mac_apps_helper._refresh_spotlight_metadata(
        [app_bundle],
        mdimport_path=tmp_path / "missing-mdimport",
        mdls_path=tmp_path / "mdls",
    )


def test_spotlight_refresh_warns_when_unverified_import_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed import should warn even when metadata verification is unavailable."""
    app_bundle = tmp_path / "Example.app"
    (app_bundle / "Contents").mkdir(parents=True)
    (app_bundle / "Contents" / "Info.plist").write_text("", encoding="utf-8")
    mdimport = tmp_path / "mdimport"
    mdimport.write_text("", encoding="utf-8")

    def _run(
        command: list[str],
        *,
        check: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        assert not check
        assert timeout > 0
        return subprocess.CompletedProcess(command, 73)

    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run)

    mac_apps_helper._refresh_spotlight_metadata(
        [app_bundle],
        mdimport_path=mdimport,
        mdls_path=tmp_path / "missing-mdls",
    )

    assert capsys.readouterr().err == (
        "warning: Spotlight refresh incomplete: mdimport exited 73; "
        f"mdls is unavailable at {tmp_path / 'missing-mdls'}\n"
    )


def test_spotlight_refresh_waits_and_accepts_successful_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A delayed metadata record should trigger one import retry and then succeed."""
    app_bundle = _fake_app_bundle(tmp_path, "Example.app")
    mdimport = tmp_path / "mdimport"
    mdls = tmp_path / "mdls"
    mdimport.write_text("", encoding="utf-8")
    mdls.write_text("", encoding="utf-8")
    imported_apps: list[Path] = []
    inspected_apps: list[Path] = []
    sleeps: list[float] = []
    clock = [0.0]
    mdls_results = iter([
        {
            "kMDItemCFBundleIdentifier": "com.example.Stale",
            "kMDItemContentType": "com.apple.application-bundle",
            "kMDItemVersion": "1.0",
        },
        {
            "kMDItemCFBundleIdentifier": "com.example.Example",
            "kMDItemContentType": "com.apple.application-bundle",
            "kMDItemVersion": "0.9",
        },
        {
            "kMDItemCFBundleIdentifier": "com.example.Example",
            "kMDItemContentType": "com.apple.application-bundle",
            "kMDItemVersion": "1.0",
        },
    ])

    def _run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool = False,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        assert not check
        assert timeout > 0
        if command[:2] == [str(mdimport), "-i"]:
            imported_apps.append(Path(command[2]))
            return subprocess.CompletedProcess(command, 0)
        assert command[:3] == [
            str(mdls),
            "-plist",
            "-",
        ]
        assert capture_output
        inspected_apps.append(Path(command[3]))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=plistlib.dumps(next(mdls_results)),
        )

    def _sleep(duration: float) -> None:
        sleeps.append(duration)
        clock[0] += duration

    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run)
    monkeypatch.setattr(mac_apps_helper.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mac_apps_helper.time, "sleep", _sleep)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_REFRESH_TIMEOUT_SECONDS", 4)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_COMMAND_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_VERIFY_INTERVAL_SECONDS", 1)

    mac_apps_helper._refresh_spotlight_metadata(
        [app_bundle],
        mdimport_path=mdimport,
        mdls_path=mdls,
    )

    assert imported_apps == [app_bundle, app_bundle]
    assert inspected_apps == [app_bundle, app_bundle, app_bundle]
    assert sleeps == [1, 1]
    assert capsys.readouterr().err == ""


def test_spotlight_refresh_globally_bounds_tool_timeouts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A wedged metadata service should warn once within one batch deadline."""
    app_bundles = [
        _fake_app_bundle(tmp_path, "First.app"),
        _fake_app_bundle(tmp_path, "Second.app"),
    ]
    mdimport = tmp_path / "mdimport"
    mdls = tmp_path / "mdls"
    mdimport.write_text("", encoding="utf-8")
    mdls.write_text("", encoding="utf-8")
    clock = [0.0]
    commands: list[list[str]] = []

    def _run(
        command: list[str],
        *,
        check: bool,
        timeout: float,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        assert not check
        commands.append(command)
        clock[0] += timeout
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run)
    monkeypatch.setattr(mac_apps_helper.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_REFRESH_TIMEOUT_SECONDS", 4)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_REFRESH_SECONDS_PER_APP", 0)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_COMMAND_TIMEOUT_SECONDS", 1)

    mac_apps_helper._refresh_spotlight_metadata(
        app_bundles,
        mdimport_path=mdimport,
        mdls_path=mdls,
    )

    assert clock[0] <= 4
    assert len(commands) <= 4
    warning = capsys.readouterr().err
    assert warning.count("warning:") == 1
    assert "timed out" in warning


def test_spotlight_commands_stop_at_expired_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No Spotlight process should start after the shared deadline expires."""
    monkeypatch.setattr(mac_apps_helper.time, "monotonic", lambda: 10.0)

    assert (
        mac_apps_helper._import_spotlight_metadata(
            [tmp_path / "Example.app"],
            mdimport_path=tmp_path / "mdimport",
            deadline=10.0,
        )
        == "Spotlight refresh deadline expired before mdimport"
    )
    assert mac_apps_helper._spotlight_record_matches_bundle(
        tmp_path / "Example.app",
        expected_identity=(None, None),
        mdls_path=tmp_path / "mdls",
        deadline=10.0,
    ) == (False, None)


@pytest.mark.parametrize(
    ("plist_payload", "expected"),
    [
        (["not", "a", "mapping"], (None, None)),
        (
            {
                "CFBundleIdentifier": "com.example.Fallback",
                "CFBundleVersion": "42",
            },
            ("com.example.Fallback", "42"),
        ),
        (
            {
                "CFBundleIdentifier": 7,
                "CFBundleShortVersionString": 9,
            },
            (None, None),
        ),
    ],
)
def test_bundle_spotlight_identity_handles_plist_variants(
    plist_payload: object,
    expected: tuple[str | None, str | None],
    tmp_path: Path,
) -> None:
    """Bundle identity parsing should tolerate valid but incomplete plists."""
    app_bundle = tmp_path / "Example.app"
    (app_bundle / "Contents").mkdir(parents=True)
    with (app_bundle / "Contents" / "Info.plist").open("wb") as plist_file:
        plistlib.dump(plist_payload, plist_file)

    assert mac_apps_helper._bundle_spotlight_identity(app_bundle) == expected


@pytest.mark.parametrize(
    ("return_code", "stdout", "expected_match"),
    [
        (1, b"", False),
        (0, b"not a plist", False),
        (0, plistlib.dumps(["not", "a", "mapping"]), False),
        (
            0,
            plistlib.dumps({"kMDItemContentType": "com.apple.application-bundle"}),
            True,
        ),
    ],
)
def test_spotlight_record_rejects_unusable_mdls_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    return_code: int,
    stdout: bytes,
    *,
    expected_match: bool,
) -> None:
    """Only a usable mdls application record should satisfy verification."""

    def _run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, return_code, stdout=stdout)

    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run)
    assert mac_apps_helper._spotlight_record_matches_bundle(
        tmp_path / "Example.app",
        expected_identity=(None, None),
        mdls_path=tmp_path / "mdls",
        deadline=mac_apps_helper.time.monotonic() + 1,
    ) == (expected_match, None)


@pytest.mark.parametrize(
    ("metadata", "expected_match"),
    [
        (
            {
                "kMDItemCFBundleIdentifier": "com.example.App",
                "kMDItemContentType": "com.apple.application-bundle",
            },
            False,
        ),
        (
            {
                "kMDItemCFBundleIdentifier": "com.example.App",
                "kMDItemContentType": "com.apple.application-bundle",
                "kMDItemVersion": "1.0",
            },
            True,
        ),
        (
            {"kMDItemContentType": "com.apple.application-bundle"},
            False,
        ),
    ],
)
def test_spotlight_record_requires_every_known_bundle_identity_field(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metadata: dict[str, object],
    *,
    expected_match: bool,
) -> None:
    """Known bundle identifiers and versions must both match the indexed record."""

    def _run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=plistlib.dumps(metadata),
        )

    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run)
    assert mac_apps_helper._spotlight_record_matches_bundle(
        tmp_path / "Example.app",
        expected_identity=("com.example.App", "1.0"),
        mdls_path=tmp_path / "mdls",
        deadline=mac_apps_helper.time.monotonic() + 1,
    ) == (expected_match, None)


def test_spotlight_refresh_reports_retry_command_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Retry-only import and inspection failures should reach the single warning."""
    app_bundle = _fake_app_bundle(tmp_path, "Example.app")
    mdimport = tmp_path / "mdimport"
    mdls = tmp_path / "mdls"
    mdimport.write_text("", encoding="utf-8")
    mdls.write_text("", encoding="utf-8")
    clock = [0.0]
    import_count = 0

    def _run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal import_count
        if command[0] == str(mdimport):
            import_count += 1
            return subprocess.CompletedProcess(command, 0 if import_count == 1 else 73)
        if clock[0] < 2:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=plistlib.dumps({}),
            )
        raise OSError("mdls unavailable during retry")

    def _sleep(duration: float) -> None:
        clock[0] += duration

    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run)
    monkeypatch.setattr(mac_apps_helper.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mac_apps_helper.time, "sleep", _sleep)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_REFRESH_TIMEOUT_SECONDS", 4)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_COMMAND_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_VERIFY_INTERVAL_SECONDS", 1)

    mac_apps_helper._refresh_spotlight_metadata(
        [app_bundle],
        mdimport_path=mdimport,
        mdls_path=mdls,
    )

    warning = capsys.readouterr().err
    assert "mdimport exited 73" in warning
    assert "could not launch mdls" in warning


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
    """The shared macApp passthru should advertise copy mode for dockable bundles."""
    mac_app = expect_instance(
        nix_source_fragment_expr(
            "overlays/_lib/mk-mac-app-passthru.nix",
            "  macApp = ",
            "\n  // macApp;",
        ),
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "installMode").value,
        StringPrimitive(value="copy"),
    )


def test_dmg_app_helper_forwards_custom_stdenv_to_derivation() -> None:
    """A custom DMG stdenv should reach the shared derivation constructor."""
    mk_unpacked_app = expect_instance(
        nix_source_fragment_expr(
            "overlays/_lib/helpers/darwin-apps.nix",
            "  mkUnpackedApp =\n",
            ";\nin\n",
        ),
        FunctionDefinition,
    )
    unpacked_stdenv = next(
        argument
        for argument in mk_unpacked_app.argument_set
        if isinstance(argument, Identifier) and argument.name == "stdenv"
    )
    assert_nix_ast_equal(unpacked_stdenv.default_value, "prev.stdenvNoCC")
    unpacked_derivation = expect_instance(mk_unpacked_app.output, FunctionCall)
    assert_nix_ast_equal(unpacked_derivation.name, "stdenv.mkDerivation")

    mk_dmg_app = _darwin_app_builder("mkDmgApp", "mkDmgApp7zz")
    dmg_stdenv = next(
        argument
        for argument in mk_dmg_app.argument_set
        if isinstance(argument, Identifier) and argument.name == "stdenv"
    )
    assert_nix_ast_equal(dmg_stdenv.default_value, "prev.stdenvNoCC")
    unpacked_call = expect_instance(mk_dmg_app.output, FunctionCall)
    assert_nix_ast_equal(unpacked_call.name, Identifier(name="mkUnpackedApp"))
    unpacked_args = expect_instance(unpacked_call.argument, AttributeSet)
    inherited_names = {
        name.name
        for inherit in unpacked_args.values
        if isinstance(inherit, Inherit)
        for name in inherit.names
    }
    assert "stdenv" in inherited_names


def test_dmg_app_helper_supports_an_explicit_source_name() -> None:
    """DMG callers may override the versioned source name without changing its default."""
    mk_dmg_app = _darwin_app_builder("mkDmgApp", "mkDmgApp7zz")
    source_name_argument = next(
        argument
        for argument in mk_dmg_app.argument_set
        if isinstance(argument, Identifier) and argument.name == "sourceName"
    )
    assert_nix_ast_equal(source_name_argument.default_value, "null")

    unpacked_call = expect_instance(mk_dmg_app.output, FunctionCall)
    unpacked_args = expect_instance(unpacked_call.argument, AttributeSet)
    source_name = expect_instance(
        expect_binding(unpacked_args.values, "srcName").value,
        IfExpression,
    )
    assert_nix_ast_equal(source_name.condition, "sourceName == null")
    assert_nix_ast_equal(
        source_name.consequence,
        '"${capitalizedAppName}_${info.version}_${arch}.dmg"',
    )
    assert_nix_ast_equal(source_name.alternative, Identifier(name="sourceName"))


@pytest.mark.parametrize(
    ("name", "next_name"),
    [
        ("mkDmgApp", "mkDmgApp7zz"),
        ("mkZipApp", "mkPkgApp"),
    ],
)
def test_archive_app_helpers_reject_missing_bundle_executables(
    name: str,
    next_name: str,
) -> None:
    """Archive app builders must fail instead of emitting dangling bin links."""
    helper = _darwin_app_builder(name, next_name)
    unpacked_call = expect_instance(helper.output, FunctionCall)
    unpacked_args = expect_instance(unpacked_call.argument, AttributeSet)
    unpack = expect_instance(
        expect_binding(unpacked_args.values, "unpack").value,
        AttributeSet,
    )
    install_phase = expect_instance(
        expect_binding(unpack.values, "installPhase").value,
        IndentedString,
    )

    marker = "${prev.lib.optionalString makeBinary ("
    rendered_phase = install_phase.rebuild()
    guarded_call_source = rendered_phase.split(marker, 1)[1].split(")}", 1)[0]
    assert_nix_ast_equal(
        parse_nix_expr(guarded_call_source),
        """
        guardedBinLink {
          bundleName = resolvedBundleName;
          executableName = resolvedExecutableName;
          inherit binaryName;
        }
        """,
    )


@cache
def _guarded_bin_link_script() -> str:
    """Evaluate the shared link guard; Nix ASTs cannot expose interpolated shell."""
    return nix_eval_raw(
        FunctionCall(
            name=nix_import(REPO_ROOT / "overlays/_lib/helpers/guarded-bin-link.nix"),
            argument=nix_attrset({
                "binaryName": "demo",
                "bundleName": "Demo.app",
                "executableName": "Demo",
            }),
        )
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
@pytest.mark.parametrize(
    "executable_state", ["missing", "non_executable", "executable"]
)
def test_guarded_bin_link_requires_an_executable(
    tmp_path: Path,
    *,
    executable_state: str,
) -> None:
    """Evaluate rendered shell because AST inspection cannot resolve interpolation."""
    output = tmp_path / "output"
    executable = output / "Applications/Demo.app/Contents/MacOS/Demo"
    executable.parent.mkdir(parents=True)
    (output / "bin").mkdir()
    if executable_state != "missing":
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
    if executable_state == "executable":
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(  # noqa: S603
        [bash, "-eu", "-c", _guarded_bin_link_script()],
        check=False,
        capture_output=True,
        env={**os.environ, "out": str(output)},
        text=True,
    )

    link = output / "bin/demo"
    executable_is_valid = executable_state == "executable"
    assert result.returncode == (0 if executable_is_valid else 1)
    assert link.is_symlink() is executable_is_valid
    if executable_is_valid:
        assert link.resolve() == executable


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
            "        installPhase = ",
            ";\n      };",
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
    helper_entries = _mac_apps_fragment_expr(
        "      helperEntries = ",
        ";\n    in\n    callMacAppsHelper",
    )
    assert_nix_ast_equal(
        nix_let(
            {
                "bundleSourcePath": parse_nix_expr("entry: entry.sourcePath"),
                "entries": nix_list([]),
            },
            helper_entries,
        ),
        """
        let
          bundleSourcePath = entry: entry.sourcePath;
          entries = [];
        in
          map (entry: {
            inherit (entry) bundleName mode;
            preventDowngrade = entry.preventDowngrade or false;
            sourcePath = bundleSourcePath entry;
          }) entries
        """,
    )
    formals = [
        expect_instance(argument, Identifier) for argument in system_script.argument_set
    ]
    assert [formal.name for formal in formals] == [
        "entries",
        "stateDirectory",
        "stateName",
        "writable",
        "targetDirectory",
    ]
    assert all(formal.default_value is None for formal in formals[:-1])
    assert_nix_ast_equal(formals[-1].default_value, '"/Applications"')
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
                    "preventDowngrade": False,
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
    source_directory = tmp_path / "source"
    first_source = _fake_app_bundle(source_directory, "First.app")
    second_source = _fake_app_bundle(source_directory, "Second.app")

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
                "preventDowngrade": False,
                "sourcePath": str(first_source),
            },
            {
                "bundleName": "Second.app",
                "mode": "copy",
                "preventDowngrade": False,
                "sourcePath": str(second_source),
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


def test_system_applications_records_ownership_before_spotlight_interruption(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An interrupted metadata refresh must not orphan an installed managed app."""
    source_bundle = _fake_app_bundle(tmp_path / "source", "Example.app")
    payload_path, target_bundle = _write_system_applications_payload(
        tmp_path,
        source_bundle=source_bundle,
        mode="copy",
        prevent_downgrade=False,
    )
    real_is_file = Path.is_file

    def _is_file(path: Path) -> bool:
        if path in {
            mac_apps_helper.LSREGISTER_PATH,
            mac_apps_helper.MDIMPORT_PATH,
            mac_apps_helper.MDLS_PATH,
        }:
            return True
        return real_is_file(path)

    def _run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        if command[0] == _rsync_path():
            shutil.copytree(
                Path(command[-2].removesuffix("/")),
                Path(command[-1]),
                dirs_exist_ok=True,
            )
            return subprocess.CompletedProcess(command, 0)
        if command[0] == str(mac_apps_helper.LSREGISTER_PATH):
            return subprocess.CompletedProcess(command, 0)
        if command[:2] == [str(mac_apps_helper.MDIMPORT_PATH), "-i"]:
            raise KeyboardInterrupt
        msg = f"unexpected command: {command}"
        raise AssertionError(msg)

    monkeypatch.setattr(Path, "is_file", _is_file)
    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run)

    with pytest.raises(KeyboardInterrupt):
        mac_apps_helper.main(["prog", "system-applications", str(payload_path)])

    assert target_bundle.is_dir()
    assert (tmp_path / ".nixcfg-mac-apps" / "test-manager.txt").read_text(
        encoding="utf-8"
    ) == "Example.app\n"


def test_system_applications_creates_readable_ownership_manifest(
    tmp_path: Path,
) -> None:
    """A manifest write must ignore a restrictive process umask."""
    payload_path = tmp_path / "payload.json"
    state_file = tmp_path / ".nixcfg-mac-apps" / "test-manager.txt"
    state_file.parent.mkdir(mode=0o755)
    payload_path.write_text(
        json.dumps({
            "entries": [],
            "rsyncPath": _rsync_path(),
            "stateDirectory": str(state_file.parent),
            "stateName": "test-manager",
            "targetDirectory": str(tmp_path / "Applications"),
            "writable": False,
        }),
        encoding="utf-8",
    )

    previous_umask = os.umask(0o077)
    try:
        assert (
            mac_apps_helper.main([
                "prog",
                "system-applications",
                str(payload_path),
            ])
            == 0
        )
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(state_file.stat().st_mode) == 0o644
    assert stat.S_IMODE(state_file.parent.stat().st_mode) == 0o755


@pytest.mark.parametrize("existing_mode", [0o200, 0o600, 0o666])
def test_system_applications_normalizes_existing_manifest_mode(
    tmp_path: Path,
    existing_mode: int,
) -> None:
    """An activation must replace unsafe manifest modes with safe audit access."""
    payload_path = tmp_path / "payload.json"
    state_file = tmp_path / ".nixcfg-mac-apps" / "test-manager.txt"
    state_file.parent.mkdir()
    state_file.write_text("", encoding="utf-8")
    state_file.chmod(existing_mode)
    payload_path.write_text(
        json.dumps({
            "entries": [],
            "rsyncPath": _rsync_path(),
            "stateDirectory": str(state_file.parent),
            "stateName": "test-manager",
            "targetDirectory": str(tmp_path / "Applications"),
            "writable": False,
        }),
        encoding="utf-8",
    )

    assert (
        mac_apps_helper.main([
            "prog",
            "system-applications",
            str(payload_path),
        ])
        == 0
    )

    assert stat.S_IMODE(state_file.stat().st_mode) == 0o644


def test_managed_app_manifest_replace_failure_preserves_previous_ownership(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed atomic promotion must retain the last complete ownership record."""
    state_file = tmp_path / "test-manager.txt"
    state_file.write_text("Existing.app\n", encoding="utf-8")

    def _replace(source: Path, destination: str | Path) -> Path:
        source_path = Path(source)
        assert Path(destination) == state_file
        assert source_path.parent == state_file.parent
        assert source_path.read_text(encoding="utf-8") == "Replacement.app\n"
        raise OSError("simulated manifest promotion failure")

    monkeypatch.setattr(Path, "replace", _replace)

    with pytest.raises(OSError, match="simulated manifest promotion failure"):
        mac_apps_helper._write_managed_app_manifest(
            state_file,
            ["Replacement.app"],
        )

    assert state_file.read_text(encoding="utf-8") == "Existing.app\n"
    assert list(tmp_path.glob(".test-manager.txt.*.tmp")) == []


def test_system_applications_cli_refuses_downgrade_before_any_mutation(
    tmp_path: Path,
) -> None:
    """A protected newer app must abort the entire activation preflight."""
    safe_source_bundle = _fake_app_bundle(
        tmp_path / "source",
        "Safe.app",
        version="2.0",
    )
    source_bundle = _fake_app_bundle(
        tmp_path / "source",
        "Protected.app",
        version="8.23.0-beta.1",
    )
    (source_bundle / "Contents" / "source.txt").write_text(
        "older source",
        encoding="utf-8",
    )
    target_bundle = _fake_app_bundle(
        tmp_path / "Applications",
        "Protected.app",
        version="8.24.0-beta.1",
    )
    installed_marker = target_bundle / "Contents" / "installed.txt"
    installed_marker.write_text("newer installed app", encoding="utf-8")

    stale_bundle = tmp_path / "Applications" / "Stale.app"
    stale_bundle.mkdir()
    state_directory = tmp_path / ".nixcfg-mac-apps"
    state_directory.mkdir()
    state_file = state_directory / "test-manager.txt"
    original_manifest = "Protected.app\nStale.app\n"
    state_file.write_text(original_manifest, encoding="utf-8")
    payload_path, _ = _write_system_applications_payload(
        tmp_path,
        source_bundle=source_bundle,
        mode="copy",
        prevent_downgrade=True,
    )
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["entries"].insert(
        0,
        {
            "bundleName": safe_source_bundle.name,
            "mode": "copy",
            "preventDowngrade": False,
            "sourcePath": str(safe_source_bundle),
        },
    )
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    stderr = StringIO()
    with redirect_stderr(stderr), pytest.raises(SystemExit) as exc:
        mac_apps_helper.main(["prog", "system-applications", str(payload_path)])

    assert exc.value.code == 1
    assert "Refusing to downgrade" in stderr.getvalue()
    assert "8.24.0-beta.1" in stderr.getvalue()
    assert "8.23.0-beta.1" in stderr.getvalue()
    assert not (tmp_path / "Applications" / "Safe.app").exists()
    assert installed_marker.read_text(encoding="utf-8") == "newer installed app"
    assert stale_bundle.is_dir()
    assert state_file.read_text(encoding="utf-8") == original_manifest


def test_system_applications_cli_refuses_lower_build_of_same_short_version(
    tmp_path: Path,
) -> None:
    """Equal marketing versions must use the build number as a tie-breaker."""
    source_bundle = _fake_app_bundle(tmp_path / "source", "Protected.app")
    target_bundle = _fake_app_bundle(tmp_path / "Applications", "Protected.app")
    for app_bundle, build_version in [(source_bundle, "1"), (target_bundle, "2")]:
        with (app_bundle / "Contents" / "Info.plist").open("wb") as plist_file:
            plistlib.dump(
                {
                    "CFBundleIdentifier": "com.example.Protected",
                    "CFBundleShortVersionString": "1.0",
                    "CFBundleVersion": build_version,
                },
                plist_file,
            )
    installed_marker = target_bundle / "Contents" / "installed.txt"
    installed_marker.write_text("newer build", encoding="utf-8")
    payload_path, _ = _write_system_applications_payload(
        tmp_path,
        source_bundle=source_bundle,
        mode="copy",
        prevent_downgrade=True,
    )

    stderr = StringIO()
    with redirect_stderr(stderr), pytest.raises(SystemExit) as exc:
        mac_apps_helper.main(["prog", "system-applications", str(payload_path)])

    assert exc.value.code == 1
    assert "Refusing to downgrade" in stderr.getvalue()
    assert "build 2" in stderr.getvalue()
    assert "build 1" in stderr.getvalue()
    assert installed_marker.read_text(encoding="utf-8") == "newer build"


@pytest.mark.parametrize(
    ("source_build", "installed_build"),
    [("2", "1"), ("2", "2")],
)
def test_system_applications_cli_allows_non_downgrade_builds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source_build: str,
    installed_build: str,
) -> None:
    """Equal marketing versions should allow equal or newer source builds."""
    source_bundle = _fake_app_bundle(tmp_path / "source", "Protected.app")
    target_bundle = _fake_app_bundle(tmp_path / "Applications", "Protected.app")
    for app_bundle, build_version in [
        (source_bundle, source_build),
        (target_bundle, installed_build),
    ]:
        with (app_bundle / "Contents" / "Info.plist").open("wb") as plist_file:
            plistlib.dump(
                {
                    "CFBundleIdentifier": "com.example.Protected",
                    "CFBundleShortVersionString": "1.0",
                    "CFBundleVersion": build_version,
                },
                plist_file,
            )
    payload_path, _ = _write_system_applications_payload(
        tmp_path,
        source_bundle=source_bundle,
        mode="copy",
        prevent_downgrade=True,
    )
    monkeypatch.setattr(
        mac_apps_helper,
        "_refresh_launch_services_registration",
        lambda _path: None,
    )
    monkeypatch.setattr(
        mac_apps_helper,
        "_refresh_spotlight_metadata",
        lambda _paths: None,
    )

    assert mac_apps_helper.main(["prog", "system-applications", str(payload_path)]) == 0
    with (target_bundle / "Contents" / "Info.plist").open("rb") as plist_file:
        installed_info = plistlib.load(plist_file)
    assert installed_info["CFBundleVersion"] == source_build


@pytest.mark.parametrize(
    ("source_version", "installed_version"),
    [
        ("8.24.0-beta.1", None),
        ("8.24.0-beta.1", "8.24.0-beta.1"),
        ("8.24.0-beta.1", "8.23.0-beta.1"),
        ("8.24.0-beta.10", "8.24.0-beta.9"),
        ("8.24.0", "8.24.0-beta.10"),
    ],
)
def test_system_applications_cli_allows_non_downgrade_versions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source_version: str,
    installed_version: str | None,
) -> None:
    """The guard should allow first installs, equal versions, and upgrades."""
    source_bundle = _fake_app_bundle(
        tmp_path / "source",
        "Protected.app",
        version=source_version,
    )
    payload_path, target_bundle = _write_system_applications_payload(
        tmp_path,
        source_bundle=source_bundle,
        mode="copy",
        prevent_downgrade=True,
    )
    if installed_version is not None:
        _fake_app_bundle(
            tmp_path / "Applications",
            "Protected.app",
            version=installed_version,
        )

    monkeypatch.setattr(
        mac_apps_helper,
        "_refresh_launch_services_registration",
        lambda _path: None,
    )
    monkeypatch.setattr(
        mac_apps_helper,
        "_refresh_spotlight_metadata",
        lambda _paths: None,
    )

    assert mac_apps_helper.main(["prog", "system-applications", str(payload_path)]) == 0
    with (target_bundle / "Contents" / "Info.plist").open("rb") as plist_file:
        installed_info = plistlib.load(plist_file)
    assert installed_info["CFBundleShortVersionString"] == source_version


def test_system_applications_cli_compares_build_versions_when_short_versions_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Build versions are a valid fallback only when both short versions are absent."""
    source_bundle = _fake_app_bundle(tmp_path / "source", "Protected.app")
    target_bundle = _fake_app_bundle(tmp_path / "Applications", "Protected.app")
    for app_bundle, build_version in [(source_bundle, "24"), (target_bundle, "23")]:
        with (app_bundle / "Contents" / "Info.plist").open("wb") as plist_file:
            plistlib.dump(
                {
                    "CFBundleIdentifier": "com.example.Protected",
                    "CFBundleVersion": build_version,
                },
                plist_file,
            )
    payload_path, _ = _write_system_applications_payload(
        tmp_path,
        source_bundle=source_bundle,
        mode="copy",
        prevent_downgrade=True,
    )
    monkeypatch.setattr(
        mac_apps_helper,
        "_refresh_launch_services_registration",
        lambda _path: None,
    )
    monkeypatch.setattr(
        mac_apps_helper,
        "_refresh_spotlight_metadata",
        lambda _paths: None,
    )

    assert mac_apps_helper.main(["prog", "system-applications", str(payload_path)]) == 0
    with (target_bundle / "Contents" / "Info.plist").open("rb") as plist_file:
        installed_info = plistlib.load(plist_file)
    assert installed_info["CFBundleVersion"] == "24"
    assert "CFBundleShortVersionString" not in installed_info


def test_system_applications_cli_rejects_missing_source_before_cleanup(
    tmp_path: Path,
) -> None:
    """All source bundles must exist before stale apps can be removed."""
    stale_bundle = tmp_path / "Applications" / "Stale.app"
    stale_bundle.mkdir(parents=True)
    state_directory = tmp_path / ".nixcfg-mac-apps"
    state_directory.mkdir()
    state_file = state_directory / "test-manager.txt"
    state_file.write_text("Stale.app\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        mac_apps_helper._system_applications({
            "entries": [
                {
                    "bundleName": "Missing.app",
                    "mode": "copy",
                    "preventDowngrade": False,
                    "sourcePath": str(tmp_path / "source" / "Missing.app"),
                }
            ],
            "rsyncPath": _rsync_path(),
            "stateDirectory": str(state_directory),
            "stateName": "test-manager",
            "targetDirectory": str(tmp_path / "Applications"),
            "writable": False,
        })

    assert exc.value.code == 1
    assert stale_bundle.is_dir()
    assert state_file.read_text(encoding="utf-8") == "Stale.app\n"


@pytest.mark.parametrize(
    ("source_info", "target_info", "expected_error"),
    [
        (
            ["not", "a", "mapping"],
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleShortVersionString": "2.0",
            },
            "bundle metadata could not be read",
        ),
        (
            {"CFBundleShortVersionString": "1.0"},
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleShortVersionString": "2.0",
            },
            "CFBundleIdentifier is missing",
        ),
        (
            {
                "CFBundleIdentifier": "com.example.Source",
                "CFBundleShortVersionString": "1.0",
            },
            {
                "CFBundleIdentifier": "com.example.Target",
                "CFBundleShortVersionString": "2.0",
            },
            "bundle identifiers differ",
        ),
        (
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleVersion": "1",
            },
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleShortVersionString": "2.0",
            },
            "comparable bundle versions are missing",
        ),
        (
            {"CFBundleIdentifier": "com.example.Protected"},
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleVersion": "2",
            },
            "comparable bundle versions are missing",
        ),
        (
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleShortVersionString": "not a version!",
            },
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleShortVersionString": "2.0",
            },
            "bundle version is invalid",
        ),
        (
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleShortVersionString": "1.0",
                "CFBundleVersion": "1",
            },
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleShortVersionString": "1.0",
                "CFBundleVersion": "not a build!",
            },
            "bundle build version is invalid",
        ),
        (
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleShortVersionString": "1.0",
            },
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleShortVersionString": "1.0",
                "CFBundleVersion": "2",
            },
            "comparable bundle build versions are missing",
        ),
        (
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleShortVersionString": "1.0",
                "CFBundleVersion": "2",
            },
            {
                "CFBundleIdentifier": "com.example.Protected",
                "CFBundleShortVersionString": "1.0",
            },
            "comparable bundle build versions are missing",
        ),
    ],
)
def test_system_applications_cli_fails_closed_for_uncomparable_protected_apps(
    tmp_path: Path,
    source_info: object,
    target_info: object,
    expected_error: str,
) -> None:
    """Protected apps with ambiguous identities or versions must not be replaced."""
    source_bundle = _fake_app_bundle(tmp_path / "source", "Protected.app")
    target_bundle = _fake_app_bundle(tmp_path / "Applications", "Protected.app")
    for app_bundle, info in [
        (source_bundle, source_info),
        (target_bundle, target_info),
    ]:
        with (app_bundle / "Contents" / "Info.plist").open("wb") as plist_file:
            plistlib.dump(info, plist_file)
    installed_marker = target_bundle / "Contents" / "installed.txt"
    installed_marker.write_text("unchanged", encoding="utf-8")
    payload_path, _ = _write_system_applications_payload(
        tmp_path,
        source_bundle=source_bundle,
        mode="copy",
        prevent_downgrade=True,
    )

    stderr = StringIO()
    with redirect_stderr(stderr), pytest.raises(SystemExit) as exc:
        mac_apps_helper.main(["prog", "system-applications", str(payload_path)])

    assert exc.value.code == 1
    assert expected_error in stderr.getvalue()
    assert installed_marker.read_text(encoding="utf-8") == "unchanged"


def test_system_applications_cli_indexes_materialized_app_for_spotlight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Activation should submit a finished managed app bundle to Spotlight."""
    source_bundle = _fake_app_bundle(tmp_path / "source")
    payload_path, target_bundle = _write_system_applications_payload(
        tmp_path,
        source_bundle=source_bundle,
        mode="copy",
    )
    commands = _FakeMacAppDiscoveryCommands([(0, "com.apple.application-bundle\n")])
    monkeypatch.setattr(Path, "is_file", lambda path: commands.is_file(path))
    monkeypatch.setattr(mac_apps_helper.subprocess, "run", commands.run)

    assert mac_apps_helper.main(["prog", "system-applications", str(payload_path)]) == 0
    assert commands.imported_apps == [target_bundle]
    assert commands.inspected_apps == [target_bundle]


def test_system_applications_cli_submits_each_app_to_spotlight_after_installs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Activation should independently submit every finished bundle to Spotlight."""
    source_bundles = [
        _fake_app_bundle(tmp_path / "source", "First.app"),
        _fake_app_bundle(tmp_path / "source", "Second.app"),
    ]
    target_directory = tmp_path / "Applications"
    target_bundles = [target_directory / app.name for app in source_bundles]
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({
            "entries": [
                {
                    "bundleName": app.name,
                    "mode": "symlink",
                    "preventDowngrade": False,
                    "sourcePath": str(app),
                }
                for app in source_bundles
            ],
            "rsyncPath": _rsync_path(),
            "stateDirectory": str(tmp_path / ".nixcfg-mac-apps"),
            "stateName": "test-manager",
            "targetDirectory": str(target_directory),
            "writable": False,
        }),
        encoding="utf-8",
    )
    commands = _FakeMacAppDiscoveryCommands([(0, "com.apple.application-bundle\n")])
    monkeypatch.setattr(Path, "is_file", lambda path: commands.is_file(path))
    monkeypatch.setattr(mac_apps_helper.subprocess, "run", commands.run)

    assert mac_apps_helper.main(["prog", "system-applications", str(payload_path)]) == 0
    assert commands.import_batches == [[app] for app in target_bundles]


def test_system_applications_cli_scales_spotlight_budget_with_app_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A larger app set should retain time to verify every Spotlight record."""
    source_bundles = [
        _fake_app_bundle(tmp_path / "source", f"App{index:02}.app")
        for index in range(40)
    ]
    target_directory = tmp_path / "Applications"
    target_bundles = [target_directory / app.name for app in source_bundles]
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({
            "entries": [
                {
                    "bundleName": app.name,
                    "mode": "symlink",
                    "preventDowngrade": False,
                    "sourcePath": str(app),
                }
                for app in source_bundles
            ],
            "rsyncPath": _rsync_path(),
            "stateDirectory": str(tmp_path / ".nixcfg-mac-apps"),
            "stateName": "test-manager",
            "targetDirectory": str(target_directory),
            "writable": False,
        }),
        encoding="utf-8",
    )
    commands = _FakeMacAppDiscoveryCommands([(0, "com.apple.application-bundle\n")])
    clock = [0.0]

    def _run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        if command[:2] == [str(mac_apps_helper.MDIMPORT_PATH), "-i"]:
            assert timeout is not None
            clock[0] += min(0.75, timeout)
        return commands.run(
            command,
            check=check,
            capture_output=capture_output,
            timeout=timeout,
        )

    monkeypatch.setattr(Path, "is_file", lambda path: commands.is_file(path))
    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run)
    monkeypatch.setattr(mac_apps_helper.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_REFRESH_TIMEOUT_SECONDS", 4)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_REFRESH_SECONDS_PER_APP", 1)

    assert mac_apps_helper.main(["prog", "system-applications", str(payload_path)]) == 0
    assert commands.inspected_apps == target_bundles
    assert "warning: Spotlight" not in capsys.readouterr().err


def test_system_applications_cli_retries_missing_spotlight_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Activation should retry once and warn when Spotlight keeps no record."""
    source_bundle = _fake_app_bundle(tmp_path / "source")
    payload_path, target_bundle = _write_system_applications_payload(
        tmp_path,
        source_bundle=source_bundle,
        mode="symlink",
    )
    commands = _FakeMacAppDiscoveryCommands([(0, "(null)\n")])
    clock = [0.0]

    def _sleep(duration: float) -> None:
        clock[0] += duration

    monkeypatch.setattr(Path, "is_file", lambda path: commands.is_file(path))
    monkeypatch.setattr(mac_apps_helper.subprocess, "run", commands.run)
    monkeypatch.setattr(mac_apps_helper.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mac_apps_helper.time, "sleep", _sleep)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_REFRESH_TIMEOUT_SECONDS", 4)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_COMMAND_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_VERIFY_INTERVAL_SECONDS", 1)

    assert mac_apps_helper.main(["prog", "system-applications", str(payload_path)]) == 0
    assert commands.imported_apps == [target_bundle, target_bundle]
    assert commands.inspected_apps == [target_bundle] * 4
    assert capsys.readouterr().err.endswith(
        "warning: Spotlight refresh incomplete: metadata is still missing or stale "
        f"for {target_bundle}\n"
    )


def test_system_applications_cli_survives_spotlight_launch_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unavailable Spotlight tools should warn once without failing activation."""
    source_bundle = _fake_app_bundle(tmp_path / "source")
    payload_path, _target_bundle = _write_system_applications_payload(
        tmp_path,
        source_bundle=source_bundle,
        mode="symlink",
    )
    real_is_file = Path.is_file
    clock = [0.0]

    def _is_file(path: Path) -> bool:
        if path in {
            mac_apps_helper.LSREGISTER_PATH,
            mac_apps_helper.MDIMPORT_PATH,
            mac_apps_helper.MDLS_PATH,
        }:
            return True
        return real_is_file(path)

    def _run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        if command[0] == str(mac_apps_helper.LSREGISTER_PATH):
            return subprocess.CompletedProcess(command, 0)
        if command[0] in {
            str(mac_apps_helper.MDIMPORT_PATH),
            str(mac_apps_helper.MDLS_PATH),
        }:
            raise OSError("metadata service unavailable")
        pytest.fail(f"unexpected command: {command}")

    def _sleep(duration: float) -> None:
        clock[0] += duration

    monkeypatch.setattr(Path, "is_file", _is_file)
    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run)
    monkeypatch.setattr(mac_apps_helper.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mac_apps_helper.time, "sleep", _sleep)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_REFRESH_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_COMMAND_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(mac_apps_helper, "SPOTLIGHT_VERIFY_INTERVAL_SECONDS", 1)

    assert mac_apps_helper.main(["prog", "system-applications", str(payload_path)]) == 0
    assert (tmp_path / ".nixcfg-mac-apps" / "test-manager.txt").read_text(
        encoding="utf-8"
    ) == "Focus.app\n"
    warning = capsys.readouterr().err
    assert warning.count("warning:") == 1
    assert "could not launch mdimport" in warning
    assert "could not launch mdls" in warning


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
                        "preventDowngrade": False,
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
    assert_nix_ast_equal(
        expect_binding(darwin_applications.values, "text").value,
        """
        lib.mkAfter (
          macApps.applicationsScript {
            entries = activeMacAppEntries;
            stateDirectory = "/Applications/.nixcfg-mac-apps";
            stateName = "darwin-system";
            targetDirectory = "/Applications";
            writable = false;
          }
        )
        """,
    )

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
    assert_nix_ast_equal(
        expect_binding(copy_apps.values, "enable").value,
        Primitive(value=False),
    )
    assert_nix_ast_equal(
        expect_binding(link_apps.values, "enable").value,
        Primitive(value=False),
    )

    home_binding = expect_instance(
        expect_binding(home_config.values, "home").value,
        AttributeSet,
    )
    home_activation = expect_instance(
        expect_binding(home_binding.values, "activation").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(home_activation.values, "nixcfgUserApplications").value,
        """
        lib.mkIf (userEntries != [ ]) (
          lib.hm.dag.entryAfter [ "nixcfgRemoveManagedApplicationProfileCopies" ] (
            macApps.applicationsScript {
              entries = userEntries;
              stateDirectory = "${config.home.homeDirectory}/Applications/.nixcfg-mac-apps";
              stateName = "home-manager-user";
              targetDirectory = "${config.home.homeDirectory}/Applications";
              writable = true;
            }
          )
        )
        """,
    )


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
    assert_nix_ast_equal(optionals_call.argument, "managedEntries != [ ]")

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
    assert_nix_ast_equal(unique_call.argument, Identifier(name="managedEntries"))

    overlap_call = expect_instance(
        expect_instance(assertion_list[1], Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        overlap_call.name,
        identifier_attr_path("macApps", "managedAppsNotInPackageListsAssertion"),
    )
    overlap_args = expect_instance(overlap_call.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(overlap_args.values, "entries").value,
        Identifier(name="managedEntries"),
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
    assert_nix_ast_equal(mk_if.argument, "managedEntries != [ ]")

    entry_after = expect_instance(
        expect_instance(cleanup.argument, Parenthesis).value, FunctionCall
    )
    entry_after_name = expect_instance(entry_after.name, FunctionCall)
    assert_nix_ast_equal(
        entry_after_name.name,
        identifier_attr_path("lib", "hm", "dag", "entryAfter"),
    )
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
    assert_nix_ast_equal(
        expect_binding(remove_args.values, "bundleNames").value,
        Identifier(name="managedBundleNames"),
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
    assert_nix_ast_equal(mk_if.argument, "managedEntries != [ ]")

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
    managed_bundle_inherits = [
        expr
        for expr in leak_audit_args.values
        if isinstance(expr, Inherit)
        and [name.name for name in expr.names] == ["managedBundleNames"]
    ]
    assert len(managed_bundle_inherits) == 1
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
    formals = [
        expect_instance(argument, Identifier) for argument in leak_script.argument_set
    ]
    assert [formal.name for formal in formals] == [
        "packagePaths",
        "managedBundleNames",
        "label",
    ]
    assert all(formal.default_value is None for formal in formals[:-1])
    assert_nix_ast_equal(formals[-1].default_value, '"home.packages"')
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
          managedBundleNames = unique managedBundleNames;
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
    formals = [
        expect_instance(argument, Identifier) for argument in remove_script.argument_set
    ]
    assert [formal.name for formal in formals] == [
        "bundleNames",
        "targetDirectory",
    ]
    assert all(formal.default_value is None for formal in formals)
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
          bundleNames = unique bundleNames;
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
    """Evaluate conflicts because AST inspection cannot resolve package identities."""
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
def test_managed_app_overlap_assertion_accepts_context_carrying_output_paths() -> None:
    """Evaluate context paths because AST inspection cannot coerce real Nix strings."""

    def package(pname: str, bundle_name: str) -> AttributeSet:
        return nix_attrset({
            "pname": pname,
            "outPath": Identifier(name="sharedOutPath"),
            "passthru.macApp": {
                "bundleName": bundle_name,
                "bundleRelPath": f"Applications/{bundle_name}",
            },
        })

    expression = nix_let(
        {
            "sharedOutPath": _curried_call(
                identifier_attr_path("builtins", "toFile"),
                Primitive(value="shared-mac-app-output"),
                Primitive(value="context-carrying output"),
            ),
            "managedPkg": package("managed", "Managed.app"),
            "candidatePkg": package("candidate", "Elsewhere.app"),
        },
        Assertion(
            expression=FunctionCall(
                name=identifier_attr_path("builtins", "hasContext"),
                argument=Identifier(name="sharedOutPath"),
            ),
            body=FunctionCall(
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
                                    "bundleName": "Managed.app",
                                    "mode": "copy",
                                })
                            ]),
                            "packageLists": nix_list([
                                nix_attrset({
                                    "label": "home.packages",
                                    "packages": nix_list([
                                        Identifier(name="candidatePkg")
                                    ]),
                                })
                            ]),
                        }),
                    )
                ),
            ),
        ),
    )

    assert json.loads(_mac_apps_eval(expression)) == {
        "assertion": False,
        "message": (
            "nixcfg.macApps.applications packages must not also appear in other "
            "installed package lists.\n"
            "- Managed.app (managed) also appears in home.packages as candidate."
        ),
    }


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_app_overlap_assertion_allows_distinct_package_lists() -> None:
    """Evaluate distinct packages because AST inspection cannot resolve output identities."""
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
    """Evaluate tryEval semantics that AST inspection cannot establish structurally."""
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


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_app_overlap_assertion_preserves_conflict_order() -> None:
    """Evaluate conflict messages because AST inspection cannot establish output order."""
    alpha = _fake_mac_app_package(
        "alpha",
        "/nix/store/fake-alpha",
        "Alpha.app",
    )
    beta = _fake_mac_app_package(
        "beta",
        "/nix/store/fake-beta",
        "Beta.app",
    )
    result = _managed_app_overlap_assertion_result(
        [
            nix_attrset({
                "label": "home.packages",
                "packages": nix_list([
                    _fake_mac_app_package(
                        "beta-alias",
                        "/nix/store/fake-beta-alias",
                        "Beta.app",
                    ),
                    _fake_mac_app_package(
                        "alpha-by-output",
                        "/nix/store/fake-alpha",
                        "Elsewhere.app",
                    ),
                    _fake_mac_app_package(
                        "alpha-by-both",
                        "/nix/store/fake-alpha",
                        "Alpha.app",
                    ),
                ]),
            }),
            nix_attrset({
                "label": "environment.systemPackages",
                "packages": nix_list([
                    _fake_mac_app_package(
                        "alpha-system-alias",
                        "/nix/store/fake-alpha-system-alias",
                        "Alpha.app",
                    )
                ]),
            }),
        ],
        entries=[
            nix_attrset({
                "package": alpha,
                "bundleName": "Alpha.app",
                "mode": "copy",
            }),
            nix_attrset({
                "package": beta,
                "bundleName": "Beta.app",
                "mode": "copy",
            }),
        ],
    )

    assert result == {
        "assertion": False,
        "message": (
            "nixcfg.macApps.applications packages must not also appear in other "
            "installed package lists.\n"
            "- Alpha.app (alpha) also appears in home.packages as "
            "alpha-by-output.\n"
            "- Alpha.app (alpha) also appears in home.packages as "
            "alpha-by-both.\n"
            "- Alpha.app (alpha) also appears in environment.systemPackages as "
            "alpha-system-alias.\n"
            "- Beta.app (beta) also appears in home.packages as beta-alias."
        ),
    }


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_app_overlap_assertion_keeps_unused_packages_lazy() -> None:
    """Evaluate laziness because AST inspection cannot prove thrown values stay unused."""
    empty_candidates = _managed_app_overlap_assertion_result(
        [nix_attrset({"label": "home.packages", "packages": nix_list([])})],
        entries=[
            nix_attrset({
                "package": parse_nix_expr('throw "unused managed package"'),
                "bundleName": "Unused.app",
                "mode": "copy",
            })
        ],
    )
    empty_entries = _managed_app_overlap_assertion_result(
        [
            nix_attrset({
                "label": "home.packages",
                "packages": nix_list([
                    parse_nix_expr('throw "unused candidate package"')
                ]),
            })
        ],
        entries=[],
    )
    unused_managed_metadata = _managed_app_overlap_assertion_result(
        [
            nix_attrset({
                "label": "home.packages",
                "packages": nix_list([
                    _fake_mac_app_package(
                        "candidate",
                        "/nix/store/fake-candidate",
                        "Candidate.app",
                    )
                ]),
            })
        ],
        entries=[
            nix_attrset({
                "package": nix_attrset({
                    "pname": "managed",
                    "outPath": "/nix/store/fake-managed",
                    "passthru.macApp.bundleName": parse_nix_expr(
                        'throw "unused managed bundle metadata"'
                    ),
                }),
                "bundleName": "Managed.app",
                "mode": "copy",
            })
        ],
    )

    expected = {
        "assertion": True,
        "message": (
            "nixcfg.macApps.applications packages must not also appear in other "
            "installed package lists."
        ),
    }
    assert empty_candidates == expected
    assert empty_entries == expected
    assert unused_managed_metadata == expected


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_app_overlap_assertion_normalizes_each_package_once() -> None:
    """Evaluate traces because AST inspection cannot observe normalization frequency."""

    def traced_package(
        pname: str,
        out_path: str,
        bundle_name: str,
        marker: str,
    ) -> AttributeSet:
        return nix_attrset({
            "pname": pname,
            "outPath": parse_nix_expr(
                f'{{ __toString = _: builtins.trace "{marker}" "{out_path}"; }}'
            ),
            "passthru.macApp": {
                "bundleName": bundle_name,
                "bundleRelPath": f"Applications/{bundle_name}",
            },
        })

    entries = [
        nix_attrset({
            "package": traced_package(
                "managed-first",
                "/nix/store/fake-managed-first",
                "ManagedFirst.app",
                "normalize-managed-first",
            ),
            "bundleName": "ManagedFirst.app",
            "mode": "copy",
        }),
        nix_attrset({
            "package": traced_package(
                "managed-second",
                "/nix/store/fake-managed-second",
                "ManagedSecond.app",
                "normalize-managed-second",
            ),
            "bundleName": "ManagedSecond.app",
            "mode": "copy",
        }),
    ]
    candidates = [
        traced_package(
            f"candidate-{index}",
            f"/nix/store/fake-candidate-{index}",
            f"Candidate{index}.app",
            f"normalize-candidate-{index}",
        )
        for index in range(3)
    ]
    expression = FunctionCall(
        name=identifier_attr_path("builtins", "toJSON"),
        argument=Parenthesis(
            value=FunctionCall(
                name=identifier_attr_path(
                    "macApps", "managedAppsNotInPackageListsAssertion"
                ),
                argument=nix_attrset({
                    "entries": nix_list(entries),
                    "packageLists": nix_list([
                        nix_attrset({
                            "label": "home.packages",
                            "packages": nix_list(candidates),
                        })
                    ]),
                }),
            )
        ),
    )
    wrapped_expression = nix_let(
        {
            "context": FunctionCall(
                name=nix_import(REPO_ROOT / "tests/nix/mac-apps/eval-context.nix"),
                argument=nix_attrset({"rsyncPath": _rsync_path()}),
            ),
            "macApps": identifier_attr_path("context", "macApps"),
        },
        expression,
    )
    result = nix_eval_result(wrapped_expression, raw=True)

    assert json.loads(result.stdout)["assertion"] is True
    for marker in [
        "normalize-managed-first",
        "normalize-managed-second",
        "normalize-candidate-0",
        "normalize-candidate-1",
        "normalize-candidate-2",
    ]:
        assert result.stderr.count(f"trace: {marker}\n") == 1


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
    assert [name.name for name in version_inherit.names] == ["version"]

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


def test_codex_desktop_package_ships_the_unified_chatgpt_bundle() -> None:
    """Codex Desktop should install the merged app under its upstream ChatGPT name."""
    package_source = Path(REPO_ROOT / "packages/codex-desktop/default.nix").read_text(
        encoding="utf-8"
    )
    package = expect_instance(parse_nix_expr(package_source), FunctionDefinition)
    derivation = expect_instance(package.output, FunctionCall)
    derivation_args = expect_instance(derivation.argument, AttributeSet)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkZipApp"))
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "appName").value,
        StringPrimitive(value="ChatGPT"),
    )
    # The zip's ChatGPT.app payload must flow through mkZipApp defaults so the
    # bundle, executable, and source path all stay on the upstream name.
    derivation_bindings = binding_map(derivation_args.values)
    assert "sourceAppPath" not in derivation_bindings
    assert "executableName" not in derivation_bindings
    assert "bundleName" not in derivation_bindings


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
        expect_binding(derivation_args.values, "sourceName").value,
        StringPrimitive(value="zen.macos-universal.dmg"),
    )
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "codesignApp").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "macApp").value,
        '{ installMode = "copy"; }',
    )
    assert_nix_ast_equal(
        expect_binding(derivation_args.values, "stdenv").value,
        """
        stdenvNoCC.override (old: {
          shell = "${bash-dynamic-pipe-heredoc}/bin/bash";
          initialPath = [ bash-dynamic-pipe-heredoc ] ++ old.initialPath;
          allowedRequisites = old.allowedRequisites ++ [ bash-dynamic-pipe-heredoc ];
          extraAttrs = old.extraAttrs // {
            shellPackage = bash-dynamic-pipe-heredoc;
          };
        })
        """,
    )

    install_hook = expect_instance(
        expect_binding(derivation_args.values, "postInstallApp").value,
        IndentedString,
    )
    install_shell = parse_shell(indented_string_body(install_hook.rebuild()))
    commands = command_texts(install_shell)
    assignments = {
        node_text(node, install_shell.sanitized)
        for node in iter_nodes(install_shell.tree.root_node, "variable_assignment")
    }

    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "packages/zen-twilight/default.nix",
            "    expected_version=${",
            "}\n",
        ),
        "lib.escapeShellArg selfSource.version",
    )
    assert "expected_version=__NIX_INTERP__" in assignments
    assert 'actual_version="$app_version-$build_id"' in assignments
    assert '[[ "$actual_version" != "$expected_version" ]]' in commands

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


def test_zed_nightly_darwin_installer_uses_dynamic_pipe_safe_bash() -> None:
    """Zed's plist heredoc should run under the patched Darwin Bash."""
    package_path = "packages/zed-editor-nightly/default.nix"
    package = expect_instance(
        parse_nix_expr(Path(REPO_ROOT / package_path).read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    zed_override = expect_instance(
        expect_scope_binding(package.output, "zedOverride").value,
        FunctionDefinition,
    )
    install_phase = expect_instance(
        expect_binding(zed_override.output.values, "installPhase").value,
        IfExpression,
    )
    install_shell = parse_shell(
        indented_string_body(
            expect_instance(install_phase.consequence, IndentedString).rebuild()
        )
    )

    assert_nix_ast_equal(
        nix_source_fragment_expr(
            package_path,
            "capacity, so scope the upstream backport to this one script.\n          ${",
            "}/bin/bash ${./install_zed_nightly_app.sh}",
        ),
        Identifier(name="bash-dynamic-pipe-heredoc"),
    )
    assert len(command_texts(install_shell, "__NIX_INTERP__/bin/bash")) == 1


def test_george_direnv_uses_dynamic_pipe_safe_bash() -> None:
    """Production direnv config should select the patched Darwin Bash."""
    root = _module_output("home/george/configuration.nix")
    programs = expect_instance(
        expect_binding(root.values, "programs").value,
        AttributeSet,
    )
    direnv = expect_instance(
        expect_binding(programs.values, "direnv").value,
        AttributeSet,
    )
    direnv_config = expect_instance(
        expect_binding(direnv.values, "config").value,
        AttributeSet,
    )
    global_config = expect_instance(
        expect_binding(direnv_config.values, "global").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(global_config.values, "bash_path").value,
        '"${pkgs.bash-dynamic-pipe-heredoc}/bin/bash"',
    )
    assert_nix_ast_equal(
        expect_binding(global_config.values, "warn_timeout").value,
        Primitive(value=0),
    )


def test_work_mac_app_routes_preserve_system_and_user_scopes() -> None:
    """The compact routing table must retain every package and route policy."""
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "home/george/work.nix",
            "  systemApp = ",
            ";\n  routing =",
        ),
        'package: { inherit package; scope = "system"; }',
    )
    routing = expect_instance(
        nix_source_fragment_expr(
            "home/george/work.nix", "  routing = ", ";\n  projection ="
        ),
        AttributeSet,
    )
    expected_system_routes = {
        "agentlog": "pkgs.agentlog",
        "aside": "pkgs.aside",
        "baseten-switch": "pkgs.baseten-switch",
        "bb": "pkgs.bb",
        "buzz": "pkgs.buzz",
        "clearly": "pkgs.clearly",
        "coast-local": "pkgs.coast-local",
        "energy": "pkgs.energy",
        "executor": "pkgs.executor",
        "factory": "pkgs.factory",
        "gemini": "pkgs.gemini",
        "github-copilot": "pkgs.github-copilot-app",
        "gooeypi": "pkgs.gooeypi",
        "hermes": "pkgs.hermes-desktop",
        "hq": "pkgs.hq",
        "humanlayer": "pkgs.humanlayer",
        "mach-studio": "pkgs.mach-studio",
        "onepassword": "pkgs.onepassword",
        "openchamber": "pkgs.openchamber",
        "paseo": "pkgs.paseo",
        "reflect": "pkgs.reflect-open",
        "screen-studio": "pkgs.screen-studio",
        "traycer": "pkgs.traycer",
        "unsloth": "pkgs.unsloth",
        "voiceos": "pkgs.voiceos",
        "waku": "pkgs.waku",
        "writer-computer": "pkgs.writer-computer",
        "zeron": "pkgs.zeron",
        "zo": "pkgs.zo",
    }
    for name, package in expected_system_routes.items():
        assert_nix_ast_equal(_route_package(routing, name), package)
        assert_nix_ast_equal(
            _route_scope(routing, name),
            StringPrimitive(value="system"),
        )

    gemini = expect_instance(_routing_entry(routing, "gemini"), AttributeSet)
    assert_nix_ast_equal(
        expect_binding(gemini.values, "preventDowngrade").value,
        Primitive(value=True),
    )

    expected_user_routes = {
        "claude-code-url-handler": "pkgs.claude-code-url-handler",
        "cleanshot": "pkgs.cleanshot",
        "freelens": "pkgs.freelens",
        "grok-build": "pkgs.grok-build",
        "tailscale": "pkgs.tailscale-app",
        "town-assistant": "pkgs.town-assistant-nightly",
        "warp-preview": "pkgs.warp-preview",
    }
    for name, package in expected_user_routes.items():
        assert_nix_ast_equal(_route_package(routing, name), package)
        assert not _route_has_scope(routing, name)


def test_george_config_manages_mutable_gui_apps_via_scoped_applications() -> None:
    """George's config should single-source managed macOS app routing."""
    root = _module_output("home/george/configuration.nix")
    nixcfg = expect_instance(expect_binding(root.values, "nixcfg").value, AttributeSet)

    assert_nix_ast_equal(
        expect_scope_binding(nixcfg, "macAppHelpers").value,
        "import ../../lib/mac-apps.nix { inherit lib pkgs; }",
    )
    routing = expect_instance(
        expect_scope_binding(nixcfg, "managedMacAppRouting").value,
        AttributeSet,
    )
    expected_packages = {
        "slack": "pkgs.slack",
        "ghostty": "pkgs.ghostty-tip",
        "zed": "pkgs.zed-editor-nightly",
        "zen-twilight": "pkgs.zen-twilight",
        "code-cursor": "pkgs.code-cursor",
        "vscode-insiders": "pkgs.vscode-insiders",
        "superset": "pkgs.superset",
        "goose": "pkgs.goose-desktop",
        "nordvpn": "pkgs.nordvpn",
        "orbstack": "pkgs.orbstack",
        "utm": "pkgs.utm",
        "zoom": "pkgs.zoom-us",
    }
    for name, package in expected_packages.items():
        assert_nix_ast_equal(_route_package(routing, name), package)
    for name in ("nordvpn", "orbstack"):
        assert_nix_ast_equal(
            _route_scope(routing, name),
            StringPrimitive(value="system"),
        )
    assert not _route_has_scope(routing, "utm")
    assert not _route_has_scope(routing, "zoom")

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


def test_george_config_enables_signal_beta_downgrade_protection_opt_in() -> None:
    """Signal Beta should opt in while ordinary managed apps retain the default."""
    root = _module_output("home/george/configuration.nix")
    nixcfg = expect_instance(expect_binding(root.values, "nixcfg").value, AttributeSet)
    routing = expect_instance(
        expect_scope_binding(nixcfg, "managedMacAppRouting").value,
        AttributeSet,
    )
    entries = binding_map(routing.values)
    signal_entry = expect_instance(entries['"signal-beta"'].value, AttributeSet)
    slack_entry = expect_instance(entries["slack"].value, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(signal_entry.values, "package").value,
        identifier_attr_path("pkgs", "signal-beta"),
    )
    assert_nix_ast_equal(
        expect_binding(signal_entry.values, "preventDowngrade").value,
        Primitive(value=True),
    )
    assert "preventDowngrade" not in binding_map(slack_entry.values)


def test_spacedrive_overlay_only_clears_broken_metadata_on_darwin() -> None:
    """The overlay should preserve Linux's broken flag while enabling the Darwin app."""
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
        expect_binding(tiny_overlays.values, "spacedrive").value,
        """
withManagedMacApp
  (prev.spacedrive.overrideAttrs (old: {
    meta =
      old.meta
      // prev.lib.optionalAttrs prev.stdenv.hostPlatform.isDarwin {
        broken = false;
      };
  }))
  "Spacedrive.app"
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

    assert "chatgpt" not in binding_map(tiny_overlays.values)
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
    assert_nix_ast_equal(
        expect_binding(tiny_overlays.values, "utm").value,
        _curried_call(
            Identifier(name="withManagedMacApp"),
            identifier_attr_path("prev", "utm"),
            StringPrimitive(value="UTM.app", raw_string=True),
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
    assert [name.name for name in version_inherit.names] == ["version"]
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
    assert [name.name for name in version_inherit.names] == ["version"]

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
    assert [name.name for name in src_hash_inherit.names] == ["hash"]

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


def test_george_config_routes_only_the_unified_chatgpt_app() -> None:
    """The merged app should be the only managed ChatGPT application."""
    root = _module_output("home/george/configuration.nix")
    nixcfg = expect_instance(expect_binding(root.values, "nixcfg").value, AttributeSet)
    routing = expect_instance(
        expect_scope_binding(nixcfg, "managedMacAppRouting").value,
        AttributeSet,
    )

    assert "chatgpt" not in binding_map(routing.values)

    codex = expect_instance(expect_binding(routing.values, "codex").value, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(codex.values, "package").value,
        identifier_attr_path("pkgs", "codex-desktop"),
    )
    assert "bundleName" not in binding_map(codex.values)


def test_darwin_gui_package_set_retains_only_unified_chatgpt_app() -> None:
    """The default Darwin package set should retain only the unified app."""
    root = _module_output("modules/home/packages.nix")
    package_set_table = expect_instance(
        expect_scope_binding(root, "packageSetTable").value,
        WithStatement,
    )
    gui_apps = next(
        entry
        for entry in package_set_table.body.value
        if expect_instance(
            expect_binding(entry.values, "name").value,
            StringPrimitive,
        ).value
        == "guiApps"
    )
    package_terms = _concat_terms(expect_binding(gui_apps.values, "packages").value)
    darwin_optionals = expect_instance(package_terms[-1], FunctionCall)
    assert_nix_ast_equal(darwin_optionals.name, "lib.optionals stdenv.isDarwin")
    darwin_packages = expect_instance(darwin_optionals.argument, NixList)
    packages_by_name = {
        expect_instance(package, Identifier).name: package
        for package in darwin_packages.value
    }

    assert_nix_ast_equal(
        packages_by_name["codex-desktop"],
        Identifier(name="codex-desktop"),
    )
    assert "chatgpt" not in packages_by_name


def test_dock_configs_keep_the_targeted_gc_mitigation_scope_explicit() -> None:
    """Dock modules should consume resolved app paths instead of hard-coded app dirs."""

    def dock_items(
        relative_path: str,
    ) -> tuple[
        str,
        NixList,
        FunctionCall,
        list[Inherit],
        list[Inherit],
    ]:
        expr = expect_instance(
            nix_file_expr(relative_path),
            FunctionDefinition,
        )
        call = expect_instance(expr.output, FunctionCall)
        assert_nix_ast_equal(call.name, "dock.mkDockModule")
        args = expect_instance(call.argument, AttributeSet)
        activation = expect_instance(
            expect_binding(args.values, "activationName").value, StringPrimitive
        ).value
        apps = expect_instance(expect_binding(args.values, "apps").value, NixList)
        dock_context = expect_instance(
            expect_scope_binding(call, "dockContext").value, FunctionCall
        )
        context_inherits = [value for value in call.scope if isinstance(value, Inherit)]
        argument_inherits = [
            value for value in args.values if isinstance(value, Inherit)
        ]
        return (
            activation,
            apps,
            dock_context,
            context_inherits,
            argument_inherits,
        )

    (
        george_activation,
        george_dock,
        george_context,
        george_context_inherits,
        george_argument_inherits,
    ) = dock_items("modules/darwin/george/dock-apps.nix")
    (
        town_activation,
        town_dock,
        town_context,
        town_context_inherits,
        town_argument_inherits,
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
        matching_inherits = [
            inherit_expr
            for inherit_expr in context_inherits
            if [name.name for name in inherit_expr.names]
            == ["appPath", "homeDirectory"]
        ]
        assert len(matching_inherits) == 1
        assert_nix_ast_equal(
            matching_inherits[0].from_expression,
            Identifier(name="dockContext"),
        )
    for argument_inherits in (george_argument_inherits, town_argument_inherits):
        matching_inherits = [
            inherit_expr
            for inherit_expr in argument_inherits
            if inherit_expr.from_expression is None
            and [name.name for name in inherit_expr.names]
            == ["homeDirectory", "options", "pkgs"]
        ]
        assert len(matching_inherits) == 1

    assert_nix_ast_equal(
        george_dock,
        """
        [
          "/System/Applications/Calendar.app"
          "/System/Applications/Messages.app"
          (appPath "slack" "Slack.app")
          (appPath "claude" "Claude.app")
          (appPath "zen-twilight" "Twilight.app")
          (appPath "ghostty" "Ghostty.app")
          (appPath "zed" "Zed Nightly.app")
          (appPath "datagrip" "DataGrip.app")
          "/System/Applications/Notes.app"
          (appPath "spotify" "Spotify.app")
          "/System/Applications/System Settings.app"
        ]
        """,
    )
    assert_nix_ast_equal(
        town_dock,
        """
        [
          "/System/Applications/Calendar.app"
          "/System/Applications/Messages.app"
          (appPath "onepassword" "1Password.app")
          (appPath "slack" "Slack.app")
          (appPath "zen-twilight" "Twilight.app")
          (appPath "google-chrome" "Google Chrome.app")
          (appPath "town-assistant" "Town Assistant.app")
          (appPath "codex" "ChatGPT.app")
          (appPath "claude" "Claude.app")
          (appPath "opencode" "OpenCode Desktop Dev.app")
          (appPath "zed" "Zed Nightly.app")
          (appPath "code-cursor" "Cursor.app")
          (appPath "vscode-insiders" "Visual Studio Code - Insiders.app")
          (appPath "ghostty" "Ghostty.app")
          (appPath "datagrip" "DataGrip.app")
          (appPath "notion" "Notion.app")
          "/System/Applications/Notes.app"
          (appPath "figma" "Figma.app")
          (appPath "linear" "Linear.app")
          (appPath "spotify" "Spotify.app")
          "/System/Applications/System Settings.app"
        ]
        """,
    )


def test_dock_activation_updates_and_orders_items_without_clearing_the_dock() -> None:
    """Dock activation should enforce list order without risking an empty Dock."""
    mk_dock_module = expect_instance(
        nix_source_fragment_expr(
            "modules/darwin/george/dock-lib.nix",
            "  mkDockModule =\n",
            "\n}",
        ),
        FunctionDefinition,
    )
    dock_label = expect_scope_binding(mk_dock_module.output, "dockLabel").value
    positioned_apps = expect_scope_binding(
        mk_dock_module.output, "positionedApps"
    ).value
    positioned_others = expect_scope_binding(
        mk_dock_module.output, "positionedOthers"
    ).value
    add_apps = expect_scope_binding(mk_dock_module.output, "addAppCommands").value
    add_others = expect_scope_binding(mk_dock_module.output, "addOtherCommands").value
    remove_others = expect_scope_binding(
        mk_dock_module.output, "removeOtherCommands"
    ).value

    assert_nix_ast_equal(
        dock_label,
        'path: lib.removeSuffix ".app" (builtins.baseNameOf path)',
    )
    assert_nix_ast_equal(
        positioned_apps,
        "lib.imap1 (position: app: { inherit app position; }) apps",
    )
    assert_nix_ast_equal(
        positioned_others,
        "lib.imap1 (position: other: other // { inherit position; }) others",
    )

    def mapped_shell(expression: NixExpression):
        outer_call = expect_instance(expression, FunctionCall)
        mapping_call = expect_instance(outer_call.name, FunctionCall)
        mapper = expect_instance(
            expect_instance(mapping_call.argument, Parenthesis).value,
            FunctionDefinition,
        )
        shell_body = expect_instance(mapper.output, IndentedString)
        return parse_shell(indented_string_body(shell_body.rebuild()))

    assert command_texts(mapped_shell(remove_others)) == [
        '"$dockutil" --find __NIX_INTERP__ --section others',
        '"$dockutil" --remove __NIX_INTERP__ --section others --no-restart',
        'echo "warning: failed to remove stale Dock item __NIX_INTERP__"',
    ]
    assert command_texts(mapped_shell(add_apps)) == [
        "[ -e __NIX_INTERP__ ]",
        '"$dockutil" --add __NIX_INTERP__ --replacing __NIX_INTERP__ '
        "--position __NIX_INTERP__ --section apps --no-restart",
        'echo "warning: failed to add Dock app __NIX_INTERP__"',
        'echo "warning: skipping missing Dock app __NIX_INTERP__"',
    ]
    assert command_texts(mapped_shell(add_others)) == [
        "[ -e __NIX_INTERP__ ]",
        '"$dockutil" --add __NIX_INTERP__ --replacing __NIX_INTERP__ '
        "--position __NIX_INTERP__ --section others --sort __NIX_INTERP__ "
        "--no-restart",
        'echo "warning: failed to add Dock item __NIX_INTERP__"',
        'echo "warning: skipping missing Dock item __NIX_INTERP__"',
    ]


def test_george_config_does_not_install_repo_managed_editor_cli_wrappers() -> None:
    """Editor app copies should no longer be accompanied by repo-managed CLI wrappers."""
    assert not (REPO_ROOT / "home/george/bin/_managed-app-cli-wrapper").exists()
    assert not (REPO_ROOT / "home/george/bin/code-insiders").exists()
    assert not (REPO_ROOT / "home/george/bin/cursor").exists()
