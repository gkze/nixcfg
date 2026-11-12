"""Focused tests for the Baseten Switch source updater."""

import asyncio
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.tests._nix_source import nix_file_expr
from lib.tests._package_registry import is_darwin_overlay_export
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import load_repo_module
from lib.update.nix import _build_fetch_from_github_call
from lib.update.paths import REPO_ROOT
from lib.update.updaters import UpdateContext

_PACKAGE_DIR = REPO_ROOT / "packages/baseten-switch"

COMMIT = "b" * 40


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/baseten-switch/updater.py",
        "baseten_switch_updater_dedicated_test",
    )


def _run(coro):
    return asyncio.run(coro)


def test_bulk_updates_resume_after_current_darwin_suite_passes() -> None:
    """The 0.7.2 native release passed the full headed-Darwin Go and Swift suites."""
    assert _load_module().BasetenSwitchUpdater.bulk_update_hold is None


def test_source_expression_tracks_the_immutable_upstream_commit() -> None:
    """Hash the resolved source commit rather than a mutable release tag."""
    module = _load_module()

    assert_nix_ast_equal(
        module.BasetenSwitchUpdater._src_expr(COMMIT),
        _build_fetch_from_github_call(
            "basetenlabs",
            "baseten-switch",
            rev=COMMIT,
            fetch_submodules=False,
        ),
    )


def test_package_runs_checkphase_for_every_version() -> None:
    """The 0.5.1 SIGTRAP skip expired; no version-pinned check skip remains."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/baseten-switch/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_scope_binding(package.output, "package").value,
        FunctionCall,
    )
    assert "doCheck" not in binding_map(
        expect_instance(derivation.argument, AttributeSet).values
    )


def test_overlay_does_not_restate_the_hosted_checkphase_skip() -> None:
    """The Darwin overlay exposes the package itself, with no hand-written skip."""
    registry = expect_instance(
        nix_file_expr("packages/registry.nix"), FunctionDefinition
    )
    assert is_darwin_overlay_export(
        expect_instance(registry.output, AttributeSet), "baseten-switch"
    )


def test_package_serializes_go_packages_with_timing_sensitive_probe_tests() -> None:
    """The 20 ms health-probe fixture must not compete with parallel packages."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/baseten-switch/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_scope_binding(package.output, "package").value,
        FunctionCall,
    )
    check_phase = expect_instance(
        expect_binding(
            expect_instance(derivation.argument, AttributeSet).values,
            "checkPhase",
        ).value,
        IndentedString,
    )

    shell = parse_shell(indented_string_body(check_phase.rebuild()))
    assert command_texts(shell, "go") == ["go test -p 1 ./..."]


_WINDOW_SERVER_TESTS = (
    "testRouterWindowContentUsesNonObscuredLayoutGuide",
    "testTrackedStatusMenuRefreshProjectsAndRebuildsAuthAttention",
    "testUpdateDetailsKeepUsableContentHeightWhenHostedWithoutShowing",
    "testUpdateWindowCentersUsingItsFinalContentSizeWithoutShowing",
    "testAvailableMenuItemDispatchesNativeActionToUpdateWindowCallback",
    "testUnconfiguredDesktopIsDiscoverableAndOpensItsConfiguration",
    "testCurrentMenuKeepsManualCheckBeforeOpenAppAndNoReleaseMarker",
    "testReleaseChangesDoNotRebuildTrackedMenuOrChangeRoutingHealth",
)


def test_package_skips_xctests_that_need_a_window_server() -> None:
    """AppKit SIGTRAPs creating windows or status items with no window server."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/baseten-switch/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_scope_binding(package.output, "package").value,
        FunctionCall,
    )
    check_phase = expect_instance(
        expect_binding(
            expect_instance(derivation.argument, AttributeSet).values,
            "checkPhase",
        ).value,
        IndentedString,
    )

    shell = parse_shell(indented_string_body(check_phase.rebuild()))
    swift_tests = {
        " ".join(command.replace("\\\n", " ").split())
        for command in command_texts(shell, "env")
        if "/usr/bin/swift test" in command
    }
    assert len(swift_tests) == 1
    (command,) = swift_tests
    assert "--skip testUnusableSecondaryReceiptRetainsRecoveryGate" in command
    assert command.endswith(" ".join(f"--skip {name}" for name in _WINDOW_SERVER_TESTS))


def test_package_does_not_launch_the_swift_testing_helper() -> None:
    """The empty Swift Testing helper SIGTRAPs and fails an otherwise live XCTest run."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/baseten-switch/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_scope_binding(package.output, "package").value,
        FunctionCall,
    )
    check_phase = expect_instance(
        expect_binding(
            expect_instance(derivation.argument, AttributeSet).values,
            "checkPhase",
        ).value,
        IndentedString,
    )

    shell = parse_shell(indented_string_body(check_phase.rebuild()))
    swift_tests = [
        " ".join(command.replace("\\\n", " ").split())
        for command in command_texts(shell, "env")
        if "/usr/bin/swift test" in command
    ]
    assert len(swift_tests) == 1
    assert "--disable-swift-testing" in swift_tests[0]
    assert "--enable-swift-testing" not in swift_tests[0]


