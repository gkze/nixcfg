"""Additional tests for update.nix hash helpers and build flows."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from lib.update.config import resolve_config
from lib.update.events import CommandResult, UpdateEvent, UpdateEventKind
from lib.update.nix import (
    _NIX_BUILD_SEMAPHORE_STATE,
    _emit_sri_hash_from_build_result,
    _extract_nix_hash,
    _FixedOutputBuildOptions,
    _get_nix_build_semaphore,
    _run_fixed_output_build,
    _tail_output_excerpt,
    compute_drv_fingerprint,
    compute_fixed_output_hash,
    compute_overlay_hash,
    get_current_nix_platform,
    normalize_nix_platform,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _collect_events(stream: AsyncIterator[UpdateEvent]) -> list[UpdateEvent]:
    async def _run() -> list[UpdateEvent]:
        items: list[UpdateEvent] = []
        async for item in stream:
            items.append(item)
        return items

    return asyncio.run(_run())


def test_platform_normalization_and_current_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normalize arch/OS aliases and derive current platform."""
    assert normalize_nix_platform("arm64", "Darwin") == "aarch64-darwin"
    assert normalize_nix_platform("amd64", "linux") == "x86_64-linux"

    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert get_current_nix_platform() == "x86_64-linux"


def test_tail_output_excerpt_variants() -> None:
    """Render empty, full, and truncated output excerpts."""
    assert _tail_output_excerpt("", max_lines=2) == "<no output>"
    assert _tail_output_excerpt("a\nb", max_lines=3) == "a\nb"
    truncated = _tail_output_excerpt("1\n2\n3", max_lines=2)
    assert "last 2 of 3 lines" in truncated
    assert truncated.endswith("2\n3")


