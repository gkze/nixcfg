"""Smoke tests for George's generated Neovim keymap catalog."""

from __future__ import annotations

from functools import cache
from typing import Any

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import expect_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT

_CATALOG_SCOPE_NAMES = {
    "alpha",
    "blinkCmp",
    "gitlinker",
    "global",
    "lsp",
    "telescope",
    "treesitterSelection",
    "treesitterTextobjectsMove",
    "treesitterTextobjectsSelect",
}


@cache
def _catalog() -> dict[str, Any]:
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "home/george/nvim-keymaps.nix").read_text(encoding="utf-8")
        ),
        AttributeSet,
    )
    return {
        binding.name: _scope_payload(expect_instance(binding.value, AttributeSet))
        for binding in root.values
        if isinstance(binding, Binding) and binding.name in _CATALOG_SCOPE_NAMES
    }


def _string_binding(attrset: AttributeSet, name: str) -> str:
    return expect_instance(
        expect_binding(attrset.values, name).value,
        StringPrimitive,
    ).value


def _item_payload(item: AttributeSet) -> dict[str, str]:
    return {
        "key": _string_binding(item, "key"),
        "desc": _string_binding(item, "desc"),
    }


def _unwrap(expr: object) -> object:
    while isinstance(expr, Parenthesis):
        expr = expr.value
    return expr


def _curried_call(expr: object) -> tuple[str, list[object]]:
    args: list[object] = []
    current = expect_instance(_unwrap(expr), FunctionCall)
    while True:
        args.append(_unwrap(current.argument))
        name = _unwrap(current.name)
        if isinstance(name, FunctionCall):
            current = name
            continue
        identifier = expect_instance(name, Identifier)
        args.reverse()
        return identifier.name, args


def _materialize_item(expr: object) -> dict[str, str]:
    node = _unwrap(expr)
    if isinstance(node, AttributeSet):
        return _item_payload(node)

    name, args = _curried_call(node)
    assert name in {"mkMapItem", "mkTextobjectSelectItem"}
    assert len(args) == 3
    return {
        "key": expect_instance(args[0], StringPrimitive).value,
        "desc": expect_instance(args[2], StringPrimitive).value,
    }


def _scope_payload(scope: AttributeSet) -> dict[str, Any]:
    sections = expect_instance(expect_binding(scope.values, "sections").value, NixList)
    return {
        "sections": [
            {
                "items": [
                    _materialize_item(item)
                    for item in expect_instance(
                        expect_binding(
                            expect_instance(section, AttributeSet).values, "items"
                        ).value,
                        NixList,
                    ).value
                ]
            }
            for section in sections.value
        ]
    }


def _scope_item_count(scope: dict[str, Any]) -> int:
    return sum(len(section["items"]) for section in scope["sections"])


def _catalog_entries() -> set[tuple[str, str, str]]:
    entries: set[tuple[str, str, str]] = set()
    for scope_name, scope in _catalog().items():
        for section in scope["sections"]:
            for item in section["items"]:
                entries.add((scope_name, item["key"], item["desc"]))
    return entries


def test_keymap_catalog_scope_counts_match_expected_smoke_snapshot() -> None:
    """The generated catalog should keep the current scope inventory stable."""
    catalog = _catalog()

    assert {name: _scope_item_count(scope) for name, scope in catalog.items()} == {
        "alpha": 4,
        "blinkCmp": 7,
        "gitlinker": 1,
        "global": 53,
        "lsp": 9,
        "telescope": 1,
        "treesitterSelection": 4,
        "treesitterTextobjectsMove": 48,
        "treesitterTextobjectsSelect": 18,
    }
    assert sum(_scope_item_count(scope) for scope in catalog.values()) == 145


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
    assert ("global", "[b", "Previous buffer") in entries
    assert ("global", "<leader>h", "Focus left pane") in entries
    assert ("global", "<C-A-h>", "Treewalker left") in entries
    assert ("global", "<C-A-j>", "Treewalker down") in entries
    assert ("global", "<C-A-k>", "Treewalker up") in entries
    assert ("global", "<C-A-l>", "Treewalker right") in entries
    assert ("global", "<leader>t", "Toggle terminal") in entries
    assert ("global", "<leader>T", "New tab") in entries
    assert ("global", "<leader>q", "Close tab") in entries
    assert ("lsp", "gd", "Go to definition") in entries
    assert ("lsp", "gs", "Signature help") in entries
    assert ("telescope", "<CR>", "Select multi or default") in entries
