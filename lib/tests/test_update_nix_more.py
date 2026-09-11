"""Additional tests for update.nix hash helpers and build flows."""

import asyncio

import pytest

from lib.tests._updater_helpers import collect_events
from lib.update.config import resolve_config
from lib.update.events import (
    CommandResult,
    EventSink,
    UpdateEvent,
    UpdateEventKind,
    ignore_event,
)
from lib.update.nix import (
    _emit_sri_hash_from_build_result,
    _extract_nix_hash,
    _FixedOutputBuildOptions,
    _is_retryable_fixed_output_hash_failure,
    _run_fixed_output_build,
    _tail_output_excerpt,
    compute_drv_fingerprint,
    compute_fixed_output_hash,
    compute_overlay_hash,
    get_current_nix_platform,
    is_retryable_nix_network_failure,
    normalize_nix_platform,
)


def _collect_events(operation):
    return asyncio.run(collect_events(operation))


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


def test_retryable_fixed_output_hash_failure_classification() -> None:
    """Retry transient fetch failures only when Nix has not produced a hash."""
    transient = CommandResult(
        args=["nix"],
        returncode=1,
        stdout="",
        stderr=(
            "curl: (22) The requested URL returned error: 502\n"
            "error: cannot download source from any mirror"
        ),
    )
    assert _is_retryable_fixed_output_hash_failure(transient)
    assert is_retryable_nix_network_failure(
        stdout=transient.stdout,
        stderr=transient.stderr,
    )

    pnpm_timeout = CommandResult(
        args=["nix"],
        returncode=1,
        stdout="",
        stderr=(
            "The operation was aborted due to timeout\n"
            "TimeoutError: The operation was aborted due to timeout"
        ),
    )
    assert _is_retryable_fixed_output_hash_failure(pnpm_timeout)
    assert not is_retryable_nix_network_failure(
        stdout=pnpm_timeout.stdout,
        stderr=pnpm_timeout.stderr,
    )

    bun_extract_failure = CommandResult(
        args=["nix"],
        returncode=1,
        stdout="",
        stderr=(
            'error: Fail extracting tarball for "mermaid"\n'
            "error: Fail extracting tarball from mermaid"
        ),
    )
    assert _is_retryable_fixed_output_hash_failure(bun_extract_failure)
    assert not is_retryable_nix_network_failure(
        stdout=bun_extract_failure.stdout,
        stderr=bun_extract_failure.stderr,
    )

    http2_protocol_failure = CommandResult(
        args=["nix"],
        returncode=1,
        stdout="",
        stderr=(
            "the server made an unrecoverable HTTP protocol violation\n"
            "Caused by: [92] Stream error in the HTTP/2 framing layer"
        ),
    )
    assert _is_retryable_fixed_output_hash_failure(http2_protocol_failure)
    assert is_retryable_nix_network_failure(
        stdout=http2_protocol_failure.stdout,
        stderr=http2_protocol_failure.stderr,
    )

    for libcurl_failure in (
        "Failure when receiving data from the peer",
        "Operation too slow. Less than 1 bytes/sec transferred the last 5 seconds",
    ):
        assert is_retryable_nix_network_failure(stdout="", stderr=libcurl_failure)

    hash_mismatch = CommandResult(
        args=["nix"],
        returncode=1,
        stdout="",
        stderr=(
            "error: hash mismatch in fixed-output derivation\n"
            "  specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
            "     got:    sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=\n"
            "HTTP error 502 while another substituter was queried"
        ),
    )
    assert not _is_retryable_fixed_output_hash_failure(hash_mismatch)
    assert not is_retryable_nix_network_failure(
        stdout=hash_mismatch.stdout,
        stderr=hash_mismatch.stderr,
    )

    permanent = CommandResult(
        args=["nix"],
        returncode=1,
        stdout="",
        stderr="error: file 'missing.nix' was not found",
    )
    assert not _is_retryable_fixed_output_hash_failure(permanent)
    assert not is_retryable_nix_network_failure(
        stdout=permanent.stdout,
        stderr=permanent.stderr,
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
    direct = _collect_events(
        lambda emit: _emit_sri_hash_from_build_result("demo", result, emit=emit)
    )
    assert direct == []
    assert direct.result == "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    async def _convert(
        _source: str, _hash: str, *, emit: EventSink = ignore_event
    ) -> object:
        return "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="

    monkeypatch.setattr(
        "lib.update.nix._extract_nix_hash", lambda _output, config=None: "legacy"
    )
    monkeypatch.setattr("lib.update.nix.convert_nix_hash_to_sri", _convert)
    converted = _collect_events(
        lambda emit: _emit_sri_hash_from_build_result("demo", result, emit=emit)
    )
    assert converted.result == "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


