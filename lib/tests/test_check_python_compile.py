"""Tests for the repo-local Python compilation smoke-check helper."""

from __future__ import annotations

import sys
from pathlib import Path

from lib.check_python_compile import (
    _build_parser,
    _has_glob,
    _is_ignored,
    _matches_glob,
    compile_paths,
    iter_target_paths,
    main,
)


def test_glob_and_ignore_helpers_cover_edge_cases() -> None:
    """Glob detection and ignore checks should match repo scanning rules."""
    assert _has_glob("lib/**/*.py") is True
    assert _has_glob("lib/check_python_compile.py") is False
    assert _is_ignored(Path(".git/config")) is True
    assert _is_ignored(Path("lib/tests/test_check_python_compile.py")) is False
    assert _matches_glob(Path("pkg/module.py"), "**/*.py") is True
    assert _matches_glob(Path("pkg/module.py"), "pkg/*.py") is True
    assert _matches_glob(Path("pkg/module.py"), "*.py") is True
    assert _matches_glob(Path("module.py"), "**/*.py") is True


def test_iter_target_paths_deduplicates_and_skips_ignored_paths(tmp_path: Path) -> None:
    """Expansion should skip ignored dirs, non-files, and repeated matches."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "module.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "stub.pyi").write_text("x: int\n", encoding="utf-8")
    (tmp_path / "pkg" / "notes.txt").write_text("hi\n", encoding="utf-8")
    (tmp_path / "pkg" / "dir.py").mkdir()

    paths = list(
        iter_target_paths(
            ["**/*.py", "**/*.pyi", "pkg/module.py", "pkg/missing.py"],
            root=tmp_path,
        )
    )

    assert paths == [Path("pkg/module.py"), Path("pkg/stub.pyi")]


def test_iter_target_paths_skips_missing_direct_matches_and_duplicate_globs(
    tmp_path: Path,
) -> None:
    """Direct paths should be filtered through is_file and duplicate matches dropped."""
    module = tmp_path / "module.py"
    module.write_text("x = 1\n", encoding="utf-8")

    assert list(
        iter_target_paths(["module.py", "*.py", "missing.py"], root=tmp_path)
    ) == [Path("module.py")]


def test_iter_target_paths_skips_ignored_root_filenames(tmp_path: Path) -> None:
    """Ignored filenames should be filtered even when they are not inside ignored dirs."""
    (tmp_path / "__pycache__").write_text("cached\n", encoding="utf-8")
    (tmp_path / "module.py").write_text("x = 1\n", encoding="utf-8")

    assert list(iter_target_paths(["*"], root=tmp_path)) == [Path("module.py")]


def test_iter_target_paths_rechecks_direct_path_identity(
    monkeypatch, tmp_path: Path
) -> None:
    """The direct-path guard should skip candidates when repeated Path() calls differ."""

    class _FakePath:
        __hash__ = object.__hash__

        def __init__(self, raw: str) -> None:
            self.raw = raw

        def __eq__(self, _other: object) -> bool:
            return False

    monkeypatch.setattr("lib.check_python_compile._has_glob", lambda _pattern: False)
    monkeypatch.setattr("lib.check_python_compile.Path", _FakePath)

    assert list(iter_target_paths(["module.py"], root=tmp_path)) == []


def test_compile_paths_sets_temp_pycache_prefix_and_reports_failures(
    monkeypatch,
) -> None:
    """Compilation should restore sys.pycache_prefix and accumulate failures."""
    seen: list[tuple[str, str | None]] = []
    original_prefix = sys.pycache_prefix
    sys.pycache_prefix = None

    monkeypatch.setattr(
        "lib.check_python_compile.iter_target_paths",
        lambda _patterns: iter([Path("ok.py"), Path("bad.py")]),
    )

    def _fake_compile_file(path: str, *, quiet: int, force: bool) -> bool:
        seen.append((path, sys.pycache_prefix))
        assert quiet == 1
        assert force is True
        return path != "bad.py"

    monkeypatch.setattr(
        "lib.check_python_compile.compileall.compile_file", _fake_compile_file
    )

    try:
        assert compile_paths(["**/*.py"]) is False
    finally:
        sys.pycache_prefix = original_prefix

    assert len(seen) == 2
    assert all(prefix is not None for _path, prefix in seen)
    assert sys.pycache_prefix == original_prefix


def test_compile_paths_preserves_existing_pycache_prefix(monkeypatch) -> None:
    """An existing pycache prefix should be left unchanged while compiling."""
    seen: list[str | None] = []
    original_prefix = sys.pycache_prefix
    sys.pycache_prefix = "/existing-prefix"

    monkeypatch.setattr(
        "lib.check_python_compile.iter_target_paths",
        lambda _patterns: iter([Path("ok.py")]),
    )
    monkeypatch.setattr(
        "lib.check_python_compile.compileall.compile_file",
        lambda _path, **_kwargs: seen.append(sys.pycache_prefix) or True,
    )

    try:
        assert compile_paths(["ok.py"]) is True
    finally:
        sys.pycache_prefix = original_prefix

    assert seen == ["/existing-prefix"]


def test_main_and_parser_cover_cli_surface(monkeypatch) -> None:
    """The parser and main entrypoint should pass through argv cleanly."""
    parser = _build_parser()
    assert parser.parse_args(["lib/**/*.py"]).paths == ["lib/**/*.py"]

    monkeypatch.setattr(
        "lib.check_python_compile.compile_paths", lambda paths: list(paths) == ["ok.py"]
    )

    assert main(["ok.py"]) == 0
    assert main(["bad.py"]) == 1
