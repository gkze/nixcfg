"""Tests for local CI test-pipeline helper module."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from lib.nix.tests._assertions import check
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


def test_parse_args_and_print_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run this test case."""
    args = object.__getattribute__(pipeline, "_parse_args")([
        "--full",
        "--source",
        "demo",
        "--resolve-only",
        "--keep-artifacts",
    ])
    if not args.full and args.resolve_only and args.keep_artifacts:
        raise AssertionError
    check(args.source == "demo")

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
    monkeypatch.setattr(pipeline.tempfile, "mkdtemp", lambda _prefix="": str(work))
    monkeypatch.setattr(pipeline, "_print_summary", lambda *_a, **_k: None)

    # resolve-only path
    monkeypatch.setattr(
        pipeline,
        "_parse_args",
        lambda _argv: SimpleNamespace(
            full=False, source=None, resolve_only=True, keep_artifacts=False
        ),
    )
    monkeypatch.setattr(pipeline, "_phase_resolve", lambda _pinned: True)
    check(pipeline.main([]) == 0)

    # resolve failure
    monkeypatch.setattr(pipeline, "_phase_resolve", lambda _pinned: False)
    check(pipeline.main([]) == 1)

    # compute failure
    monkeypatch.setattr(
        pipeline,
        "_parse_args",
        lambda _argv: SimpleNamespace(
            full=False, source="demo", resolve_only=False, keep_artifacts=False
        ),
    )
    monkeypatch.setattr(pipeline, "_phase_resolve", lambda _pinned: True)
    monkeypatch.setattr(pipeline, "_phase_compute", lambda _pinned, _source=None: False)
    check(pipeline.main([]) == 1)

    # full merge failure
    monkeypatch.setattr(
        pipeline,
        "_parse_args",
        lambda _argv: SimpleNamespace(
            full=True, source=None, resolve_only=False, keep_artifacts=False
        ),
    )
    monkeypatch.setattr(pipeline, "_phase_compute", lambda _pinned, _source=None: True)
    monkeypatch.setattr(pipeline, "_phase_merge", lambda _work: False)
    check(pipeline.main([]) == 1)

    # final validate success/failure
    monkeypatch.setattr(pipeline, "_phase_merge", lambda _work: True)
    monkeypatch.setattr(pipeline, "_phase_validate", lambda: True)
    check(pipeline.main([]) == 0)
    monkeypatch.setattr(pipeline, "_phase_validate", lambda: False)
    check(pipeline.main([]) == 1)
