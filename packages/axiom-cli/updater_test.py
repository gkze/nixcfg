"""Behavioral tests for the axiom-cli Go vendor-hash updater."""

from types import ModuleType

from lib.tests._updater_helpers import load_repo_module


def _load_module() -> ModuleType:
    return load_repo_module("packages/axiom-cli/updater.py", "axiom_cli_updater_test")


def test_bulk_update_hold_skips_hosted_linux_arm_goproxy_stream_errors() -> None:
    """Linux ARM GOPROXY stream errors stall vendor hashing; do not rematerialize."""
    hold = _load_module().AxiomCliUpdater.bulk_update_hold
    assert hold is not None
    assert "ubuntu-24.04-arm" in hold
    assert "INTERNAL_ERROR" in hold
