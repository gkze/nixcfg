"""Structural contracts for updater-owned immutable GitHub sources."""

import re

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, binding_map, expect_binding
from lib.tests._nix_source import nix_file_binding_expr
from lib.update.sources import load_all_sources

_IMMUTABLE_SOURCE_NAMES = (
    "baseten",
    "baseten-switch",
    "rio",
    "crush",
    "turso",
    "turso-cli",
    "tsgolint",
    "neutils",
    "sentry-cli",
    "mdformat",
    "codex-v8",
)


@pytest.mark.parametrize(
    ("path", "binding", "revision"),
    [
        ("packages/baseten/default.nix", "src", "commit"),
        ("packages/baseten-switch/default.nix", "src", "commit"),
        ("overlays/rio/default.nix", "src", "commit"),
        ("overlays/crush/default.nix", "src", "commit"),
        ("overlays/turso/default.nix", "src", "commit"),
        ("overlays/turso-cli/default.nix", "src", "commit"),
        ("overlays/tsgolint/default.nix", "src", "commit"),
        ("packages/neutils/default.nix", "src", "commit"),
        ("overlays/sentry-cli/default.nix", "filteredSrc", "selfSource.commit"),
        ("overlays/mdformat.nix", "src", "info.commit"),
        ("overlays/codex-v8/default.nix", "codex-v8", "selfSource.commit"),
    ],
)
def test_source_fetches_use_only_updater_persisted_commits(
    path: str,
    binding: str,
    revision: str,
) -> None:
    """Every audited source fetch must consume its immutable commit field."""
    source = expect_instance(nix_file_binding_expr(path, binding), FunctionCall)
    arguments = expect_instance(source.argument, AttributeSet)

    assert_nix_ast_equal(expect_binding(arguments.values, "rev").value, revision)
    assert "tag" not in binding_map(arguments.values)


@pytest.mark.parametrize("source_name", _IMMUTABLE_SOURCE_NAMES)
def test_immutable_source_fetches_have_persisted_commits(source_name: str) -> None:
    """Every commit-backed fetch must have usable checked-in source metadata."""
    commit = load_all_sources().entries[source_name].commit

    assert commit is not None
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
