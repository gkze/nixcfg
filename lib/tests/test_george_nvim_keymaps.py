"""Smoke tests for George's generated Neovim keymap catalog."""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from lib.update.paths import REPO_ROOT


@cache
def _catalog() -> dict[str, Any]:
    root = Path(REPO_ROOT).resolve()
    nix = shutil.which("nix")
    assert nix is not None
    expr = f"""
let
  data = import {root}/home/george/nvim-keymaps.nix;
in {{
  global = data.global;
  lsp = data.lsp;
  treesitterSelection = data.treesitterSelection;
  treesitterTextobjectsMove = data.treesitterTextobjectsMove;
  treesitterTextobjectsSelect = data.treesitterTextobjectsSelect;
  blinkCmp = data.blinkCmp;
  telescope = data.telescope;
  gitlinker = data.gitlinker;
  alpha = data.alpha;
}}
"""
    result = subprocess.run(  # noqa: S603
        [nix, "eval", "--impure", "--json", "--expr", expr],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


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
        "global": 55,
        "lsp": 9,
        "telescope": 1,
        "treesitterSelection": 4,
        "treesitterTextobjectsMove": 36,
        "treesitterTextobjectsSelect": 18,
    }
    assert sum(_scope_item_count(scope) for scope in catalog.values()) == 135


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_keymap_catalog_keeps_critical_navigation_and_opencode_bindings() -> None:
    """Critical bindings should survive the catalog refactor unchanged."""
    entries = _catalog_entries()

    assert ("global", "<leader>km", "Browse keymap cheat sheet") in entries
    assert ("global", "<leader>kd", "Open keymap doc") in entries
    assert ("global", "<leader>oa", "OpenCode ask") in entries
    assert ("global", "<leader>ot", "OpenCode toggle") in entries
    assert ("lsp", "gd", "Go to definition") in entries
    assert ("telescope", "<CR>", "Select multi or default") in entries
