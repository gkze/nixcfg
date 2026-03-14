"""Nix-based hash computation helpers for updater implementations."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import platform
from typing import TYPE_CHECKING

from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.operator import Operator
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet
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
    expect_command_result,
    require_value,
)
from lib.update.flake import nixpkgs_expression
from lib.update.nix_expr import compact_nix_expr
from lib.update.paths import get_repo_file
from lib.update.process import (
    NixBuildOptions,
    RunCommandOptions,
    convert_nix_hash_to_sri,
    run_command,
    run_nix_build,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


_ARCH_ALIASES = {
    "aarch64": "aarch64",
    "amd64": "x86_64",
    "arm64": "aarch64",
    "x86_64": "x86_64",
}

_OS_ALIASES = {
    "darwin": "darwin",
    "linux": "linux",
}


def normalize_nix_platform(machine: str, os_name: str) -> str:
    """Normalize machine/OS names into a Nix platform identifier."""
    normalized_machine = machine.lower()
    normalized_os = os_name.lower()
    arch = _ARCH_ALIASES.get(normalized_machine, normalized_machine)
    nix_os = _OS_ALIASES.get(normalized_os, normalized_os)
    return f"{arch}-{nix_os}"


def get_current_nix_platform() -> str:
    """Return the current machine as a Nix platform string."""
    return normalize_nix_platform(platform.machine(), platform.system())


_HASH_MISMATCH_INDICATORS = (
    "hash mismatch",
    "HashMismatch",
    "specified:",
)

_PLATFORM_HASH_PAYLOAD_SIZE = 2


def _quote_attr(name: str) -> str:
    return StringPrimitive(value=name).rebuild()


def _select_attrs(expression: NixExpression, *attributes: str) -> NixExpression:
    selected = expression
    for attribute in attributes:
        selected = Select(expression=selected, attribute=attribute)
    return selected


def _nix_string_or_expr(value: str | NixExpression) -> NixExpression:
    if isinstance(value, NixExpression):
        return value
    return StringPrimitive(value=value)


def _fake_hash_expr() -> NixExpression:
    return _select_attrs(Identifier(name="pkgs"), "lib", "fakeHash")


def _build_get_flake_expr(flake_url: str) -> FunctionCall:
    return FunctionCall(
        name=_select_attrs(Identifier(name="builtins"), "getFlake"),
        argument=StringPrimitive(value=flake_url),
    )


def _build_fetch_from_github_call(
    owner: str,
    repo: str,
    *,
    rev: str | None = None,
    tag: str | None = None,
    hash_value: str | NixExpression | None = None,
    post_fetch: str | None = None,
) -> FunctionCall:
    if (rev is None) == (tag is None):
        msg = "Expected exactly one of rev or tag for fetchFromGitHub"
        raise ValueError(msg)

    bindings: list[Binding | Inherit] = [
        Binding(name="owner", value=owner),
        Binding(name="repo", value=repo),
        Binding(
            name="hash",
            value=_fake_hash_expr()
            if hash_value is None
            else _nix_string_or_expr(hash_value),
        ),
    ]
    if rev is not None:
        bindings.append(Binding(name="rev", value=rev))
    if tag is not None:
        bindings.append(Binding(name="tag", value=tag))
    if post_fetch is not None:
        bindings.append(Binding(name="postFetch", value=post_fetch))
    return FunctionCall(
        name=_select_attrs(Identifier(name="pkgs"), "fetchFromGitHub"),
        argument=AttributeSet(values=bindings),
    )


def _build_fetch_from_github_expr(
    owner: str,
    repo: str,
    *,
    rev: str | None = None,
    tag: str | None = None,
    hash_value: str | NixExpression | None = None,
    post_fetch: str | None = None,
) -> str:
    return compact_nix_expr(
        _build_fetch_from_github_call(
            owner,
            repo,
            rev=rev,
            tag=tag,
            hash_value=hash_value,
            post_fetch=post_fetch,
        ).rebuild(),
    )


def _build_fetch_yarn_deps_expr(
    src_expr: NixExpression,
    *,
    yarn_lock_suffix: str = "/yarn.lock",
    hash_value: str | NixExpression | None = None,
) -> str:
    expression = LetExpression(
        local_variables=[Binding(name="src", value=src_expr)],
        value=FunctionCall(
            name=_select_attrs(Identifier(name="pkgs"), "fetchYarnDeps"),
            argument=AttributeSet(
                values=[
                    Binding(
                        name="yarnLock",
                        value=BinaryExpression(
                            left=Identifier(name="src"),
                            operator=Operator(name="+"),
                            right=StringPrimitive(value=yarn_lock_suffix),
                        ),
                    ),
                    Binding(
                        name="hash",
                        value=_fake_hash_expr()
                        if hash_value is None
                        else _nix_string_or_expr(hash_value),
                    ),
                ]
            ),
        ),
    )
    return compact_nix_expr(expression.rebuild())


def _build_flake_attr_expr(
    flake_url: str,
    *attributes: str,
    quoted_indices: tuple[int, ...] = (),
) -> str:
    quoted = set(quoted_indices)
    value: NixExpression = Identifier(name="flake")
    for index, attribute in enumerate(attributes):
        value = Select(
            expression=value,
            attribute=_quote_attr(attribute) if index in quoted else attribute,
        )
    expression = LetExpression(
        local_variables=[Binding(name="flake", value=_build_get_flake_expr(flake_url))],
        value=value,
    )
    return compact_nix_expr(expression.rebuild())


def _build_overlay_attr_expr(
    source: str,
    attr_path: str,
    *,
    system: str | None = None,
) -> str:
    expression: NixExpression = Parenthesis(
        value=_build_overlay_expression(source, system=system),
    )
    for attribute in attr_path.removeprefix(".").split("."):
        if not attribute:
            continue
        expression = Select(expression=expression, attribute=attribute)
    return compact_nix_expr(expression.rebuild())


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


@dataclasses.dataclass(frozen=True)
class _FixedOutputBuildOptions:
    success_error: str
    allow_failure: bool = False
    suppress_patterns: tuple[str, ...] | None = None
    env: Mapping[str, str] | None = None
    verbose: bool = False
    config: UpdateConfig | None = None


async def _run_fixed_output_build(
    source: str,
    expr: str,
    *,
    options: _FixedOutputBuildOptions,
) -> EventStream:
    result_drain = ValueDrain()
    async for event in drain_value_events(
        run_nix_build(
            expr,
            options=NixBuildOptions(
                source=source,
                allow_failure=options.allow_failure,
                suppress_patterns=options.suppress_patterns,
                env=options.env,
                verbose=options.verbose,
                config=options.config,
            ),
        ),
        result_drain,
        parse=expect_command_result,
    ):
        yield event
    result = require_value(result_drain, "nix build did not return output")
    if result.returncode == 0:
        raise RuntimeError(options.success_error)
    yield UpdateEvent.value(source, result)


@dataclasses.dataclass
class _NixBuildSemaphoreState:
    semaphore: asyncio.Semaphore | None = None
    size: int | None = None


_NIX_BUILD_SEMAPHORE_STATE = _NixBuildSemaphoreState()


def _get_nix_build_semaphore(config: UpdateConfig) -> asyncio.Semaphore:
    """Lazily create a semaphore to limit concurrent ``nix build`` processes.

    Each ``nix build --impure`` evaluates nixpkgs with the full overlay, using
    1-2 GB of RAM.  Without a limit, running all sources concurrently can
    exhaust memory.
    """
    if (
        _NIX_BUILD_SEMAPHORE_STATE.semaphore is None
        or _NIX_BUILD_SEMAPHORE_STATE.size != config.max_nix_builds
    ):
        _NIX_BUILD_SEMAPHORE_STATE.semaphore = asyncio.Semaphore(config.max_nix_builds)
        _NIX_BUILD_SEMAPHORE_STATE.size = config.max_nix_builds
    semaphore = _NIX_BUILD_SEMAPHORE_STATE.semaphore
    if semaphore is None:
        msg = "failed to initialize nix build semaphore"
        raise RuntimeError(msg)
    return semaphore


async def compute_fixed_output_hash(
    source: str,
    expr: str,
    *,
    env: Mapping[str, str] | None = None,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute an SRI hash by extracting nix fixed-output mismatch output."""
    config = resolve_active_config(config)
    expr = _build_nix_expr(expr)
    semaphore = _get_nix_build_semaphore(config)
    async with semaphore:
        result_drain = ValueDrain()
        async for event in drain_value_events(
            _run_fixed_output_build(
                source,
                expr,
                options=_FixedOutputBuildOptions(
                    allow_failure=True,
                    suppress_patterns=FIXED_OUTPUT_NOISE,
                    verbose=True,
                    success_error=(
                        "Expected nix build to fail with hash mismatch, but it succeeded"
                    ),
                    env=env,
                    config=config,
                ),
            ),
            result_drain,
            parse=expect_command_result,
        ):
            yield event
        result = require_value(result_drain, "nix build did not return output")
        async for event in _emit_sri_hash_from_build_result(
            source,
            result,
            config=config,
        ):
            yield event