def test_run_fixed_output_build_and_compute_fixed_output_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface successful mismatch extraction and success-path guard rails."""

    async def _build_success(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        await emit(
            UpdateEvent(
                source="demo",
                kind=UpdateEventKind.COMMAND_END,
                payload=CommandResult(args=["nix"], returncode=0, stdout="", stderr=""),
            )
        )
        return CommandResult(args=["nix"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "lib.update.nix.run_nix_build", lambda *_args, **_kwargs: _build_success()
    )
    with pytest.raises(RuntimeError, match="it succeeded"):
        _collect_events(
            lambda emit: _run_fixed_output_build(
                "demo",
                "pkgs.hello",
                options=_FixedOutputBuildOptions(success_error="it succeeded"),
                emit=emit,
            )
        )

    async def _build_failure(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        failed = CommandResult(args=["nix"], returncode=1, stdout="", stderr="stderr")
        await emit(
            UpdateEvent(kind=UpdateEventKind.COMMAND_END, source="demo", payload=failed)
        )
        return failed

    monkeypatch.setattr(
        "lib.update.nix.run_nix_build", lambda *_args, **_kwargs: _build_failure()
    )
    failed_events = _collect_events(
        lambda emit: _run_fixed_output_build(
            "demo",
            "pkgs.hello",
            options=_FixedOutputBuildOptions(success_error="it succeeded"),
            emit=emit,
        )
    )
    assert failed_events.result.returncode == 1

    # compute_fixed_output_hash end-to-end with mocked subflows
    monkeypatch.setattr(
        "lib.update.nix._run_fixed_output_build",
        lambda *_args, **_kwargs: _build_failure(),
    )

    async def _emit_sri(
        _source: str,
        _result: CommandResult,
        *,
        config: object = None,
        emit: EventSink = ignore_event,
    ) -> object:
        _ = config
        return "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="

    monkeypatch.setattr("lib.update.nix._emit_sri_hash_from_build_result", _emit_sri)
    events = _collect_events(
        lambda emit: compute_fixed_output_hash("demo", "pkgs.hello", emit=emit)
    )
    assert events.result == "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="


def test_compute_fixed_output_hash_retries_transient_source_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry source fetch flakes before extracting the fixed-output hash."""
    attempts: list[int] = []
    sleep_delays: list[float] = []

    async def _build(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        attempts.append(1)
        if len(attempts) == 1:
            result = CommandResult(
                args=["nix"],
                returncode=1,
                stdout="",
                stderr=(
                    "curl: (22) The requested URL returned error: 502\n"
                    "error: cannot download source from any mirror"
                ),
            )
        else:
            result = CommandResult(
                args=["nix"],
                returncode=1,
                stdout="",
                stderr=(
                    "error: hash mismatch in fixed-output derivation\n"
                    "  specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
                    "     got:    "
                    "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0="
                ),
            )
        return result

    async def _sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr("lib.update.nix._run_fixed_output_build", _build)
    monkeypatch.setattr("lib.update.nix.asyncio.sleep", _sleep)

    cfg = resolve_config(retry_backoff=0.25)
    events = _collect_events(
        lambda emit: compute_fixed_output_hash(
            "demo", "pkgs.hello", config=cfg, emit=emit
        )
    )

    assert len(attempts) == 2
    assert sleep_delays == [0.25]
    assert [
        event.message for event in events if event.kind is UpdateEventKind.STATUS
    ] == ["fixed-output source fetch hit a transient failure; retrying..."]
    assert events.result == "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0="


def test_compute_fixed_output_hash_stops_after_transient_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface the final fetch failure after the bounded retry budget."""
    attempts: list[int] = []
    sleep_delays: list[float] = []

    async def _build(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        attempts.append(1)
        return CommandResult(
            args=["nix"],
            returncode=1,
            stdout="",
            stderr="curl: (22) The requested URL returned error: 502",
        )

    async def _sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr("lib.update.nix._run_fixed_output_build", _build)
    monkeypatch.setattr("lib.update.nix.asyncio.sleep", _sleep)

    with pytest.raises(RuntimeError, match="Could not find hash"):
        _collect_events(
            lambda emit: compute_fixed_output_hash("demo", "pkgs.hello", emit=emit)
        )

    assert len(attempts) == 3
    assert sleep_delays == [1.0, 1.0]


def test_compute_overlay_hash_embeds_fake_hash_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delegate overlay hashing without ambient environment configuration."""
    captured: dict[str, object] = {}

    async def _fake_compute(
        source: str,
        expr: str,
        *,
        env: dict[str, str] | None = None,
        config: object,
        emit: EventSink = ignore_event,
    ) -> object:
        captured.update({"source": source, "expr": expr, "env": env, "config": config})
        return "ok"

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fake_compute)
    events = _collect_events(
        lambda emit: compute_overlay_hash("demo", system="x86_64-linux", emit=emit)
    )
    assert events.result == "ok"
    assert captured["source"] == "demo"
    assert captured["env"] is None