def test_package_prints_a_compact_backtrace_when_swift_tests_trap() -> None:
    """A hosted XCTest SIGTRAP must name its frame inside Nix's 25-line tail."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/baseten-switch/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_scope_binding(package.output, "package").value,
        FunctionCall,
    )
    check_phase = expect_instance(
        expect_binding(
            expect_instance(derivation.argument, AttributeSet).values,
            "checkPhase",
        ).value,
        IndentedString,
    )

    shell = parse_shell(indented_string_body(check_phase.rebuild()))
    swift_tests = [
        " ".join(command.replace("\\\n", " ").split())
        for command in command_texts(shell, "env")
        if "/usr/bin/swift test" in command
    ]
    assert len(swift_tests) == 1
    assert (
        'SWIFT_BACKTRACE="enable=yes,interactive=no,color=no,preset=full,'
        'threads=crashed,registers=none,images=mentioned,limit=40,top=0"'
        " /usr/bin/swift test"
    ) in swift_tests[0]


def test_package_applies_the_reviewed_nix_integration_patch() -> None:
    """Retain Nix integration without patches for fixes already shipped upstream."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/baseten-switch/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_scope_binding(package.output, "package").value,
        FunctionCall,
    )
    arguments = expect_instance(derivation.argument, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(arguments.values, "patches").value,
        "[ ./nix-managed.patch ./raw-telemetry-test-clock.patch ]",
    )


def test_package_embeds_the_exact_cli_used_by_the_copied_app() -> None:
    """The GUI and exposed CLI must resolve to one signed package artifact."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/baseten-switch/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_scope_binding(package.output, "package").value,
        FunctionCall,
    )
    install_phase = expect_instance(
        expect_binding(
            expect_instance(derivation.argument, AttributeSet).values,
            "installPhase",
        ).value,
        IndentedString,
    )

    shell = parse_shell(indented_string_body(install_phase.rebuild()))
    assert (
        'install -m0755 "$TMPDIR/baseten-switch-cli" "$bundled_cli"'
        in command_texts(shell, "install")
    )
    assert command_texts(shell, "ln") == [
        'ln -s "$bundled_cli" "$out/bin/baseten-switch"'
    ]
    assert command_texts(shell, "/usr/bin/lipo") == [
        '/usr/bin/lipo "$bundled_cli" -verify_arch arm64 x86_64'
    ]
    codesign_commands = {
        " ".join(command.replace("\\\n", " ").split())
        for command in command_texts(shell, "/usr/bin/codesign")
    }
    assert (
        "/usr/bin/codesign --force --deep --sign - "
        "--preserve-metadata=identifier,entitlements,flags,runtime "
        '"$app_bundle"'
    ) in codesign_commands


def test_fetch_latest_skips_drafts_and_accepts_beta_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Follow Baseten's published prereleases while the product remains beta."""
    module = _load_module()
    updater = module.BasetenSwitchUpdater()
    calls: list[tuple[str, int]] = []

    async def _fetch(_session, endpoint: str, *, config, per_page: int):
        assert config is updater.config
        calls.append((endpoint, per_page))
        return [
            {"tag_name": "v0.4.0", "draft": True, "prerelease": True},
            {"tag_name": "v0.3.0", "draft": False, "prerelease": True},
        ]

    monkeypatch.setattr(module, "fetch_github_api_paginated", _fetch)
    monkeypatch.setattr(
        updater,
        "_resolve_release_tag_commit",
        lambda _session, tag: asyncio.sleep(0, result=COMMIT),
    )

    result = _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))

    assert result.version == "0.3.0"
    assert result.metadata == {"commit": COMMIT, "tag": "v0.3.0"}
    assert calls == [("repos/basetenlabs/baseten-switch/releases", 100)]


def test_fetch_latest_rejects_non_mapping_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed release-list entries before selecting a version."""
    module = _load_module()
    updater = module.BasetenSwitchUpdater()
    monkeypatch.setattr(
        module,
        "fetch_github_api_paginated",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=["invalid"]),
    )

    with pytest.raises(TypeError, match="Unexpected release payload type: str"):
        _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))


def test_fetch_latest_requires_release_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject published release entries without a usable tag."""
    module = _load_module()
    updater = module.BasetenSwitchUpdater()
    monkeypatch.setattr(
        module,
        "fetch_github_api_paginated",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=[{"tag_name": "", "draft": False, "prerelease": True}],
        ),
    )

    with pytest.raises(RuntimeError, match="Missing tag_name in release payload"):
        _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))


def test_fetch_latest_requires_published_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report a clear failure when the API exposes drafts only."""
    module = _load_module()
    updater = module.BasetenSwitchUpdater()
    monkeypatch.setattr(
        module,
        "fetch_github_api_paginated",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=[{"tag_name": "v0.4.0", "draft": True}],
        ),
    )

    with pytest.raises(RuntimeError, match="No published releases found"):
        _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))


_ANALYTICS_TEST_HELPER = """\
func analyticsGateway(t *testing.T, now time.Time) (*Gateway, string) {
\tt.Helper()
}

func appendAnalyticsEvent(t *testing.T, dir string, event telemetry.EventV1) {
\tt.Helper()
\twriter, err := telemetry.NewWriter(telemetry.WriterOptions{Dir: dir})
\tif err != nil {
\t\tt.Fatal(err)
\t}
}
"""


def test_raw_telemetry_test_clock_patch_pins_writes_to_event_time(
    tmp_path: Path,
) -> None:
    """The July 2026 fixture must not expire under 90-day wall-clock retention."""
    git = shutil.which("git")
    assert git is not None
    helper = tmp_path / "gateway/cmd/gateway/admin_analytics_test.go"
    helper.parent.mkdir(parents=True)
    helper.write_text(_ANALYTICS_TEST_HELPER, encoding="utf-8")

    subprocess.run(  # noqa: S603 -- fixed repository-owned patch
        [git, "apply", str(_PACKAGE_DIR / "raw-telemetry-test-clock.patch")],
        cwd=tmp_path,
        check=True,
    )

    assert helper.read_text(encoding="utf-8") == _ANALYTICS_TEST_HELPER.replace(
        "telemetry.WriterOptions{Dir: dir}",
        "telemetry.WriterOptions{Dir: dir, Now: func() time.Time { "
        "return event.CompletedAt }}",
    )
