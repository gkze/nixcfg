"""Tests for the shared crate2nix Cargo.nix normalizer helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from nix_manipulator import parse
from nix_manipulator.expressions.path import NixPath

from lib.cargo_nix_normalizer import (
    _ensure_root_src_argument,
    _normalize_with_fallback,
    _rewrite_root_src_paths,
    normalize,
)
from lib.tests._nix_ast import assert_nix_ast_equal


def test_normalize_rewrites_local_workspace_paths() -> None:
    """AST-driven rewrites should convert local paths to rootSrc-relative strings."""
    sample = """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
}:
rec {
  foo = { src = ./crates/foo; };
  bar = { src = ./vendor/v8; };
}
"""

    normalized, rewrites, added_root_src = normalize(
        sample,
        local_path_prefixes=("crates", "vendor"),
    )

    assert added_root_src is True
    assert rewrites == 2
    assert_nix_ast_equal(
        normalized,
        """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
, rootSrc ? ./.
}:
rec {
  foo = { src = "${rootSrc}/crates/foo"; };
  bar = { src = "${rootSrc}/vendor/v8"; };
}
""",
    )


def test_normalize_rewrites_exact_local_workspace_roots() -> None:
    """AST-driven rewrites should handle exact ``./dir`` source bindings too."""
    sample = """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
}:
rec {
  foo = { src = ./cli; };
}
"""

    normalized, rewrites, added_root_src = normalize(
        sample,
        local_path_prefixes=("cli",),
    )

    assert added_root_src is True
    assert rewrites == 1
    assert_nix_ast_equal(
        normalized,
        """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
, rootSrc ? ./.
}:
rec {
  foo = { src = "${rootSrc}/cli"; };
}
""",
    )


def test_normalize_fallback_rewrites_store_paths() -> None:
    """Fallback regex rewrites should handle crate2nix store-path source bindings."""
    sample = """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
}:
rec {
  foo = {
    src = lib.cleanSourceWith {
      filter = sourceFilter;
      src = ../../nix/store/abc-source/crates/foo;
    };
  };
}
"""

    pattern = re.compile(
        r"(?P<needle>(?:\.\./)+nix/store/[^/]+-source/(?P<suffix>[^;]+))"
    )

    normalized, rewrites, added_root_src = _normalize_with_fallback(
        sample,
        local_path_prefixes=("crates",),
        fallback_patterns=(pattern,),
        rewrite_nixpkgs_config=True,
    )

    assert added_root_src is True
    assert rewrites == 1
    assert_nix_ast_equal(
        normalized,
        """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
, rootSrc ? ./.
}:
rec {
  foo = {
    src = lib.cleanSourceWith {
      filter = sourceFilter;
      src = "${rootSrc}/crates/foo";
    };
  };
        }
