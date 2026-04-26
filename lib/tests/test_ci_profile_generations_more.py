"""Additional tests for generation profiling helper."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lib.nix.commands.base import CommandResult, ProcessDone, ProcessLine
from lib.update.ci import build_shared_closure as bsc
from lib.update.ci import profile_generations as pg


def test_nix_verbosity_helpers_cover_zero_and_positive() -> None:
    """Map CLI verbosity into nix verbosity flags."""
    assert object.__getattribute__(pg, "_nix_verbosity_args")(0) == []
    assert object.__getattribute__(pg, "_nix_verbosity_args")(2) == ["-vv"]
    assert object.__getattribute__(pg, "_nix_verbosity_from_cli")(0) == 0
    assert object.__getattribute__(pg, "_nix_verbosity_from_cli")(1) == 0
    assert object.__getattribute__(pg, "_nix_verbosity_from_cli")(3) == 2


def test_default_home_profile_uses_user_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build default HM profile path from Path.home()."""
    monkeypatch.setattr(pg.Path, "home", staticmethod(lambda: Path("/tmp/home")))
    path = object.__getattribute__(pg, "_default_home_profile")()
    assert path == "/tmp/home/.local/state/nix/profiles/home-manager"


def test_path_exists_wrapper(tmp_path: Path) -> None:
    """Return true only when target path exists."""
    existing = tmp_path / "exists"
    existing.write_text("x", encoding="utf-8")
    assert object.__getattribute__(pg, "_path_exists")(str(existing)) is True
    assert (
        object.__getattribute__(pg, "_path_exists")(str(tmp_path / "missing")) is False
    )


def test_query_deriver_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve derivation path and reject unknown/failed results."""

    async def _ok(*_args: object, **_kwargs: object) -> CommandResult:
        return CommandResult(
            args=["nix-store"], returncode=0, stdout="/nix/store/demo.drv\n", stderr=""
        )

    monkeypatch.setattr(pg, "run_nix", _ok)
    assert (
        asyncio.run(object.__getattribute__(pg, "_query_deriver")("/profile"))
        == "/nix/store/demo.drv"
    )

    async def _bad_exit(*_args: object, **_kwargs: object) -> CommandResult:
        return CommandResult(args=["nix-store"], returncode=1, stdout="", stderr="err")

    monkeypatch.setattr(pg, "run_nix", _bad_exit)
    try:
        asyncio.run(object.__getattribute__(pg, "_query_deriver")("/profile"))
    except pg.NixCommandError:
        pass
    else:
        raise AssertionError("expected NixCommandError")

    async def _unknown(*_args: object, **_kwargs: object) -> CommandResult:
        return CommandResult(
            args=["nix-store"], returncode=0, stdout="unknown-deriver\n", stderr=""
        )

    monkeypatch.setattr(pg, "run_nix", _unknown)
    with pytest.raises(RuntimeError, match="Could not resolve deriver"):
        asyncio.run(object.__getattribute__(pg, "_query_deriver")("/profile"))


def test_resolve_targets_raises_for_required_missing_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Error when a required target profile path does not exist."""
    monkeypatch.setattr(pg, "_path_exists", lambda _path: False)

    with pytest.raises(RuntimeError, match="Profile path not found"):
        asyncio.run(
            object.__getattribute__(pg, "_resolve_targets")(
                target="system",
                system_profile="/missing",
                home_profile="/unused",
            )
        )


def test_resolve_targets_raises_when_deriver_fails_for_required_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bubble deriver failure for required target selection."""
    monkeypatch.setattr(pg, "_path_exists", lambda _path: True)

    async def _boom(_path: str) -> str:
        msg = "bad deriver"
        raise RuntimeError(msg)

    monkeypatch.setattr(pg, "_query_deriver", _boom)

    with pytest.raises(RuntimeError, match="bad deriver"):
        asyncio.run(
            object.__getattribute__(pg, "_resolve_targets")(
                target="system",
                system_profile="/run/current-system",
                home_profile="/hm",
            )
        )


def test_resolve_targets_skips_optional_home_when_deriver_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip optional home-manager target on deriver failure for target=all."""

    def _exists(path: str) -> bool:
        return path in {"/run/current-system", "/hm"}

    async def _query(path: str) -> str:
        if path == "/hm":
            msg = "hm failed"
            raise RuntimeError(msg)
        return "/nix/store/system.drv"

    monkeypatch.setattr(pg, "_path_exists", _exists)
    monkeypatch.setattr(pg, "_query_deriver", _query)

    resolved = asyncio.run(
        object.__getattribute__(pg, "_resolve_targets")(
            target="all",
            system_profile="/run/current-system",
            home_profile="/hm",
        )
    )
    assert len(resolved) == 1
    assert resolved[0].name == "system"


