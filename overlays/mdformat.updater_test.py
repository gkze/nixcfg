"""Focused contracts for the flat mdformat source and updater."""

# ruff: noqa: N999, S101 -- flat sidecar name and pytest assertions are intentional.

import json
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.tests._updater_helpers import (
    collect_events,
    load_repo_module,
    run_async,
)
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.nix import _build_fetch_from_github_call
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import ModuleType

_SOURCE_PATH = REPO_ROOT / "overlays" / "mdformat.sources.json"
_UPDATED_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _load_module() -> ModuleType:
    return load_repo_module(
        "overlays/mdformat.updater.py",
        "mdformat_flat_updater_test",
    )


def _install_source_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    async def _fixed_hash(
        name: str,
        expr: str,
        *,
        isolate_by_drv_hash: bool = False,
        env: object = None,
        config: object = None,
    ) -> AsyncIterator[UpdateEvent]:
        calls.append({
            "name": name,
            "expr": expr,
            "isolate_by_drv_hash": isolate_by_drv_hash,
            "env": env,
            "config": config,
        })
        yield UpdateEvent.value(name, _UPDATED_HASH)

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)
    return calls


def test_mdformat_overlay_reads_its_updater_owned_source() -> None:
    """Keep the exact tag and source hash in the flat metadata sidecar."""
    source = SourceEntry.model_validate(
        json.loads(_SOURCE_PATH.read_text(encoding="utf-8"))
    )
    assert source == SourceEntry(
        version="1.0.0",
        hashes=HashCollection.from_value([
            HashEntry.create(
                "srcHash",
                "sha256-fo4xO4Y89qPAggEjwuf6dnTyu1JzhZVdJyUqGNpti7g=",
            )
        ]),
    )

    overlay = expect_instance(
        nix_file_expr("overlays/mdformat.nix"), FunctionDefinition
    )
    output = expect_instance(overlay.output, AttributeSet)
    assert_nix_ast_equal(
        expect_instance(expect_binding(output.scope, "info").value, NixExpression),
        "selfSource",
    )


def test_mdformat_update_resolves_pypi_version_and_hashes_matching_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh the GitHub source from the authoritative PyPI version."""
    module = _load_module()
    updater = module.MdformatUpdater()
    fetch_calls: list[tuple[object, str, dict[str, object]]] = []

    async def _fetch_json(
        session: object,
        url: str,
        **kwargs: object,
    ) -> object:
        fetch_calls.append((session, url, kwargs))
        return {"info": {"version": "1.1.0"}}

    monkeypatch.setattr(module, "fetch_json", _fetch_json)
    hash_calls = _install_source_hash(monkeypatch)
    session = object()
    current = SourceEntry(
        version="1.0.0",
        hashes=HashCollection.from_value([
            HashEntry.create(
                "srcHash",
                "sha256-fo4xO4Y89qPAggEjwuf6dnTyu1JzhZVdJyUqGNpti7g=",
            )
        ]),
    )

    events = run_async(collect_events(updater.update_stream(current, session)))

    assert updater.get_derivation_validations() == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.mdformat",
            mode="build",
        ),
    )
    assert fetch_calls == [
        (
            session,
            "https://pypi.org/pypi/mdformat/json",
            {
                "request_timeout": updater.config.default_timeout,
                "config": updater.config,
            },
        )
    ]
    assert len(hash_calls) == 1
    assert_nix_ast_equal(
        str(hash_calls[0]["expr"]),
        _build_fetch_from_github_call(
            "hukkin",
            "mdformat",
            tag="1.1.0",
            fetch_submodules=False,
        ),
    )
    result_events = [event for event in events if event.kind is UpdateEventKind.RESULT]
    assert result_events == [
        UpdateEvent.result(
            "mdformat",
            SourceEntry(
                version="1.1.0",
                hashes=HashCollection.from_value([
                    HashEntry.create("srcHash", _UPDATED_HASH)
                ]),
            ),
        )
    ]


@pytest.mark.parametrize(
    ("payload", "error_type", "match"),
    [
        (None, TypeError, "Expected JSON object"),
        ({}, TypeError, "Expected JSON object"),
        ({"info": {"version": ""}}, RuntimeError, "Empty PyPI version"),
    ],
)
def test_mdformat_update_rejects_malformed_pypi_metadata(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    error_type: type[Exception],
    match: str,
) -> None:
    """Fail closed when PyPI does not provide a usable release version."""
    module = _load_module()

    async def _fetch_json(*_args: object, **_kwargs: object) -> object:
        return payload

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    with pytest.raises(error_type, match=match):
        run_async(module.MdformatUpdater().fetch_latest(object()))