""",
    )


def test_normalize_preserves_preamble_and_skips_existing_root_src() -> None:
    """Normalization should keep a leading newline and avoid duplicate rootSrc."""
    sample = """
{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
, rootSrc ? ./.
}:
rec {
  foo = { src = ./crates/foo; };
}
"""

    normalized, rewrites, added_root_src = normalize(
        sample,
        local_path_prefixes=("crates",),
    )

    assert normalized.startswith("\n")
    assert rewrites == 1
    assert added_root_src is False
    assert normalized.count("rootSrc ? ./.;") == 0
    assert normalized.count("rootSrc ? ./.") == 1


def test_ensure_root_src_argument_inserts_before_ellipses() -> None:
    """The helper should place rootSrc before an ellipsis argument."""
    source = "{ crateConfig, ... }: {}"
    parsed = parse(source)
    root = parsed.expr

    assert _ensure_root_src_argument(root) is True
    assert [argument.rebuild() for argument in root.argument_set] == [
        "crateConfig",
        "rootSrc ? ./.",
        "...",
    ]


def test_ensure_root_src_argument_inserts_before_ellipses_without_crate_config() -> (
    None
):
    """Ellipsis-only signatures should still place rootSrc before the ellipsis."""
    source = "{ nixpkgs, ... }: {}"
    parsed = parse(source)

    assert _ensure_root_src_argument(parsed.expr) is True
    assert [argument.rebuild() for argument in parsed.expr.argument_set] == [
        "nixpkgs",
        "rootSrc ? ./.",
        "...",
    ]


def test_ensure_root_src_argument_appends_when_no_special_markers_exist() -> None:
    """Plain argument sets should append rootSrc at the end."""
    source = "{ nixpkgs }: {}"
    parsed = parse(source)

    assert _ensure_root_src_argument(parsed.expr) is True
    assert [argument.rebuild() for argument in parsed.expr.argument_set] == [
        "nixpkgs",
        "rootSrc ? ./.",
    ]


def test_ensure_root_src_argument_rejects_non_list_argument_sets() -> None:
    """The helper should fail clearly when the parsed signature shape is unexpected."""
    source = "{ crateConfig }: {}"
    parsed = parse(source)
    root = parsed.expr
    root.argument_set = tuple(root.argument_set)

    with pytest.raises(TypeError, match="attribute-set function signature"):
        _ensure_root_src_argument(root)


def test_normalize_with_fallback_requires_insertion_marker() -> None:
    """Fallback insertion should fail when the crateConfig terminator is absent."""
    with pytest.raises(
        RuntimeError, match="Could not find crateConfig block terminator"
    ):
        _normalize_with_fallback(
            "{ nixpkgs ? <nixpkgs> }: {}",
            local_path_prefixes=(),
            fallback_patterns=(),
            rewrite_nixpkgs_config=False,
        )


def test_normalize_with_fallback_skips_non_matching_suffixes() -> None:
    """Fallback rewrites should leave unrelated store paths untouched."""
    sample = """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
}:
rec {
  foo = { src = ../../nix/store/abc-source/vendor/v8; };
}
"""

    normalized, rewrites, added_root_src = _normalize_with_fallback(
        sample,
        local_path_prefixes=("crates",),
        fallback_patterns=(),
        rewrite_nixpkgs_config=False,
    )

    assert added_root_src is True
    assert rewrites == 0
    assert "../../nix/store/abc-source/vendor/v8" in normalized


def test_normalize_uses_fallback_for_parse_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed input should route through the fallback implementation."""
    seen: dict[str, object] = {}

    def _fake_fallback(
        text: str,
        *,
        local_path_prefixes: tuple[str, ...],
        fallback_patterns: tuple[re.Pattern[str], ...],
        rewrite_nixpkgs_config: bool,
    ) -> tuple[str, int, bool]:
        seen.update({
            "text": text,
            "local_path_prefixes": local_path_prefixes,
            "fallback_patterns": fallback_patterns,
            "rewrite_nixpkgs_config": rewrite_nixpkgs_config,
        })
        return ("normalized", 3, True)

    monkeypatch.setattr(
        "lib.cargo_nix_normalizer.parse",
        lambda _text: SimpleNamespace(expr=object(), contains_error=True),
    )
    monkeypatch.setattr(
        "lib.cargo_nix_normalizer._normalize_with_fallback", _fake_fallback
    )

    assert normalize(
        "broken",
        local_path_prefixes=("workspace",),
        fallback_patterns=(re.compile("x"),),
        rewrite_nixpkgs_config=True,
    ) == ("normalized", 3, True)
    assert seen == {
        "text": "broken",
        "local_path_prefixes": ("workspace",),
        "fallback_patterns": (re.compile("x"),),
        "rewrite_nixpkgs_config": True,
    }


