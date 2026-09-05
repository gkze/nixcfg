"""Nix-based hash computation helpers for updater implementations."""

import asyncio
import dataclasses
import json
import platform
from pathlib import Path
from typing import TYPE_CHECKING

from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.list import NixList
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
    StatusInfo,
    StatusKind,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_command_result,
    require_value,
)
from lib.update.flake import nixpkgs_expression
from lib.update.nix_expr import compact_nix_expr, select_attrs
from lib.update.paths import get_repo_file, local_flake_url
from lib.update.process import (
    NixBuildOptions,
    RunCommandOptions,
    convert_nix_hash_to_sri,
    run_command,
    run_nix_build,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lib.nix.models.sources import SourceEntry


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

_FIXED_OUTPUT_HASH_MAX_ATTEMPTS = 3
_NIX_NETWORK_TRANSIENT_MARKERS = (
    "502 Bad Gateway",
    "503 Service Unavailable",
    "504 Gateway Time-out",
    "Could not resolve host",
    "Failure when receiving data from the peer",
    "Failed to connect",
    "HTTP error 502",
    "HTTP error 503",
    "HTTP error 504",
    "HTTP protocol violation",
    "HTTP/2 framing layer",
    "HTTP/2 stream",
    "NameServerFailure",
    "Operation too slow",
    "Temporary failure in name resolution",
    "connection reset",
    "returned error: 502",
    "returned error: 503",
    "returned error: 504",
)

_FIXED_OUTPUT_ONLY_TRANSIENT_MARKERS = (
    "Operation timed out",
    "aborted due to timeout",
    "cannot download source from any mirror",
    "Fail extracting tarball",
    "timed out",
)

_PLATFORM_HASH_PAYLOAD_SIZE = 2


def _quote_attr(name: str) -> str:
    return StringPrimitive(value=name).rebuild()


def _nix_string_or_expr(value: str | NixExpression) -> NixExpression:
    if isinstance(value, NixExpression):
        return value
    return StringPrimitive(value=value)


def _fake_hash_expr() -> NixExpression:
    return select_attrs(Identifier(name="pkgs"), "lib", "fakeHash")


def _build_get_flake_expr(flake_url: str) -> FunctionCall:
    return FunctionCall(
        name=select_attrs(Identifier(name="builtins"), "getFlake"),
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
    fetch_submodules: bool | None = None,
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
    if fetch_submodules is not None:
        bindings.append(
            Binding(name="fetchSubmodules", value=Primitive(value=fetch_submodules))
        )
    return FunctionCall(
        name=select_attrs(Identifier(name="pkgs"), "fetchFromGitHub"),
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
    fetch_submodules: bool | None = None,
) -> str:
    return compact_nix_expr(
        _build_fetch_from_github_call(
            owner,
            repo,
            rev=rev,
            tag=tag,
            hash_value=hash_value,
            post_fetch=post_fetch,
            fetch_submodules=fetch_submodules,
        ).rebuild(),
    )


def _build_fetchgit_call(
    url: str,
    rev: str,
    *,
    hash_value: str | NixExpression | None = None,
    fetch_submodules: bool = True,
) -> FunctionCall:
    bindings: list[Binding | Inherit] = [
        Binding(name="url", value=url),
        Binding(name="rev", value=rev),
        Binding(
            name="hash",
            value=_fake_hash_expr()
            if hash_value is None
            else _nix_string_or_expr(hash_value),
        ),
    ]
    bindings.append(
        Binding(name="fetchSubmodules", value=Primitive(value=fetch_submodules))
    )
    return FunctionCall(
        name=select_attrs(Identifier(name="pkgs"), "fetchgit"),
        argument=AttributeSet(values=bindings),
    )


def _build_fetchgit_expr(
    url: str,
    rev: str,
    *,
    hash_value: str | NixExpression | None = None,
    fetch_submodules: bool = True,
) -> str:
    return compact_nix_expr(
        _build_fetchgit_call(
            url,
            rev,
            hash_value=hash_value,
            fetch_submodules=fetch_submodules,
        ).rebuild(),
    )


def _build_fetch_pnpm_deps_expr(
    src_expr: NixExpression,
    *,
    pname: str,
    version: str,
    fetcher_version: int,
    pnpm: NixExpression | None = None,
    hash_value: str | NixExpression | None = None,
) -> str:
    fetch_pnpm_bindings: list[Binding | Inherit] = [
        Binding(name="pname", value=pname),
        Binding(name="version", value=version),
        Inherit(names=[Identifier(name="src")]),
    ]
    if pnpm is not None:
        fetch_pnpm_bindings.append(Binding(name="pnpm", value=pnpm))
    fetch_pnpm_bindings.extend([
        Binding(name="fetcherVersion", value=Primitive(value=fetcher_version)),
        Binding(
            name="hash",
            value=_fake_hash_expr()
            if hash_value is None
            else _nix_string_or_expr(hash_value),
        ),
    ])
    expression = LetExpression(
        local_variables=[Binding(name="src", value=src_expr)],
        value=FunctionCall(
            name=select_attrs(Identifier(name="pkgs"), "fetchPnpmDeps"),
            argument=AttributeSet(values=fetch_pnpm_bindings),
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
    source_overrides: Mapping[str, SourceEntry] | None = None,
    fake_hashes: bool | None = None,
) -> str:
    expression: NixExpression = Parenthesis(
        value=_build_overlay_expression(
            source,
            system=system,
            source_overrides=source_overrides,
            fake_hashes=fake_hashes,
        ),
    )
    for attribute in attr_path.removeprefix(".").split("."):
        if not attribute:
            continue
        expression = Select(expression=expression, attribute=attribute)
    return compact_nix_expr(expression.rebuild())


def _build_package_path_attr_expr(
    package: str,
    attr_path: str,
    *,
    system: str | None = None,
    repo_root: str | None = None,
    package_args: Mapping[str, NixExpression] | None = None,
    source_overrides: Mapping[str, SourceEntry] | None = None,
    fake_hashes: bool | None = None,
) -> str:
    package_materialization = FunctionCall(
        name=FunctionCall(
            name=Identifier(name="import"),
            argument=Parenthesis(
                value=BinaryExpression(
                    operator=Operator(name="+"),
                    left=select_attrs(Identifier(name="rootFlake"), "outPath"),
                    right=StringPrimitive(value="/lib/package-materialization.nix"),
                )
            ),
        ),
        argument=AttributeSet.from_dict({
            "src": select_attrs(Identifier(name="rootFlake"), "outPath"),
            "lib": select_attrs(Identifier(name="pkgs"), "lib"),
            "outputs": Identifier(name="flake"),
        }),
    )
    package_function = Select(
        expression=Parenthesis(
            value=FunctionCall(
                name=select_attrs(
                    Identifier(name="packageMaterialization"),
                    "packageFunctionsForSystem",
                ),
                argument=Identifier(name="system"),
            )
        ),
        attribute=_quote_attr(package),
    )
    package_expr: NixExpression = FunctionCall(
        name=FunctionCall(
            name=FunctionCall(
                name=select_attrs(Identifier(name="pkgs"), "lib", "callPackageWith"),
                argument=Identifier(name="applied"),
            ),
            argument=package_function,
        ),
        argument=AttributeSet(
            values=[
                Binding(
                    name="inputs",
                    value=select_attrs(Identifier(name="rootFlake"), "inputs"),
                ),
                Binding(name="outputs", value=Identifier(name="flake")),
                *(
                    Binding(name=name, value=value)
                    for name, value in (package_args or {}).items()
                ),
            ]
        ),
    )
    expression = package_expr
    for attribute in attr_path.removeprefix(".").split("."):
        if attribute:
            if expression is package_expr:
                expression = Parenthesis(value=expression)
            expression = Select(expression=expression, attribute=attribute)
    expression = LetExpression(
        local_variables=[
            *_contextual_overlay_bindings(
                system=system,
                repo_root=repo_root,
                source_overrides=source_overrides,
                fake_hashes=fake_hashes,
            ),
            Binding(name="packageMaterialization", value=package_materialization),
        ],
        value=expression,
    )
    return compact_nix_expr(expression.rebuild())


def _build_repo_package_attr_expr(
    package_file: str,
    attr_path: str,
    *,
    system: str | None = None,
    repo_root: str | None = None,
    package_args: Mapping[str, NixExpression] | None = None,
    source_overrides: Mapping[str, SourceEntry] | None = None,
    fake_hashes: bool | None = None,
) -> str:
    """Evaluate an internal package file that is intentionally not exported."""
    package_path = Parenthesis(
        value=BinaryExpression(
            operator=Operator(name="+"),
            left=select_attrs(Identifier(name="rootFlake"), "outPath"),
            right=StringPrimitive(value=f"/{package_file}"),
        )
    )
    return _build_call_package_attr_expr(
        package_path,
        attr_path,
        system=system,
        repo_root=repo_root,
        package_args=package_args,
        source_overrides=source_overrides,
        fake_hashes=fake_hashes,
    )


def _build_call_package_attr_expr(
    package_path: NixExpression,
    attr_path: str,
    *,
    system: str | None,
    repo_root: str | None,
    package_args: Mapping[str, NixExpression] | None,
    source_overrides: Mapping[str, SourceEntry] | None,
    fake_hashes: bool | None,
) -> str:
    package_expr: NixExpression = FunctionCall(
        name=FunctionCall(
            name=FunctionCall(
                name=select_attrs(Identifier(name="pkgs"), "lib", "callPackageWith"),
                argument=Identifier(name="applied"),
            ),
            argument=package_path,
        ),
        argument=AttributeSet(
            values=[
                Binding(
                    name="inputs",
                    value=select_attrs(Identifier(name="rootFlake"), "inputs"),
                ),
                Binding(name="outputs", value=Identifier(name="flake")),
                *(
                    Binding(name=name, value=value)
                    for name, value in (package_args or {}).items()
                ),
            ]
        ),
    )
    expression = package_expr
    for attribute in attr_path.removeprefix(".").split("."):
        if not attribute:
            continue
        if expression is package_expr:
            expression = Parenthesis(value=expression)
        expression = Select(expression=expression, attribute=attribute)
    expression = LetExpression(
        local_variables=_contextual_overlay_bindings(
            system=system,
            repo_root=repo_root,
            source_overrides=source_overrides,
            fake_hashes=fake_hashes,
        ),
        value=expression,
    )
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
    has_mismatch_signal = _has_hash_mismatch_signal(output)
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


def _has_hash_mismatch_signal(output: str) -> bool:
    return any(indicator in output for indicator in _HASH_MISMATCH_INDICATORS)


def is_retryable_nix_network_failure(*, stdout: str, stderr: str) -> bool:
    """Return whether Nix reported a transient network or substituter failure."""
    output = f"{stderr}\n{stdout}"
    if _has_hash_mismatch_signal(output):
        return False
    folded = output.casefold()
    return any(marker.casefold() in folded for marker in _NIX_NETWORK_TRANSIENT_MARKERS)


def _is_retryable_fixed_output_hash_failure(result: CommandResult) -> bool:
    output = f"{result.stderr}\n{result.stdout}"
    if _has_hash_mismatch_signal(output):
        return False
    if is_retryable_nix_network_failure(
        stdout=result.stdout,
        stderr=result.stderr,
    ):
        return True
    folded = output.casefold()
    return any(
        marker.casefold() in folded for marker in _FIXED_OUTPUT_ONLY_TRANSIENT_MARKERS
    )


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
    isolate_by_drv_hash: bool = False,
    env: Mapping[str, str] | None = None,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute an SRI hash by extracting nix fixed-output mismatch output.

    ``isolate_by_drv_hash`` salts the probe output using the unsalted derivation
    path.  This prevents concurrent probes for distinct derivations that share
    the same fake fixed-output hash from being coalesced by Nix.
    """
    config = resolve_active_config(config)
    expr = _build_nix_expr(expr, isolate_by_drv_hash=isolate_by_drv_hash)
    semaphore = _get_nix_build_semaphore(config)
    attempt = 1
    while True:
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
                            "Expected nix build to fail with hash mismatch, "
                            "but it succeeded"
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
        if (
            attempt < _FIXED_OUTPUT_HASH_MAX_ATTEMPTS
            and _is_retryable_fixed_output_hash_failure(result)
        ):
            attempt += 1
            yield UpdateEvent.status(
                source,
                "fixed-output source fetch hit a transient failure; retrying...",
                operation="compute_hash",
                status=StatusInfo(
                    kind=StatusKind.RETRY,
                    value=f"attempt {attempt}/{_FIXED_OUTPUT_HASH_MAX_ATTEMPTS}",
                ),
            )
            await asyncio.sleep(max(0.0, config.default_retry_backoff))
            continue
        async for event in _emit_sri_hash_from_build_result(
            source,
            result,
            config=config,
        ):
            yield event
        return


def _build_nix_expr(
    body: str | NixExpression,
    *,
    isolate_by_drv_hash: bool = False,
) -> str:
    body_expression = parse(body).expr if isinstance(body, str) else body
    local_variables: list[Binding | Inherit] = [
        Binding(name="pkgs", value=nixpkgs_expression())
    ]
    if isolate_by_drv_hash:
        local_variables.append(
            Binding(name="nixcfgFixedOutputProbe", value=body_expression)
        )
        body_expression = FunctionCall(
            name=FunctionCall(
                name=select_attrs(
                    Identifier(name="pkgs"),
                    "testers",
                    "invalidateFetcherByDrvHash",
                ),
                argument=Parenthesis(
                    value=FunctionDefinition(
                        argument_set=Identifier(name="_"),
                        output=Identifier(name="nixcfgFixedOutputProbe"),
                    )
                ),
            ),
            argument=AttributeSet(
                values=[
                    Binding(
                        name="name",
                        value="nixcfg-fixed-output-probe",
                    )
                ]
            ),
        )
    expression = LetExpression(
        local_variables=local_variables,
        value=body_expression,
    )
    return compact_nix_expr(expression.rebuild())


def _build_drv_path_expr(body: str | NixExpression) -> str:
    expression = LetExpression(
        local_variables=[
            Binding(
                name="drv",
                value=parse(body).expr if isinstance(body, str) else body,
            ),
        ],
        value=select_attrs(Identifier(name="drv"), "drvPath"),
    )
    return compact_nix_expr(expression.rebuild())


def _build_overlay_expression(
    source: str,
    *,
    system: str | None = None,
    repo_root: str | None = None,
    source_overrides: Mapping[str, SourceEntry] | None = None,
    fake_hashes: bool | None = None,
) -> NixExpression:
    """Evaluate a package with the host's shared overlay order.

    The contextual scope supplies candidate source metadata and hash settings
    without changing the checkout or evaluating the host configuration.
    """
    return LetExpression(
        local_variables=_contextual_overlay_bindings(
            system=system,
            repo_root=repo_root,
            source_overrides=source_overrides,
            fake_hashes=fake_hashes,
        ),
        value=Select(
            expression=Identifier(name="applied"),
            attribute=_quote_attr(source),
        ),
    )


def _contextual_overlay_bindings(
    *,
    system: str | None,
    repo_root: str | None,
    source_overrides: Mapping[str, SourceEntry] | None,
    fake_hashes: bool | None = None,
) -> list[Binding | Inherit]:
    """Build one explicit update-evaluation scope shared by package probes."""
    repo_path = get_repo_file(".") if repo_root is None else Path(repo_root).resolve()
    flake_url = local_flake_url(repo_path)
    system_expr: NixExpression = (
        select_attrs(Identifier(name="builtins"), "currentSystem")
        if system is None
        else StringPrimitive(value=system)
    )
    source_overrides_expr: NixExpression = (
        FunctionCall(
            name=select_attrs(Identifier(name="builtins"), "fromJSON"),
            argument=StringPrimitive(
                value=json.dumps(
                    {name: entry.to_dict() for name, entry in source_overrides.items()},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
        )
        if source_overrides
        else AttributeSet(values=[])
    )
    contextual_import = FunctionCall(
        name=FunctionCall(
            name=Identifier(name="import"),
            argument=Parenthesis(
                value=BinaryExpression(
                    operator=Operator(name="+"),
                    left=select_attrs(Identifier(name="rootFlake"), "outPath"),
                    right=StringPrimitive(value="/default.nix"),
                )
            ),
        ),
        argument=AttributeSet.from_dict({
            "src": select_attrs(Identifier(name="rootFlake"), "outPath"),
            "inputs": select_attrs(Identifier(name="rootFlake"), "inputs"),
            "lib": select_attrs(Identifier(name="pkgs"), "lib"),
            "pkgsFor": FunctionCall(
                name=select_attrs(Identifier(name="builtins"), "listToAttrs"),
                argument=NixList(
                    value=[
                        AttributeSet.from_dict({
                            "name": Identifier(name="system"),
                            "value": Identifier(name="pkgs"),
                        })
                    ]
                ),
            ),
            "evaluationContext": AttributeSet.from_dict({
                "fakeHashes": Primitive(
                    value=source_overrides is None
                    if fake_hashes is None
                    else fake_hashes
                ),
                "sourceOverrides": source_overrides_expr,
            }),
        }),
    )
    return [
        Binding(name="rootFlake", value=_build_get_flake_expr(flake_url)),
        Binding(name="system", value=system_expr),
        Binding(
            name="pkgs",
            value=FunctionCall(
                name=FunctionCall(
                    name=Identifier(name="import"),
                    argument=select_attrs(
                        Identifier(name="rootFlake"), "inputs", "nixpkgs"
                    ),
                ),
                argument=AttributeSet.from_dict({
                    "system": Identifier(name="system"),
                    "config": AttributeSet.from_dict({
                        "allowUnfree": Primitive(value=True),
                        "allowInsecurePredicate": FunctionDefinition(
                            argument_set=Identifier(name="_"),
                            output=Primitive(value=True),
                        ),
                    }),
                }),
            ),
        ),
        Binding(name="flake", value=contextual_import),
        Binding(
            name="applied",
            value=FunctionCall(
                name=select_attrs(Identifier(name="pkgs"), "appendOverlays"),
                argument=Parenthesis(
                    value=FunctionCall(
                        name=FunctionCall(
                            name=Identifier(name="import"),
                            argument=Parenthesis(
                                value=BinaryExpression(
                                    operator=Operator(name="+"),
                                    left=select_attrs(
                                        Identifier(name="rootFlake"), "outPath"
                                    ),
                                    right=StringPrimitive(
                                        value="/lib/package-overlays.nix"
                                    ),
                                ),
                            ),
                        ),
                        argument=AttributeSet.from_dict({
                            "inputs": select_attrs(
                                Identifier(name="rootFlake"), "inputs"
                            ),
                            "outputs": Identifier(name="flake"),
                        }),
                    ),
                ),
            ),
        ),
    ]


def _build_overlay_expr(
    source: str,
    *,
    system: str | None = None,
    repo_root: str | None = None,
    source_overrides: Mapping[str, SourceEntry] | None = None,
    fake_hashes: bool | None = None,
) -> str:
    return compact_nix_expr(
        _build_overlay_expression(
            source,
            system=system,
            repo_root=repo_root,
            source_overrides=source_overrides,
            fake_hashes=fake_hashes,
        ).rebuild()
    )


async def compute_overlay_hash(
    source: str,
    *,
    system: str | None = None,
    config: UpdateConfig | None = None,
    repo_root: str | None = None,
    source_overrides: Mapping[str, SourceEntry] | None = None,
    fake_hashes: bool | None = None,
) -> EventStream:
    """Compute a hash by building the overlay with explicit fake-hash context.

    The contextual library makes its source hash helpers return ``lib.fakeHash``.
    The real overlay derivation then fails with a hash mismatch from which we
    extract the correct hash.

    The overlay definition in ``overlays/default.nix`` is the single source of truth.
    """
    expr = _build_overlay_expr(
        source,
        system=system,
        repo_root=repo_root,
        source_overrides=source_overrides,
        fake_hashes=fake_hashes,
    )
    async for event in compute_fixed_output_hash(
        source,
        expr,
        config=config,
    ):
        yield event


async def compute_drv_fingerprint(
    source: str,
    *,
    system: str | None = None,
    config: UpdateConfig | None = None,
    repo_root: str | None = None,
    source_overrides: Mapping[str, SourceEntry] | None = None,
    fake_hashes: bool | None = None,
) -> str:
    """Compute a stable derivation fingerprint for staleness detection.

    Evaluates the package with explicit fake-hash context and extracts the
    ``.drv`` store-path hash using ``nix eval --raw <expr>.drvPath``. Because
    the fake hash is constant, the path is a pure function of the build closure.

    Any change to *any* transitive build input — a nixpkgs bump, a Deno
    version change, a source force-push, a build-script edit — changes the
    ``.drv`` hash.  Conversely, identical inputs always produce the same
    hash.  This gives us maximally precise staleness detection: zero false
    negatives and zero false positives.
    """
    expr = _build_overlay_expr(
        source,
        system=system,
        repo_root=repo_root,
        source_overrides=source_overrides,
        fake_hashes=fake_hashes,
    )
    return await compute_expr_drv_fingerprint(source, expr, config=config)


async def compute_expr_drv_fingerprint(
    source: str,
    expr: str,
    *,
    config: UpdateConfig | None = None,
) -> str:
    """Compute a stable derivation fingerprint for an arbitrary Nix expression."""
    config = resolve_active_config(config)
    expr = _build_drv_path_expr(expr)
    args = ["nix", "eval", "--quiet", "--raw", "--impure", "--expr", expr]

    result_drain = ValueDrain()
    async for _event in drain_value_events(
        run_command(
            args,
            options=RunCommandOptions(
                source=source,
                error="nix eval did not return output",
                config=config,
            ),
        ),
        result_drain,
        parse=expect_command_result,
    ):
        pass  # discard streaming events during fingerprint eval
    result = require_value(result_drain, "nix eval did not return output")
    if result.returncode != 0:
        msg = f"nix eval failed:\n{result.stderr}"
        raise RuntimeError(msg)

    drv_path = result.stdout.strip()
    if not drv_path:
        msg = "nix eval returned empty drvPath"
        raise RuntimeError(msg)

    # The .drv key is "<hash>-<name>.drv" (Nix 2.20+) or the full
    # "/nix/store/<hash>-<name>.drv". Strip the store prefix if present so the
    # fingerprint is just the Nix hash portion. A Nix version change that
    # alters the derivation hash algorithm would change the fingerprint,
    # conservatively triggering recomputation — the correct behaviour.
    if "/" in drv_path:
        drv_path = drv_path.rsplit("/", 1)[-1]
    return drv_path.split("-", 1)[0]


__all__ = [
    "_build_fetch_from_github_call",
    "_build_fetch_from_github_expr",
    "_build_fetchgit_call",
    "_build_fetchgit_expr",
    "_build_flake_attr_expr",
    "_build_nix_expr",
    "_build_overlay_attr_expr",
    "_build_overlay_expr",
    "_build_package_path_attr_expr",
    "_build_repo_package_attr_expr",
    "_emit_sri_hash_from_build_result",
    "_run_fixed_output_build",
    "compute_drv_fingerprint",
    "compute_expr_drv_fingerprint",
    "compute_fixed_output_hash",
    "compute_overlay_hash",
    "get_current_nix_platform",
    "is_retryable_nix_network_failure",
    "normalize_nix_platform",
]
