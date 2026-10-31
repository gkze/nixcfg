"""Classify command output as a transient infrastructure fault or a real failure.

This module is the single owner of the text markers the update pipeline uses to
decide whether a failed fetch, prefetch, build, or crate2nix generation is worth
retrying. Consumers compose the named marker sets below and add only extras
that are specific to their own tool (documented at the consumer).

Marker matching is case-insensitive (:func:`contains_marker`), except for
:data:`HASH_MISMATCH_INDICATORS`, whose ``HashMismatch`` spelling is a
structured Nix error name rather than prose.
"""

import re
import signal

# -- Network markers --------------------------------------------------------

# Resolver failures as printed by curl/libcurl (and therefore Nix fetchers).
DNS_RESOLUTION_MARKERS = (
    "Could not resolve host",
    "Temporary failure in name resolution",
)

# curl/libcurl transfer faults that say nothing about the requested artifact.
CURL_TRANSFER_MARKERS = (
    *DNS_RESOLUTION_MARKERS,
    "Failure when receiving data from the peer",
    "Failed to connect",
    "HTTP protocol violation",
    "HTTP/2 framing layer",
    "HTTP/2 stream",
    "Operation too slow",
    "connection reset",
    # 39435878398 prepare-x86: go vendor prefetch printed Go's http2
    # framing error (proxy.golang.org INTERNAL_ERROR), not curl's
    # "HTTP/2 stream".
    "stream error: stream ID",
    "INTERNAL_ERROR; received from peer",
)

# Upstream gateway errors in the spellings HTTP servers use. Numeric 5xx in
# curl/git/Nix spellings is matched by ``_HTTP_5XX`` instead.
HTTP_GATEWAY_MARKERS = (
    "502 Bad Gateway",
    "503 Service Unavailable",
    "504 Gateway Time-out",
)
# Do not match HTTP/2 or 4xx; those are protocol framing and permanent client
# errors.
_HTTP_5XX = re.compile(
    r"(?:http(?:\s+error)?\s+5\d\d|returned error:\s*5\d\d)", re.IGNORECASE
)
# fetchurl prints the derivation *name*, not the word "source". Deno JSR
# files use names like `_std_collections-1.1.6-sum_of_test.ts` (#1257).
_CANNOT_DOWNLOAD_FROM_MIRROR = re.compile(
    r"cannot download \S+ from any mirror", re.IGNORECASE
)

# Every transient network fault Nix can report while fetching or substituting.
NIX_NETWORK_TRANSIENT_MARKERS = (
    *CURL_TRANSFER_MARKERS,
    *HTTP_GATEWAY_MARKERS,
    "NameServerFailure",
    # 39292190782: zon2nix/zig fetch printed only ``err(default):
    # BadHttpStatus`` for a Zig package 5xx, with no numeric HTTP status.
    "BadHttpStatus",
    "ConnectionTimedOut",
    "HttpConnectionClosing",
    "RPC failed",
    "ReadFailed",
    "TlsInitializationFailed",
    "UnknownHostName",
    # 39912925016 warm-rust 0: the nix CLI found no usable network interface,
    # turned substituters off for that invocation, and reported cache.nixos.org
    # llvm outputs as builds that ``--max-jobs 0`` then refused.
    "you don't have Internet access",
    # macOS ENOBUFS: the kernel ran out of socket buffers for one write.
    # 40606669118 hit it in a crate2nix git fetch. 41165477135 prepare-darwin-3
    # hit it in zon2nix's nix-prefetch-git store add for zigfsm. The second
    # had no retry, so neutils failed the shard. Every fetch caller shares
    # this marker now; a fresh attempt gets new buffers.
    "No buffer space available",
)

# Transfer timeouts. These are deliberately *not* part of the Nix network set:
# a build that prints "timed out" may be a test-suite timeout, so only callers
# whose command is a pure transfer (prefetch, fixed-output fetch) add them.
TRANSFER_TIMEOUT_MARKERS = (
    "Operation timed out",
    "timed out",
)

