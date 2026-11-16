"""Behavioral tests for the axiom-cli Go vendor-hash updater."""

from types import ModuleType

from lib.tests._updater_helpers import load_repo_module


def _load_module() -> ModuleType:
    return load_repo_module("packages/axiom-cli/updater.py", "axiom_cli_updater_test")


def test_bulk_update_hold_is_clear_once_goproxy_can_vendor_axiom_go() -> None:
    """The v0.20.0 pin stays on the bulk path.

    Update 36577228134 failed Linux ARM's prefetch of axiom-go v0.37.0 with a
    GOPROXY stream INTERNAL_ERROR. That fault is a retryable network failure,
    and the module (including v0.38.0, which CLI v0.22.0 requires) is published.
    Nixpkgs go_1_27 at this pin is 1.27.3, the same floor as that CLI.
    """
    assert _load_module().AxiomCliUpdater.bulk_update_hold is None
