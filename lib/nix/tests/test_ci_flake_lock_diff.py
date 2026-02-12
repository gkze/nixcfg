"""Tests for flake.lock diff rendering used in CI PR bodies."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from lib.update.ci.flake_lock_diff import run_diff

if TYPE_CHECKING:
    from pathlib import Path


def _write_flake_lock(path: Path, *, revs: dict[str, str]) -> None:
    nodes: dict[str, object] = {
        "root": {
            "inputs": {name: name for name in sorted(revs)},
        },
    }
    for name, rev in revs.items():
        nodes[name] = {
            "locked": {
                "type": "github",
                "owner": "example",
                "repo": name,
                "rev": rev,
                "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                "lastModified": 1700000000,
            },
            "original": {
                "type": "github",
                "owner": "example",
                "repo": name,
            },
        }

    payload = {
        "nodes": nodes,
        "root": "root",
        "version": 7,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_run_diff_renders_updated_added_and_removed_inputs(tmp_path: Path) -> None:
    """Render a human-readable summary for changed flake inputs."""
    old_lock = tmp_path / "old-flake.lock"
    new_lock = tmp_path / "new-flake.lock"
    _write_flake_lock(old_lock, revs={"alpha": "aaaaaaaa", "beta": "bbbbbbbb"})
    _write_flake_lock(new_lock, revs={"alpha": "cccccccc", "gamma": "dddddddd"})

    diff = run_diff(old_lock, new_lock)

    assert "### Updated flake inputs" in diff  # noqa: S101
    assert "| Input | Source | From | To | Diff |" in diff  # noqa: S101
    assert (  # noqa: S101
        "| alpha | [example/alpha](https://github.com/example/alpha) | "
        "[aaaaaaa](https://github.com/example/alpha/commit/aaaaaaaa) | "
        "[ccccccc](https://github.com/example/alpha/commit/cccccccc) | "
        "[Diff](https://github.com/example/alpha/compare/aaaaaaaa...cccccccc) |" in diff
    )
    assert "### Added flake inputs" in diff  # noqa: S101
    assert (  # noqa: S101
        "| gamma | [example/gamma](https://github.com/example/gamma) | "
        "[ddddddd](https://github.com/example/gamma/commit/dddddddd) |" in diff
    )
    assert "### Removed flake inputs" in diff  # noqa: S101
    assert (  # noqa: S101
        "| beta | [example/beta](https://github.com/example/beta) | "
        "[bbbbbbb](https://github.com/example/beta/commit/bbbbbbbb) |" in diff
    )


def test_run_diff_returns_empty_string_when_no_changes(tmp_path: Path) -> None:
    """Return an empty string when revisions are unchanged."""
    old_lock = tmp_path / "old-flake.lock"
    new_lock = tmp_path / "new-flake.lock"
    _write_flake_lock(old_lock, revs={"alpha": "aaaaaaaa"})
    _write_flake_lock(new_lock, revs={"alpha": "aaaaaaaa"})

    diff = run_diff(old_lock, new_lock)

    assert diff == ""  # noqa: S101
