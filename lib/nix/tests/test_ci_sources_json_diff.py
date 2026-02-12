"""Tests for per-package sources.json diff rendering."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

from lib.update.ci.sources_json_diff import run_diff

if TYPE_CHECKING:
    from pathlib import Path


class _MonkeyPatchLike(Protocol):
    def setattr(self, target: str, value: object) -> None: ...


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_run_diff_returns_no_changes_message(tmp_path: Path) -> None:
    """Return stable no-change text when JSON payloads are equal."""
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    payload = {"version": "1.0.0", "hashes": [{"hashType": "sha256", "hash": "abc"}]}
    _write_json(old_file, payload)
    _write_json(new_file, payload)

    diff = run_diff(old_file, new_file)

    assert diff == "No source entry changes detected."  # noqa: S101


def test_run_diff_prefers_jd_output_when_available(
    tmp_path: Path,
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Use jd output first so CLI diffs align with jd style."""
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    _write_json(old_file, {"version": "1.0.0"})
    _write_json(new_file, {"version": "1.1.0"})

    monkeypatch.setattr(
        "lib.update.ci.sources_json_diff._render_jd_diff",
        lambda _old_path, _new_path: '@ ["version"]\n- "1.0.0"\n+ "1.1.0"',
    )

    diff = run_diff(old_file, new_file)

    assert diff.startswith('@ ["version"]')  # noqa: S101
    assert '+ "1.1.0"' in diff  # noqa: S101


def test_run_diff_uses_structural_fallback_when_jd_not_available(
    tmp_path: Path,
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Render path-based hunks when jd and graphtage are unavailable."""
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    _write_json(old_file, {"hashes": [{"hash": "old"}]})
    _write_json(new_file, {"hashes": [{"hash": "new"}]})

    monkeypatch.setattr(
        "lib.update.ci.sources_json_diff._render_jd_diff",
        lambda _old_path, _new_path: "",
    )
    monkeypatch.setattr(
        "lib.update.ci.sources_json_diff._render_graphtage_diff",
        lambda _old_data, _new_data: "",
    )

    diff = run_diff(old_file, new_file)

    assert '@ ["hashes", 0, "hash"]' in diff  # noqa: S101
    assert '- "old"' in diff  # noqa: S101
    assert '+ "new"' in diff  # noqa: S101


def test_run_diff_explicit_jd_format_falls_back_when_jd_unavailable(
    tmp_path: Path,
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Avoid false no-change output when --format jd is unavailable."""
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    _write_json(old_file, {"version": "1.0.0"})
    _write_json(new_file, {"version": "1.1.0"})

    monkeypatch.setattr(
        "lib.update.ci.sources_json_diff._render_jd_diff",
        lambda _old_path, _new_path: "",
    )

    diff = run_diff(old_file, new_file, output_format="jd")

    assert diff != "No source entry changes detected."  # noqa: S101
    assert '@ ["version"]' in diff  # noqa: S101


def test_run_diff_summary_format_is_legible(tmp_path: Path) -> None:
    """Render concise field-level summary output for PR body readability."""
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    _write_json(
        old_file,
        {
            "version": "1.0.0",
            "hashes": {"x86_64-linux": "oldhash"},
        },
    )
    _write_json(
        new_file,
        {
            "version": "1.1.0",
            "hashes": {"x86_64-linux": "newhash", "aarch64-darwin": "darwinhash"},
        },
    )

    diff = run_diff(old_file, new_file, output_format="summary")

    assert 'changed version: "1.0.0" -> "1.1.0"' in diff  # noqa: S101
    assert (  # noqa: S101
        'changed hashes.x86_64-linux: "oldhash" -> "newhash"' in diff
    )
    assert 'added hashes.aarch64-darwin: "darwinhash"' in diff  # noqa: S101


def test_run_diff_summary_format_handles_removed_fields(tmp_path: Path) -> None:
    """Render removed entries clearly in summary mode."""
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    _write_json(old_file, {"input": "demo", "urls": {"linux": "https://example.test"}})
    _write_json(new_file, {"input": "demo"})

    diff = run_diff(old_file, new_file, output_format="summary")

    assert 'removed urls.linux: "https://example.test"' in diff  # noqa: S101
