"""Tests for warm-fod-cache CI helper."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from lib.nix.commands.base import CommandResult, NixCommandError
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import check
from lib.update.ci import warm_fod_cache as wfc

if TYPE_CHECKING:
    from collections.abc import Coroutine

    import pytest


def test_format_duration_and_detect_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    check(object.__getattribute__(wfc, "_format_duration")(2.0) == "2.0s")
    check(object.__getattribute__(wfc, "_format_duration")(90) == "1m 30s")
    check(object.__getattribute__(wfc, "_format_duration")(4000) == "1h 6m")

    monkeypatch.setattr(
        wfc, "normalize_nix_platform", lambda machine, system: f"{machine}-{system}"
    )
    monkeypatch.setattr(wfc.platform, "machine", lambda: "x86")
    monkeypatch.setattr(wfc.platform, "system", lambda: "linux")
    check(object.__getattribute__(wfc, "_detect_system")() == "x86-linux")


def test_platform_entries_and_find_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    entry = SourceEntry(
        version="1",
        hashes=HashCollection(
            entries=[
                HashEntry.create(
                    "nodeModulesHash",
                    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    platform="x86_64-linux",
                ),
                HashEntry.create(
                    "sha256", "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
                ),
            ]
        ),
    )
    hits = object.__getattribute__(wfc, "_platform_fod_entries")(entry, "x86_64-linux")
    check(len(hits) == 1)

    monkeypatch.setattr(
        wfc,
        "package_file_map",
        lambda _name: {"demo": Path("path")},
    )
    monkeypatch.setattr("lib.update.sources.load_source_entry", lambda _path: entry)
    targets = object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux")
    check(len(targets) == 1)
    check(targets[0].package == "demo")
    check(targets[0].fod_attr == ".node_modules")

    monkeypatch.setattr(
        wfc,
        "package_file_map",
        lambda _name: {"mux": Path("path")},
    )
    targets = object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux")
    check(len(targets) == 1)
    check(targets[0].package == "mux")
    check(targets[0].fod_attr == ".offlineCache")

    bad_entry = SourceEntry(
        version="1",
        hashes=HashCollection(
            entries=[
                HashEntry.create(
                    "denoDepsHash",
                    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    platform="x86_64-linux",
                ),
            ]
        ),
    )
    monkeypatch.setattr("lib.update.sources.load_source_entry", lambda _path: bad_entry)
    check(object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux") == [])

    no_entries = SourceEntry(version="1", hashes=HashCollection(mapping={"x": "y"}))
    check(
        object.__getattribute__(wfc, "_platform_fod_entries")(
            no_entries, "x86_64-linux"
        )
        == []
    )

    def _boom(_path: Path) -> SourceEntry:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr("lib.update.sources.load_source_entry", _boom)
    check(object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux") == [])


def test_build_fod_expr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    monkeypatch.setattr(
        wfc,
        "_build_overlay_attr_expr",
        lambda package, attr_path, *, system=None: (
            f"overlay-attr:{package}:{attr_path}:{system}"
        ),
    )
    expr = object.__getattribute__(wfc, "_build_fod_expr")(
        "demo", ".node_modules", system="x86_64-linux"
    )
    check(expr == "overlay-attr:demo:.node_modules:x86_64-linux")


def test_resolve_output_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    async def _ok_run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(
            args=["nix"],
            returncode=0,
            stdout=(
                '[{"outputs": {"out": "/nix/store/abc"}}, '
                '{"outputs": {"doc": "/tmp/not-store"}}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("lib.nix.commands.base.run_nix", _ok_run_nix)
    paths = asyncio.run(object.__getattribute__(wfc, "_resolve_output_paths")("expr"))
    check(paths == ["/nix/store/abc"])

    async def _bad_run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix"], returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr("lib.nix.commands.base.run_nix", _bad_run_nix)
    check(
        asyncio.run(object.__getattribute__(wfc, "_resolve_output_paths")("expr")) == []
    )

    async def _bad_json_run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix"], returncode=0, stdout="not-json", stderr="")

    monkeypatch.setattr("lib.nix.commands.base.run_nix", _bad_json_run_nix)
    check(
        asyncio.run(object.__getattribute__(wfc, "_resolve_output_paths")("expr")) == []
    )

    async def _mixed_json(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(
            args=["nix"],
            returncode=0,
            stdout='["not-dict", {"outputs": {"out": "/nix/store/ok"}}]',
            stderr="",
        )

    monkeypatch.setattr("lib.nix.commands.base.run_nix", _mixed_json)
    check(
        asyncio.run(object.__getattribute__(wfc, "_resolve_output_paths")("expr"))
        == ["/nix/store/ok"]
    )


def test_push_to_cachix_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    check(
        asyncio.run(object.__getattribute__(wfc, "_push_to_cachix")([], "cache"))
        is True
    )

    monkeypatch.setattr(wfc.shutil, "which", lambda _name: None)
    check(
        asyncio.run(
            object.__getattribute__(wfc, "_push_to_cachix")(["/nix/store/a"], "cache")
        )
        is False
    )

    monkeypatch.setattr(wfc.shutil, "which", lambda _name: "/usr/bin/cachix")

    class _Proc:
        def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self) -> tuple[bytes, bytes]:
            """Run this test case."""
            return object.__getattribute__(self, "_stdout"), object.__getattribute__(
                self, "_stderr"
            )

    monkeypatch.setattr(
        wfc.asyncio,
        "create_subprocess_exec",
        lambda *_a, **_k: asyncio.sleep(0, result=_Proc(1, b"", b"bad")),
    )
    check(
        asyncio.run(
            object.__getattribute__(wfc, "_push_to_cachix")(["/nix/store/a"], "cache")
        )
        is False
    )

    monkeypatch.setattr(
        wfc.asyncio,
        "create_subprocess_exec",
        lambda *_a, **_k: asyncio.sleep(0, result=_Proc(0, b"ok", b"")),
    )
    check(
        asyncio.run(
            object.__getattribute__(wfc, "_push_to_cachix")(["/nix/store/a"], "cache")
        )
        is True
    )

    monkeypatch.setattr(
        wfc.asyncio,
        "create_subprocess_exec",
        lambda *_a, **_k: asyncio.sleep(0, result=_Proc(0, b"", b"")),
    )
    check(
        asyncio.run(
            object.__getattribute__(wfc, "_push_to_cachix")(["/nix/store/a"], "cache")
        )
        is True
    )


def test_build_one_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    target = wfc.FodTarget(
        package="demo", hash_type="nodeModulesHash", fod_attr=".node_modules"
    )
    monkeypatch.setattr(wfc, "_build_fod_expr", lambda *_args, **_kwargs: "expr")
    monkeypatch.setattr(wfc, "nix_build", lambda **_k: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(
        wfc,
        "_resolve_output_paths",
        lambda _expr: asyncio.sleep(0, result=["/nix/store/a"]),
    )
    pushed: list[tuple[list[str], str]] = []
    monkeypatch.setattr(
        wfc,
        "_push_to_cachix",
        lambda paths, cache: asyncio.sleep(
            0, result=pushed.append((paths, cache)) is None or True
        ),
    )

    check(
        asyncio.run(
            object.__getattribute__(wfc, "_build_one")(
                target, "x86_64-linux", cache_name="cache"
            )
        )
        is True
    )

    check(
        asyncio.run(
            object.__getattribute__(wfc, "_build_one")(
                target, "x86_64-linux", cache_name=None
            )
        )
        is True
    )
    check(pushed == [(["/nix/store/a"], "cache")])

    monkeypatch.setattr(
        wfc, "_resolve_output_paths", lambda _expr: asyncio.sleep(0, result=[])
    )
    check(
        asyncio.run(
            object.__getattribute__(wfc, "_build_one")(
                target, "x86_64-linux", cache_name="cache"
            )
        )
        is True
    )

    async def _fail_build(**_k: object) -> list[object]:
        raise NixCommandError(
            CommandResult(args=["nix"], returncode=1, stdout="", stderr=""), "fail"
        )

    monkeypatch.setattr(wfc, "nix_build", _fail_build)
    check(
        asyncio.run(
            object.__getattribute__(wfc, "_build_one")(
                target, "x86_64-linux", cache_name=None
            )
        )
        is False
    )


def test_async_main_and_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    monkeypatch.setattr(wfc, "_detect_system", lambda: "x86_64-linux")
    monkeypatch.delenv("CACHIX_NAME", raising=False)
    monkeypatch.setattr(wfc, "_find_fod_targets", lambda _system: [])
    check(
        asyncio.run(
            object.__getattribute__(wfc, "_async_main")(
                system=None,
                dry_run=False,
                cachix_cache=None,
            )
        )
        == 0
    )

    targets = [
        wfc.FodTarget(
            package="demo", hash_type="nodeModulesHash", fod_attr=".node_modules"
        )
    ]
    monkeypatch.setattr(wfc, "_find_fod_targets", lambda _system: targets)

    check(
        asyncio.run(
            object.__getattribute__(wfc, "_async_main")(
                system="x86_64-linux",
                dry_run=True,
                cachix_cache="cache",
            )
        )
        == 0
    )

    monkeypatch.setattr(
        wfc, "_build_one", lambda *_a, **_k: asyncio.sleep(0, result=True)
    )
    check(
        asyncio.run(
            object.__getattribute__(wfc, "_async_main")(
                system="x86_64-linux",
                dry_run=False,
                cachix_cache="cache",
            )
        )
        == 0
    )

    monkeypatch.setattr(
        wfc, "_build_one", lambda *_a, **_k: asyncio.sleep(0, result=False)
    )
    check(
        asyncio.run(
            object.__getattribute__(wfc, "_async_main")(
                system="x86_64-linux",
                dry_run=False,
                cachix_cache="cache",
            )
        )
        == 1
    )

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        wfc.logging, "basicConfig", lambda **kwargs: calls.append(kwargs)
    )

    def _run(coro: Coroutine[object, object, object]) -> int:
        coro.close()
        return 0

    monkeypatch.setattr(wfc.asyncio, "run", _run)
    rc = wfc.main(["--verbose"])
    check(rc == 0)
    check(calls[-1]["level"] == wfc.logging.DEBUG)
