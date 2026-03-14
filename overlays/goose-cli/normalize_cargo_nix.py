#!/usr/bin/env python3
"""Normalize generated crate2nix output for the checked-in Goose Cargo.nix.

crate2nix assumes the generated Cargo.nix lives next to the workspace source.
In this repo we instead check Cargo.nix into overlays/goose-cli/ and feed the
real, already-patched Goose source tree separately via rootSrc.

This helper applies the stable local fixups we currently need after running
`crate2nix generate`:

1. add `rootSrc ? ./.` to the top-level function arguments if missing
2. rewrite `./crates/...` and `./vendor/...` paths to `${rootSrc}/...`

The implementation intentionally uses nix-manipulator so these edits are driven
by the parsed Nix AST instead of ad-hoc text replacement.

Usage:
    python overlays/goose-cli/normalize_cargo_nix.py overlays/goose-cli/Cargo.nix
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path

from nix_manipulator import parse
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.primitive import StringPrimitive

_SKIP_TRAVERSAL_FIELDS = {
    "after",
    "attrpath_order",
    "before",
    "scope",
    "scope_state",
    "source_path",
}

_ROOTSRC_MARKER = "rootSrc ? ./."
_ROOTSRC_INSERTION_MARKER = "    else {}\n}:"
_SRC_BINDING_PATTERN = re.compile(r"src = \./((?:crates|vendor)/[^;]+);")


def _source_suffix(path: str) -> str | None:
    """Return the rootSrc-relative suffix for source paths we normalize."""
    for prefix in ("./crates/", "./vendor/"):
        if path.startswith(prefix):
            return path.removeprefix("./")
    return None


def _preserve_file_preamble(original: str, rebuilt: str) -> str:
    """Keep nix-manipulator from dropping the generated file's leading newline."""
    if original.startswith("\n") and not rebuilt.startswith("\n"):
        return f"\n{rebuilt}"
    return rebuilt


def _ensure_root_src_argument(root: FunctionDefinition) -> bool:
    """Add `rootSrc ? ./.` to the top-level function if it is missing."""
    if not isinstance(root.argument_set, list):
        msg = "Expected Cargo.nix to use an attribute-set function signature"
        raise TypeError(msg)

    for argument in root.argument_set:
        if isinstance(argument, Identifier) and argument.name == "rootSrc":
            return False

    new_argument = Identifier(name="rootSrc", default_value=NixPath(path="./."))

    insert_at = len(root.argument_set)
    for index, argument in enumerate(root.argument_set):
        if isinstance(argument, Identifier) and argument.name == "crateConfig":
            insert_at = index + 1
            break
        if argument.__class__.__name__ == "Ellipses":
            insert_at = index
            break

    root.argument_set.insert(insert_at, new_argument)
    return True


def _rewrite_root_src_paths(expr: NixExpression) -> int:
    """Rewrite local crate source paths to `${rootSrc}/...` strings."""
    rewrites = 0

    def transform(node: NixExpression) -> NixExpression:
        nonlocal rewrites

        if isinstance(node, NixPath):
            suffix = _source_suffix(node.path)
            if suffix is not None:
                rewrites += 1
                return StringPrimitive(
                    value=f"${{rootSrc}}/{suffix}",
                    raw_string=True,
                    before=list(node.before),
                    after=list(node.after),
                )

        if not is_dataclass(node):
            return node

        for field_info in fields(node):
            if field_info.name in _SKIP_TRAVERSAL_FIELDS:
                continue
            current_value = getattr(node, field_info.name)
            updated_value = transform_value(current_value)
            if updated_value is not current_value:
                setattr(node, field_info.name, updated_value)

        return node

    def transform_value(value: object) -> object:
        if isinstance(value, NixExpression):
            return transform(value)
        if isinstance(value, list):
            updated_items: list[object] = []
            changed = False
            for item in value:
                if isinstance(item, NixExpression):
                    updated_item = transform(item)
                    changed = changed or updated_item is not item
                    updated_items.append(updated_item)
                else:
                    updated_items.append(item)
            return updated_items if changed else value
        return value

    transform(expr)
    return rewrites


def _root_src_argument_text() -> str:
    """Render the canonical `rootSrc ? ./.` argument via nix-manipulator."""
    return Identifier(name="rootSrc", default_value=NixPath(path="./.")).rebuild()


def _root_src_string_text(suffix: str) -> str:
    """Render the canonical `${rootSrc}/...` string via nix-manipulator."""
    return StringPrimitive(value=f"${{rootSrc}}/{suffix}", raw_string=True).rebuild()


def _normalize_with_fallback(text: str) -> tuple[str, int, bool]:
    """Use minimal text surgery when nix-manipulator cannot parse the whole file.

    crate2nix's generated Cargo.nix currently contains constructs that make
    nix-manipulator return a top-level RawExpression with ERROR nodes. In that
    case we still use nix-manipulator to construct the inserted Nix fragments,
    but apply them with tightly scoped replacements.
    """
    added_root_src = False
    if _ROOTSRC_MARKER not in text:
        if _ROOTSRC_INSERTION_MARKER not in text:
            msg = "Could not find crateConfig block terminator to insert rootSrc"
            raise RuntimeError(msg)
        replacement = f"    else {{}}\n, {_root_src_argument_text()}\n}}:"
        text = text.replace(_ROOTSRC_INSERTION_MARKER, replacement, 1)
        added_root_src = True

    def rewrite_src_binding(match: re.Match[str]) -> str:
        suffix = match.group(1)
        return f"src = {_root_src_string_text(suffix)};"

    text, path_rewrites = _SRC_BINDING_PATTERN.subn(rewrite_src_binding, text)
    return text, path_rewrites, added_root_src


def normalize(text: str) -> tuple[str, int, bool]:
    """Return normalized Cargo.nix text plus replacement counts."""
    parsed = parse(text)
    root = parsed.expr

    if isinstance(root, FunctionDefinition) and not parsed.contains_error:
        added_root_src = _ensure_root_src_argument(root)
        path_rewrites = _rewrite_root_src_paths(root)
        normalized = _preserve_file_preamble(text, parsed.rebuild())
        return normalized, path_rewrites, added_root_src

    return _normalize_with_fallback(text)


def main() -> int:
    """Normalize a Goose Cargo.nix file in place and report what changed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="overlays/goose-cli/Cargo.nix")
    args = parser.parse_args()

    path = Path(args.path)
    original = path.read_text()
    normalized, path_rewrites, added_root_src = normalize(original)

    if normalized != original:
        path.write_text(normalized)

    status = []
    status.append("added rootSrc" if added_root_src else "rootSrc already present")
    status.append(f"rewrote {path_rewrites} source path(s)")
    status.append("updated file" if normalized != original else "no content change")
    sys.stdout.write(f"{path}: " + ", ".join(status) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
