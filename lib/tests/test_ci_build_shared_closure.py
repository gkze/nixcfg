"""Tests for shared closure build helper."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from lib.nix.commands.base import CommandResult, NixCommandError
from lib.update.ci import build_shared_closure as bsc

if TYPE_CHECKING:
    from collections.abc import Coroutine

    import pytest


_async_main = bsc._async_main
_build_derivations = bsc._build_derivations
_collect_derivations = bsc._collect_derivations
_eval_one = bsc._eval_one
_format_duration = bsc._format_duration


def test_format_duration() -> None:
    """Format short, minute, and hour durations for CI logs."""
    assert _format_duration(2.3) == "2.3s"
    assert _format_duration(90) == "1m 30s"
    assert _format_duration(4000) == "1h 6m"


def test_eval_and_collect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluate one ref and collect derivations from multiple refs."""

    async def _dry_run(ref: str, **kwargs: object) -> set[str]:
        impure = kwargs.get("impure", True)
        timeout_s = kwargs.get("timeout", 0.0)
        _ = (impure, timeout_s)
        return {f"{ref}-a", f"{ref}-b"}

    monkeypatch.setattr(bsc, "nix_build_dry_run", _dry_run)
    drvs = asyncio.run(_eval_one(".#x"))
    assert drvs == {".#x-a", ".#x-b"}

    all_drvs = asyncio.run(_collect_derivations([".#a", ".#b"]))
    assert ".#a-a" in all_drvs
    assert ".#b-b" in all_drvs


def test_build_derivations_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover empty, dry-run, success, failure, and timeout build branches."""
    assert asyncio.run(_build_derivations(set())) is True
    assert asyncio.run(_build_derivations({"/nix/store/a.drv"}, dry_run=True)) is True

    async def _ok_realise(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix-store"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bsc, "nix_store_realise", _ok_realise)
    assert (
        asyncio.run(
            _build_derivations({
                "/nix/store/a.drv",
                "/nix/store/b.drv",
            })
        )
        is True
    )

    async def _bad_realise(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix-store"], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(bsc, "nix_store_realise", _bad_realise)
    assert asyncio.run(_build_derivations({"/nix/store/a.drv"})) is False

    async def _timeout_realise(*_a: object, **_k: object) -> CommandResult:
        raise NixCommandError(
            CommandResult(args=["nix-store"], returncode=-1, stdout="", stderr=""),
            "timeout",
        )

    monkeypatch.setattr(bsc, "nix_store_realise", _timeout_realise)
    assert asyncio.run(_build_derivations({"/nix/store/a.drv"})) is False


def test_build_profiler_tracks_check_activity() -> None:
    """Profile Nix check-output activity from internal-json log events."""
    profiler = bsc.BuildProfiler()
    expected_duration = 3.5
    profiler.ingest_line(
        (
            '@nix {"action":"start","id":7,"text":"checking outputs of '
            "'/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-demo.drv'" + '"}'
        ),
        now=10.0,
    )
    profiler.ingest_line('@nix {"action":"stop","id":7}', now=13.5)

    assert len(profiler.events) == 1
    assert profiler.events[0].derivation.endswith("-demo.drv")
    assert round(profiler.events[0].duration_seconds, 1) == expected_duration
    assert profiler.events[0].completed is True


def test_async_main_and_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Propagate async main status and configure CLI logging verbosity."""

    async def _collect(
        _refs: list[str],
        *,
        mode: str = "union",
        nix_verbosity: int = 0,
    ) -> set[str]:
        _ = (mode, nix_verbosity)
        return {"/nix/store/a.drv"}

    async def _build(
        _drvs: set[str],
        *,
        dry_run: bool = False,
        nix_verbosity: int = 0,
        profiler: object | None = None,
    ) -> bool:
        _ = (nix_verbosity, profiler)
        return not dry_run

    monkeypatch.setattr(bsc, "_collect_derivations", _collect)
    monkeypatch.setattr(bsc, "_build_derivations", _build)

    assert (
        asyncio.run(
            _async_main(
                flake_refs=[".#a"],
                dry_run=False,
            )
        )
        == 0
    )
    assert (
        asyncio.run(
            _async_main(
                flake_refs=[".#a"],
                dry_run=True,
            )
        )
        == 1
    )
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        bsc.logging, "basicConfig", lambda **kwargs: calls.append(kwargs)
    )

    def _run(coro: Coroutine[object, object, object]) -> int:
        coro.close()
        return 0

    monkeypatch.setattr(bsc.asyncio, "run", _run)
    rc = bsc.main([".#a", "--verbose"])
    assert rc == 0
    assert calls[-1]["level"] == bsc.logging.DEBUG
