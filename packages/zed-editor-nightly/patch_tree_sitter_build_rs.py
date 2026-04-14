"""Patch tree-sitter build.rs to accept multiple include paths."""

from __future__ import annotations

from pathlib import Path

_EXPECTED_ARGC = 1
_OLD = (
    "        config\n"
    '            .define("TREE_SITTER_FEATURE_WASM", "")\n'
    '            .define("static_assert(...)", "")\n'
    '            .include(env::var("DEP_WASMTIME_C_API_INCLUDE").unwrap());\n'
)
_NEW = (
    "        config\n"
    '            .define("TREE_SITTER_FEATURE_WASM", "")\n'
    '            .define("static_assert(...)", "");\n'
    '        if let Ok(include) = env::var("DEP_WASMTIME_C_API_INCLUDE") {\n'
    "            for include in include.split_whitespace() {\n"
    "                config.include(include);\n"
    "            }\n"
    "        }\n"
)


def patch_file(path: Path) -> None:
    """Patch one tree-sitter build.rs file in place."""
    text = path.read_text()
    if _OLD not in text:
        msg = "tree-sitter patch target not found"
        raise SystemExit(msg)
    path.write_text(text.replace(_OLD, _NEW, 1))


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
