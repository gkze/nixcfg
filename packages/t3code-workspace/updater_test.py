"""Tests for the T3 Code workspace updater."""

from types import ModuleType

import pytest
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEventKind
from lib.update.nix import PreparedProbe, _contextual_overlay_bindings
from lib.update.nix_expr import identifier_attr_path
from lib.update.updaters import VersionInfo
from lib.update.updaters.core import UpdateContext

HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
NEW_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/t3code-workspace/updater.py", "t3code_workspace_updater_test"
    )


def _source_entry(*, drv_hash: str | None = None) -> SourceEntry:
    payload: dict[str, object] = {
        "input": "t3code",
        "version": "main",
        "hashes": [
            {
                "hashType": "nodeModulesHash",
                "hash": HASH,
                "platform": "aarch64-darwin",
            }
        ],
    }
    if drv_hash is not None:
        payload["drvHash"] = drv_hash
    return SourceEntry.model_validate(payload)


def _install_main_version(
    monkeypatch: pytest.MonkeyPatch,
    updater: object,
) -> None:
    async def _fetch_latest(
        _session: object, *, context: UpdateContext | None = None
    ) -> VersionInfo:
        return VersionInfo(version="main")

    monkeypatch.setattr(updater, "fetch_latest", _fetch_latest)


def _expected_workspace_expr() -> LetExpression:
    package_expr = FunctionCall(
        name=FunctionCall(
            name=FunctionCall(
                name=identifier_attr_path("pkgs", "lib", "callPackageWith"),
                argument=Identifier(name="applied"),
            ),
            argument=Parenthesis(
                value=BinaryExpression(
                    operator=Operator(name="+"),
                    left=identifier_attr_path("rootFlake", "outPath"),
                    right=StringPrimitive(
                        value="/packages/t3code-workspace/default.nix"
                    ),
                )
            ),
        ),
        argument=AttributeSet(
            values=[
                Binding(
                    name="inputs",
                    value=identifier_attr_path("rootFlake", "inputs"),
                ),
                Binding(name="outputs", value=Identifier(name="flake")),
            ]
        ),
    )
    return LetExpression(
        local_variables=_contextual_overlay_bindings(
            system="aarch64-darwin",
            repo_root=None,
            source_overrides=None,
        ),
        value=package_expr,
    )


def test_t3code_workspace_updater_tracks_only_aarch64_darwin() -> None:
    """The helper package should only run on its single supported platform."""
    updater_cls = _load_module().T3CodeWorkspaceUpdater

    assert updater_cls.input_name == "t3code"
    assert updater_cls.hash_type == "nodeModulesHash"
    assert updater_cls.platform_specific is True
    assert updater_cls.materialize_when_current is True
    assert updater_cls.native_only is True
    assert updater_cls.supported_platforms == ("aarch64-darwin",)


def _probe_boundaries(monkeypatch: pytest.MonkeyPatch):
    prepared = []
    built = []

    async def prepare(source, expressions, **_kwargs):
        prepared.append((source, expressions))
        assert_nix_ast_equal(expressions["aarch64-darwin"], _expected_workspace_expr())
        return {
            key: PreparedProbe(
                "/nix/store/prepared-workspace.drv", "prepared-fingerprint", expr
            )
            for key, expr in expressions.items()
        }

    async def build(source, probe, **_kwargs):
        built.append((source, probe))
        return NEW_HASH

    monkeypatch.setattr("lib.update.nix.prepare_fixed_output_probes", prepare)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", build)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform", lambda: "aarch64-darwin"
    )
    return prepared, built


def test_workspace_build_and_certificate_use_one_prepared_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not certify a later evaluation after building an earlier derivation."""
    updater = _load_module().T3CodeWorkspaceUpdater()
    _install_main_version(monkeypatch, updater)
    prepared, built = _probe_boundaries(monkeypatch)
    context = UpdateContext(current=None)
    events = _run(
        _collect(
            lambda emit: updater.update_stream(
                _source_entry(drv_hash="previous"), object(), context=context, emit=emit
            )
        )
    )
    assert len(prepared) == len(built) == 1
    assert built[0][0] == "t3code-workspace"
    assert built[0][1].drv_path == "/nix/store/prepared-workspace.drv"
    assert events.result.drv_hash == built[0][1].fingerprint
    assert events.result.hashes.entries[0].hash == NEW_HASH
    assert events.result.platform_drv_hashes == {
        "aarch64-darwin": "prepared-fingerprint"
    }


def test_workspace_reuses_a_certified_current_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A current artifact with an exact per-platform proof requires no build."""
    updater = _load_module().T3CodeWorkspaceUpdater()
    _install_main_version(monkeypatch, updater)
    prepared, built = _probe_boundaries(monkeypatch)
    current = SourceEntry(
        version="main",
        input="t3code",
        hashes=[
            {
                "hashType": "nodeModulesHash",
                "hash": NEW_HASH,
                "platform": "aarch64-darwin",
            }
        ],
        drv_hash="prepared-fingerprint",
        platform_drv_hashes={"aarch64-darwin": "prepared-fingerprint"},
    )
    events = _run(
        _collect(lambda emit: updater.update_stream(current, object(), emit=emit))
    )
    assert events.result is None
    assert len(prepared) == 1
    assert built == []


def test_workspace_cannot_publish_after_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure to hash the prepared dependency output produces no certificate."""
    updater = _load_module().T3CodeWorkspaceUpdater()
    _install_main_version(monkeypatch, updater)
    _probe_boundaries(monkeypatch)
    emitted = []

    async def fail(*_args, **_kwargs):
        raise RuntimeError("dependency build failed")

    async def emit(event):
        emitted.append(event)

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", fail)
    with pytest.raises(RuntimeError, match="dependency build failed"):
        _run(updater.update_stream(_source_entry(), object(), emit=emit))
    assert not any(event.kind is UpdateEventKind.RESULT for event in emitted)


def test_workspace_skips_unsupported_platform_before_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux must not start a Darwin workspace probe."""
    updater = _load_module().T3CodeWorkspaceUpdater()
    prepared, built = _probe_boundaries(monkeypatch)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform", lambda: "x86_64-linux"
    )
    events = _run(
        _collect(lambda emit: updater.update_stream(None, object(), emit=emit))
    )
    assert events.result is None
    assert prepared == built == []