# Fixed-output fetchers (pnpm/bun/mirror fetchers) add their own flake text.
FIXED_OUTPUT_TRANSIENT_MARKERS = (
    *TRANSFER_TIMEOUT_MARKERS,
    "aborted due to timeout",
    "Fail extracting tarball",
)

# Resolver failures as printed by Python's socket layer (getaddrinfo).
PYTHON_RESOLVER_MARKERS = (
    "getaddrinfo failed",
    "name or service not known",
    "no address associated with hostname",
    "nodename nor servname provided, or not known",
)

# aiohttp connection failures (exception class name and message prefix).
AIOHTTP_CONNECT_MARKERS = (
    "cannot connect to host",
    "clientconnectorerror",
)

# -- Store and build markers ------------------------------------------------

HASH_MISMATCH_INDICATORS = (
    "hash mismatch",
    "HashMismatch",
    "specified:",
)

# Hosted macos-15 store filesystems fault mid-build with EILSEQ. Nix reports
# that as "Illegal byte sequence" on unlink or pread; the same machines also
# die with SIGBUS (signal 10). After that I/O damage the next nix invocation
# can fail only on a vanished store .drv, a vanished store build input, an
# unreadable Cachix-substituted crate2nix rlib (rustc E0463 after --extern
# /nix/store/...rlib *and* an I/O or existence note), or a crashed daemon.
# Bare E0463 with a store ``--extern`` and no I/O note is a builder failure
# (#1258). None of the store-fault signals are derivation failures.
#
# 38394692516 slot 4: APFS ``(IOStorageFamily) disk2s7: I/O error`` while
# clang-wrapper ranlib wrote ``libonnxruntime_common.a``. The builder then
# exited 2. That write-EIO is a host storage fault, not a deterministic
# cctools race: the same volume, Data, and container all logged I/O errors,
# and ``/nix`` still had tens of GiB free.
_WRITE_EIO_MARKERS = (
    "Input/output error",
    "can't write to output file",
)
NIX_STORE_TRANSIENT_MARKERS = (
    "Illegal byte sequence",
    *_WRITE_EIO_MARKERS,
    "Nix daemon disconnected unexpectedly",
    "cannot open connection to remote store 'daemon'",
    # #1257: EILSEQ unlink of an active-builds lock, then the daemon died.
    'opening lock file "/nix/var/nix/active-builds/',
)

# Legacy Nix says "error: builder for". Determinate Nix v3 says
# "error: Cannot build" / "error: Build failed due to failed dependency".
# Bare "Reason:" and "Output paths:" are log noise, not failure evidence.
_PERMANENT_BUILD_FAILURE_MARKERS = (
    "error: builder for",
    "error: cannot build",
    "error: build failed due to failed dependency",
)
# A builder that actually ran and exited. Substitute EILSEQ also makes
# Determinate Nix print "Cannot build" / "Reason: 1 dependency failed".
_BUILDER_EXIT_MARKERS = (
    "failed with exit code",
    "error: builder for",
)
_STORE_BUS_SIGNALS = frozenset({10, signal.SIGBUS})
_STORE_EXTERN_RLIB = re.compile(
    r"--extern\s+\S+=/nix/store/[0-9a-z]{32}-[^\s]+\.rlib",
    re.IGNORECASE,
)
_RUSTC_CANT_FIND_CRATE = re.compile(
    r"error\[e0463\]:\s*can't find crate",
    re.IGNORECASE,
)
# rustc locator notes for a missing or non-file --extern path, or the store
# rlib itself missing or EILSEQ. Bare E0463 (#1258 rust_agent_ui /
# language_models) prints none of these.
_STORE_RLIB_IO = (
    re.compile(
        r"extern location for \S+ (?:does not exist|is not a file):", re.IGNORECASE
    ),
    re.compile(
        r'opening file "/nix/store/[^"]+\.rlib": no such file or directory',
        re.IGNORECASE,
    ),
    re.compile(r"/nix/store/\S+\.rlib[^\n]*illegal byte sequence", re.IGNORECASE),
)
# 38606641679 slot 2: APFS pread EILSEQ while substituting emdash-pnpm-deps,
# then pnpmConfigHook `cat $pnpmDeps/.fetcher-version` on the leftover path.
_MISSING_STORE_FETCHER_VERSION = re.compile(
    r"(/nix/store/[0-9a-z]{32}-[A-Za-z0-9+._?=-]+)/\.fetcher-version: "
    r"no such file or directory",
    re.IGNORECASE,
)
_FAILED_DRV_PATH = re.compile(
    r"(?:cannot build|builder for) '(/nix/store/[0-9a-z]{32}-[^']+\.drv)'",
    re.IGNORECASE,
)


