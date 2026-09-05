"""Focused contracts for the flat mdformat source and updater."""

# ruff: noqa: N999, S101 -- flat sidecar name and pytest assertions are intentional.

from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.tests._source_metadata import (
    assert_release_version,
    assert_structured_source_hashes,
)
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
COMMIT = "d" * 40


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
    """Keep one versioned source closure in the flat metadata sidecar."""
    source = SourceEntry.model_validate_json(_SOURCE_PATH.read_text(encoding="utf-8"))
    assert_release_version(source.version)
    assert_structured_source_hashes(
        source,
        hash_types={"srcHash"},
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
    commit_calls: list[tuple[object, str]] = []

    async def _fetch_json(
        session: object,
        url: str,
        **kwargs: object,
    ) -> object:
        fetch_calls.append((session, url, kwargs))
        return {"info": {"version": "1.1.0"}}

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    async def _resolve_commit(session: object, tag: str) -> str:
        commit_calls.append((session, tag))
        return COMMIT

    monkeypatch.setattr(updater, "_resolve_release_tag_commit", _resolve_commit)
    hash_calls = _install_source_hash(monkeypatch)
    session = object()
    current = SourceEntry(
        version="1.1.0",
        commit="c" * 40,
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
    assert commit_calls == [(session, "1.1.0")]
    assert len(hash_calls) == 1
    assert_nix_ast_equal(
        str(hash_calls[0]["expr"]),
        _build_fetch_from_github_call(
            "hukkin",
            "mdformat",
            rev=COMMIT,
            fetch_submodules=False,
        ),
    )
    result_events = [event for event in events if event.kind is UpdateEventKind.RESULT]
    assert result_events == [
        UpdateEvent.result(
            "mdformat",
            SourceEntry(
                version="1.1.0",
                commit=COMMIT,
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
