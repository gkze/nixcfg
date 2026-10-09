"""Additional tests for update.nix hash helpers and build flows."""

import asyncio
import signal
import subprocess
from pathlib import Path

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
    is_retryable_nix_store_failure,
    is_transient_store_interruption,
    normalize_nix_platform,
    recover_nix_store_after_fault,
    should_restart_nix_daemon,
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

    # #1257 argus+home-george: fetchurl name is the JSR file, not "source".
    named_mirror = (
        "error: Cannot build '/nix/store/7z11drai9yzh0d6g4kr5aa7pw6l9ab2f-"
        "_std_collections-1.1.6-sum_of_test.ts.drv'.\n"
        "       Reason: builder failed with exit code 1.\n"
        "       > error: cannot download "
        "_std_collections-1.1.6-sum_of_test.ts from any mirror"
    )
    assert is_retryable_nix_network_failure(stdout="", stderr=named_mirror)
    assert _is_retryable_fixed_output_hash_failure(
        CommandResult(args=["nix"], returncode=1, stdout="", stderr=named_mirror)
    )
    assert is_retryable_nix_network_failure(
        stdout="",
        stderr="error: cannot download source from any mirror",
    )

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

    store_fault = CommandResult(
        args=["nix"],
        returncode=1,
        stdout="",
        stderr=(
            'error: cannot unlink "/nix/store/kif07wvrg569fqmqh9a0zkqx8x3lkmhx-'
            'replay-10.67.0.tgz": Illegal byte sequence'
        ),
    )
    assert _is_retryable_fixed_output_hash_failure(store_fault)
    assert is_retryable_nix_store_failure(
        stdout=store_fault.stdout,
        stderr=store_fault.stderr,
    )
    assert is_transient_store_interruption(store_fault.stderr)
    assert not is_retryable_nix_network_failure(
        stdout=store_fault.stdout,
        stderr=store_fault.stderr,
    )
    builder_and_store = (
        "error: builder for '/nix/store/abc-replay.drv' failed with exit code 1\n"
        'error: cannot unlink "/nix/store/abc-replay.tgz": Illegal byte sequence'
    )
    assert not is_retryable_nix_store_failure(stdout="", stderr=builder_and_store)
    assert not is_transient_store_interruption(builder_and_store)
    assert not is_transient_store_interruption(
        "error: hash mismatch in fixed-output derivation\nIllegal byte sequence"
    )
    assert is_transient_store_interruption("terminated by signal 10")
    assert is_transient_store_interruption(f"terminated by signal {signal.SIGBUS}\n")
    assert not is_transient_store_interruption("terminated by signal 15")
    assert not is_transient_store_interruption("terminated by signal 100")
    assert not is_transient_store_interruption(
        "terminated by signal 100\nterminated by signal 15"
    )
    assert is_transient_store_interruption(
        "terminated by signal 100 then terminated by signal 10"
    )
    assert not is_transient_store_interruption(
        "error: builder for '/nix/store/abc.drv' failed\nterminated by signal 10"
    )
    cannot_build = (
        "error: Cannot build '/nix/store/abc.drv'.\n"
        "Reason: builder failed with exit code 1.\n"
        'error: cannot unlink "/nix/store/abc.tgz": Illegal byte sequence'
    )
    assert not is_retryable_nix_store_failure(stdout="", stderr=cannot_build)
    assert not is_transient_store_interruption(cannot_build)
    assert not is_transient_store_interruption(
        "error: Cannot build '/nix/store/abc.drv'.\nterminated by signal 10"
    )
    substitute_eilseq = (
        'error: clearing flags of path "/nix/store/s6r17xm79153d1h8wk7axg58whz0bw1j-'
        'bun-cache/share/bun-packages/lie@3.3.0": Illegal byte sequence\n'
        "error: path '/nix/store/s6r17xm79153d1h8wk7axg58whz0bw1j-bun-cache' is "
        "required, but there is no substituter that can build it\n"
        "error: some substitutes for the outputs of derivation "
        "'/nix/store/pp1lw91z156n1k67c9y46ygs952drra8-bun-cache.drv' failed "
        "(usually happens due to networking issues); try '--fallback' to build "
        "derivation from source\n"
        "error: Cannot build '/nix/store/a7l7d5dr4zav4fcfd7ywzkxb5vsi95jk-"
        "superset-1.30.2.drv'.\n"
        "       Reason: 1 dependency failed.\n"
        "error (ignored): write of 1 bytes: Illegal byte sequence\n"
        "error: creating log file "
        '"/nix/var/log/nix/drvs/08/wzx3233whhrfcrbdbv5lcjhp7nz9iw-'
        'ansi-escapes-7.3.0.tgz.drv.bz2": Invalid argument'
    )
    assert is_retryable_nix_store_failure(stdout="", stderr=substitute_eilseq)
    assert is_transient_store_interruption(substitute_eilseq)
    assert is_transient_store_interruption(
        "error: Build failed due to failed dependency\n"
        "Illegal byte sequence\n"
        "terminated by signal 10"
    )
    assert is_transient_store_interruption(
        "Reason: builder failed\nOutput paths:\nIllegal byte sequence"
    )


