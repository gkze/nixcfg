"""Tests for workflow step helpers used by CI."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from lib.update.ci import workflow_steps

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _completed(
    args: list[str],
    *,
    stdout: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout)


def test_generate_pr_body_includes_sources_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render per-package diffs and stable file links in PR body output."""
    (tmp_path / "flake.lock").write_text("{}\n", encoding="utf-8")
    sources_file = tmp_path / "packages/demo/sources.json"
    sources_file.parent.mkdir(parents=True, exist_ok=True)
    sources_file.write_text('{"version":"2.0.0"}\n', encoding="utf-8")
    output_file = tmp_path / "pr-body.md"

    def _fake_run(
        args: list[str],
        *,
        check: bool = True,
        capture_output: bool = False,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        if args == ["git", "show", "HEAD:flake.lock"]:
            return _completed(args, stdout='{"nodes":{}}\n')
        if args == ["git", "ls-files", "--others", "--exclude-standard"]:
            return _completed(args, stdout="")
        if args == [
            "git",
            "diff",
            "--name-only",
            "HEAD",
            "--",
            ":(glob)packages/**/sources.json",
            ":(glob)overlays/**/sources.json",
        ]:
            return _completed(args, stdout="packages/demo/sources.json\n")
        if args == ["git", "show", "HEAD:packages/demo/sources.json"]:
            return _completed(args, stdout='{"version":"1.0.0"}\n')
        msg = f"unexpected command: {args}"
        raise AssertionError(msg)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(workflow_steps, "_run", _fake_run)
    monkeypatch.setattr(workflow_steps, "run_flake_lock_diff", lambda _old, _new: "")
    _unified_diff = (
        "--- old/source-entry.json\n"
        "+++ new/source-entry.json\n"
        "@@ -1,3 +1,3 @@\n"
        " {\n"
        '-  "version": "1.0.0"\n'
        '+  "version": "2.0.0"\n'
        " }"
    )
    monkeypatch.setattr(
        workflow_steps,
        "run_sources_diff",
        lambda _old, _new, output_format: (
            _unified_diff if output_format == "unified" else ""
        ),
    )

    exit_code = workflow_steps.main([
        "generate-pr-body",
        "--output",
        str(output_file),
        "--workflow-url",
        "https://github.com/acme/nixcfg/actions/runs/42",
        "--server-url",
        "https://github.com",
        "--repository",
        "acme/nixcfg",
        "--base-ref",
        "main",
    ])

    assert exit_code == 0  # noqa: S101
    rendered = output_file.read_text(encoding="utf-8")
    assert "No flake.lock input changes detected." in rendered  # noqa: S101
    assert "### Per-package sources.json changes" in rendered  # noqa: S101
    assert '-  "version": "1.0.0"' in rendered  # noqa: S101
    assert '+  "version": "2.0.0"' in rendered  # noqa: S101
    assert (  # noqa: S101
        "https://github.com/acme/nixcfg/blob/update_flake_lock_action/packages/demo/sources.json"
        in rendered
    )
