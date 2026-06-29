"""Patch tree-sitter build.rs with an exact-count source codemod."""

from __future__ import annotations

import re
from pathlib import Path

from lib.codemods.text import regex_replace_file_exactly

_EXPECTED_ARGC = 1
_PATTERN = re.compile(
    r"^(?P<indent>[ \t]*)(?P<config>[^\n]+)\n"
    r'(?P=indent)    \.define\("TREE_SITTER_FEATURE_WASM", ""\)\n'
    r'(?P=indent)    \.define\("static_assert\(\.\.\.\)", ""\)\n'
    r'(?P=indent)    \.include\(env::var\("DEP_WASMTIME_C_API_INCLUDE"\)\.unwrap\(\)\);',
    flags=re.MULTILINE,
)


def _replacement(match: re.Match[str]) -> str:
    statement_indent = match["indent"]
    child_indent = f"{statement_indent}    "
    grandchild_indent = f"{child_indent}    "
    return f"""{statement_indent}{match["config"]}
{child_indent}.define("TREE_SITTER_FEATURE_WASM", "")
{child_indent}.define("static_assert(...)", "");
{statement_indent}if let Ok(include) = env::var("DEP_WASMTIME_C_API_INCLUDE") {{
{child_indent}for include in include.split_whitespace() {{
{grandchild_indent}config.include(include);
{child_indent}}}
{statement_indent}}}"""


def patch_file(path: Path) -> None:
    """Patch one tree-sitter build.rs file in place."""
    regex_replace_file_exactly(
        path,
        pattern=_PATTERN,
        replacement=_replacement,
        expected_count=1,
        context="tree-sitter build.rs patch",
    )


def main(argv: list[str] | None = None) -> int:
    """Patch the requested tree-sitter build.rs file in place."""
    args = list(argv or [])
    if len(args) != _EXPECTED_ARGC:
        msg = "usage: patch_tree_sitter_build_rs.py <binding_rust/build.rs>"
        raise SystemExit(msg)
    (build_rs,) = args
    patch_file(Path(build_rs))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    import sys

    raise SystemExit(main(sys.argv[1:]))