def test_extract_nix_hash_success_and_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extract parsed hash and produce actionable extraction errors."""

    class _Parsed:
        hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    monkeypatch.setattr(
        "lib.update.nix.HashMismatchError.from_output",
        lambda _output, _result: _Parsed(),
    )
    assert _extract_nix_hash("anything") == _Parsed.hash

    monkeypatch.setattr(
        "lib.update.nix.HashMismatchError.from_output", lambda _output, _result: None
    )
    with pytest.raises(RuntimeError, match="Hash mismatch detected"):
        _extract_nix_hash("hash mismatch\nspecified:")

    with pytest.raises(RuntimeError, match="Could not find hash"):
        _extract_nix_hash("plain stderr")


def test_extract_nix_hash_parses_representative_nix_outputs() -> None:
    """Parse representative fixed-output and legacy Nix mismatch formats."""
    fod_output = (
        "error: hash mismatch in fixed-output derivation "
        "'/nix/store/demo-source.drv':\n"
        "  specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
        "     got:    sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=\n"
    )
    assert (
        _extract_nix_hash(fod_output)
        == "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0="
    )
    nar_output = (
        "error: hash mismatch importing path '/nix/store/abc-foo';\n"
        "  specified: 0c5b8vw40d1178xlpddw65q9gf1h2186jcc3p4swinwggbllv8mk\n"
        "  got:       1d6b9xw51a1289ymqaax76ra2gi2i3297kdd4q5sxjaxhicnmwal\n"
    )
    assert (
        _extract_nix_hash(nar_output)
        == "1d6b9xw51a1289ymqaax76ra2gi2i3297kdd4q5sxjaxhicnmwal"
    )


def test_emit_sri_hash_from_build_result_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emit SRI directly or convert legacy hash formats."""
    result = CommandResult(args=["nix"], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(
        "lib.update.nix._extract_nix_hash",
        lambda _output, config=None: (
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        ),
    )
    direct = _collect_events(_emit_sri_hash_from_build_result("demo", result))
    assert len(direct) == 1
    assert direct[0].kind == UpdateEventKind.VALUE

    async def _convert(_source: str, _hash: str) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.value(
            "demo", "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
        )

    monkeypatch.setattr(
        "lib.update.nix._extract_nix_hash", lambda _output, config=None: "legacy"
    )
    monkeypatch.setattr("lib.update.nix.convert_nix_hash_to_sri", _convert)
    converted = _collect_events(_emit_sri_hash_from_build_result("demo", result))
    assert (
        converted[-1].payload == "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
    )


def test_run_fixed_output_build_and_compute_fixed_output_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface successful mismatch extraction and success-path guard rails."""

    async def _build_success(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent(
            source="demo",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(args=["nix"], returncode=0, stdout="", stderr=""),
        )
        yield UpdateEvent.value(
            "demo",
            CommandResult(args=["nix"], returncode=0, stdout="", stderr=""),
        )

    monkeypatch.setattr(
        "lib.update.nix.run_nix_build", lambda *_args, **_kwargs: _build_success()
    )
    with pytest.raises(RuntimeError, match="it succeeded"):
        _collect_events(
            _run_fixed_output_build(
                "demo",
                "pkgs.hello",
                options=_FixedOutputBuildOptions(success_error="it succeeded"),
            )
        )

    async def _build_failure(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        failed = CommandResult(args=["nix"], returncode=1, stdout="", stderr="stderr")
        yield UpdateEvent(
            kind=UpdateEventKind.COMMAND_END, source="demo", payload=failed
        )
        yield UpdateEvent.value("demo", failed)

    monkeypatch.setattr(
        "lib.update.nix.run_nix_build", lambda *_args, **_kwargs: _build_failure()
    )
    failed_events = _collect_events(
        _run_fixed_output_build(
            "demo",
            "pkgs.hello",
            options=_FixedOutputBuildOptions(success_error="it succeeded"),
        )
    )
    assert failed_events[-1].kind == UpdateEventKind.VALUE

    # compute_fixed_output_hash end-to-end with mocked subflows
    monkeypatch.setattr(
        "lib.update.nix._get_nix_build_semaphore", lambda _cfg: asyncio.Semaphore(1)
    )
    monkeypatch.setattr(
        "lib.update.nix._run_fixed_output_build",
        lambda *_args, **_kwargs: _build_failure(),
    )

    async def _emit_sri(
        _source: str, _result: CommandResult, *, config: object = None
    ) -> AsyncIterator[UpdateEvent]:
        _ = config
        yield UpdateEvent.value(
            "demo", "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
        )

    monkeypatch.setattr("lib.update.nix._emit_sri_hash_from_build_result", _emit_sri)
    events = _collect_events(compute_fixed_output_hash("demo", "pkgs.hello"))
    assert events[-1].payload == "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="


def test_get_nix_build_semaphore_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reuse semaphore by size and raise when semaphore cannot be created."""
    cfg = resolve_config(max_nix_builds=2)
    _NIX_BUILD_SEMAPHORE_STATE.semaphore = None
    _NIX_BUILD_SEMAPHORE_STATE.size = None
    first = _get_nix_build_semaphore(cfg)
    second = _get_nix_build_semaphore(cfg)
    assert first is second

    monkeypatch.setattr("lib.update.nix.asyncio.Semaphore", lambda _n: None)
    _NIX_BUILD_SEMAPHORE_STATE.semaphore = None
    _NIX_BUILD_SEMAPHORE_STATE.size = None
    with pytest.raises(RuntimeError, match="failed to initialize"):
        _get_nix_build_semaphore(cfg)


def test_compute_overlay_hash_passes_fake_hash_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delegate overlay hash computation with FAKE_HASHES env."""
    captured: dict[str, object] = {}

    async def _fake_compute(
        source: str,
        expr: str,
        *,
        env: dict[str, str],
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        captured.update({"source": source, "expr": expr, "env": env, "config": config})
        yield UpdateEvent.value(source, "ok")

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fake_compute)
    events = _collect_events(compute_overlay_hash("demo", system="x86_64-linux"))
    assert events[-1].payload == "ok"
    assert captured["source"] == "demo"
    assert captured["env"] == {"FAKE_HASHES": "1"}


def test_compute_drv_fingerprint_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extract stable drv fingerprint and report eval/parsing failures."""

    async def _run_command_success(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        result = CommandResult(
            args=["nix"],
            returncode=0,
            stdout='{"derivations": {"/nix/store/abc123-demo.drv": {}}}',
            stderr="",
        )
        yield UpdateEvent(
            kind=UpdateEventKind.COMMAND_END, source="demo", payload=result
        )
        yield UpdateEvent.value("demo", result)

    monkeypatch.setattr("lib.update.nix.run_command", _run_command_success)
    fingerprint = _run_async(compute_drv_fingerprint("demo"))
    assert fingerprint == "abc123"

    async def _run_command_old_style(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        result = CommandResult(
            args=["nix"],
            returncode=0,
            stdout='{"/nix/store/def456-demo.drv": {}}',
            stderr="",
        )
        yield UpdateEvent(
            kind=UpdateEventKind.COMMAND_END, source="demo", payload=result
        )
        yield UpdateEvent.value("demo", result)

    monkeypatch.setattr("lib.update.nix.run_command", _run_command_old_style)
    assert _run_async(compute_drv_fingerprint("demo")) == "def456"

    async def _run_command_nonzero(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        result = CommandResult(args=["nix"], returncode=1, stdout="", stderr="bad")
        yield UpdateEvent(
            kind=UpdateEventKind.COMMAND_END, source="demo", payload=result
        )
        yield UpdateEvent.value("demo", result)

    monkeypatch.setattr("lib.update.nix.run_command", _run_command_nonzero)
    with pytest.raises(RuntimeError, match="nix derivation show failed"):
        _run_async(compute_drv_fingerprint("demo"))

    async def _run_command_bad_json(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[UpdateEvent]:
        result = CommandResult(args=["nix"], returncode=0, stdout="{", stderr="")
        yield UpdateEvent(
            kind=UpdateEventKind.COMMAND_END, source="demo", payload=result
        )
        yield UpdateEvent.value("demo", result)

    monkeypatch.setattr("lib.update.nix.run_command", _run_command_bad_json)
    with pytest.raises(RuntimeError, match="Failed to parse"):
        _run_async(compute_drv_fingerprint("demo"))


def _run_async[T](awaitable: asyncio.Future[T] | asyncio.Task[T] | object) -> T:
    return asyncio.run(awaitable)  # type: ignore[arg-type]
