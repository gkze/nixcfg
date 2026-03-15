"""Tests for local CI test-pipeline helper module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from lib.tests._assertions import check
from lib.update.ci import test_pipeline as pipeline

if TYPE_CHECKING:
    import pytest


def test_phase_resolve_success_and_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run this test case."""
    pinned = tmp_path / "pinned.json"

    def _resolve_main(argv: list[str]) -> int:
        out = Path(argv[1])
        out.write_text(json.dumps({"a": "1"}), encoding="utf-8")
        return 0

    monkeypatch.setattr("lib.update.ci.resolve_versions.main", _resolve_main)
    check(object.__getattribute__(pipeline, "_phase_resolve")(pinned))

    monkeypatch.setattr("lib.update.ci.resolve_versions.main", lambda _argv: 2)
    check(not object.__getattribute__(pipeline, "_phase_resolve")(pinned))


def test_phase_compute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run this test case."""
    pinned = tmp_path / "pinned.json"
    monkeypatch.setattr("lib.update.cli.run_updates", lambda _opts: 0)
    check(object.__getattribute__(pipeline, "_phase_compute")(pinned))

    monkeypatch.setattr("lib.update.cli.run_updates", lambda _opts: 1)
    check(
        not object.__getattribute__(pipeline, "_phase_compute")(pinned, source="demo")
    )


def test_split_sources_and_merge_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run this test case."""
    work = tmp_path / "work"
    repo_root = tmp_path / "repo"
    src = repo_root / "packages" / "demo" / "sources.json"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(pipeline.Path, "cwd", staticmethod(lambda: repo_root))
    monkeypatch.setattr(pipeline, "package_file_map", lambda _name: {"demo": src})
    roots = object.__getattribute__(pipeline, "_split_sources_by_platform")(work)
    check(set(roots) == set(pipeline.CI_PLATFORMS))
    for root in roots.values():
        copied = root / src.relative_to(Path.cwd())
        check(copied.exists())

    monkeypatch.setattr(pipeline, "_split_sources_by_platform", lambda _work: roots)
    monkeypatch.setattr("lib.update.ci.merge_sources.main", lambda _args: 0)
    check(object.__getattribute__(pipeline, "_phase_merge")(work))

    monkeypatch.setattr("lib.update.ci.merge_sources.main", lambda _args: 1)
    check(not object.__getattribute__(pipeline, "_phase_merge")(work))


def test_phase_validate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run this test case."""
    good = tmp_path / "good.json"
    bad = tmp_path / "bad.json"
    good.write_text(
        json.dumps({
            "hashes": [
                {
                    "hashType": "sha256",
                    "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                }
            ],
            "version": "1",
        }),
        encoding="utf-8",
    )
    bad.write_text("not-json", encoding="utf-8")

    monkeypatch.setattr(pipeline, "package_file_map", lambda _name: {"good": good})
    check(object.__getattribute__(pipeline, "_phase_validate")())

    monkeypatch.setattr(
        pipeline, "package_file_map", lambda _name: {"good": good, "bad": bad}
    )
    check(not object.__getattribute__(pipeline, "_phase_validate")())


def test_main_parses_typer_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Main delegates option parsing to the Typer app."""
    called: dict[str, object] = {}

    def _fake_run(
        *,
        full: bool = False,
        source: str | None = None,
        resolve_only: bool = False,
        keep_artifacts: bool = False,
    ) -> int:
        called.update({
            "full": full,
            "source": source,
            "resolve_only": resolve_only,
            "keep_artifacts": keep_artifacts,
        })
        return 0

    monkeypatch.setattr(pipeline, "run", _fake_run)
    rc = pipeline.main([
        "--full",
        "--source",
        "demo",
        "--resolve-only",
        "--keep-artifacts",
    ])

    check(rc == 0)
    check(called["full"] is True)
    check(called["source"] == "demo")
    check(called["resolve_only"] is True)
    check(called["keep_artifacts"] is True)


