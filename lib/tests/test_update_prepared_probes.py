"""Prepared derivations couple hash results to their evaluated certificates."""

import asyncio
import json
from collections.abc import Mapping

import pytest
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.parser import parse

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.update.config import resolve_config
from lib.update.events import CommandResult
from lib.update.nix import (
    PreparedProbe,
    _prepared_probe_expr,
    compute_fixed_output_hash,
    prepare_fixed_output_probes,
)
from lib.update.updaters import UpdateContext, VersionInfo
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

_NATIVE = "aarch64-darwin"
_FOREIGN = "x86_64-linux"
_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
_NEW_HASH = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
_DRV = "/nix/store/" + "0" * 32 + "-probe.drv"
_ORIGINAL = "/nix/store/" + "1" * 32 + "-original.drv"


def test_prepare_matrix_preserves_paths_and_uses_one_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matrix shares a process and retains a distinct build and fingerprint identity."""
    calls = []
    expressions = {_NATIVE: "pkgs.hello", _FOREIGN: "pkgs.git"}

    async def command(args, **_kwargs):
        calls.append(args)
        assert_nix_ast_equal(args[-1], _prepared_probe_expr(expressions))
        return CommandResult(
            args=args,
            returncode=0,
            stdout=json.dumps({
                key: {"drvPath": _DRV, "fingerprintPath": _ORIGINAL}
                for key in expressions
            }),
            stderr="",
        )

    monkeypatch.setattr("lib.update.nix.run_command", command)
    result = asyncio.run(prepare_fixed_output_probes("matrix", expressions))
    assert len(calls) == 1
    assert result == {
        key: PreparedProbe(_DRV, "1" * 32, expr) for key, expr in expressions.items()
    }
    # Parse the generated AST rather than accepting ad hoc interpolation.
    assert parse(_prepared_probe_expr(expressions)).expr is not None


def test_prepared_matrix_shares_the_isolation_scope() -> None:
    """Each target is bound once beneath one shared pinned nixpkgs scope."""
    matrix = expect_instance(
        parse(_prepared_probe_expr({_NATIVE: "pkgs.hello", _FOREIGN: "pkgs.git"})).expr,
        AttributeSet,
    )
    assert [binding.name for binding in matrix.scope] == ["pkgs"]
    for platform, expression in ((_NATIVE, "pkgs.hello"), (_FOREIGN, "pkgs.git")):
        target = expect_instance(
            expect_binding(matrix.values, f'"{platform}"').value, AttributeSet
        )
        assert [binding.name for binding in target.scope] == ["original"]
        assert_nix_ast_equal(target.scope[0].value, expression)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {_NATIVE: None},
        {_NATIVE: {"drvPath": "./bad.drv", "fingerprintPath": _ORIGINAL}},
        {_NATIVE: {"drvPath": None, "fingerprintPath": _ORIGINAL}},
        {_NATIVE: {"drvPath": _DRV, "fingerprintPath": None}},
        {_NATIVE: {"drvPath": _DRV, "fingerprintPath": "./bad.drv"}},
    ],
)
def test_prepare_rejects_invalid_results(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    """Only the exact requested target matrix with absolute derivation paths is trusted."""

    async def command(args, **_kwargs):
        return CommandResult(
            args=args, returncode=0, stdout=json.dumps(payload), stderr=""
        )

    monkeypatch.setattr("lib.update.nix.run_command", command)
    with pytest.raises((RuntimeError, TypeError)):
        asyncio.run(prepare_fixed_output_probes("matrix", {_NATIVE: "pkgs.hello"}))


def test_prepare_empty_and_failed_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty matrix is a no-op; evaluation failure is explicit."""
    calls = []

    async def command(args, **_kwargs):
        calls.append(args)
        return CommandResult(
            args=args, returncode=1, stdout="", stderr="broken dependency"
        )

    monkeypatch.setattr("lib.update.nix.run_command", command)
    assert asyncio.run(prepare_fixed_output_probes("empty", {})) == {}
    assert calls == []
    with pytest.raises(RuntimeError, match="broken dependency"):
        asyncio.run(prepare_fixed_output_probes("matrix", {_NATIVE: "pkgs.hello"}))


def test_retry_builds_the_same_prepared_derivation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retries cannot re-evaluate changed checkout state and certify another build."""
    calls = []

    async def build(_source, expr, *, options, **_kwargs):
        calls.append((expr, options.derivation_path))
        return CommandResult(
            args=["nix", "build"],
            returncode=1,
            stdout="",
            stderr=(
                "HTTP error 502"
                if len(calls) == 1
                else f"error: hash mismatch in fixed-output derivation '{_DRV}':\n specified: {_HASH}\n got: {_NEW_HASH}\n"
            ),
        )

    monkeypatch.setattr("lib.update.nix._run_fixed_output_build", build)
    result = asyncio.run(
        compute_fixed_output_hash(
            "probe",
            PreparedProbe(_DRV, "1" * 32),
            config=resolve_config(retry_backoff=0),
        )
    )
    assert result == _NEW_HASH
    assert calls == [("", _DRV), ("", _DRV)]


def test_prepared_probe_rejects_a_dependency_hash_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nested dependency failure cannot certify the selected fixed-output target."""

    async def build(_source, _expr, **_kwargs):
        return CommandResult(
            args=["nix", "build"],
            returncode=1,
            stdout="",
            stderr=(
                f"error: hash mismatch in fixed-output derivation '{_ORIGINAL}':\n"
                f" specified: {_HASH}\n got: {_NEW_HASH}\n"
            ),
        )

    monkeypatch.setattr("lib.update.nix._run_fixed_output_build", build)
    with pytest.raises(RuntimeError, match="not the prepared probe"):
        asyncio.run(compute_fixed_output_hash("probe", PreparedProbe(_DRV, "cert")))


