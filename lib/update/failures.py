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
)

# Upstream gateway errors in the spellings Nix, curl, and HTTP servers use.
HTTP_GATEWAY_MARKERS = (
    "502 Bad Gateway",
    "503 Service Unavailable",
    "504 Gateway Time-out",
    "HTTP error 502",
    "HTTP error 503",
    "HTTP error 504",
    "returned error: 502",
    "returned error: 503",
    "returned error: 504",
)

# Every transient network fault Nix can report while fetching or substituting.
NIX_NETWORK_TRANSIENT_MARKERS = (
    *CURL_TRANSFER_MARKERS,
    *HTTP_GATEWAY_MARKERS,
    "NameServerFailure",
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
    "cannot download source from any mirror",
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
# /nix/store/...rlib), or a crashed daemon. None of those are derivation
# failures.
NIX_STORE_TRANSIENT_MARKERS = (
    "Illegal byte sequence",
    "Nix daemon disconnected unexpectedly",
    "cannot open connection to remote store 'daemon'",
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
    """Return whether rustc exited E0463 after ``--extern`` named a store rlib.

    Hosted macos-15 can copy a crate2nix ``-lib`` output from Cachix and still
    leave the hashed rlib missing or unreadable. rustc then exits 1 with
    ``error[E0463]: can't find crate`` even though the command line named
    ``--extern <crate>=/nix/store/…-lib/lib/lib<crate>-<hash>.rlib``.

    Update #1244 hit that on ``rust_agent_settings`` immediately after
    substituting ``rust_settings`` / ``rust_project`` ``-lib`` paths. The
    crate names matched Cargo.nix; the same extra-filename scheme built those
    crates earlier. That is the same store fault as a vanished build input,
    not a missing crate in the generated graph. E0463 without a store rlib
    ``--extern``, or E0786 (stripped ``.rmeta``), is not this signal.
    """
    if _RUSTC_CANT_FIND_CRATE.search(output) is None:
        return False
    return _STORE_EXTERN_RLIB.search(output) is not None


def _has_vanished_store_artifact(output: str) -> bool:
    """Return whether a builder died only because a store input vanished."""
    return _has_vanished_store_build_input(output) or _has_unreadable_store_rlib(output)


def _has_builder_exit(output: str) -> bool:
    if _has_vanished_store_artifact(output):
        return False
    folded = output.casefold()
    return any(marker in folded for marker in _BUILDER_EXIT_MARKERS)


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
    """Return whether Nix reported a transient network or substituter failure."""
    output = f"{stderr}\n{stdout}"
    if has_hash_mismatch_signal(output):
        return False
    return contains_marker(output, NIX_NETWORK_TRANSIENT_MARKERS)


def is_retryable_nix_store_failure(*, stdout: str, stderr: str) -> bool:
    """Return whether Nix hit a transient store I/O fault, not a bad derivation."""
    output = f"{stderr}\n{stdout}"
    if _has_permanent_build_failure(output):
        return False
    return _has_store_transient_signal(output)


def is_transient_store_interruption(text: str) -> bool:
    """Return whether validation stopped on a runner store fault.

    Hosted macOS jobs lose the store mid-build (``Illegal byte sequence``,
    a vanished store ``.drv``, a vanished store build input, an unreadable
    Cachix-substituted crate2nix rlib, a crashed Nix daemon, or SIGBUS). A
    hash mismatch or a builder that actually exited (``failed with exit
    code``, ``error: builder for``) must not continue as if the store had
    only faulted. ``error: Cannot build`` / ``Reason: 1 dependency failed``
    after a substitute EILSEQ is still a store fault, as is ``builder
    failed with exit code 1`` when the only builder log is a vanished
    ``/nix/store/`` build input or rustc E0463 after ``--extern`` named a
    store rlib.
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
    "has_hash_mismatch_signal",
    "is_retryable_fixed_output_failure",
    "is_retryable_nix_network_failure",
    "is_retryable_nix_store_failure",
    "is_transient_store_interruption",
]