def test_normalize_uses_fallback_for_non_function_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-function parse roots should fall back to text surgery too."""
    monkeypatch.setattr(
        "lib.cargo_nix_normalizer.parse",
        lambda _text: SimpleNamespace(expr=object(), contains_error=False),
    )
    monkeypatch.setattr(
        "lib.cargo_nix_normalizer._normalize_with_fallback",
        lambda *_args, **_kwargs: ("fallback", 1, False),
    )

    assert normalize("not-a-function") == ("fallback", 1, False)


def test_normalize_ast_path_rewrites_store_sources() -> None:
    """AST normalization should rewrite store-backed local sources too."""
    sample = """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
}:
rec {
  exact = { src = /nix/store/abc-source/crates; };
  nested = { src = /nix/store/abc-source/crates/foo; };
}
"""

    normalized, rewrites, added_root_src = normalize(
        sample,
        local_path_prefixes=("crates",),
        rewrite_nixpkgs_config=True,
    )

    assert added_root_src is True
    assert rewrites == 2
    assert '"${rootSrc}/crates"' in normalized
    assert '"${rootSrc}/crates/foo"' in normalized


def test_normalize_ast_path_store_prefix_matching_covers_loop_paths() -> None:
    """Store-path prefix matching should handle misses before later matches."""
    sample = """{ crateConfig }: {
  later = { src = /nix/store/abc-source/crates/foo; };
  miss = { src = /nix/store/abc-source/vendor/v8; };
}
"""

    normalized, rewrites, added_root_src = normalize(
        sample,
        local_path_prefixes=("vendoring", "crates"),
    )

    assert added_root_src is True
    assert rewrites == 1
    assert '"${rootSrc}/crates/foo"' in normalized
    assert "/nix/store/abc-source/vendor/v8" in normalized


@dataclass
class _Holder:
    items: list[object]


def test_normalize_ast_path_rewrites_nixpkgs_config_when_rendered_text_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The AST path should apply the nixpkgs config text rewrite when present."""
    source = "{ crateConfig }: {}"
    parsed = parse(source)
    monkeypatch.setattr(
        "lib.cargo_nix_normalizer.parse",
        lambda _text: SimpleNamespace(
            expr=parsed.expr,
            contains_error=False,
            rebuild=lambda: "import nixpkgs { config = {}; }",
        ),
    )

    normalized, rewrites, added_root_src = normalize(
        "ignored",
        rewrite_nixpkgs_config=True,
    )

    assert normalized == "import nixpkgs { }"
    assert rewrites == 0
    assert added_root_src is True


def test_rewrite_root_src_paths_handles_non_dataclass_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Traversal should tolerate non-dataclass nodes."""
    source = "{ crateConfig }: {}"
    parsed = parse(source)
    monkeypatch.setattr("lib.cargo_nix_normalizer.is_dataclass", lambda _node: False)
    assert _rewrite_root_src_paths(parsed.expr, local_path_prefixes=("crates",)) == 0


def test_rewrite_root_src_paths_handles_mixed_lists() -> None:
    """Traversal should leave non-expression list items untouched while rewriting paths."""
    holder = _Holder(items=[object(), NixPath(path="./crates/foo")])
    assert _rewrite_root_src_paths(holder, local_path_prefixes=("crates",)) == 1
    assert holder.items[0].__class__ is object
    assert holder.items[1].rebuild() == '"${rootSrc}/crates/foo"'


def test_normalize_with_fallback_handles_existing_root_src_and_empty_prefixes() -> None:
    """Fallback mode should skip rootSrc insertion and honor explicit regexes."""
    sample = """{ nixpkgs ? <nixpkgs>
, rootSrc ? ./.
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
}:
rec {
  foo = { src = ./workspace/foo; };
}
"""

    normalized, rewrites, added_root_src = _normalize_with_fallback(
        sample,
        local_path_prefixes=(),
        fallback_patterns=(re.compile(r"(?P<needle>\./workspace/(?P<suffix>[^;]+))"),),
        rewrite_nixpkgs_config=False,
    )

    assert added_root_src is False
    assert rewrites == 1
    assert normalized.count("rootSrc ? ./.") == 1
    assert '"${rootSrc}/foo"' in normalized
