"""Focused branch-closure tests for high-coverage update helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lib.nix.commands.base import CommandResult as LibCommandResult
from lib.nix.commands.base import ProcessDone, ProcessLine
from lib.nix.models.sources import HashEntry
from lib.update.events import (
    CommandResult,
    GatheredValues,
    UpdateEvent,
    UpdateEventKind,
    expect_source_hashes,
)
from lib.update.process import NixBuildOptions, RunCommandOptions, StreamCommandOptions
from lib.update.ui_state import OperationKind, OperationState, _set_operation_status

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _collect(stream: AsyncIterator[UpdateEvent]) -> list[UpdateEvent]:
    async def _run() -> list[UpdateEvent]:
        items: list[UpdateEvent] = []
        async for item in stream:
            items.append(item)
        return items

    return asyncio.run(_run())


def test_deno_lock_known_version_skips_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid unexpected-version warning for supported lock versions."""
    from lib.update import deno_lock

    lock_file = tmp_path / "deno.lock"
    lock_file.write_text(
        json.dumps({"version": "5", "jsr": {}, "npm": {}}), encoding="utf-8"
    )

    async def _resolve_jsr(_lock_jsr: dict[str, dict[str, object]]) -> list[object]:
        return []

    monkeypatch.setattr("lib.update.deno_lock._resolve_all_jsr", _resolve_jsr)
    monkeypatch.setattr("lib.update.deno_lock._resolve_all_npm", lambda _lock_npm: [])
    manifest = asyncio.run(deno_lock.resolve_deno_deps(lock_file))
    assert manifest.lock_version == "5"
    assert "Unexpected deno.lock version" not in caplog.text


def test_events_expect_source_hashes_rejects_mixed_list() -> None:
    """Reject list payloads that mix ``HashEntry`` and non-entries."""
    good = HashEntry.create(
        "sha256", "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )
    with pytest.raises(TypeError, match="Expected SourceHashes payload"):
        _ = expect_source_hashes([good, "bad"])


def test_compute_drv_fingerprint_without_store_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse old/new derivation shapes when key has no slash."""
    from lib.update.nix import compute_drv_fingerprint

    async def _run_command(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        result = CommandResult(
            args=["nix"],
            returncode=0,
            stdout='{"derivations": {"abc123-demo.drv": {}}}',
            stderr="",
        )
        yield UpdateEvent(
            source="demo", kind=UpdateEventKind.COMMAND_END, payload=result
        )
        yield UpdateEvent.value("demo", result)

    monkeypatch.setattr("lib.update.nix.run_command", _run_command)
    assert asyncio.run(compute_drv_fingerprint("demo")) == "abc123"


def test_nix_cargo_yields_passthrough_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forward non-gather events from ``gather_event_streams``."""
    from lib.update.nix_cargo import compute_import_cargo_lock_output_hashes
    from lib.update.updaters.base import CargoLockGitDep

    async def _fake_gather(_streams: object) -> AsyncIterator[object]:
        yield UpdateEvent.status("demo", "prefetching")
        yield GatheredValues(values={"crate-1.0.0": "sha256-demo"})

    monkeypatch.setattr("lib.update.nix_cargo.gather_event_streams", _fake_gather)
    monkeypatch.setattr(
        "lib.update.nix_cargo.get_flake_input_node",
        lambda _name: type(
            "_Node",
            (),
            {"locked": type("_L", (), {"owner": "o", "repo": "r", "rev": "v"})()},
        )(),
    )

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr("lib.update.nix_cargo.aiohttp.ClientSession", _Session)
    monkeypatch.setattr(
        "lib.update.nix_cargo.fetch_url",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=(
                b'[[package]]\nname = "crate"\nversion = "1.0.0"\n'
                b'source = "git+https://github.com/a/b?x#deadbeef"\n'
            ),
        ),
    )

    deps = [
        CargoLockGitDep(git_dep="crate-1.0.0", hash_type="sha256", match_name="crate")
    ]
    events = _collect(
        compute_import_cargo_lock_output_hashes(
            "demo",
            "input",
            lockfile_path="Cargo.lock",
            git_deps=deps,
        )
    )
    assert any(event.kind == UpdateEventKind.STATUS for event in events)


def test_stream_command_timeout_override_and_empty_sanitized_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use explicit timeout and skip empty sanitized lines."""
    from lib.update.process import stream_command

    captured: dict[str, object] = {}

    async def _stream_process(
        _args: list[str],
        *,
        timeout: float,
        env: object,
    ) -> AsyncIterator[ProcessLine | ProcessDone]:
        _ = env
        captured["timeout"] = timeout
        yield ProcessLine("stdout", "\x1b[31m\x1b[0m\n")
        yield ProcessDone(
            LibCommandResult(args=["echo", "x"], returncode=0, stdout="", stderr="")
        )

    monkeypatch.setattr("lib.update.process.stream_process", _stream_process)
    events = _collect(
        stream_command(
            ["echo", "x"],
            options=StreamCommandOptions(source="demo", command_timeout=2.5),
        )
    )
    assert captured["timeout"] == 2.5
    assert [event.kind for event in events] == [
        UpdateEventKind.COMMAND_START,
        UpdateEventKind.COMMAND_END,
    ]


def test_run_nix_build_without_verbose(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not include ``--verbose`` when verbose mode is disabled."""
    from lib.update.process import run_nix_build

    captured: dict[str, object] = {}

    async def _run_command(
        args: list[str], *, options: RunCommandOptions
    ) -> AsyncIterator[UpdateEvent]:
        captured["args"] = args
        yield UpdateEvent.status(options.source, "ok")

    monkeypatch.setattr("lib.update.process.run_command", _run_command)
    _ = _collect(
        run_nix_build(
            "pkgs.hello", options=NixBuildOptions(source="demo", verbose=False)
        )
    )
    args = captured["args"]
    assert isinstance(args, list)
    assert "--verbose" not in args


def test_validate_source_discovery_consistency_missing_in_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report sources present in Nix but missing from Python scan."""
    from lib.update.sources import validate_source_discovery_consistency

    monkeypatch.setattr("lib.update.sources.python_source_names", lambda: {"a"})
    monkeypatch.setattr("lib.update.sources.nix_source_names", lambda: {"a", "b"})
    with pytest.raises(RuntimeError, match="Missing in Python source scan: b"):
        validate_source_discovery_consistency()


def test_ui_state_set_status_preserves_message_when_none() -> None:
    """Keep existing message when no replacement/clear flag is provided."""
    op = OperationState(kind=OperationKind.CHECK_VERSION, label="Checking")
    op.message = "old"
    _set_operation_status(op, "success", message=None, clear_message=False)
    assert op.message == "old"