def test_print_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Render summary output and cleanup behavior."""
    logs: list[str] = []
    monkeypatch.setattr(pipeline, "_log", logs.append)
    removed: list[Path] = []

    def _rmtree(path: str | Path) -> None:
        removed.append(Path(path))

    monkeypatch.setattr(
        pipeline.shutil,
        "rmtree",
        _rmtree,
    )

    work = tmp_path / "work"
    work.mkdir()
    object.__getattribute__(pipeline, "_print_summary")(
        [("a", True), ("b", False)], work, keep=False
    )
    check(removed == [work])
    check(any("Pipeline FAILED" in line for line in logs))

    logs.clear()
    work2 = tmp_path / "work2"
    work2.mkdir()
    object.__getattribute__(pipeline, "_print_summary")([("a", True)], work2, keep=True)
    check(any("Artifacts kept" in line for line in logs))


def test_main_flow_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run this test case."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(pipeline.tempfile, "mkdtemp", lambda _prefix="": str(work))
    monkeypatch.setattr(pipeline, "_print_summary", lambda *_a, **_k: None)

    # resolve-only path
    monkeypatch.setattr(pipeline, "_phase_resolve", lambda _pinned: True)
    check(pipeline.main(["--resolve-only"]) == 0)

    # resolve failure
    monkeypatch.setattr(pipeline, "_phase_resolve", lambda _pinned: False)
    check(pipeline.main(["--resolve-only"]) == 1)

    # compute failure
    monkeypatch.setattr(pipeline, "_phase_resolve", lambda _pinned: True)
    monkeypatch.setattr(pipeline, "_phase_compute", lambda _pinned, _source=None: False)
    check(pipeline.main(["--source", "demo"]) == 1)

    # full merge failure
    monkeypatch.setattr(pipeline, "_phase_compute", lambda _pinned, _source=None: True)
    monkeypatch.setattr(pipeline, "_phase_merge", lambda _work: False)
    check(pipeline.main(["--full"]) == 1)

    # final validate success/failure
    monkeypatch.setattr(pipeline, "_phase_merge", lambda _work: True)
    monkeypatch.setattr(pipeline, "_phase_validate", lambda: True)
    monkeypatch.setattr(pipeline, "_phase_crate2nix", lambda: True)
    check(pipeline.main(["--full"]) == 0)
    monkeypatch.setattr(pipeline, "_phase_validate", lambda: False)
    check(pipeline.main(["--full"]) == 1)


def test_run_and_print_summary_remaining_branch_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cover run(full=False) and summary branch when work dir is absent."""
    work = tmp_path / "work"
    work.mkdir()
    real_print_summary = pipeline._print_summary
    monkeypatch.setattr(pipeline.tempfile, "mkdtemp", lambda _prefix="": str(work))
    monkeypatch.setattr(pipeline, "_phase_resolve", lambda _pinned: True)
    monkeypatch.setattr(pipeline, "_phase_compute", lambda _pinned, _source=None: True)
    monkeypatch.setattr(pipeline, "_phase_validate", lambda: True)
    monkeypatch.setattr(pipeline, "_phase_crate2nix", lambda: True)
    monkeypatch.setattr(pipeline, "_print_summary", lambda *_a, **_k: None)
    check(
        pipeline.run(full=False, resolve_only=False, source=None, keep_artifacts=False)
        == 0
    )

    logs: list[str] = []
    monkeypatch.setattr(pipeline, "_log", logs.append)
    missing = tmp_path / "missing-work"
    real_print_summary([("a", True)], missing, keep=False)
    check(any("All phases passed" in line for line in logs))


def test_phase_crate2nix_logs_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Log phase results based on the crate2nix command exit code."""
    logs: list[str] = []
    monkeypatch.setattr(pipeline, "_log", logs.append)
    monkeypatch.setattr(pipeline.crate2nix, "main", lambda _args: 0)
    check(pipeline._phase_crate2nix() is True)
    check(any("crate2nix freshness OK" in line for line in logs))

    logs.clear()
    monkeypatch.setattr(pipeline.crate2nix, "main", lambda _args: 9)
    check(pipeline._phase_crate2nix() is False)
    check(any("pipeline crate2nix exited 9" in line for line in logs))
