"""Behavior tests for transient-failure classification."""

import signal

import pytest

from lib.update import crate2nix
from lib.update.events import CommandResult
from lib.update.failures import (
    is_retryable_fixed_output_failure,
    is_retryable_nix_network_failure,
    is_retryable_nix_store_failure,
    is_transient_store_interruption,
)


def _fixed_output_retryable(result: CommandResult) -> bool:
    return is_retryable_fixed_output_failure(stdout=result.stdout, stderr=result.stderr)


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
    assert _fixed_output_retryable(transient)
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
    assert _fixed_output_retryable(pnpm_timeout)
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
    assert _fixed_output_retryable(bun_extract_failure)
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
    assert _fixed_output_retryable(http2_protocol_failure)
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
    assert not _fixed_output_retryable(hash_mismatch)
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
    assert not _fixed_output_retryable(permanent)
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
    assert _fixed_output_retryable(store_fault)
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


@pytest.mark.parametrize(
    "message",
    [
        "nix-prefetch-git\nfatal: Temporary failure in name resolution",
        "index.crates.io\nconnection reset by peer",
        "nix-prefetch-git\nerror: 504 Gateway Time-out",
    ],
)
def test_crate2nix_generation_retries_shared_network_faults(message: str) -> None:
    """crate2nix composes the shared Nix network set inside its fetch context.

    The hand-copied crate2nix list had drifted: resolver failures and resets
    during cargo/git fetches failed the generation instead of retrying.
    """
    assert crate2nix._is_retryable_crate2nix_generate_failure(message)
    # The shared markers still need a crate2nix fetch context.
    assert not crate2nix._is_retryable_crate2nix_generate_failure(
        message.split("\n", 1)[1]
    )
