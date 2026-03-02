"""Additional tests for flake-lock diff helper internals."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lib.nix.models.flake_lock import FlakeLock
from lib.tests._assertions import check
from lib.update.ci import flake_lock_diff as fld

if TYPE_CHECKING:
    import pytest


def _info(**overrides: str) -> fld.InputInfo:
    base = {
        "name": "alpha",
        "type": "github",
        "owner": "example",
        "repo": "alpha",
        "rev": "aaaaaaa",
        "rev_full": "aaaaaaaa",
        "date": "2024-01-01",
    }
    base.update(overrides)
    return fld.InputInfo(**base)


def test_format_helpers_cover_non_github_and_missing_fields() -> None:
    """Render source/revision/compare cells for different input shapes."""
    github_info = _info()
    plain_info = _info(type="path", owner="", repo="", rev_full="", date="")

    check(fld._format_source(github_info) == "example/alpha")
    check(fld._format_source(plain_info) == "alpha")
    check("https://github.com/example/alpha" in fld._format_source_cell(github_info))
    check(fld._format_source_cell(plain_info) == "alpha")
    check(fld._format_rev_date(github_info) == "aaaaaaa (2024-01-01)")
    check(fld._format_rev_date(plain_info) == "aaaaaaa")
    check("/commit/aaaaaaaa" in fld._format_revision_cell(github_info))
    check(fld._format_revision_cell(plain_info) == "aaaaaaa")
    check(
        "/compare/aaaaaaaa...bbbbbbbb"
        in fld._format_compare_cell(
            github_info, _info(rev="bbbbbbb", rev_full="bbbbbbbb")
        )
    )
    check(fld._format_compare_cell(github_info, _info(type="gitlab")) == "-")


def test_append_helpers_and_markdown_escape() -> None:
    """Append sections/tables only when data exists and escape pipes."""
    lines: list[str] = []
    fld._append_section(lines, "### A", [])
    check(lines == [])

    fld._append_section(lines, "### A", ["line"])
    check(lines == ["### A", "line"])

    fld._append_section(lines, "### B", ["line2"])
    check(lines[2] == "")

    fld._append_table(lines, "### T", ["C"], [["x|y"]])
    check(any("x\\|y" in line for line in lines))

    before = list(lines)
    fld._append_table(lines, "### Empty", ["C"], [])
    check(lines == before)


def test_get_input_info_none_and_zero_timestamp() -> None:
    """Return None for missing input and empty date for zero timestamp."""
    lock = FlakeLock.model_validate({
        "nodes": {
            "root": {
                "inputs": {
                    "alpha": "alpha",
                }
            },
            "alpha": {
                "locked": {
                    "type": "github",
                    "owner": "example",
                    "repo": "alpha",
                    "rev": "aaaaaaaa",
                    "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    "lastModified": 0,
                },
                "original": {
                    "type": "github",
                    "owner": "example",
                    "repo": "alpha",
                },
            },
        },
        "root": "root",
        "version": 7,
    })

    check(fld.get_input_info(lock, "missing") is None)
    info = fld.get_input_info(lock, "alpha")
    check(info is not None)
    if info is not None:
        check(info.date == "")


def test_run_and_main_wrappers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Execute run wrapper and delegated main entrypoint."""
    old_path = tmp_path / "old.lock"
    new_path = tmp_path / "new.lock"
    old_path.write_text("{}\n", encoding="utf-8")
    new_path.write_text("{}\n", encoding="utf-8")

    called: dict[str, object] = {}

    def _fake_run_diff(old: Path, new: Path) -> None:
        called["old"] = old
        called["new"] = new

    monkeypatch.setattr(fld, "_run_diff", _fake_run_diff)
    check(fld.run(old_lock=old_path, new_lock=new_path) == 0)
    check(called["old"] == old_path)
    check(called["new"] == new_path)

    monkeypatch.setattr(
        fld,
        "run_main",
        lambda _app, argv=None, prog_name="": (
            23 if prog_name == "flake-lock-diff" else 0
        ),
    )
    check(fld.main(["--help"]) == 23)


def test_run_diff_printer_writes_output_when_nonempty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print run_diff output only when there is content."""
    monkeypatch.setattr(fld, "run_diff", lambda *_a: "DIFF")
    fld._run_diff(Path("old"), Path("new"))
    check(capsys.readouterr().out == "DIFF\n")

    monkeypatch.setattr(fld, "run_diff", lambda *_a: "")
    fld._run_diff(Path("old"), Path("new"))
    check(capsys.readouterr().out == "")


def test_cli_callback_invokes_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise callback wrapper that raises typer.Exit with run code."""
    monkeypatch.setattr(fld, "run", lambda **_kwargs: 7)
    check(fld.main(["old.lock", "new.lock"]) == 7)
