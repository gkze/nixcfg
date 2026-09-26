"""Resolve the real repair inventory without running Nix or update discovery."""

import pytest
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import parse_nix_expr
from lib.tests._package_registry import registry_override_metadata
from lib.update import derivation_validation as validation
from lib.update.paths import get_repo_root
from lib.update.updaters import ensure_updaters_loaded


@pytest.mark.parametrize("system", ["aarch64-darwin", "aarch64-linux", "x86_64-linux"])
@pytest.mark.parametrize("native_builds_only", [False, True])
def test_full_inventory_respects_package_platforms(
    monkeypatch: pytest.MonkeyPatch, system: str, *, native_builds_only: bool
) -> None:
    """Local retries and CI retain supported gates without inventing Linux apps."""
    registry = ensure_updaters_loaded()
    monkeypatch.setattr(validation, "get_current_nix_platform", lambda: system)
    captured: list[validation.DerivationValidationRequest] = []

    def capture(requests, **_kwargs):
        captured.extend(requests)
        return ()

    monkeypatch.setattr(validation, "validate_derivation_requests", capture)
    assert (
        validation.validate_derivations(
            None,
            updaters=registry,
            all_declared_systems=True,
            native_builds_only=native_builds_only,
        )
        == ()
    )

    portable = {
        "baseten",
        "mdformat",
        "treesitter-textobjects",
        "tsgolint",
        "turso",
        "turso-cli",
    }
    darwin = {
        "antigravity",
        "baseten-switch",
        "bb",
        "buzz",
        "clearly",
        "energy",
        "executor",
        "github-copilot-app",
        "hermes-desktop",
        "hq",
        "mach-studio",
        "openchamber",
        "paseo",
        "reflect-open",
        "rio",
        "unsloth",
        "waku",
        "writer-computer",
        "zen-twilight",
        "zeron",
    }
    builds = [request for request in captured if request.mode == "build"]
    expected = portable | (
        darwin if system == "aarch64-darwin" or not native_builds_only else set()
    )
    assert {request.source for request in builds} == expected
    for request in builds:
        target = request.installable.partition("#")[2].split(".")[1]
        assert target == ("aarch64-darwin" if request.source in darwin else system)

    # Cross-system evaluation is independent of native build sharding.
    assert {
        (request.source, request.installable)
        for request in captured
        if request.mode == "eval"
    } == {
        (name, f".#pkgs.{target}.{name}.drvPath")
        for name in (
            "codex",
            "gitbutler",
            "goose-cli",
            "superset",
            "zed-editor-nightly",
        )
        for target in ("aarch64-darwin", "x86_64-linux")
    }

    # Compare resolved targets with the Nix registry, not updater host eligibility.
    parsed = expect_instance(
        parse_nix_expr((get_repo_root() / "packages/registry.nix").read_text()),
        FunctionDefinition,
    )
    restrictions = registry_override_metadata(
        expect_instance(parsed.output, AttributeSet)
    )
    for request in captured:
        constraint = restrictions.get(request.source, {}).get("constraint")
        target = request.installable.partition("#")[2].split(".")[1]
        if constraint == "darwin":
            assert target.endswith("-darwin"), request
        elif isinstance(constraint, list):
            assert target in constraint, request

    # A valid update host need not be an exported validation target.
    assert "x86_64-darwin" in registry["baseten-switch"].supported_platforms
