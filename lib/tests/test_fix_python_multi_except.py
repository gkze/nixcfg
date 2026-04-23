"""Tests for the multi-except normalization pre-pass."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.fix_python_multi_except import (
    _build_parser,
    _normalize_multi_except_line,
    main,
    normalize_multi_except_path,
    normalize_multi_except_text,
)


def test_normalize_multi_except_line_handles_alias_comments_and_newlines() -> None:
    """Single-line invalid multi-excepts should gain parentheses and preserve trivia."""
    assert (
        _normalize_multi_except_line("except ValueError, TypeError as exc:  # note\n")
        == "except (ValueError, TypeError) as exc:  # note\n"
    )
    assert _normalize_multi_except_line("except (ValueError, TypeError):\n") == (
        "except (ValueError, TypeError):\n"
    )
    assert (
        _normalize_multi_except_line("except ValueError:\n") == "except ValueError:\n"
    )


def test_normalize_multi_except_text_and_path_cover_change_detection(
    tmp_path: Path,
) -> None:
    """Text and path helpers should rewrite only invalid single-line clauses."""
    assert (
        normalize_multi_except_text("except A, B:\npass\n") == "except (A, B):\npass\n"
    )

    changed = tmp_path / "changed.py"
    changed.write_text("except A, B:\n    pass\n", encoding="utf-8")
    assert normalize_multi_except_path(changed) is True
    assert changed.read_text(encoding="utf-8") == "except (A, B):\n    pass\n"

    clean = tmp_path / "clean.py"
    clean.write_text("except ValueError:\n    pass\n", encoding="utf-8")
    assert normalize_multi_except_path(clean) is False


def test_main_optionally_runs_pyupgrade(monkeypatch, tmp_path: Path) -> None:
    """CLI should normalize paths and invoke pyupgrade only when requested."""
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("except A, B:\n    pass\n", encoding="utf-8")
    second.write_text("except C, D:\n    pass\n", encoding="utf-8")

    calls: list[object] = []
    monkeypatch.setattr(
        "lib.fix_python_multi_except.subprocess.run",
        lambda args, *, check: calls.append((args, check)),
    )

    assert (
        main([
            "--pyupgrade-exe",
            "pyupgrade",
            "--pyupgrade-arg=--py311-plus",
            str(first),
            str(second),
        ])
        == 0
    )
    assert first.read_text(encoding="utf-8") == "except (A, B):\n    pass\n"
    assert second.read_text(encoding="utf-8") == "except (C, D):\n    pass\n"
    assert calls == [
        (
            [
                "pyupgrade",
                "--py311-plus",
                str(first),
                str(second),
            ],
            True,
        )
    ]


def test_main_skips_pyupgrade_without_paths(monkeypatch) -> None:
    """Pyupgrade should not run when no paths were provided."""
    called = False

    def _fake_run(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("lib.fix_python_multi_except.subprocess.run", _fake_run)

    assert main(["--pyupgrade-exe", "pyupgrade"]) == 0
    assert called is False


def test_parser_and_main_cover_remaining_cli_shapes(tmp_path: Path) -> None:
    """The CLI should parse forwarded args and skip pyupgrade when unset."""
    parser = _build_parser()
    args = parser.parse_args(["--pyupgrade-arg=--py313-plus", "demo.py"])
    assert args.pyupgrade_arg == ["--py313-plus"]
    assert args.paths == ["demo.py"]

    path = tmp_path / "demo.py"
    path.write_text("except A, B:\n    pass\n", encoding="utf-8")
    assert main([str(path)]) == 0
    assert path.read_text(encoding="utf-8") == "except (A, B):\n    pass\n"


def test_parser_rejects_missing_pyupgrade_arg_value() -> None:
    """The CLI should surface argparse errors for incomplete forwarded args."""
    with pytest.raises(SystemExit, match="2"):
        _build_parser().parse_args(["--pyupgrade-arg"])