class _Updater(FlakeInputHashUpdater):
    name = "prepared-test"
    hash_type = "nodeModulesHash"
    platform_specific = True

    def _probe_expressions(self, source: SourceEntry) -> dict[str, str]:
        _ = source
        return {key: key for key in self._platform_targets(_NATIVE)}


def _current(certificates: dict[str, str] | None = None) -> SourceEntry:
    return SourceEntry(
        version="old",
        hashes=HashCollection(
            entries=[
                HashEntry.create("nodeModulesHash", _HASH, platform=key)
                for key in (_NATIVE, _FOREIGN)
            ]
        ),
        drv_hash="legacy",
        platform_drv_hashes=certificates,
    )


def _boundaries(monkeypatch: pytest.MonkeyPatch):
    prepared = []
    built = []

    async def prepare(_source: str, expressions: Mapping[str, str], **_kwargs):
        prepared.append(tuple(expressions))
        return {
            key: PreparedProbe(_DRV, key + "-new", expr)
            for key, expr in expressions.items()
        }

    async def build(_source: str, probe: PreparedProbe, **_kwargs):
        built.append(probe.fingerprint)
        return _NEW_HASH

    monkeypatch.setattr("lib.update.nix.get_current_nix_platform", lambda: _NATIVE)
    monkeypatch.setattr("lib.update.nix.prepare_fixed_output_probes", prepare)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", build)
    return prepared, built


def test_only_changed_platform_is_built_and_finalization_reuses_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform-local change preserves the independently certified foreign output."""
    prepared, built = _boundaries(monkeypatch)
    updater = _Updater(config=resolve_config(hash_build_platforms=(_NATIVE, _FOREIGN)))
    current = _current({_NATIVE: "old", _FOREIGN: _FOREIGN + "-new"})
    context = UpdateContext(current=current)
    info = VersionInfo(version="new")

    async def run():
        hashes = await updater.fetch_hashes(info, object(), context=context)
        return await updater._finalize_result(
            updater.build_result(info, hashes), context=context
        )

    result = asyncio.run(run())
    assert prepared == [(_NATIVE, _FOREIGN)]
    assert built == [_NATIVE + "-new"]
    assert {entry.platform: entry.hash for entry in result.hashes.entries} == {
        _NATIVE: _NEW_HASH,
        _FOREIGN: _HASH,
    }
    assert result.platform_drv_hashes == {
        _NATIVE: _NATIVE + "-new",
        _FOREIGN: _FOREIGN + "-new",
    }


def test_legacy_entries_probe_all_platforms_and_partial_merge_drops_overwritten_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old schema remains readable and supplies no invented per-platform proof."""
    _prepared, built = _boundaries(monkeypatch)
    updater = _Updater(config=resolve_config(hash_build_platforms=(_NATIVE, _FOREIGN)))
    current = _current()
    asyncio.run(
        updater.fetch_hashes(
            VersionInfo(version="new"), object(), context=UpdateContext(current=current)
        )
    )
    assert built == [_NATIVE + "-new", _FOREIGN + "-new"]
    certified = _current({_NATIVE: "native", _FOREIGN: "foreign"})
    incoming = SourceEntry(
        hashes=[HashEntry.create("nodeModulesHash", _NEW_HASH, platform=_NATIVE)]
    )
    merged = certified.merge_native_update(incoming)
    assert merged.platform_drv_hashes == {_FOREIGN: "foreign"}
    assert SourceEntry.model_validate(merged.to_dict()).equivalent_to(merged)


def test_foreign_platform_probes_run_concurrently_after_native_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both foreign builds start together, after preparation and the native check."""
    _boundaries(monkeypatch)
    other = "aarch64-linux"
    updater = _Updater(
        config=resolve_config(hash_build_platforms=(_NATIVE, _FOREIGN, other))
    )
    started: list[str] = []

    async def run():
        both_foreign_started = asyncio.Event()

        async def build(_source, probe, **_kwargs):
            started.append(probe.fingerprint)
            if probe.fingerprint != _NATIVE + "-new":
                if len(started) == len(updater.config.hash_build_platforms):
                    both_foreign_started.set()
                await both_foreign_started.wait()
            return _NEW_HASH

        monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", build)
        return await asyncio.wait_for(
            updater.fetch_hashes(
                VersionInfo(version="new"),
                object(),
                context=UpdateContext(current=None),
            ),
            timeout=1,
        )

    result = asyncio.run(run())
    assert started[0] == _NATIVE + "-new"
    assert {entry.platform for entry in result} == {_NATIVE, _FOREIGN, other}