def test_resolve_targets_raises_when_no_targets_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise explicit no-targets error when optional paths are absent."""
    monkeypatch.setattr(pg, "_path_exists", lambda _path: True)
    monkeypatch.setattr(
        pg, "_query_deriver", lambda _path: asyncio.sleep(0, result="/nix/store/x.drv")
    )
    with pytest.raises(RuntimeError, match="No generation targets resolved"):
        asyncio.run(
            object.__getattribute__(pg, "_resolve_targets")(
                target="none",  # type: ignore[arg-type]
                system_profile="/missing-system",
                home_profile="/missing-home",
            )
        )


def test_build_rebuild_args_respect_custom_substituters() -> None:
    """Include substituter options when public-cache-only is disabled."""
    target = pg.GenerationTarget(
        name="home-manager",
        profile_path="/profiles/hm",
        derivation="/nix/store/dddddddddddddddddddddddddddddddd-home.drv",
    )

    args = object.__getattribute__(pg, "_build_rebuild_args")(
        target,
        nix_verbosity=0,
        public_cache_only=False,
        substituters="https://cache.example",
        extra_substituters="https://cache.extra",
    )

    assert "substituters" in args
    assert "https://cache.example" in args
    assert "extra-substituters" in args
    assert "https://cache.extra" in args
    assert args[-1].endswith(".drv^*")

    args_extra_only = object.__getattribute__(pg, "_build_rebuild_args")(
        target,
        nix_verbosity=0,
        public_cache_only=False,
        substituters=None,
        extra_substituters="https://cache.extra",
    )
    assert "substituters" not in args_extra_only
    assert "extra-substituters" in args_extra_only

    args_sub_only = object.__getattribute__(pg, "_build_rebuild_args")(
        target,
        nix_verbosity=0,
        public_cache_only=False,
        substituters="https://cache.example",
        extra_substituters=None,
    )
    assert "substituters" in args_sub_only
    assert "extra-substituters" not in args_sub_only


def test_resolve_targets_home_only_skips_system_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve only home-manager target when target=home-manager."""
    monkeypatch.setattr(pg, "_path_exists", lambda _path: True)
    monkeypatch.setattr(
        pg, "_query_deriver", lambda _path: asyncio.sleep(0, result="/nix/store/hm.drv")
    )
    resolved = asyncio.run(
        object.__getattribute__(pg, "_resolve_targets")(
            target="home-manager",
            system_profile="/system",
            home_profile="/hm",
        )
    )
    assert len(resolved) == 1
    assert resolved[0].name == "home-manager"


def test_profile_target_returns_false_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle timeout from streamed build process."""
    target = pg.GenerationTarget(
        name="system",
        profile_path="/run/current-system",
        derivation="/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-system.drv",
    )

    async def _timeout(*_args: object, **_kwargs: object):
        raise TimeoutError
        yield ProcessLine(stream="stdout", text="unreachable")

    monkeypatch.setattr(pg, "stream_process", _timeout)

    ok = asyncio.run(
        object.__getattribute__(pg, "_profile_target")(
            target,
            profiler=bsc.BuildProfiler(),
            nix_verbosity=0,
            public_cache_only=True,
            substituters=None,
            extra_substituters=None,
        )
    )
    assert ok is False


def test_profile_target_handles_missing_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return false when stream ends without ProcessDone."""
    target = pg.GenerationTarget(
        name="system",
        profile_path="/run/current-system",
        derivation="/nix/store/ffffffffffffffffffffffffffffffff-system.drv",
    )

    async def _stream(*_args: object, **_kwargs: object):
        yield ProcessLine(stream="stdout", text="log line\n")

    monkeypatch.setattr(pg, "stream_process", _stream)

    ok = asyncio.run(
        object.__getattribute__(pg, "_profile_target")(
            target,
            profiler=bsc.BuildProfiler(),
            nix_verbosity=0,
            public_cache_only=True,
            substituters=None,
            extra_substituters=None,
        )
    )
    assert ok is False


