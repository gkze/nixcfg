"""Behavioral tests for the canonical sources.json diff contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lib.update.ci import sources_json_diff


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_run_diff_returns_no_changes_for_semantically_equal_json(
    tmp_path: Path,
) -> None:
    """Ignore source formatting and object-key order."""
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    old_file.write_text('{"version":"1.0.0","input":"demo"}', encoding="utf-8")
    new_file.write_text(
        '{\n  "input": "demo",\n  "version": "1.0.0"\n}\n',
        encoding="utf-8",
    )

    assert (
        sources_json_diff.run_diff(old_file, new_file)
        == "No source entry changes detected."
    )


def test_run_diff_renders_canonical_unified_json(tmp_path: Path) -> None:
    """Render one deterministic, in-process diff for changed source entries."""
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    _write_json(old_file, {"version": "1.0.0"})
    _write_json(new_file, {"version": "1.1.0"})

    assert sources_json_diff.run_diff(old_file, new_file) == (
        "--- old/source-entry.json\n"
        "+++ new/source-entry.json\n"
        "@@ -1,3 +1,3 @@\n"
        " {\n"
        '-  "version": "1.0.0"\n'
        '+  "version": "1.1.0"\n'
        " }"
    )


def test_run_diff_requires_source_entry_objects(tmp_path: Path) -> None:
    """Reject top-level JSON values that cannot be a sources.json entry."""
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    _write_json(old_file, [])
    _write_json(new_file, {})

    with pytest.raises(TypeError, match="Expected JSON object"):
        sources_json_diff.run_diff(old_file, new_file)


def test_cli_prints_the_canonical_diff_without_format_selection(tmp_path: Path) -> None:
    """Expose the same single diff contract through the public command."""
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    _write_json(old_file, {"version": "1"})
    _write_json(new_file, {"version": "2"})

    result = CliRunner().invoke(
        sources_json_diff.app,
        [str(old_file), str(new_file)],
    )

    assert result.exit_code == 0
    assert result.output.startswith("--- old/source-entry.json\n")
    assert '-  "version": "1"' in result.output
    assert '+  "version": "2"' in result.output