def contains_marker(text: str, markers: tuple[str, ...]) -> bool:
    """Return whether *text* contains any of *markers*, ignoring case."""
    folded = text.casefold()
    return any(marker.casefold() in folded for marker in markers)


def has_hash_mismatch_signal(output: str) -> bool:
    """Return whether Nix reported a hash mismatch (case-sensitive)."""
    return any(indicator in output for indicator in HASH_MISMATCH_INDICATORS)


def _has_vanished_store_build_input(output: str) -> bool:
    """Return whether a builder died because a store build input vanished.

    Hosted macos-15 EILSEQ can delete stdenv hooks while realizing substitutes.
    Determinate Nix then starts the derivation and the builder exits 1 with::

        build input /nix/store/<hash>-move-lib64.sh does not exist

    That is the same store fault as a vanished ``.drv``, not a package defect.
    Other "does not exist" text is not this signal.
    """
    for line in output.splitlines():
        folded = line.casefold()
        if "build input /nix/store/" in folded and folded.rstrip().endswith(
            "does not exist"
        ):
            return True
    return False


def _has_unreadable_store_rlib(output: str) -> bool:
    """Return whether rustc E0463 names a vanished or unreadable store rlib.

    Hosted macos-15 can copy a crate2nix ``-lib`` output from Cachix and still
    leave the hashed rlib missing or unreadable. rustc then exits 1 with
    ``error[E0463]: can't find crate`` and a locator note that the
    ``--extern`` store path does not exist, is not a file, vanished, or hit
    EILSEQ.

    Update #1244 is that store-fault shape. Update #1258 is not: Darwin
    ``rust_agent_ui`` printed bare E0463 for ``language_models`` after
    ``--extern`` named a readable substituted rlib, with no locator I/O note,
    three identical times. Bare E0463 plus a store ``--extern`` is a
    permanent builder failure. E0463 without a store rlib ``--extern``, or
    E0786 (stripped ``.rmeta``), is not this signal either.
    """
    if _RUSTC_CANT_FIND_CRATE.search(output) is None:
        return False
    if _STORE_EXTERN_RLIB.search(output) is None:
        return False
    return any(pattern.search(output) for pattern in _STORE_RLIB_IO)


def corrupt_store_paths(output: str) -> tuple[str, ...]:
    """Return leftover substituted store paths a store-fault retry must repair.

    Hosted macos-15 can register a Cachix NAR after APFS EILSEQ (38606641679
    cascade-warmup slot 2). The next ``nix build --fallback`` then starts
    emdash against that leftover path; nixpkgs ``pnpmConfigHook`` exits 1
    with ``cat: /nix/store/<hash>-<name>-pnpm-deps/.fetcher-version: No such
    file or directory``. That is the same store fault as a vanished stdenv
    hook, not a missing fetcherVersion in the derivation.
    """
    return tuple(
        dict.fromkeys(
            m.group(1) for m in _MISSING_STORE_FETCHER_VERSION.finditer(output)
        )
    )


