"""Tests for current-generation rebuild profiling helper."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from lib.update.ci import build_shared_closure as bsc
from lib.update.ci import profile_generations as pg

if TYPE_CHECKING:
    from collections.abc import Coroutine

    import pytest


def test_build_rebuild_args_public_cache_only() -> None:
    """Run this test case."""
    target = pg.GenerationTarget(
        name="system",
        profile_path="/run/current-system",
        derivation="/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-system.drv",
    )
    args = object.__getattribute__(pg, "_build_rebuild_args")(
        target,
        nix_verbosity=1,
        public_cache_only=True,
        substituters=None,
        extra_substituters=None,
    )

    assert args[0:2] == ["nix", "build"]
    assert "--rebuild" in args
    assert "--log-format" in args
    assert "internal-json" in args
    assert "https://cache.nixos.org" in args
    assert args[-1] == f"{target.derivation}^*"


def test_resolve_targets_skips_missing_optional_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""

    def _exists(path: str) -> bool:
        return path != "/profiles/home-manager"

    async def _deriver(path: str) -> str:
        return f"/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-{Path(path).name}.drv"

    monkeypatch.setattr(pg, "_path_exists", _exists)
    monkeypatch.setattr(pg, "_query_deriver", _deriver)

    targets = asyncio.run(
        object.__getattribute__(pg, "_resolve_targets")(
            target="all",
            system_profile="/run/current-system",
            home_profile="/profiles/home-manager",
        )
    )

    assert len(targets) == 1
    assert targets[0].name == "system"


def test_async_main_profiles_and_writes_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    target = pg.GenerationTarget(
        name="system",
        profile_path="/run/current-system",
        derivation="/nix/store/cccccccccccccccccccccccccccccccc-system.drv",
    )

    monkeypatch.setattr(
        pg,
        "_resolve_targets",
        lambda **_kwargs: asyncio.sleep(0, result=[target]),
    )

    async def _profile_target(
        _target: pg.GenerationTarget,
        *,
        profiler: bsc.BuildProfiler,
        **_kwargs: object,
    ) -> bool:
        profiler.events.append(
            bsc.BuildProfileEvent(
                derivation="/nix/store/cccccccccccccccccccccccccccccccc-system.drv",
                duration_seconds=1.25,
                completed=True,
            )
        )
        return True

    monkeypatch.setattr(pg, "_profile_target", _profile_target)
    monkeypatch.setattr(pg, "_log_profile_summary", lambda _events: None)

    captured: dict[str, object] = {}

    def _capture_write_report(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(pg, "_write_profile_report", _capture_write_report)

    output_path = Path("artifacts/profile.json")
    rc = asyncio.run(
        object.__getattribute__(pg, "_async_main")(
            target="system",
            system_profile="/run/current-system",
            home_profile="/profiles/home-manager",
            profile_output=output_path,
            public_cache_only=True,
            dry_run=False,
            verbosity=0,
        )
    )

    assert rc == 0
    assert captured["output_path"] == output_path
    assert captured["flake_refs"] == ["/run/current-system"]
    assert captured["derivation_count"] == 1


def test_main_enables_debug_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        pg.logging, "basicConfig", lambda **kwargs: calls.append(kwargs)
    )

    def _run(coro: Coroutine[object, object, object]) -> int:
        coro.close()
        return 0

    monkeypatch.setattr(pg.asyncio, "run", _run)

    rc = pg.main(["--verbose"])
    assert rc == 0
    assert calls[-1]["level"] == pg.logging.DEBUG
