"""Focused tests for the turso-cli updater."""

from types import ModuleType
from typing import TYPE_CHECKING

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import (
    collect_events,
    install_fixed_hash_stream,
    load_repo_module,
    run_async,
)
from lib.update.derivation_validation import DerivationValidation
from lib.update.nix import _build_fetch_from_github_call, _build_overlay_expr
from lib.update.updaters import VersionInfo

if TYPE_CHECKING:
    import pytest

SRC_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
VENDOR_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
COMMIT = "e" * 40


def _load_module() -> ModuleType:
    return load_repo_module(
        "overlays/turso-cli/updater.py",
        "turso_cli_updater_dedicated_test",
    )


def test_turso_cli_validates_the_updated_package_build() -> None:
    """The updater should realize turso-cli after refreshing its hashes."""
    module = _load_module()

    assert module.TursoCliUpdater.get_derivation_validations() == (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
        ),
    )


def test_turso_cli_computes_source_then_vendor_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash the release source before evaluating the overlaid Go package."""
    module = _load_module()
    updater = module.TursoCliUpdater()
    info = VersionInfo(version="9.9.9", metadata={"commit": COMMIT})
    calls = install_fixed_hash_stream(
        monkeypatch,
        (
            (None, SRC_HASH),
            (None, VENDOR_HASH),
        ),
    )

    events = run_async(collect_events(updater.fetch_hashes(info, object())))

    assert len(calls) == 2
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "tursodatabase",
            "turso-cli",
            rev=COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        str(calls[1]["expr"]),
        _build_overlay_expr(
            "turso-cli",
            source_overrides={
                "turso-cli": SourceEntry(
                    version=info.version,
                    commit=COMMIT,
                    hashes=[
                        HashEntry.create("srcHash", SRC_HASH),
                        HashEntry.create("vendorHash", updater.config.fake_hash),
                    ],
                )
            },
        ),
    )
    assert calls[0]["env"] is None
    assert calls[1]["env"] is None
    assert events[-1].payload == [
        HashEntry.create("srcHash", SRC_HASH),
        HashEntry.create("vendorHash", VENDOR_HASH),
    ]