def test_compute_drv_fingerprint_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extract stable drv fingerprints and report eval failures."""

    async def _run_command_success(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        result = CommandResult(
            args=["nix"],
            returncode=0,
            stdout="/nix/store/abc123-demo.drv\n",
            stderr="",
        )
        await emit(
            UpdateEvent(kind=UpdateEventKind.COMMAND_END, source="demo", payload=result)
        )
        return result

    monkeypatch.setattr("lib.update.nix.run_command", _run_command_success)
    fingerprint = _run_async(compute_drv_fingerprint("demo"))
    assert fingerprint == "abc123"

    async def _run_command_old_style(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        result = CommandResult(
            args=["nix"],
            returncode=0,
            stdout="def456-demo.drv",
            stderr="",
        )
        await emit(
            UpdateEvent(kind=UpdateEventKind.COMMAND_END, source="demo", payload=result)
        )
        return result

    monkeypatch.setattr("lib.update.nix.run_command", _run_command_old_style)
    assert _run_async(compute_drv_fingerprint("demo")) == "def456"

    async def _run_command_nonzero(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        result = CommandResult(args=["nix"], returncode=1, stdout="", stderr="bad")
        await emit(
            UpdateEvent(kind=UpdateEventKind.COMMAND_END, source="demo", payload=result)
        )
        return result

    monkeypatch.setattr("lib.update.nix.run_command", _run_command_nonzero)
    with pytest.raises(RuntimeError, match="nix eval failed"):
        _run_async(compute_drv_fingerprint("demo"))

    async def _run_command_empty_stdout(
        *_args: object, emit: EventSink = ignore_event, **_kwargs: object
    ) -> object:
        result = CommandResult(args=["nix"], returncode=0, stdout="", stderr="")
        await emit(
            UpdateEvent(kind=UpdateEventKind.COMMAND_END, source="demo", payload=result)
        )
        return result

    monkeypatch.setattr("lib.update.nix.run_command", _run_command_empty_stdout)
    with pytest.raises(RuntimeError, match="empty drvPath"):
        _run_async(compute_drv_fingerprint("demo"))


def _run_async[T](awaitable: asyncio.Future[T] | asyncio.Task[T] | object) -> T:
    return asyncio.run(awaitable)  # type: ignore[arg-type]
