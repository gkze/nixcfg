"""Focused contracts for the flat treesitter-textobjects source and updater."""

# ruff: noqa: N999, S101 -- flat sidecar name and pytest assertions are intentional.

from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, nix_apply
from lib.tests._nix_source import nix_file_expr
from lib.tests._source_metadata import (
    assert_immutable_commit,
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
from lib.update.nix_expr import select_attrs
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import ModuleType

_COMMIT = "52bda74e087034408e2d563cb4499c1601038f9d"
_SOURCE_HASH = "sha256-9qpTwJqfkpF/M7MVE2VgEU9ptIYUcNvWrWM0nQxXo7M="
_SOURCE_PATH = REPO_ROOT / "overlays" / "treesitter-textobjects.sources.json"
_UPDATED_COMMIT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_UPDATED_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _load_module() -> ModuleType:
    return load_repo_module(
        "overlays/treesitter-textobjects.updater.py",
        "treesitter_textobjects_flat_updater_test",
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


def _treesitter_source_expression() -> FunctionCall:
    overlay = expect_instance(
        nix_file_expr("overlays/vim-plugin-overrides.nix"),
        FunctionDefinition,
    )
    output = expect_instance(overlay.output, AttributeSet)
    extension = expect_instance(
        expect_binding(output.values, "vimPlugins").value,
        FunctionCall,
    )
    first_lambda = expect_instance(
        expect_instance(extension.argument, Parenthesis).value,
        FunctionDefinition,
    )
    second_lambda = expect_instance(first_lambda.output, FunctionDefinition)
    overrides = expect_instance(second_lambda.output, AttributeSet)
    plugin = expect_instance(
        expect_binding(overrides.values, "nvim-treesitter-textobjects").value,
        FunctionCall,
    )
    plugin_attrs = expect_instance(plugin.argument, AttributeSet)
    return expect_instance(
        expect_binding(plugin_attrs.values, "src").value,
        FunctionCall,
    )


def test_treesitter_textobjects_overlay_reads_updater_owned_commit() -> None:
    """Keep the moving branch behind one immutable source commit and closure."""
    source = SourceEntry.model_validate_json(_SOURCE_PATH.read_text(encoding="utf-8"))
    assert source.version == "main"
    assert_immutable_commit(source.commit)
    assert_structured_source_hashes(
        source,
        hash_types={"srcHash"},
    )

    assert_nix_ast_equal(
        _treesitter_source_expression(),
        FunctionCall(
            name=select_attrs(Identifier(name="prev"), "fetchFromGitHub"),
            argument=AttributeSet(
                values=[
                    Binding(name="owner", value=StringPrimitive(value="gkze")),
                    Binding(
                        name="repo",
                        value=StringPrimitive(value="nvim-treesitter-textobjects"),
                    ),
                    Binding(
                        name="rev",
                        value=select_attrs(
                            Identifier(name="treesitterTextobjectsSource"),
                            "commit",
                        ),
                    ),
                    Binding(
                        name="hash",
                        value=nix_apply(
                            select_attrs(Identifier(name="slib"), "sourceHash"),
                            StringPrimitive(value="treesitter-textobjects"),
                            StringPrimitive(value="srcHash"),
                        ),
                    ),
                ]
            ),
        ),
    )


@pytest.mark.parametrize("current_commit", [None, _COMMIT])
def test_treesitter_textobjects_update_hashes_the_main_branch_head(
    monkeypatch: pytest.MonkeyPatch,
    current_commit: str | None,
) -> None:
    """Resolve main to a new immutable commit before hashing its source tree."""
    module = _load_module()
    updater = module.TreesitterTextobjectsUpdater()
    api_calls: list[tuple[object, str, object]] = []

    async def _fetch_github_api(
        session: object,
        path: str,
        *,
        config: object = None,
    ) -> object:
        api_calls.append((session, path, config))
        return {"sha": _UPDATED_COMMIT}

    monkeypatch.setattr(module, "fetch_github_api", _fetch_github_api)
    hash_calls = _install_source_hash(monkeypatch)
    session = object()
    current = SourceEntry(
        version="main",
        commit=current_commit,
        hashes=HashCollection.from_value([HashEntry.create("srcHash", _SOURCE_HASH)]),
    )

    events = run_async(collect_events(updater.update_stream(current, session)))

    assert updater.get_derivation_validations() == (
        DerivationValidation(
            installable=("path:.#pkgs.{system}.vimPlugins.nvim-treesitter-textobjects"),
            mode="build",
        ),
    )
    assert api_calls == [
        (
            session,
            "repos/gkze/nvim-treesitter-textobjects/commits/main",
            updater.config,
        )
    ]
    assert len(hash_calls) == 1
    assert_nix_ast_equal(
        str(hash_calls[0]["expr"]),
        _build_fetch_from_github_call(
            "gkze",
            "nvim-treesitter-textobjects",
            rev=_UPDATED_COMMIT,
            fetch_submodules=False,
        ),
    )
    result_events = [event for event in events if event.kind is UpdateEventKind.RESULT]
    assert result_events == [
        UpdateEvent.result(
            "treesitter-textobjects",
            SourceEntry(
                version="main",
                commit=_UPDATED_COMMIT,
                hashes=HashCollection.from_value([
                    HashEntry.create("srcHash", _UPDATED_HASH)
                ]),
            ),
        )
    ]


@pytest.mark.parametrize(
    ("payload", "error_type", "match"),
    [
        (None, TypeError, "must be a JSON object"),
        ({}, RuntimeError, "no immutable commit"),
        ({"sha": "main"}, RuntimeError, "no immutable commit"),
    ],
)
def test_treesitter_textobjects_update_rejects_mutable_branch_metadata(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    error_type: type[Exception],
    match: str,
) -> None:
    """Fail closed unless GitHub resolves main to a full immutable SHA."""
    module = _load_module()

    async def _fetch_github_api(*_args: object, **_kwargs: object) -> object:
        return payload

    monkeypatch.setattr(module, "fetch_github_api", _fetch_github_api)

    with pytest.raises(error_type, match=match):
        run_async(module.TreesitterTextobjectsUpdater().fetch_latest(object()))


def test_treesitter_textobjects_hashing_requires_immutable_commit_metadata() -> None:
    """Reject callers that bypass the branch resolver with a mutable revision."""
    module = _load_module()
    updater = module.TreesitterTextobjectsUpdater()

    with pytest.raises(RuntimeError, match="metadata has no immutable commit"):
        run_async(
            collect_events(
                updater.fetch_hashes(
                    VersionInfo(version="main", metadata={"commit": "main"}),
                    object(),
                )
            )
        )