def test_profile_target_handles_nonzero_exit_and_success_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover failed and successful terminal command results."""
    target = pg.GenerationTarget(
        name="system",
        profile_path="/run/current-system",
        derivation="/nix/store/gggggggggggggggggggggggggggggggg-system.drv",
    )

    async def _failed(*_args: object, **_kwargs: object):
        yield ProcessDone(
            result=CommandResult(args=["nix"], returncode=2, stdout="", stderr="")
        )

    monkeypatch.setattr(pg, "stream_process", _failed)
    ok = asyncio.run(
        object.__getattribute__(pg, "_profile_target")(
            target,
            profiler=bsc.BuildProfiler(),
            nix_verbosity=0,
            public_cache_only=True,
            substituters=None,
            extra_substituters=None,
        )
    )
    assert ok is False

    profiler = bsc.BuildProfiler()

    async def _success(*_args: object, **_kwargs: object):
        yield ProcessLine(
            stream="stderr",
            text=(
                '@nix {"action":"start","id":1,"text":"building '
                "'/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-demo.drv'" + '"}\n'
            ),
        )
        yield ProcessLine(stream="stderr", text='@nix {"action":"stop","id":1}\n')
        yield ProcessDone(
            result=CommandResult(args=["nix"], returncode=0, stdout="", stderr="")
        )

    monkeypatch.setattr(pg, "stream_process", _success)
    ok = asyncio.run(
        object.__getattribute__(pg, "_profile_target")(
            target,
            profiler=profiler,
            nix_verbosity=0,
            public_cache_only=True,
            substituters=None,
            extra_substituters=None,
        )
    )
    assert ok is True
    assert len(profiler.events) == 1


def test_async_main_validates_public_cache_flag_combo() -> None:
    """Reject substituter overrides with public-cache-only mode."""
    with pytest.raises(RuntimeError, match="cannot be combined"):
        asyncio.run(
            object.__getattribute__(pg, "_async_main")(
                target="system",
                system_profile="/run/current-system",
                home_profile="/profiles/home-manager",
                profile_output=Path("/tmp/out.json"),
                public_cache_only=True,
                substituters="https://cache.example",
                extra_substituters=None,
                dry_run=True,
                verbosity=0,
            )
        )


def test_async_main_dry_run_logs_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return success in dry-run mode after logging generated commands."""
    target = pg.GenerationTarget(
        name="system",
        profile_path="/run/current-system",
        derivation="/nix/store/hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh-system.drv",
    )
    monkeypatch.setattr(
        pg, "_resolve_targets", lambda **_kwargs: asyncio.sleep(0, result=[target])
    )
    seen: list[str] = []
    monkeypatch.setattr(
        pg.log, "info", lambda msg, *args: seen.append(msg % args if args else msg)
    )

    rc = asyncio.run(
        object.__getattribute__(pg, "_async_main")(
            target="system",
            system_profile="/run/current-system",
            home_profile="/home/test/.local/state/nix/profiles/home-manager",
            profile_output=Path("/tmp/out.json"),
            public_cache_only=False,
            substituters="https://cache.example",
            extra_substituters="https://cache.extra",
            dry_run=True,
            verbosity=2,
        )
    )
    assert rc == 0
    assert any("DRY RUN:" in msg for msg in seen)


def test_run_returns_one_on_top_level_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch runtime failures from async main and return failure code."""
    monkeypatch.setattr(pg.logging, "basicConfig", lambda **_kwargs: None)

    def _boom(coro: object) -> int:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise RuntimeError("boom")

    monkeypatch.setattr(pg.asyncio, "run", _boom)
    assert pg.run() == 1
