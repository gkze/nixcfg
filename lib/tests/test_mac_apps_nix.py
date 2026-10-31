"""Regression checks for macOS application bundle management."""

import ast
import json
import os
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
import threading
from contextlib import redirect_stderr
from functools import cache
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.expressions.with_statement import WithStatement

from lib import mac_apps_helper
from lib.tests._assertions import expect_instance
from lib.tests._darwin_signing import indented_string_interpolations
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    expect_platform_branch,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.tests._nix_eval import (
    allow_nix_evaluation,
    nix_attrset,
    nix_eval_raw,
    nix_import,
    nix_let,
    nix_list,
)
from lib.tests._nix_source import (
    nix_file_binding_expr,
    nix_source_fragment_expr,
)
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
def _darwin_app_builder(
    name: str, next_name: str, separator: str = "\n\n"
) -> FunctionDefinition:
    """Parse one exported shared Darwin app builder."""
    return expect_instance(
        nix_source_fragment_expr(
            "overlays/_lib/helpers/darwin-apps.nix",
            f"  {name} =\n",
            f";{separator}  {next_name} =",
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


def _mac_apps_fragment_expr(start_marker: str, end_marker: str):
    return nix_source_fragment_expr("lib/mac-apps.nix", start_marker, end_marker)


def _concat_terms(expression: NixExpression) -> list[NixExpression]:
    """Flatten one left-associative Nix list concatenation."""
    if isinstance(expression, BinaryExpression) and expression.operator.name == "++":
        return [*_concat_terms(expression.left), *_concat_terms(expression.right)]
    return [expression]


@cache
def _module_output(relative_path: str) -> AttributeSet:
    """Parse one Nix module and return its top-level output attrset."""
    expr = expect_instance(
        parse_nix_expr(Path(REPO_ROOT / relative_path).read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    return expect_instance(expr.output, AttributeSet)


@cache
def _codesigned_dmg_install_phase() -> str:
    """Render the real DMG install phase with only derivation assembly stubbed."""
    identity = parse_nix_expr("attrs: attrs")
    prev = nix_attrset({
        "lib": {
            "concatStringsSep": identifier_attr_path("builtins", "concatStringsSep"),
            "optional": parse_nix_expr(
                "condition: value: if condition then [ value ] else [ ]"
            ),
            "optionalAttrs": parse_nix_expr(
                "condition: attrs: if condition then attrs else { }"
            ),
            "optionalString": parse_nix_expr(
                'condition: value: if condition then value else ""'
            ),
            "toUpper": parse_nix_expr("value: value"),
        },
        "darwin": {"xattr": "xattr"},
        "fetchurl": identity,
        "stdenvNoCC": {"mkDerivation": identity},
        "undmg": "undmg",
    })
    helpers = FunctionCall(
        name=nix_import(REPO_ROOT / "overlays/_lib/helpers/darwin-apps.nix"),
        argument=nix_attrset({"prev": prev, "system": "aarch64-darwin"}),
    )
    app = FunctionCall(
        name=identifier_attr_path("helpers", "mkDmgApp"),
        argument=nix_attrset({
            "pname": "demo",
            "appName": "Demo",
            "codesignApp": True,
            "makeBinary": False,
            "info": {
                "version": "1.0",
                "urls": {"aarch64-darwin": "https://example.test/demo.dmg"},
                "hashes": {"aarch64-darwin": "sha256-fixture"},
            },
        }),
    )
    return nix_eval_raw(
        nix_let(
            {"helpers": helpers, "app": app},
            identifier_attr_path("app", "installPhase"),
        )
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


def _assert_post_install_signature_verification(
    install_phase: IndentedString,
    *,
    guard: str,
) -> None:
    """Assert strict final-bundle verification after the hook."""
    shell = parse_shell(indented_string_body(install_phase.rebuild()))
    assert command_texts(shell)[-2:] == ["runHook postInstall", "__NIX_INTERP__"]

    final_interpolations = []
    for expression in indented_string_interpolations(install_phase):
        if not isinstance(expression, FunctionCall) or not isinstance(
            expression.argument, Parenthesis
        ):
            continue
        candidate = expression.argument.value
        if (
            isinstance(candidate, FunctionCall)
            and isinstance(candidate.name, Identifier)
            and candidate.name.name == "verifyBundleSignature"
        ):
            final_interpolations.append(expression)
    [final_interpolation] = final_interpolations
    optional_call = expect_instance(final_interpolation, FunctionCall)
    condition_call = expect_instance(optional_call.name, FunctionCall)
    assert_nix_ast_equal(
        condition_call.name,
        identifier_attr_path("prev", "lib", "optionalString"),
    )
    assert_nix_ast_equal(condition_call.argument, Identifier(name=guard))
    verification_call = expect_instance(
        expect_instance(optional_call.argument, Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        verification_call.name,
        Identifier(name="verifyBundleSignature"),
    )
    assert_nix_ast_equal(
        verification_call.argument,
        "''\"$out/Applications/${resolvedBundleName}\"''",
    )


@pytest.mark.parametrize(
    ("name", "next_name", "separator"),
    [
        ("mkDmgApp", "mkDmgApp7zz", "\n\n"),
        ("mkCrx3DmgApp", "mkZipApp", "\n"),
    ],
)
def test_codesign_app_helpers_verify_before_and_after_post_install(
    name: str,
    next_name: str,
    separator: str,
) -> None:
    """Generic ad-hoc signing must verify immediately and after the final hook."""
    helper = _darwin_app_builder(name, next_name, separator)
    unpacked_call = expect_instance(helper.output, FunctionCall)
    unpacked_args = expect_instance(unpacked_call.argument, AttributeSet)
    unpack = expect_instance(
        expect_binding(unpacked_args.values, "unpack").value, AttributeSet
    )
    install_owner = unpack
    if name == "mkCrx3DmgApp":
        install_owner = expect_instance(
            expect_binding(unpack.values, "drvAttrs").value, AttributeSet
        )
    install_phase = expect_instance(
        expect_binding(install_owner.values, "installPhase").value,
        IndentedString,
    )

    sign_calls: list[FunctionCall] = []
    for expression in indented_string_interpolations(install_phase):
        if not isinstance(expression, FunctionCall) or not isinstance(
            expression.argument, Parenthesis
        ):
            continue
        candidate = expression.argument.value
        if (
            isinstance(candidate, FunctionCall)
            and isinstance(candidate.name, Identifier)
            and candidate.name.name == "signAndVerifyBundle"
        ):
            sign_calls.append(candidate)

    [sign_call] = sign_calls
    sign_arguments = expect_instance(sign_call.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(sign_arguments.values, "verify").value,
        Primitive(value=True),
    )
    _assert_post_install_signature_verification(install_phase, guard="codesignApp")


def test_zip_app_can_verify_custom_signing_after_post_install() -> None:
    """Packages that sign in postInstallApp can verify the final ZIP artifact."""
    helper = _darwin_app_builder("mkZipApp", "mkPkgApp")
    unpacked_call = expect_instance(helper.output, FunctionCall)
    unpacked_args = expect_instance(unpacked_call.argument, AttributeSet)
    unpack = expect_instance(
        expect_binding(unpacked_args.values, "unpack").value, AttributeSet
    )
    install_phase = expect_instance(
        expect_binding(unpack.values, "installPhase").value,
        IndentedString,
    )

    _assert_post_install_signature_verification(
        install_phase, guard="verifyAppSignature"
    )


@pytest.mark.nix_eval(
    reason="Only evaluation renders the production install phase exercised with codesign."
)
@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
@pytest.mark.skipif(sys.platform != "darwin", reason="codesign requires Darwin")
def test_mutating_post_install_hook_fails_final_bundle_verification(
    tmp_path: Path,
) -> None:
    """A hook can pass immediate signing yet must not ship a mutated bundle."""
    output = tmp_path / "output"
    app = output / "Applications/Demo.app"
    executable = app / "Contents/MacOS/Demo"
    resources = app / "Contents/Resources"
    executable.parent.mkdir(parents=True)
    resources.mkdir()
    shutil.copyfile("/usr/bin/codesign", executable)
    executable.chmod(0o755)
    with (app / "Contents/Info.plist").open("wb") as plist_file:
        plistlib.dump(
            {
                "CFBundleExecutable": "Demo",
                "CFBundleIdentifier": "com.example.demo",
            },
            plist_file,
        )
    payload = resources / "payload.txt"
    payload.write_text("original\n", encoding="utf-8")

    install_phase = _codesigned_dmg_install_phase()
    # The real app is already materialized, so execute from the first signing
    # command onward. This retains both immediate and final verification.
    signing_phase = install_phase[install_phase.index("/usr/bin/codesign") :]
    post_install_hook = """
postInstall() {
  printf 'mutation\\n' >> "$out/Applications/Demo.app/Contents/Resources/payload.txt"
}
runHook() {
  if [ "$1" = postInstall ]; then
    postInstall
  fi
}
"""
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(  # noqa: S603
        [bash, "-eu", "-c", post_install_hook + signing_phase],
        check=False,
        capture_output=True,
        env={**os.environ, "out": str(output)},
        text=True,
    )

    # Reaching the mutation proves immediate signing and verification passed.
    assert payload.read_text(encoding="utf-8") == "original\nmutation\n"
    assert result.returncode == 1
    assert "sealed resource" in result.stderr


@pytest.mark.parametrize(
    ("name", "next_name"),
    [
        ("mkSimpleDarwinApp", "mkDmgApp"),
        ("mkDmgApp", "mkDmgApp7zz"),
        ("mkZipApp", "mkPkgApp"),
    ],
)
def test_prebuilt_app_helpers_preserve_signed_resources_by_default(
    name: str,
    next_name: str,
) -> None:
    """Stdenv fixup must not rewrite scripts covered by a vendor signature."""
    helper = _darwin_app_builder(name, next_name)
    fixup_argument = next(
        argument
        for argument in helper.argument_set
        if isinstance(argument, Identifier) and argument.name == "dontFixup"
    )
    assert_nix_ast_equal(fixup_argument.default_value, "true")


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


@pytest.mark.nix_eval(
    reason="Only evaluation renders the interpolated shell program exercised by Bash."
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


def test_pkg_app_helper_expands_pkg_into_fresh_destination() -> None:
    """Pkgutil --expand-full expects to create its destination directory."""
    install_phase = expect_instance(
        nix_source_fragment_expr(
            "overlays/_lib/helpers/darwin-apps.nix",
            "        installPhase = ",
            ";\n      };",
            occurrence=4,
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


def test_pkg_app_find_payload_accepts_nested_app_contents() -> None:
    """Tailscale 1.104.1 is Payload/App/Contents; the old Payload/Contents miss."""
    find_payload = expect_instance(
        nix_file_binding_expr(
            "overlays/_lib/helpers/darwin-apps.nix",
            "findPayload",
        ),
        IfExpression,
    )
    copy_contents_find = expect_instance(find_payload.consequence, IfExpression)
    find_shell = parse_shell(
        indented_string_body(
            expect_instance(copy_contents_find.consequence, IndentedString).rebuild()
        )
    )
    assert command_texts(find_shell, "find") == [
        'find "$pkg_dir" -maxdepth 6 -type d -name Contents -path "*/Payload/*" -print -quit'
    ]


def test_pkg_app_finds_nested_distribution_payload_contents(tmp_path: Path) -> None:
    """Tailscale 1.104.1 ships Payload/Tailscale.app/Contents, not Payload/Contents."""
    pkg_dir = tmp_path / "tailscale-app-pkg"
    nested = pkg_dir / "Distribution.pkg" / "Payload" / "Tailscale.app" / "Contents"
    nested.mkdir(parents=True)
    (pkg_dir / "Resources").mkdir()
    find = shutil.which("find")
    assert find is not None
    found = subprocess.check_output(  # noqa: S603 -- owned find against a fixture tree
        [
            find,
            str(pkg_dir),
            "-maxdepth",
            "6",
            "-type",
            "d",
            "-name",
            "Contents",
            "-path",
            "*/Payload/*",
            "-print",
            "-quit",
        ],
        text=True,
    ).strip()
    assert Path(found) == nested
    flat = subprocess.check_output(  # noqa: S603 -- prove the old path misses 1.104.1
        [
            find,
            str(pkg_dir),
            "-maxdepth",
            "4",
            "-type",
            "d",
            "-path",
            "*/Payload/Contents",
            "-print",
            "-quit",
        ],
        text=True,
    ).strip()
    assert flat == ""


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
            "  applicationsScript =\n", "\n\n  # A scope owned by the primary user"
        ),
        FunctionDefinition,
    )
    helper_entries = expect_instance(
        _mac_apps_fragment_expr(
            "  helperApplicationEntries =\n", "\n\n  applicationsScript ="
        ),
        FunctionDefinition,
    )
    assert_nix_ast_equal(
        helper_entries,
        """
        entries:
          map (entry: {
            inherit (entry) bundleName mode;
            preventDowngrade = entry.preventDowngrade or false;
            sourcePath =
              entry.sourcePath or "${entry.package}/${requiredMacAppAttr entry.package "bundleRelPath"}";
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
          entries = helperApplicationEntries entries;
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
    assert _manifest_names(state_directory / "test-manager.txt") == "Fake.app\n"
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

    def _fake_install_managed_app(
        *,
        bundle_name: str,
        source_path: str,
        target_directory: Path,
        **_kwargs: object,
    ) -> None:
        barrier.wait()
        shutil.copytree(source_path, target_directory / bundle_name)
        with lock:
            installed.append(bundle_name)

    monkeypatch.setattr(
        mac_apps_helper,
        "_install_managed_app",
        _fake_install_managed_app,
    )

    def _disable_metadata_refresh(*_args: object) -> None:
        return None

    monkeypatch.setattr(
        mac_apps_helper,
        "_refresh_launch_services_registration",
        _disable_metadata_refresh,
    )
    monkeypatch.setattr(
        mac_apps_helper,
        "_refresh_spotlight_metadata",
        _disable_metadata_refresh,
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
    assert (target_directory / "First.app").is_dir()
    assert (target_directory / "Second.app").is_dir()
    assert (
        _manifest_names(state_directory / "test-manager.txt")
        == "First.app\nSecond.app\n"
    )


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
    assert (
        _manifest_names(tmp_path / ".nixcfg-mac-apps" / "test-manager.txt")
        == "Example.app\n"
    )


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


def _manifest_names(state_file: Path) -> str:
    """Return the ownership manifest as name-only lines for assertions."""
    return "".join(
        f"{line.split(chr(9), 1)[0]}\n"
        for line in state_file.read_text(encoding="utf-8").splitlines()
    )


def test_managed_app_manifest_replace_failure_preserves_previous_ownership(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed atomic promotion must retain the last complete ownership record."""
    state_file = tmp_path / "test-manager.txt"
    state_file.write_text(
        "Existing.app\t/nix/store/old-Existing.app\n", encoding="utf-8"
    )

    def _replace(source: Path, destination: str | Path) -> Path:
        source_path = Path(source)
        assert Path(destination) == state_file
        assert source_path.parent == state_file.parent
        assert (
            source_path.read_text(encoding="utf-8")
            == "Replacement.app\t/nix/store/new-Replacement.app\n"
        )
        raise OSError("simulated manifest promotion failure")

    monkeypatch.setattr(Path, "replace", _replace)

    with pytest.raises(OSError, match="simulated manifest promotion failure"):
        mac_apps_helper._write_managed_app_manifest(
            state_file,
            [("Replacement.app", "/nix/store/new-Replacement.app")],
        )

    assert (
        state_file.read_text(encoding="utf-8")
        == "Existing.app\t/nix/store/old-Existing.app\n"
    )
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
    assert _manifest_names(state_file) == "Stale.app\n"


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
    assert (
        _manifest_names(tmp_path / ".nixcfg-mac-apps" / "test-manager.txt")
        == "Focus.app\n"
    )
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


def test_profile_bundle_leak_audit_script_reports_managed_bundle_exposure(
    tmp_path: Path,
) -> None:
    """Python leak audit behavior should stay aligned with the Nix helper wrapper."""
    managed_package = tmp_path / "cursor-package"
    (managed_package / "Applications" / "Cursor.app").mkdir(parents=True)

    leak_script = expect_instance(
        _mac_apps_fragment_expr(
            "  profileBundleLeakAuditScript =\n",
            "\n\n  systemApplicationsScript =",
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


def test_chatgpt_package_ships_the_unified_chatgpt_bundle() -> None:
    """The chatgpt package installs the merged app on Darwin and a deb on Linux."""
    package_source = Path(REPO_ROOT / "packages/chatgpt/default.nix").read_text(
        encoding="utf-8"
    )
    package = expect_instance(parse_nix_expr(package_source), FunctionDefinition)
    platform_branch = expect_platform_branch(package.output)
    assert_nix_ast_equal(platform_branch.condition, Identifier(name="isDarwin"))

    # The zip's ChatGPT.app payload must flow through mkZipApp defaults so the
    # bundle, executable, and source path all stay on the upstream name.
    # Leave sourceName unset so fetchurl uses the ZIP basename and hits the
    # prepare prefetch Cachix path (Update 37586805620).
    darwin_call = expect_instance(platform_branch.consequence, FunctionCall)
    assert_nix_ast_equal(darwin_call.name, Identifier(name="mkZipApp"))
    darwin_args = expect_instance(darwin_call.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(darwin_args.values, "appName").value,
        StringPrimitive(value="ChatGPT"),
    )
    darwin_bindings = binding_map(darwin_args.values)
    assert "sourceAppPath" not in darwin_bindings
    assert "executableName" not in darwin_bindings
    assert "bundleName" not in darwin_bindings
    assert "sourceName" not in darwin_bindings

    # Linux repacks OpenAI's Debian artifact: the version comes from the pool
    # filename, and the patchelf pass must stay scoped to the optional Qt shims
    # and the Android NDK / musl prebuilds that can never load on glibc Linux.
    linux_call = expect_instance(platform_branch.alternative, FunctionCall)
    assert_nix_ast_equal(linux_call.name, Identifier(name="stdenv.mkDerivation"))
    linux_args = expect_instance(linux_call.argument, AttributeSet)
    linux_bindings = binding_map(linux_args.values)
    assert set(linux_bindings) >= {
        "pname",
        "version",
        "src",
        "nativeBuildInputs",
        "buildInputs",
        "unpackPhase",
        "installPhase",
        "autoPatchelfIgnoreMissingDeps",
    }
    assert_nix_ast_equal(
        expect_binding(linux_args.values, "version").value,
        Identifier(name="debVersion"),
    )
    ignore_deps = expect_instance(
        expect_binding(linux_args.values, "autoPatchelfIgnoreMissingDeps").value,
        NixList,
    )
    assert [item.value for item in ignore_deps.value] == [
        "libQt5Core.so.5",
        "libQt5Gui.so.5",
        "libQt5Widgets.so.5",
        "libQt6Core.so.6",
        "libQt6Gui.so.6",
        "libQt6Widgets.so.6",
        "liblog.so",
        "libc++_shared.so",
        "libc.musl-x86_64.so.1",
        "libc.musl-aarch64.so.1",
    ]


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
    packages = expect_instance(
        expect_binding(gui_apps.values, "packages").value, FunctionCall
    )
    assert_nix_ast_equal(packages.name, "availablePackages")
    package_terms = _concat_terms(expect_instance(packages.argument, Parenthesis).value)
    darwin_optionals = expect_instance(package_terms[-1], FunctionCall)
    assert_nix_ast_equal(
        darwin_optionals.name,
        "lib.optionals stdenv.hostPlatform.isDarwin",
    )
    darwin_packages = expect_instance(darwin_optionals.argument, NixList)
    packages_by_name = {
        expect_instance(package, Identifier).name: package
        for package in darwin_packages.value
    }

    assert_nix_ast_equal(
        packages_by_name["chatgpt"],
        Identifier(name="chatgpt"),
    )
    assert "codex-desktop" not in packages_by_name


def _rendered_dock_additions(activation: str, *, section: str) -> list[Path]:
    """Read the paths a rendered Dock activation adds to one pinned section.

    Deriving the expectation from the rendered program keeps the module
    declaration as the single authority, so a declaration change moves this list
    with it instead of failing against a hand-copied literal. The declaration
    itself is validated by ``tests/nix/mac-apps/dock-declarations.nix``, which
    asserts the property rather than the contents.
    """
    paths: list[Path] = []
    for text in command_texts(parse_shell(activation), '"$dockutil"'):
        attributes = shlex.split(text)
        if "--add" not in attributes or "--section" not in attributes:
            continue
        if attributes[attributes.index("--section") + 1] != section:
            continue
        paths.append(Path(attributes[attributes.index("--add") + 1]))
    return paths


@pytest.mark.parametrize("fail_clear", [False, True])
def test_town_dock_activation_enforces_declaration(
    tmp_path: Path, fail_clear: bool
) -> None:
    """Run rendered shell: AST checks cannot prove reconciliation, retry, or quoting."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    old_apps = "Calendar\nTwilight\nCapy\nDataGrip\nUser pinned app\n"
    (state / "apps").write_text(old_apps)
    (state / "others").write_text("User pinned folder\n")
    home = tmp_path / "User's home"
    for name in ("Slack.app", "Capy Nightly.app"):
        (home / "Applications" / name).mkdir(parents=True)
    (home / "Downloads").mkdir()
    fake_dockutil = bin_dir / "dockutil"
    fake_dockutil.write_text(
        """#!/bin/sh
set -eu
operation=$1
label=$2
case "$operation" in
  --remove)
    test "$label" = all
    test "$3" = --no-restart
    if [ "$FAIL_CLEAR" = 1 ]; then exit 1; fi
    : > "$DOCK_STATE/apps"
    : > "$DOCK_STATE/others"
    ;;
  --add)
    shift 2
    while [ "$1" != --section ]; do shift; done
    printf '%s\\n' "$label" >> "$DOCK_STATE/$2"
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_dockutil.chmod(0o755)
    with allow_nix_evaluation(
        "Execute the shell generated by the actual Town Dock module"
    ):
        activation = nix_eval_raw(
            FunctionCall(
                name=nix_import(
                    REPO_ROOT / "tests/nix/mac-apps/town-dock-activation.nix"
                ),
                argument=nix_attrset({
                    "dockutil": str(tmp_path),
                    "homeDirectory": str(home),
                }),
            )
        )
    # Stub the absolute restart command as a shell function to leave the live Dock untouched.
    script = "/usr/bin/killall() { :; }\n" + activation
    environment = os.environ | {
        "DOCK_STATE": str(state),
        "FAIL_CLEAR": str(int(fail_clear)),
    }
    bash = shutil.which("bash")
    assert bash is not None
    if fail_clear:
        result = subprocess.run(  # noqa: S603 -- Executes owned activation against a fake Dock.
            [bash, "-c", script],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "failed to clear Dock items" in result.stderr
        assert (state / "apps").read_text() == old_apps
        assert (state / "others").read_text() == "User pinned folder\n"
    environment["FAIL_CLEAR"] = "0"
    expected_apps = [
        path
        for path in _rendered_dock_additions(activation, section="apps")
        if path.exists()
    ]
    expected_others = [
        path
        for path in _rendered_dock_additions(activation, section="others")
        if path.exists()
    ]
    for _ in range(2):
        result = subprocess.run(  # noqa: S603 -- Reconciles the fake Dock, including repeat activation.
            [bash, "-c", script],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        assert (state / "apps").read_text().splitlines() == [
            str(path) for path in expected_apps
        ]
        assert (state / "others").read_text().splitlines() == [
            str(path) for path in expected_others
        ]
        assert "warning: skipping missing Dock app" in result.stderr


def test_mac_apps_helper_stays_runnable_as_a_standalone_store_copy(
    tmp_path: Path,
) -> None:
    """lib/mac-apps.nix ships this file alone under a packaging-only interpreter.

    Importing anything from ``lib`` would still pass the rest of the suite (the
    repository is importable here) and then fail during activation, so the
    import set is pinned and the shipped copy is loaded with ``lib`` blocked.
    """
    source = (REPO_ROOT / "lib/mac_apps_helper.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    assert "lib" not in imported
    assert imported <= {
        "collections",
        "concurrent",
        "json",
        "os",
        "packaging",
        "pathlib",
        "plistlib",
        "shutil",
        "stat",
        "subprocess",
        "sys",
        "tempfile",
        "time",
        "typing",
    }

    shipped = tmp_path / "nixcfg-mac-apps-helper.py"
    shipped.write_text(source, encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import importlib.abc\n"
        "import importlib.util\n"
        "import sys\n"
        "\n"
        "\n"
        "class BlockRepositoryLib(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'lib' or fullname.startswith('lib.'):\n"
        "            raise ModuleNotFoundError(fullname)\n"
        "        return None\n"
        "\n"
        "\n"
        "sys.meta_path.insert(0, BlockRepositoryLib())\n"
        "spec = importlib.util.spec_from_file_location('standalone', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['standalone'] = module\n"
        "spec.loader.exec_module(module)\n"
        "assert 'lib' not in sys.modules\n"
        "assert module._as_object_dict({'a': 1}, context='probe') == {'a': 1}\n"
        "try:\n"
        "    module._as_object_dict({'a': 1, 2: 3}, context='probe')\n"
        "except TypeError as error:\n"
        "    assert 'Expected string key' in str(error)\n"
        "else:\n"
        "    raise AssertionError('non-string keys must be rejected')\n",
        encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items() if key != "PYTHONPATH"
    }
    result = subprocess.run(  # noqa: S603 -- owned probe, fixed interpreter
        [sys.executable, "-I", str(probe), str(shipped)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("writable", [True, False])
def test_copy_preserves_custom_icon_and_prunes_stale_contents(
    tmp_path: Path,
    writable: bool,
) -> None:
    """A bundle replacement must not orphan Finder's custom-icon flag."""
    source = _fake_app_bundle(tmp_path / "source")
    destination = _fake_app_bundle(tmp_path / "Applications")
    (source / "Contents" / "current").write_text("new version")
    (destination / "Contents" / "stale").write_text("old version")
    (destination / "Contents" / "Icon\r").write_bytes(b"stale nested resource")
    custom_icon = destination / "Icon\r"
    custom_icon.write_bytes(b"custom icon resource")

    mac_apps_helper._rsync_copy(
        source, destination, rsync_path=_rsync_path(), writable=writable
    )

    assert custom_icon.read_bytes() == b"custom icon resource"
    assert (destination / "Contents" / "current").read_text() == "new version"
    assert not (destination / "Contents" / "stale").exists()
    assert not (destination / "Contents" / "Icon\r").exists()


@pytest.mark.parametrize("writable", [True, False])
def test_fresh_copy_does_not_create_custom_icon(
    tmp_path: Path,
    writable: bool,
) -> None:
    """Apps without a customization retain their packaged icon resources."""
    source = _fake_app_bundle(tmp_path / "source")
    destination = tmp_path / "Applications" / source.name
    destination.mkdir(parents=True)
    (source / "Contents" / "AppIcon.icns").write_bytes(b"bundled logo")

    mac_apps_helper._rsync_copy(
        source, destination, rsync_path=_rsync_path(), writable=writable
    )

    assert (destination / "Contents" / "AppIcon.icns").read_bytes() == b"bundled logo"
    assert not (destination / "Icon\r").exists()


@pytest.mark.nix_eval(
    reason="The rendered activation shell is the concurrency contract, so parse real generator output."
)
def test_concurrent_applications_script_waits_for_every_scope() -> None:
    """Scopes start together, keep their own identity, and all get awaited."""
    script = _mac_apps_eval(
        FunctionCall(
            name=identifier_attr_path("macApps", "concurrentApplicationsScript"),
            argument=nix_list([
                {
                    "entries": [],
                    "stateDirectory": "/Applications/.nixcfg-mac-apps",
                    "stateName": "darwin-system",
                    "targetDirectory": "/Applications",
                    "writable": False,
                },
                {
                    "entries": [],
                    "stateDirectory": "/Users/george/Applications/.nixcfg-mac-apps",
                    "stateName": "home-manager-user",
                    "targetDirectory": "/Users/george/Applications",
                    "user": {"uid": 501, "name": "george"},
                    "writable": True,
                },
            ]),
        )
    )
    shell = parse_shell(script)

    assignments = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "variable_assignment")
        if node_text(node, shell.sanitized).startswith("nixcfg_app_pid_")
    ]
    assert assignments == ["nixcfg_app_pid_0=$!", "nixcfg_app_pid_1=$!"]
    assert command_texts(shell, "wait") == [
        'wait "$nixcfg_app_pid_0"',
        'wait "$nixcfg_app_pid_1"',
    ]
    # The test stub renders escapeShellArg as JSON quoting, so labels stay quoted.
    assert command_texts(shell, "nixcfg_app_failed") == [
        'nixcfg_app_failed "darwin-system"',
        'nixcfg_app_failed "home-manager-user"',
    ]
    assert command_texts(shell, "exit") == ["exit 1"]

    # The test stub renders escapeShellArg as JSON quoting, so every escaped
    # word (helper call and user name) stays double-quoted here.
    system_commands = command_texts(shell, '"python3"')
    user_commands = command_texts(shell, "launchctl")
    assert len(system_commands) == 1
    assert "system-applications" in system_commands[0]
    assert len(user_commands) == 1
    assert user_commands[0].startswith(
        'launchctl asuser 501 sudo -u "george" --set-home "python3"'
    )
    assert "system-applications" in user_commands[0]


@pytest.mark.nix_eval(
    reason="An empty scope list must render no shell, which only evaluation proves."
)
def test_concurrent_applications_script_renders_nothing_without_scopes() -> None:
    """A host without managed apps must not gain an empty activation body."""
    assert (
        _mac_apps_eval(
            FunctionCall(
                name=identifier_attr_path("macApps", "concurrentApplicationsScript"),
                argument=nix_list([]),
            )
        )
        == ""
    )