def _build_nix_expr(body: str | NixExpression) -> str:
    expression = LetExpression(
        local_variables=[Binding(name="pkgs", value=nixpkgs_expression())],
        value=parse(body).expr if isinstance(body, str) else body,
    )
    return compact_nix_expr(expression.rebuild())


def _build_overlay_expression(
    source: str, *, system: str | None = None
) -> NixExpression:
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
    flake_url = f"git+file://{get_repo_file('.')}?dirty=1"
    system_expr: NixExpression = (
        _select_attrs(Identifier(name="builtins"), "currentSystem")
        if system is None
        else StringPrimitive(value=system)
    )
    import_nixpkgs = FunctionCall(
        name=Identifier(name="import"),
        argument=_select_attrs(Identifier(name="flake"), "inputs", "nixpkgs"),
    )
    config_expr = AttributeSet(
        values=[
            Binding(name="allowUnfree", value=Primitive(value=True)),
            Binding(
                name="allowInsecurePredicate",
                value=FunctionDefinition(
                    argument_set=Identifier(name="_"),
                    output=Primitive(value=True),
                ),
            ),
        ],
    )
    overlay_fn = _select_attrs(Identifier(name="flake"), "overlays", "default")
    overlay_applied = FunctionCall(
        name=FunctionCall(name=overlay_fn, argument=Identifier(name="self")),
        argument=Identifier(name="pkgs"),
    )
    return LetExpression(
        local_variables=[
            Binding(name="flake", value=_build_get_flake_expr(flake_url)),
            Binding(name="system", value=system_expr),
            Binding(
                name="pkgs",
                value=FunctionCall(
                    name=import_nixpkgs,
                    argument=AttributeSet(
                        values=[
                            Inherit(names=[Identifier(name="system")]),
                            Binding(name="config", value=config_expr),
                        ],
                    ),
                ),
            ),
            Binding(
                name="applied",
                value=FunctionCall(
                    name=_select_attrs(Identifier(name="pkgs"), "lib", "fix"),
                    argument=Parenthesis(
                        value=FunctionDefinition(
                            argument_set=Identifier(name="self"),
                            output=BinaryExpression(
                                operator=Operator(name="//"),
                                left=Identifier(name="pkgs"),
                                right=overlay_applied,
                            ),
                        ),
                    ),
                ),
            ),
        ],
        value=Select(
            expression=Identifier(name="applied"),
            attribute=_quote_attr(source),
        ),
    )


def _build_overlay_expr(source: str, *, system: str | None = None) -> str:
    return compact_nix_expr(_build_overlay_expression(source, system=system).rebuild())


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

    result_drain = ValueDrain()
    async for _event in drain_value_events(
        run_command(
            args,
            options=RunCommandOptions(
                source=source,
                error="nix derivation show did not return output",
                env={"FAKE_HASHES": "1"},
                config=config,
            ),
        ),
        result_drain,
        parse=expect_command_result,
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


__all__ = [
    "_build_fetch_from_github_call",
    "_build_fetch_from_github_expr",
    "_build_fetch_yarn_deps_expr",
    "_build_flake_attr_expr",
    "_build_nix_expr",
    "_build_overlay_attr_expr",
    "_build_overlay_expr",
    "_emit_sri_hash_from_build_result",
    "_run_fixed_output_build",
    "compute_drv_fingerprint",
    "compute_fixed_output_hash",
    "compute_overlay_hash",
    "get_current_nix_platform",
    "normalize_nix_platform",
]