def test_missing_store_drv_after_eilseq_is_retryable() -> None:
    """Update 37165499384: a vanished store .drv is the same fault as EILSEQ."""
    missing_drv = (
        'error: opening file "/nix/store/67b3dw9p5i6qynv9mf3fhsa21cmibk57-'
        'unsloth-desktop-0.1.813-beta.drv": No such file or directory'
    )
    git_lfs_drv = (
        'error: opening file "/nix/store/xi9sai0nbg5pn04vfzaslbhj7swybc05-'
        'git-lfs-3.7.1.drv": No such file or directory'
    )
    assert is_retryable_nix_store_failure(stdout="", stderr=missing_drv)
    assert is_retryable_nix_store_failure(stdout="", stderr=git_lfs_drv)
    assert is_transient_store_interruption(missing_drv)
    assert is_retryable_nix_store_failure(
        stdout="",
        stderr=(
            "error: Cannot build '/nix/store/abc.drv'.\n"
            "Reason: 1 dependency failed.\n"
            f"{missing_drv}"
        ),
    )
    assert not is_retryable_nix_store_failure(
        stdout="",
        stderr='error: opening file "/tmp/foo": No such file or directory',
    )
    assert not is_retryable_nix_store_failure(
        stdout="",
        stderr=(
            'error: opening file "/nix/store/abc-granola-7.595.3": '
            "No such file or directory"
        ),
    )
    assert not is_retryable_nix_store_failure(
        stdout="",
        stderr="gcc: /usr/bin/ld: No such file or directory",
    )
    assert not is_retryable_nix_store_failure(
        stdout="",
        stderr=(
            "error: builder for '/nix/store/abc-waku.drv' failed with exit code 1\n"
            f"{missing_drv}"
        ),
    )
    assert not is_retryable_nix_store_failure(
        stdout="",
        stderr=f"error: hash mismatch in fixed-output derivation\n{missing_drv}",
    )
    assert not is_transient_store_interruption(
        "error: builder for '/nix/store/abc.drv' failed\n" + missing_drv
    )


def test_vanished_store_build_input_and_daemon_disconnect_are_retryable() -> None:
    """Update 37522365810: granola exit 1 was a vanished stdenv hook, then the daemon died."""
    vanished_input = (
        "error: Cannot build '/nix/store/wzrs1hpgczfxgv6q7yvp7iy39plhakw7-"
        "granola-7.626.3.drv'.\n"
        "       Reason: builder failed with exit code 1.\n"
        "       Last 1 log lines:\n"
        "       > build input /nix/store/fyaryjvghbkpfnsyw97hb3lyb37s1pd6-"
        "move-lib64.sh does not exist\n"
        'error: clearing flags of path "/nix/store/mxzgf8zlr2mbxrqp1ami2ixqsqpskv0w-'
        "apple-sdk-14.4/Platforms/MacOSX.platform/Developer/SDKs/MacOSX14.4.sdk/"
        'System/Library/PrivateFrameworks/AppleDepth.framework/Versions/Current": '
        "No such file or directory\n"
        "error: path '/nix/store/mxzgf8zlr2mbxrqp1ami2ixqsqpskv0w-apple-sdk-14.4' "
        "is required, but there is no substituter that can build it"
    )
    daemon_disconnect = (
        "error: cannot open connection to remote store 'daemon': "
        "Nix daemon disconnected unexpectedly (maybe it crashed?)"
    )
    assert is_retryable_nix_store_failure(stdout="", stderr=vanished_input)
    assert is_transient_store_interruption(vanished_input)
    assert is_retryable_nix_store_failure(stdout="", stderr=daemon_disconnect)
    assert is_transient_store_interruption(daemon_disconnect)
    active_builds_lock = (
        "error (ignored): filesystem error: in remove: Illegal byte sequence "
        '["/nix/var/nix/active-builds/35863-298"]\n'
        'error: opening lock file "/nix/var/nix/active-builds/35863-2041": '
        "Invalid argument"
    )
    assert is_retryable_nix_store_failure(stdout="", stderr=active_builds_lock)
    assert is_transient_store_interruption(active_builds_lock)
    assert should_restart_nix_daemon(stdout="", stderr=daemon_disconnect)
    assert should_restart_nix_daemon(stdout="", stderr=active_builds_lock)
    assert not should_restart_nix_daemon(stdout="", stderr="hash mismatch")
    assert not is_retryable_nix_store_failure(
        stdout="",
        stderr="gcc: /usr/bin/ld: build input does not exist",
    )
    assert not is_retryable_nix_store_failure(
        stdout="",
        stderr=(
            "error: Cannot build '/nix/store/abc-waku.drv'.\n"
            "       Reason: builder failed with exit code 1.\n"
            "       > error: linker command failed with exit code 1"
        ),
    )


