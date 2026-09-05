"""Focused tests for the Baseten CLI source updater."""

import asyncio
from types import ModuleType

import pytest

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.system_policy import supported_systems
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import load_repo_module
from lib.update.nix import _build_fetch_from_github_call
from lib.update.updaters import VersionInfo

COMMIT = "b" * 40


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/baseten/updater.py",
        "baseten_updater_dedicated_test",
    )


def test_source_expression_tracks_the_immutable_upstream_commit() -> None:
    """Hash the resolved source commit rather than a mutable release tag."""
    module = _load_module()

    assert_nix_ast_equal(
        module.BasetenUpdater._src_expr(COMMIT),
        _build_fetch_from_github_call(
            "basetenlabs",
            "baseten-cli",
            rev=COMMIT,
            fetch_submodules=False,
        ),
    )


def test_same_version_moved_tag_is_stale_and_persists_new_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recreated release tag must refresh and pin its new immutable target."""
    module = _load_module()
    updater = module.BasetenUpdater()
    calls: list[str] = []

    async def _fetch(_session: object, path: str, **_kwargs: object) -> object:
        calls.append(path)
        if path.endswith("/releases/latest"):
            return {"tag_name": "v0.4.0"}
        return {"sha": COMMIT}

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _fetch,
    )

    info = asyncio.run(updater.fetch_latest(object()))
    moved = SourceEntry(version=info.version, commit="a" * 40, hashes=[])
    unchanged = SourceEntry(version=info.version, commit=COMMIT, hashes=[])

    assert asyncio.run(updater._is_latest(moved, info)) is False
    assert asyncio.run(updater._is_latest(unchanged, info)) is True
    assert updater.build_result(
        info, [HashEntry.create("srcHash", "sha256-source")]
    ) == (
        SourceEntry(
            version="0.4.0",
            commit=COMMIT,
            hashes=[HashEntry.create("srcHash", "sha256-source")],
        )
    )
    assert calls == [
        "repos/basetenlabs/baseten-cli/releases/latest",
        "repos/basetenlabs/baseten-cli/commits/v0.4.0",
    ]


def test_source_hashing_requires_resolved_commit_metadata() -> None:
    """Do not hash or persist a mutable tag when commit resolution is absent."""
    updater = _load_module().BasetenUpdater()

    with pytest.raises(RuntimeError, match="missing an immutable source commit"):
        updater.build_result(VersionInfo("0.4.0"), [])


def test_updater_uses_go_vendor_hash_on_exported_systems() -> None:
    """Keep dependency hashing aligned with the package and flake systems."""
    updater = _load_module().BasetenUpdater

    assert updater.dependency_hash_type == "vendorHash"
    assert updater.supported_platforms == supported_systems()
