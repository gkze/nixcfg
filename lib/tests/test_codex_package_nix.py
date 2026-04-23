"""AST-level guardrails for Codex packaging surface and V8 wiring."""

from __future__ import annotations

from functools import cache

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.update.paths import REPO_ROOT


@cache
def _codex_platform_switch() -> IfExpression:
    """Return the top-level platform switch from the Codex package."""
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/codex/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, IfExpression)


@cache
def _registry_output() -> AttributeSet:
    """Return the package registry export attrset."""
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/registry.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, AttributeSet)


def test_codex_consumes_the_shared_codex_v8_overlay_source() -> None:
    """Keep the pinned codex-v8 fetch owned by the shared overlay."""
    assert_nix_ast_equal(
        expect_scope_binding(_codex_platform_switch(), "rustyV8Src").value,
        "pkgs.codex-v8",
    )


def test_codex_checks_v8_cargo_nix_version_against_the_pinned_source() -> None:
    """Catch Cargo.nix/V8 source drift during evaluation instead of at build time."""
    assert_nix_ast_equal(
        expect_scope_binding(_codex_platform_switch(), "cargoNixV8Version").value,
        "cargoNix.internal.crates.v8.version",
    )
    check_expr = expect_scope_binding(
        _codex_platform_switch(), "cargoNixV8VersionCheck"
    ).value.rebuild()

    assert "cargoNixV8Version == v8ManifestVersion" in check_expr
    assert "packages/codex/Cargo.nix has v8 version ${cargoNixV8Version}," in check_expr
    assert "expected ${v8ManifestVersion}; regenerate Cargo.nix" in check_expr
    assert_nix_ast_equal(
        expect_scope_binding(_codex_platform_switch(), "guardedCodexDrv").value,
        """
        assert cargoNixVersionCheck;
        assert cargoNixV8VersionCheck;
        codexDrvChecked
        """,
    )


def test_codex_meta_platforms_match_the_validated_surface() -> None:
    """Codex should only advertise the package surfaces wired in this repo."""
    alternative = expect_instance(_codex_platform_switch().alternative, FunctionCall)
    alternative_args = expect_instance(alternative.argument, AttributeSet)
    meta = expect_instance(
        expect_binding(alternative_args.values, "meta").value, AttributeSet
    )

    assert_nix_ast_equal(
        expect_binding(meta.values, "platforms").value,
        '[ "aarch64-darwin" "x86_64-linux" ]',
    )


def test_codex_webrtc_prebuilt_bundle_matches_the_host_platform() -> None:
    """Keep Linux builds from accidentally pinning the macOS-only WebRTC bundle."""
    webrtc_prebuilt = expect_scope_binding(_codex_platform_switch(), "webrtcPrebuilt")

    rebuilt = webrtc_prebuilt.value.rebuild()
    assert "if pkgs.stdenv.hostPlatform.isLinux then" in rebuilt
    assert "webrtc-linux-x64-release.zip" in rebuilt
    assert "webrtc-mac-arm64-release.zip" in rebuilt


def test_registry_limits_codex_to_its_validated_primary_surface() -> None:
    """Keep Codex out of unsupported flake package sets on Linux and Intel macOS."""
    overrides = expect_instance(
        expect_scope_binding(_registry_output(), "packageMetadataOverrides").value,
        AttributeSet,
    )
    entry = expect_instance(
        expect_binding(overrides.values, "codex").value, AttributeSet
    )

    assert_nix_ast_equal(
        expect_binding(entry.values, "constraint").value,
        '[ "aarch64-darwin" "x86_64-linux" ]',
    )