def test_recover_nix_store_after_fault_restarts_determinate_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1257 retried nix build against a dead daemon; kickstart first."""
    calls: list[list[str]] = []
    socket = tmp_path / "daemon-socket"
    sleeps: list[float] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[-1].endswith("determinate.nix-daemon"):
            return subprocess.CompletedProcess(args, 1, "", "no such service")
        socket.touch()
        return subprocess.CompletedProcess(args, 0, "", "")

    recover_nix_store_after_fault(platform="linux")
    recover_nix_store_after_fault(platform="linux", run=run, socket=socket)
    assert calls == []
    recover_nix_store_after_fault(platform="darwin")
    assert calls == []
    present = tmp_path / "present-socket"
    present.touch()
    recover_nix_store_after_fault(
        platform="darwin",
        run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        socket=present,
    )
    recover_nix_store_after_fault(
        platform="darwin",
        run=run,
        sleep=sleeps.append,
        socket=socket,
        wait_seconds=1.0,
    )
    assert calls[0][:4] == ["sudo", "-n", "launchctl", "kickstart"]
    assert calls[0][-1] == "system/systems.determinate.nix-daemon"
    assert calls[1][-1] == "system/org.nixos.nix-daemon"
    assert socket.is_file()
    missing = tmp_path / "missing-socket"
    recover_nix_store_after_fault(
        platform="darwin",
        run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        sleep=sleeps.append,
        socket=missing,
        wait_seconds=0.3,
    )
    assert sleeps
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    hosted = tmp_path / "hosted-socket"
    hosted_calls: list[list[str]] = []

    def hosted_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        hosted_calls.append(list(args))
        hosted.touch()
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("lib.update.nix.subprocess.run", hosted_run)
    recover_nix_store_after_fault(platform="darwin", socket=hosted)
    assert hosted_calls[0][:4] == ["sudo", "-n", "launchctl", "kickstart"]
    assert hosted.is_file()


def test_unreadable_store_rlib_e0463_is_retryable() -> None:
    """Update 37599536875: rustc E0463 after --extern store rlib is a substitute fault."""
    e0463_store_rlib = (
        "error: Cannot build '/nix/store/1vhn1bsiqchjp101n2sj5fjjk6fiw596-"
        "rust_agent_settings-0.1.0.drv'.\n"
        "       Reason: builder failed with exit code 1.\n"
        "       > Running env rustc --crate-name agent_settings "
        "src/agent_settings.rs --extern settings=/nix/store/"
        "l0sqrxbm7jiz24hjci8bpkl2mh9wwsvw-rust_settings-0.1.0-lib/lib/"
        "libsettings-7be7f1170a.rlib --extern project=/nix/store/"
        "190kd4d9c2mvcl54val1pw8fpjq34cn0-rust_project-0.1.0-lib/lib/"
        "libproject-53a1143c9f.rlib\n"
        "       > error[E0463]: can't find crate for `settings`\n"
        "       > error[E0463]: can't find crate for `project`\n"
    )
    assert is_retryable_nix_store_failure(stdout="", stderr=e0463_store_rlib)
    assert is_transient_store_interruption(e0463_store_rlib)
    assert not is_retryable_nix_store_failure(
        stdout="",
        stderr=(
            "error: Cannot build '/nix/store/abc-agent_settings.drv'.\n"
            "       Reason: builder failed with exit code 1.\n"
            "       > error[E0463]: can't find crate for `settings`"
        ),
    )
    assert not is_retryable_nix_store_failure(
        stdout="",
        stderr=(
            "error: Cannot build '/nix/store/abc-agent_settings.drv'.\n"
            "       Reason: builder failed with exit code 1.\n"
            "       > rustc --extern settings=/tmp/libsettings.rlib\n"
            "       > error[E0463]: can't find crate for `settings`"
        ),
    )
    assert not is_retryable_nix_store_failure(
        stdout="",
        stderr=(
            "error: Cannot build '/nix/store/abc-copilot.drv'.\n"
            "       Reason: builder failed with exit code 1.\n"
            "       > rustc --extern copilot=/nix/store/"
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-rust_copilot-0.1.0-lib/lib/"
            "libcopilot-bbbbbbbbbb.rlib\n"
            "       > error[E0786]: found invalid metadata files for crate `copilot`"
        ),
    )
    assert not is_transient_store_interruption(
        "error: hash mismatch in fixed-output derivation\n" + e0463_store_rlib
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
