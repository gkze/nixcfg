"""Tests for the oxlint-tsgolint updater."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import install_fixed_hash_stream, load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.nix import _build_fetchgit_call
from lib.update.updaters.base import VersionInfo, source_override_env

if TYPE_CHECKING:
    import pytest


def _load_module(module_name: str):
    return load_repo_module("overlays/oxlint-tsgolint/updater.py", module_name)


def test_oxlint_tsgolint_is_latest_rejects_fake_and_empty_hash_mappings() -> None:
    """Placeholder and empty mapping hashes should force a refresh."""
    module = _load_module("oxlint_tsgolint_latest_test")
    updater = module.OxlintTsgolintUpdater()
    latest = VersionInfo("0.21.0")

    empty = SourceEntry(version="0.21.0", hashes={})
    fake = SourceEntry(
        version="0.21.0",
        hashes={"x86_64-linux": HashCollection.FAKE_HASH_PREFIX},
    )
    real = SourceEntry(
        version="0.21.0",
        hashes={"x86_64-linux": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="},
    )

    assert _run(updater._is_latest(empty, latest)) is False
    assert _run(updater._is_latest(fake, latest)) is False
    assert _run(updater._is_latest(real, latest)) is True


def test_oxlint_tsgolint_is_latest_rejects_mismatched_empty_and_missing_entries() -> (
    None
):
    """Version mismatches and missing structured hashes should not be treated as current."""
    module = _load_module("oxlint_tsgolint_latest_entries_test")
    updater = module.OxlintTsgolintUpdater()
    latest = VersionInfo("0.21.0")

    assert (
        _run(
            updater._is_latest(
                module.UpdateContext(
                    current=SourceEntry(
                        version="0.20.0",
                        hashes={
                            "x86_64-linux": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
                        },
                    )
                ),
                latest,
            )
        )
        is False
    )
    assert (
        _run(
            updater._is_latest(
                SimpleNamespace(
                    version="0.21.0",
                    hashes=SimpleNamespace(entries=[]),
                ),
                latest,
            )
        )
        is False
    )


def test_oxlint_tsgolint_is_latest_accepts_real_structured_entries() -> None:
    """A non-placeholder structured hash entry list should count as current."""
    module = _load_module("oxlint_tsgolint_latest_real_entries_test")
    updater = module.OxlintTsgolintUpdater()
    latest = VersionInfo("0.21.0")

    assert (
        _run(
            updater._is_latest(
                SimpleNamespace(
                    version="0.21.0",
                    hashes=SimpleNamespace(
                        entries=[
                            HashEntry.create(
                                "srcHash",
                                "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                            )
                        ]
                    ),
                ),
                latest,
            )
        )
        is True
    )
    assert (
        _run(
            updater._is_latest(
                SimpleNamespace(
                    version="0.21.0",
                    hashes=SimpleNamespace(entries=None, mapping=None),
                ),
                latest,
            )
        )
        is False
    )


def test_oxlint_tsgolint_fetch_hashes_computes_src_and_vendor_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater should compute srcHash first, then vendorHash with override env."""
    module = _load_module("oxlint_tsgolint_hash_test")
    updater = module.OxlintTsgolintUpdater()
    calls = install_fixed_hash_stream(
        monkeypatch,
        (
            (None, "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
            (None, "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="),
        ),
    )

    events = _run(_collect(updater.fetch_hashes(VersionInfo("0.21.0"), object())))

    assert len(calls) == 2
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetchgit_call(
            "https://github.com/oxc-project/tsgolint.git",
            "v0.21.0",
            fetch_submodules=True,
        ),
    )
    assert_nix_ast_equal(
        str(calls[1]["expr"]),
        module._build_overlay_expr("oxlint-tsgolint"),
    )
    assert calls[1]["env"] == source_override_env(
        "oxlint-tsgolint",
        version="0.21.0",
        src_hash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        dependency_hash_type="vendorHash",
        dependency_hash=updater.config.fake_hash,
    )
    assert events[-1].payload == [
        HashEntry.create(
            "srcHash",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        ),
        HashEntry.create(
            "vendorHash",
            "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
        ),
    ]