def _has_vanished_store_artifact(output: str) -> bool:
    """Return whether a builder died only because a store input vanished."""
    return (
        _has_vanished_store_build_input(output)
        or _has_unreadable_store_rlib(output)
        or bool(corrupt_store_paths(output))
    )


def _has_builder_exit(output: str) -> bool:
    # A cctools ``ranlib`` / ``ld`` write-EIO exits the builder too, but it is
    # the hosted APFS fault itself, not a derivation failure.
    if _has_vanished_store_artifact(output) or contains_marker(
        output, _WRITE_EIO_MARKERS
    ):
        return False
    folded = output.casefold()
    return any(marker in folded for marker in _BUILDER_EXIT_MARKERS)


def failed_derivation_paths(output: str) -> tuple[str, ...]:
    """Return ``.drv`` paths Nix named in Cannot-build / builder-for lines."""
    return tuple(dict.fromkeys(_FAILED_DRV_PATH.findall(output)))


def _has_missing_store_drv(output: str) -> bool:
    """Return whether Nix could not open a store ``.drv`` after store I/O damage.

    Hosted macos-15 EILSEQ substitute faults can drop ``.drv`` files. The next
    ``nix`` invocation then reports only::

        error: opening file "/nix/store/<hash>-<name>.drv": No such file or directory

    That is the same store fault, not a missing package. Other ENOENT text,
    including a vanished non-``.drv`` store path, is not this signal.
    """
    for line in output.splitlines():
        folded = line.casefold()
        if (
            'error: opening file "/nix/store/' in folded
            and '.drv": no such file or directory' in folded
        ):
            return True
    return False


def _has_store_transient_signal(output: str) -> bool:
    """Return whether *output* names a hosted-macOS store I/O fault."""
    if contains_marker(output, NIX_STORE_TRANSIENT_MARKERS):
        return True
    return _has_missing_store_drv(output) or _has_vanished_store_artifact(output)


def _has_permanent_build_failure(output: str) -> bool:
    """Return whether Nix attributed the output to a derivation, not the store."""
    if has_hash_mismatch_signal(output):
        return True
    folded = output.casefold()
    if not any(marker in folded for marker in _PERMANENT_BUILD_FAILURE_MARKERS):
        return False
    # Hosted macos-15 can EILSEQ while realizing a substitute, then lose the
    # .drv. Nix reports "Cannot build X / Reason: 1 dependency failed" for
    # dependents. That is still a store fault unless a builder actually exited.
    if _has_store_transient_signal(output):
        return _has_builder_exit(output)
    return True


def _mentions_store_bus_signal(text: str) -> bool:
    """Return whether *text* names SIGBUS without matching a longer signal number."""
    folded = text.casefold()
    for number in _STORE_BUS_SIGNALS:
        needle = f"terminated by signal {number}"
        start = 0
        while (index := folded.find(needle, start)) != -1:
            end = index + len(needle)
            if end == len(folded) or not folded[end].isdigit():
                return True
            start = end
    return False


def is_retryable_nix_network_failure(*, stdout: str, stderr: str) -> bool:
    """Return whether Nix reported a transient network or substituter failure.

    Shared owner for updater FOD prefetch, zon2nix/zig fetch, rusty_v8 git
    503, and GOPROXY HTTP/2 ``INTERNAL_ERROR``.
    """
    output = f"{stderr}\n{stdout}"
    if has_hash_mismatch_signal(output):
        return False
    return bool(
        _CANNOT_DOWNLOAD_FROM_MIRROR.search(output)
        or _HTTP_5XX.search(output)
        or contains_marker(output, NIX_NETWORK_TRANSIENT_MARKERS)
    )


def is_retryable_nix_store_failure(*, stdout: str, stderr: str) -> bool:
    """Return whether Nix hit a transient store I/O fault, not a bad derivation."""
    output = f"{stderr}\n{stdout}"
    if _has_permanent_build_failure(output):
        return False
    return _has_store_transient_signal(output)


