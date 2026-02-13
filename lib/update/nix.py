"""Nix-based hash computation helpers for updater implementations."""

from __future__ import annotations

import asyncio
import json
import platform
from typing import TYPE_CHECKING

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.parser import parse

from lib.nix.commands.base import CommandResult as LibnixResult
from lib.nix.commands.base import HashMismatchError
from lib.nix.models.hash import is_sri
from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.constants import FIXED_OUTPUT_NOISE
from lib.update.events import (
    CommandResult,
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    require_value,
)
from lib.update.flake import nixpkgs_expr
from lib.update.nix_expr import compact_nix_expr
from lib.update.paths import get_repo_file
from lib.update.process import convert_nix_hash_to_sri, run_command, run_nix_build

if TYPE_CHECKING:
    from collections.abc import Mapping


def get_current_nix_platform() -> str:
    """Return the current machine as a Nix platform string."""
    machine = platform.machine()
    system = platform.system().lower()

    arch_map = {"arm64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}
    arch = arch_map.get(machine, machine)

    return f"{arch}-{system}"


_HASH_MISMATCH_INDICATORS = (
    "hash mismatch",
    "HashMismatch",
    "specified:",
)

_PLATFORM_HASH_PAYLOAD_SIZE = 2


def _extract_nix_hash(output: str, *, config: UpdateConfig | None = None) -> str:
    """Extract the 'got' hash from a Nix hash-mismatch error.

    Delegates to :class:`lib.nix.commands.base.HashMismatchError` for the
    actual regex matching (single source of truth for all hash formats).
    """
    dummy = LibnixResult(args=[], returncode=1, stdout="", stderr=output)
    err = HashMismatchError.from_output(output, dummy)
    if err is not None:
        return err.hash
    config = resolve_active_config(config)
    has_mismatch_signal = any(
        indicator in output for indicator in _HASH_MISMATCH_INDICATORS
    )
    if has_mismatch_signal:
        msg = (
            "Hash mismatch detected in nix output but could not extract the hash. "
            "This likely means Nix changed its error format — update the regex in "
            "lib.nix.commands.base.HashMismatchError. Output tail:\n"
            f"{_tail_output_excerpt(output, max_lines=config.default_log_tail_lines)}"
        )
    else:
        msg = (
            "Could not find hash in nix output. Output tail:\n"
            f"{_tail_output_excerpt(output, max_lines=config.default_log_tail_lines)}"
        )
    raise RuntimeError(msg)


def _tail_output_excerpt(output: str, *, max_lines: int) -> str:
    output = output.strip()
    if not output:
        return "<no output>"
    lines = output.splitlines()
    if len(lines) <= max_lines:
        return output
    tail = "\n".join(lines[-max_lines:])
    return f"... (last {max_lines} of {len(lines)} lines)\n{tail}"


async def _emit_sri_hash_from_build_result(
    source: str,
    result: CommandResult,
    *,
    config: UpdateConfig | None = None,
) -> EventStream:
    hash_value = _extract_nix_hash(result.stderr + result.stdout, config=config)
    if is_sri(hash_value):
        yield UpdateEvent.value(source, hash_value)
        return
    async for event in convert_nix_hash_to_sri(source, hash_value):
        yield event


async def _run_fixed_output_build(  # noqa: PLR0913
    source: str,
    expr: str,
    *,
    allow_failure: bool = False,
    suppress_patterns: tuple[str, ...] | None = None,
    env: Mapping[str, str] | None = None,
    verbose: bool = False,
    success_error: str,
    config: UpdateConfig | None = None,
) -> EventStream:
    result_drain = ValueDrain[CommandResult]()
    async for event in drain_value_events(
        run_nix_build(
            source,
            expr,
            allow_failure=allow_failure,
            suppress_patterns=suppress_patterns,
            env=env,
            verbose=verbose,
            config=config,
        ),
        result_drain,
    ):
        yield event
    result = require_value(result_drain, "nix build did not return output")
    if result.returncode == 0:
        raise RuntimeError(success_error)
    yield UpdateEvent.value(source, result)


_nix_build_semaphore: asyncio.Semaphore | None = None
_nix_build_semaphore_size: int | None = None


def _get_nix_build_semaphore(config: UpdateConfig) -> asyncio.Semaphore:
    """Lazily create a semaphore to limit concurrent ``nix build`` processes.

    Each ``nix build --impure`` evaluates nixpkgs with the full overlay, using
    1-2 GB of RAM.  Without a limit, running all sources concurrently can
    exhaust memory.
    """
    global _nix_build_semaphore  # noqa: PLW0603
    global _nix_build_semaphore_size  # noqa: PLW0603
    if (
        _nix_build_semaphore is None
        or _nix_build_semaphore_size != config.max_nix_builds
    ):
        _nix_build_semaphore = asyncio.Semaphore(config.max_nix_builds)
        _nix_build_semaphore_size = config.max_nix_builds
    return _nix_build_semaphore


async def compute_fixed_output_hash(
    source: str,
    expr: str,
    *,
    env: Mapping[str, str] | None = None,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute an SRI hash by extracting nix fixed-output mismatch output."""
    config = resolve_active_config(config)
    expr = compact_nix_expr(expr)
    semaphore = _get_nix_build_semaphore(config)
    async with semaphore:
        result_drain = ValueDrain[CommandResult]()
        async for event in drain_value_events(
            _run_fixed_output_build(
                source,
                expr,
                allow_failure=True,
                suppress_patterns=FIXED_OUTPUT_NOISE,
                verbose=True,
                success_error="Expected nix build to fail with hash mismatch, but it succeeded",
                env=env,
                config=config,
            ),
            result_drain,
        ):
            yield event
        result = require_value(result_drain, "nix build did not return output")
        async for event in _emit_sri_hash_from_build_result(
            source,
            result,
            config=config,
        ):
            yield event


def _build_nix_expr(body: str) -> str:
    expression = LetExpression(
        local_variables=[Binding(name="pkgs", value=parse(nixpkgs_expr()).expr)],
        value=parse(body).expr,
    )
    return compact_nix_expr(expression.rebuild())


def _build_overlay_expr(source: str, *, system: str | None = None) -> str:
    """Build a Nix expression that evaluates an overlay package.

    Uses a manual fixed-point (``lib.fix``) to apply the flake overlay on top
    of a plain nixpkgs import.  This avoids infinite recursion that occurs
    when using ``import nixpkgs { overlays = [ ... ]; }`` — that codepath
    triggers ``with self;`` in ``pkgs/top-level/aliases.nix`` which re-enters
    the overlay before its own attributes are defined, producing an infinite
    recursion on newer nixpkgs revisions.

    The ``lib.fix`` approach creates the self-referential attribute set
    *outside* of nixpkgs' own overlay machinery, so the overlay's ``final``
    parameter correctly resolves to ``pkgs // overlay final pkgs`` without
    hitting the aliases.nix ``with self`` trap.
    """
    system_nix = "builtins.currentSystem" if system is None else f'"{system}"'
    flake_url = f"git+file://{get_repo_file('.')}?dirty=1"
    # Build the expression as a raw Nix string — the nix-manipulator AST
    # does not have a node type for `lib.fix (self: ...)` lambdas, so a
    # raw template is cleaner and easier to audit than splicing AST nodes.
    return (
        "let"
        f'  flake = builtins.getFlake "{flake_url}";'
        f"  system = {system_nix};"
        "  pkgs = import flake.inputs.nixpkgs {"
        "    inherit system;"
        "    config = { allowUnfree = true; allowInsecurePredicate = _: true; };"
        "  };"
        "  applied = pkgs.lib.fix (self: pkgs // flake.overlays.default self pkgs);"
        f'in applied."{source}"'
    )


async def compute_overlay_hash(
    source: str,
    *,
    system: str | None = None,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute a hash by building the overlay package with ``FAKE_HASHES=1``.

    Builds ``pkgs."{source}"`` with ``FAKE_HASHES=1`` so that ``lib.nix``'s
    ``sourceHash*`` functions return ``lib.fakeHash``.  The real overlay
    derivation then fails with a hash-mismatch error from which we extract
    the correct hash.

    The overlay definition in ``overlays/default.nix`` is the single source of truth.
    """
    expr = _build_overlay_expr(source, system=system)
    async for event in compute_fixed_output_hash(
        source,
        expr,
        config=config,
        env={"FAKE_HASHES": "1"},
    ):
        yield event


async def compute_drv_fingerprint(
    source: str,
    *,
    system: str | None = None,
    config: UpdateConfig | None = None,
) -> str:
    """Compute a stable derivation fingerprint for staleness detection.

    Evaluates the package with ``FAKE_HASHES=1`` and extracts the ``.drv``
    store-path hash using ``nix derivation show``.  Because the fake hash is
    a constant sentinel, the ``.drv`` path is a pure function of the build
    input closure (source, toolchain, build script, stdenv, etc.).

    Any change to *any* transitive build input — a nixpkgs bump, a Deno
    version change, a source force-push, a build-script edit — changes the
    ``.drv`` hash.  Conversely, identical inputs always produce the same
    hash.  This gives us maximally precise staleness detection: zero false
    negatives and zero false positives.
    """
    config = resolve_active_config(config)
    expr = _build_overlay_expr(source, system=system)
    expr = compact_nix_expr(expr)
    args = ["nix", "derivation", "show", "--quiet", "--impure", "--expr", expr]

    result_drain = ValueDrain[CommandResult]()
    async for _event in drain_value_events(
        run_command(
            args,
            source=source,
            error="nix derivation show did not return output",
            env={"FAKE_HASHES": "1"},
            config=config,
        ),
        result_drain,
    ):
        pass  # discard streaming events during fingerprint eval
    result = require_value(result_drain, "nix derivation show did not return output")
    if result.returncode != 0:
        msg = f"nix derivation show failed:\n{result.stderr}"
        raise RuntimeError(msg)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        msg = f"Failed to parse nix derivation show output: {exc}"
        raise RuntimeError(msg) from exc

    # New Nix versions (2.20+) wrap derivations under a "derivations" key;
    # older versions use the .drv path directly as a top-level key.
    if "derivations" in data and isinstance(data["derivations"], dict):
        drv_path = next(iter(data["derivations"]))
    else:
        drv_path = next(iter(data))

    # The .drv key is "<hash>-<name>.drv" (Nix 2.20+) or the full
    # "/nix/store/<hash>-<name>.drv" (older).  Strip the store prefix if
    # present so the fingerprint is just the Nix hash portion regardless
    # of Nix version.  A Nix version change that alters the derivation
    # hash algorithm would change the fingerprint, conservatively
    # triggering recomputation — the correct behaviour.
    if "/" in drv_path:
        drv_path = drv_path.rsplit("/", 1)[-1]
    return drv_path.split("-", 1)[0]


def compute_go_vendor_hash(
    source: str,
    _input_name: str = "",
    *,
    config: UpdateConfig | None = None,
    **_kwargs: object,
) -> EventStream:
    """Compute Go vendor hash via overlay with ``FAKE_HASHES=1``."""
    return _compute_overlay_based_hash(source, config=config)


def compute_cargo_vendor_hash(
    source: str,
    _input_name: str = "",
    *,
    config: UpdateConfig | None = None,
    **_kwargs: object,
) -> EventStream:
    """Compute Cargo vendor hash via overlay with ``FAKE_HASHES=1``."""
    return _compute_overlay_based_hash(source, config=config)


def compute_npm_deps_hash(
    source: str,
    _input_name: str = "",
    *,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute NPM deps hash via overlay with ``FAKE_HASHES=1``."""
    return _compute_overlay_based_hash(source, config=config)


def compute_bun_node_modules_hash(
    source: str,
    _input_name: str = "",
    *,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute bun node_modules hash via overlay with ``FAKE_HASHES=1``."""
    return _compute_overlay_based_hash(
        source,
        system=get_current_nix_platform(),
        config=config,
    )


async def _compute_overlay_based_hash(
    source: str,
    *,
    system: str | None = None,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute a fixed-output hash via the package overlay."""
    async for event in compute_overlay_hash(source, system=system, config=config):
        yield event


__all__ = [
    "_build_nix_expr",
    "_build_overlay_expr",
    "_emit_sri_hash_from_build_result",
    "_run_fixed_output_build",
    "compute_bun_node_modules_hash",
    "compute_cargo_vendor_hash",
    "compute_drv_fingerprint",
    "compute_fixed_output_hash",
    "compute_go_vendor_hash",
    "compute_npm_deps_hash",
    "compute_overlay_hash",
    "get_current_nix_platform",
]
