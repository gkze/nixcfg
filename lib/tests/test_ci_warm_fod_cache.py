"""Tests for warm-fod-cache CI helper."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lib.nix.commands.base import CommandResult, NixCommandError
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.ci import warm_fod_cache as wfc

if TYPE_CHECKING:
    from collections.abc import Coroutine


def test_format_duration_and_detect_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    assert object.__getattribute__(wfc, "_format_duration")(2.0) == "2.0s"
    assert object.__getattribute__(wfc, "_format_duration")(90) == "1m 30s"
    assert object.__getattribute__(wfc, "_format_duration")(4000) == "1h 6m"

    monkeypatch.setattr(
        wfc, "normalize_nix_platform", lambda machine, system: f"{machine}-{system}"
    )
    monkeypatch.setattr(wfc.platform, "machine", lambda: "x86")
    monkeypatch.setattr(wfc.platform, "system", lambda: "linux")
    assert object.__getattribute__(wfc, "_detect_system")() == "x86-linux"


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
    assert len(hits) == 1

    monkeypatch.setattr(
        wfc,
        "package_file_map",
        lambda name: {"demo": Path("path")} if name == wfc.SOURCES_FILE_NAME else {},
    )
    monkeypatch.setattr("lib.update.sources.load_source_entry", lambda _path: entry)
    targets = object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux")
    assert len(targets) == 1
    assert targets[0].package == "demo"
    assert targets[0].fod_attr == ".node_modules"

    monkeypatch.setattr(
        wfc,
        "package_file_map",
        lambda name: {"mux": Path("path")} if name == wfc.SOURCES_FILE_NAME else {},
    )
    targets = object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux")
    assert len(targets) == 1
    assert targets[0].package == "mux"
    assert targets[0].fod_attr == ".offlineCache"

    deno_entry = SourceEntry(
        version="1",
        hashes=HashCollection(
            entries=[
                HashEntry.create(
                    "sha256",
                    "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                    platform="x86_64-linux",
                    url=(
                        "https://dl.deno.land/release/v2.6.10/denort-x86_64-unknown-linux-gnu.zip"
                    ),
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        wfc,
        "package_file_map",
        lambda name: (
            {"linear-cli": Path("path")}
            if name in (wfc.SOURCES_FILE_NAME, wfc._DENO_DEPS_MANIFEST_FILE_NAME)
            else {}
        ),
    )
    monkeypatch.setattr(
        "lib.update.sources.load_source_entry", lambda _path: deno_entry
    )
    targets = object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux")
    assert len(targets) == 1
    assert targets[0].package == "linear-cli"
    assert targets[0].fod_attr == ".passthru.denoDeps"

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
    monkeypatch.setattr(
        wfc,
        "package_file_map",
        lambda name: {"bad": Path("path")} if name == wfc.SOURCES_FILE_NAME else {},
    )
    monkeypatch.setattr("lib.update.sources.load_source_entry", lambda _path: bad_entry)
    assert object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux") == []

    no_entries = SourceEntry(version="1", hashes=HashCollection(mapping={"x": "y"}))
    assert (
        object.__getattribute__(wfc, "_platform_fod_entries")(
            no_entries, "x86_64-linux"
        )
        == []
    )
    assert (
        object.__getattribute__(wfc, "_has_platform_denort_hash")(
            no_entries, "x86_64-linux"
        )
        is False
    )

    def _boom(_path: Path) -> SourceEntry:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        wfc,
        "package_file_map",
        lambda name: {"boom": Path("path")} if name == wfc.SOURCES_FILE_NAME else {},
    )
    monkeypatch.setattr("lib.update.sources.load_source_entry", _boom)
    with pytest.raises(RuntimeError, match="Failed to load per-package sources.json"):
        object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux")


def test_find_fod_targets_handles_deno_edge_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    duplicate_entry = SourceEntry(
        version="1",
        hashes=HashCollection(
            entries=[
                HashEntry.create(
                    "nodeModulesHash",
                    "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    platform="x86_64-linux",
                ),
                HashEntry.create(
                    "nodeModulesHash",
                    "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                    platform="x86_64-linux",
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        wfc,
        "package_file_map",
        lambda name: {"demo": Path("path")} if name == wfc.SOURCES_FILE_NAME else {},
    )
    monkeypatch.setattr(
        "lib.update.sources.load_source_entry", lambda _path: duplicate_entry
    )
    targets = object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux")
    assert len(targets) == 1
    assert targets[0].fod_attr == ".node_modules"

    monkeypatch.setattr(
        wfc,
        "package_file_map",
        lambda name: (
            {"linear-cli": Path("manifest")}
            if name == wfc._DENO_DEPS_MANIFEST_FILE_NAME
            else {}
        ),
    )
    assert object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux") == []

    calls = 0

    def _load_then_fail(_path: Path) -> SourceEntry:
        nonlocal calls
        calls += 1
        if calls == 1:
            return SourceEntry(version="1", hashes=HashCollection(mapping={"x": "y"}))
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        wfc,
        "package_file_map",
        lambda name: (
            {"linear-cli": Path("path")}
            if name in (wfc.SOURCES_FILE_NAME, wfc._DENO_DEPS_MANIFEST_FILE_NAME)
            else {}
        ),
    )
    monkeypatch.setattr("lib.update.sources.load_source_entry", _load_then_fail)
    assert object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux") == []

    no_denort_entry = SourceEntry(
        version="1",
        hashes=HashCollection(
            entries=[
                HashEntry.create(
                    "sha256",
                    "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                    platform="x86_64-linux",
                    url="https://example.com/not-denort.zip",
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        "lib.update.sources.load_source_entry", lambda _path: no_denort_entry
    )
    assert object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux") == []

    deno_entry = SourceEntry(
        version="1",
        hashes=HashCollection(
            entries=[
                HashEntry.create(
                    "sha256",
                    "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
                    platform="x86_64-linux",
                    url=(
                        "https://dl.deno.land/release/v2.6.10/denort-x86_64-unknown-linux-gnu.zip"
                    ),
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        "lib.update.sources.load_source_entry", lambda _path: deno_entry
    )
    monkeypatch.setattr(
        wfc,
        "_resolve_fod_attr",
        lambda package, hash_type: (
            wfc._DENO_DEPS_ATTR
            if package == "linear-cli" and hash_type == "sha256"
            else None
        ),
    )
    targets = object.__getattribute__(wfc, "_find_fod_targets")("x86_64-linux")
    assert len(targets) == 1
    assert targets[0].package == "linear-cli"
    assert targets[0].fod_attr == ".passthru.denoDeps"


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
    assert expr == "overlay-attr:demo:.node_modules:x86_64-linux"


def test_resolve_output_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    async def _ok_run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(
            args=["nix"],
            returncode=0,
            stdout=(
                '[{"outputs": {"out": "/nix/store/abc"}}, {"outputs": {"doc": "/tmp/not-store"}}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("lib.nix.commands.base.run_nix", _ok_run_nix)
    paths = asyncio.run(object.__getattribute__(wfc, "_resolve_output_paths")("expr"))
    assert paths == ["/nix/store/abc"]

    async def _bad_run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix"], returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr("lib.nix.commands.base.run_nix", _bad_run_nix)
    assert (
        asyncio.run(object.__getattribute__(wfc, "_resolve_output_paths")("expr")) == []
    )

    async def _bad_json_run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix"], returncode=0, stdout="not-json", stderr="")

    monkeypatch.setattr("lib.nix.commands.base.run_nix", _bad_json_run_nix)
    assert (
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
    assert asyncio.run(
        object.__getattribute__(wfc, "_resolve_output_paths")("expr")
    ) == ["/nix/store/ok"]


def test_push_to_cachix_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    assert (
        asyncio.run(object.__getattribute__(wfc, "_push_to_cachix")([], "cache"))
        is True
    )
    monkeypatch.setattr(wfc.shutil, "which", lambda _name: None)
    assert (
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
    assert (
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
    assert (
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
    assert (
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

    assert (
        asyncio.run(
            object.__getattribute__(wfc, "_build_one")(
                target, "x86_64-linux", cache_name="cache"
            )
        )
        is True
    )
    assert (
        asyncio.run(
            object.__getattribute__(wfc, "_build_one")(
                target, "x86_64-linux", cache_name=None
            )
        )
        is True
    )
    assert pushed == [(["/nix/store/a"], "cache")]

    monkeypatch.setattr(
        wfc, "_resolve_output_paths", lambda _expr: asyncio.sleep(0, result=[])
    )
    assert (
        asyncio.run(
            object.__getattribute__(wfc, "_build_one")(
                target, "x86_64-linux", cache_name="cache"
            )
        )
        is False
    )

    monkeypatch.setattr(
        wfc,
        "_resolve_output_paths",
        lambda _expr: asyncio.sleep(0, result=["/nix/store/a"]),
    )
    monkeypatch.setattr(
        wfc, "_push_to_cachix", lambda *_a, **_k: asyncio.sleep(0, result=False)
    )
    assert (
        asyncio.run(
            object.__getattribute__(wfc, "_build_one")(
                target, "x86_64-linux", cache_name="cache"
            )
        )
        is False
    )

    async def _fail_build(**_k: object) -> list[object]:
        raise NixCommandError(
            CommandResult(args=["nix"], returncode=1, stdout="", stderr=""), "fail"
        )

    monkeypatch.setattr(wfc, "nix_build", _fail_build)
    assert (
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
    assert (
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

    assert (
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
    assert (
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
    assert (
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
    assert rc == 0
    assert calls[-1]["level"] == wfc.logging.DEBUG

    def _raise_runtime_error(_system: str) -> list[wfc.FodTarget]:
        msg = "bad sources"
        raise RuntimeError(msg)

    monkeypatch.setattr(wfc.asyncio, "run", asyncio.runners.run)
    monkeypatch.setattr(wfc, "_find_fod_targets", _raise_runtime_error)
    assert wfc.run(system="x86_64-linux") == 1
