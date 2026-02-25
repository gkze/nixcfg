"""Tests for shared closure build helper."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from lib.nix.commands.base import CommandResult, NixCommandError
from lib.nix.tests._assertions import check
from lib.update.ci import build_shared_closure as bsc

if TYPE_CHECKING:
    from collections.abc import Coroutine

    import pytest


def test_format_duration() -> None:
    """Run this test case."""
    check(object.__getattribute__(bsc, "_format_duration")(2.3) == "2.3s")
    check(object.__getattribute__(bsc, "_format_duration")(90) == "1m 30s")
    check(object.__getattribute__(bsc, "_format_duration")(4000) == "1h 6m")


def test_eval_and_collect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    async def _dry_run(ref: str, **kwargs: object) -> set[str]:
        impure = kwargs.get("impure", True)
        timeout_s = kwargs.get("timeout", 0.0)
        _ = (impure, timeout_s)
        return {f"{ref}-a", f"{ref}-b"}

    monkeypatch.setattr(bsc, "nix_build_dry_run", _dry_run)
    drvs = asyncio.run(object.__getattribute__(bsc, "_eval_one")(".#x"))
    check(drvs == {".#x-a", ".#x-b"})

    all_drvs = asyncio.run(
        object.__getattribute__(bsc, "_collect_derivations")([".#a", ".#b"])
    )
    check(".#a-a" in all_drvs)
    check(".#b-b" in all_drvs)


def test_build_derivations_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    check(
        asyncio.run(object.__getattribute__(bsc, "_build_derivations")(set())) is True
    )
    check(
        asyncio.run(
            object.__getattribute__(bsc, "_build_derivations")(
                {"/nix/store/a.drv"}, dry_run=True
            )
        )
        is True
    )

    async def _ok_realise(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix-store"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bsc, "nix_store_realise", _ok_realise)
    check(
        asyncio.run(
            object.__getattribute__(bsc, "_build_derivations")({
                "/nix/store/a.drv",
                "/nix/store/b.drv",
            })
        )
        is True
    )

    async def _bad_realise(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix-store"], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(bsc, "nix_store_realise", _bad_realise)
    check(
        asyncio.run(
            object.__getattribute__(bsc, "_build_derivations")({"/nix/store/a.drv"})
        )
        is False
    )

    async def _timeout_realise(*_a: object, **_k: object) -> CommandResult:
        raise NixCommandError(
            CommandResult(args=["nix-store"], returncode=-1, stdout="", stderr=""),
            "timeout",
        )

    monkeypatch.setattr(bsc, "nix_store_realise", _timeout_realise)
    check(
        asyncio.run(
            object.__getattribute__(bsc, "_build_derivations")({"/nix/store/a.drv"})
        )
        is False
    )


def test_async_main_and_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    async def _collect(_refs: list[str]) -> set[str]:
        return {"/nix/store/a.drv"}

    async def _build(_drvs: set[str], *, dry_run: bool = False) -> bool:
        return not dry_run

    monkeypatch.setattr(bsc, "_collect_derivations", _collect)
    monkeypatch.setattr(bsc, "_build_derivations", _build)

    check(
        asyncio.run(
            object.__getattribute__(bsc, "_async_main")(
                flake_refs=[".#a"],
                dry_run=False,
            )
        )
        == 0
    )
    check(
        asyncio.run(
            object.__getattribute__(bsc, "_async_main")(
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
    check(rc == 0)
    check(calls[-1]["level"] == bsc.logging.DEBUG)
