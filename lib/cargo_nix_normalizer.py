"""Shared helpers for normalizing checked-in crate2nix Cargo.nix files."""

from __future__ import annotations

import re
from dataclasses import fields, is_dataclass

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
_STORE_PATH_PATTERN = re.compile(r"(?:^|.*/)nix/store/[^/]+/(?P<suffix>.+)")
_FALLBACK_STORE_PATH_PATTERN = re.compile(
    r'(?P<needle>"?(?:\.\./)+nix/store/[^/]+/(?P<suffix>[^";]+)"?)'
)


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


def _rewrite_root_src_paths(
    expr: NixExpression,
    *,
    local_path_prefixes: tuple[str, ...],
) -> int:
    """Rewrite local source paths like `./crates/...` to `${rootSrc}/...`."""
    rewrites = 0

    def source_suffix(path: str) -> str | None:
        store_match = _STORE_PATH_PATTERN.match(path)
        if store_match is not None:
            store_suffix = store_match.group("suffix")
            for prefix in local_path_prefixes:
                if store_suffix == prefix or store_suffix.startswith(f"{prefix}/"):
                    return store_suffix

        for prefix in local_path_prefixes:
            exact = f"./{prefix}"
            normalized = f"./{prefix}/"
            if path == exact:
                return prefix
            if path.startswith(normalized):
                return path.removeprefix("./")
        return None

    def transform(node: NixExpression) -> NixExpression:
        nonlocal rewrites

        if isinstance(node, NixPath):
            suffix = source_suffix(node.path)
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


def _normalize_with_fallback(
    text: str,
    *,
    local_path_prefixes: tuple[str, ...],
    fallback_patterns: tuple[re.Pattern[str], ...],
    rewrite_nixpkgs_config: bool,
) -> tuple[str, int, bool]:
    """Use minimal text surgery when nix-manipulator cannot parse the file."""
    added_root_src = False
    if _ROOTSRC_MARKER not in text:
        if _ROOTSRC_INSERTION_MARKER not in text:
            msg = "Could not find crateConfig block terminator to insert rootSrc"
            raise RuntimeError(msg)
        replacement = f"    else {{}}\n, {_root_src_argument_text()}\n}}:"
        text = text.replace(_ROOTSRC_INSERTION_MARKER, replacement, 1)
        added_root_src = True

    if rewrite_nixpkgs_config:
        text = text.replace("import nixpkgs { config = {}; }", "import nixpkgs { }")

    path_rewrites = 0

    def rewrite_src_binding(match: re.Match[str]) -> str:
        nonlocal path_rewrites
        suffix = match.groupdict().get("suffix", match.group(1))
        needle = match.groupdict().get("needle", match.group(1))
        if local_path_prefixes and not any(
            suffix == prefix or suffix.startswith(f"{prefix}/")
            for prefix in local_path_prefixes
        ):
            return match.group(0)
        path_rewrites += 1
        return match.group(0).replace(needle, _root_src_string_text(suffix), 1)

    patterns = list(fallback_patterns)
    if local_path_prefixes:
        patterns.append(_FALLBACK_STORE_PATH_PATTERN)

    for pattern in patterns:
        text = pattern.sub(rewrite_src_binding, text)

    return text, path_rewrites, added_root_src


def normalize(
    text: str,
    *,
    local_path_prefixes: tuple[str, ...] = (),
    fallback_patterns: tuple[re.Pattern[str], ...] = (),
    rewrite_nixpkgs_config: bool = False,
) -> tuple[str, int, bool]:
    """Return normalized Cargo.nix text plus replacement counts."""
    parsed = parse(text)
    root = parsed.expr

    if isinstance(root, FunctionDefinition) and not parsed.contains_error:
        added_root_src = _ensure_root_src_argument(root)
        path_rewrites = _rewrite_root_src_paths(
            root,
            local_path_prefixes=local_path_prefixes,
        )
        normalized = _preserve_file_preamble(text, parsed.rebuild())
        if rewrite_nixpkgs_config:
            normalized = normalized.replace(
                "import nixpkgs { config = {}; }", "import nixpkgs { }"
            )
        return normalized, path_rewrites, added_root_src

    return _normalize_with_fallback(
        text,
        local_path_prefixes=local_path_prefixes,
        fallback_patterns=fallback_patterns,
        rewrite_nixpkgs_config=rewrite_nixpkgs_config,
    )
