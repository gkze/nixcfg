"""Smoke tests for George's generated Neovim keymap catalog."""

from __future__ import annotations

import shutil
from functools import cache
from typing import Any

import pytest

from lib.tests._nix_eval import nix_attrset, nix_eval_json, nix_import, nix_let
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT


@cache
def _catalog() -> dict[str, Any]:
    expression = nix_let(
        {"data": nix_import(REPO_ROOT / "home/george/nvim-keymaps.nix")},
        nix_attrset({
            "global": identifier_attr_path("data", "global"),
            "lsp": identifier_attr_path("data", "lsp"),
            "treesitterSelection": identifier_attr_path("data", "treesitterSelection"),
            "treesitterTextobjectsMove": identifier_attr_path(
                "data", "treesitterTextobjectsMove"
            ),
            "treesitterTextobjectsSelect": identifier_attr_path(
                "data", "treesitterTextobjectsSelect"
            ),
            "blinkCmp": identifier_attr_path("data", "blinkCmp"),
            "telescope": identifier_attr_path("data", "telescope"),
            "gitlinker": identifier_attr_path("data", "gitlinker"),
            "alpha": identifier_attr_path("data", "alpha"),
        }),
    )
    catalog = nix_eval_json(expression)
    assert isinstance(catalog, dict)
    return catalog


def _scope_item_count(scope: dict[str, Any]) -> int:
    return sum(len(section["items"]) for section in scope["sections"])


def _catalog_entries() -> set[tuple[str, str, str]]:
    entries: set[tuple[str, str, str]] = set()
    for scope_name, scope in _catalog().items():
        for section in scope["sections"]:
            for item in section["items"]:
                entries.add((scope_name, item["key"], item["desc"]))
    return entries


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_keymap_catalog_scope_counts_match_expected_smoke_snapshot() -> None:
    """The generated catalog should keep the current scope inventory stable."""
    catalog = _catalog()

    assert {name: _scope_item_count(scope) for name, scope in catalog.items()} == {
        "alpha": 4,
        "blinkCmp": 7,
        "gitlinker": 1,
        "global": 45,
        "lsp": 9,
        "telescope": 1,
        "treesitterSelection": 4,
        "treesitterTextobjectsMove": 36,
        "treesitterTextobjectsSelect": 18,
    }
    assert sum(_scope_item_count(scope) for scope in catalog.values()) == 125


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_keymap_catalog_keeps_critical_navigation_and_opencode_bindings() -> None:
    """Critical bindings should survive the catalog refactor unchanged."""
    entries = _catalog_entries()

    assert ("global", "<leader>m", "Browse keymap cheat sheet") in entries
    assert ("global", "<leader>M", "Open keymap doc") in entries
    assert ("global", "<leader>O", "OpenCode ask") in entries
    assert ("global", "<leader>o", "OpenCode toggle") in entries
    assert ("global", "<leader>c", "Toggle CodeCompanion chat") in entries
    assert ("global", "<leader>e", "Neo-tree filesystem") in entries
    assert ("global", "<leader>g", "Open Neogit") in entries
    assert ("global", "<leader>s", "Live grep") in entries
    assert ("global", "<leader>h", "Previous buffer") in entries
    assert ("global", "<leader>t", "Toggle terminal") in entries
    assert ("global", "<leader>T", "New tab") in entries
    assert ("global", "<leader>q", "Close tab") in entries
    assert ("lsp", "gd", "Go to definition") in entries
    assert ("lsp", "gs", "Signature help") in entries
    assert ("telescope", "<CR>", "Select multi or default") in entries