def should_consult_nix_builder_logs(*, stdout: str, stderr: str) -> bool:
    """Return whether streamed Nix output may have truncated a write-EIO.

    Nix prints the last 25 builder lines. Parallel cmake (38394692516
    buzz-onnxruntime ranlib EIO at 41%) then scrolls that line out, leaving
    only ``Cannot build`` + exit 2. Fetch ``nix log`` before treating that as
    a permanent derivation failure.
    """
    output = f"{stderr}\n{stdout}"
    if has_hash_mismatch_signal(output) or contains_marker(output, _WRITE_EIO_MARKERS):
        return False
    if is_retryable_nix_store_failure(stdout=stdout, stderr=stderr):
        return False
    folded = output.casefold()
    if not any(marker in folded for marker in _PERMANENT_BUILD_FAILURE_MARKERS):
        return False
    return _has_builder_exit(output) and bool(failed_derivation_paths(output))


def is_transient_store_interruption(text: str) -> bool:
    """Return whether validation stopped on a runner store fault.

    Hosted macOS jobs lose the store mid-build (``Illegal byte sequence``,
    write-EIO / ``can't write to output file``, a vanished store ``.drv``, a
    vanished store build input, an unreadable Cachix-substituted crate2nix
    rlib, a crashed Nix daemon, or SIGBUS). A hash mismatch or a builder that
    actually exited (``failed with exit code``, ``error: builder for``) must
    not continue as if the store had only faulted, unless that exit is the
    write-EIO itself. ``error: Cannot build`` / ``Reason: 1 dependency
    failed`` after a substitute EILSEQ is still a store fault, as is
    ``builder failed with exit code 1`` when the only builder log is a
    vanished ``/nix/store/`` build input, a missing
    ``/nix/store/.../.fetcher-version`` after a leftover pnpm-deps substitute,
    or rustc E0463 after ``--extern`` named a store rlib that rustc then
    reported missing or unreadable. A fixed-output hash mismatch (38669200592
    darwin-argus ``bb-pnpm-deps``) is a stale pin, not a store fault.
    """
    if _has_permanent_build_failure(text):
        return False
    if is_retryable_nix_store_failure(stdout="", stderr=text):
        return True
    return _mentions_store_bus_signal(text)


def is_retryable_fixed_output_failure(*, stdout: str, stderr: str) -> bool:
    """Return whether a fixed-output hash build failed before producing a hash."""
    output = f"{stderr}\n{stdout}"
    if has_hash_mismatch_signal(output):
        return False
    if is_retryable_nix_network_failure(
        stdout=stdout, stderr=stderr
    ) or is_retryable_nix_store_failure(stdout=stdout, stderr=stderr):
        return True
    return contains_marker(output, FIXED_OUTPUT_TRANSIENT_MARKERS)


__all__ = [
    "AIOHTTP_CONNECT_MARKERS",
    "CURL_TRANSFER_MARKERS",
    "DNS_RESOLUTION_MARKERS",
    "FIXED_OUTPUT_TRANSIENT_MARKERS",
    "HASH_MISMATCH_INDICATORS",
    "HTTP_GATEWAY_MARKERS",
    "NIX_NETWORK_TRANSIENT_MARKERS",
    "NIX_STORE_TRANSIENT_MARKERS",
    "PYTHON_RESOLVER_MARKERS",
    "TRANSFER_TIMEOUT_MARKERS",
    "contains_marker",
    "corrupt_store_paths",
    "failed_derivation_paths",
    "has_hash_mismatch_signal",
    "is_retryable_fixed_output_failure",
    "is_retryable_nix_network_failure",
    "is_retryable_nix_store_failure",
    "is_transient_store_interruption",
    "should_consult_nix_builder_logs",
]
