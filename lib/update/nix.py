"""Nix-based hash computation helpers for updater implementations."""

from __future__ import annotations

import asyncio
import json
import platform
import re
from typing import TYPE_CHECKING, cast

import aiohttp
from filelock import FileLock
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.parser import parse

from lib.nix.commands.base import CommandResult as LibnixResult
from lib.nix.commands.base import HashMismatchError
from lib.nix.models.hash import is_sri
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.constants import FIXED_OUTPUT_NOISE
from lib.update.events import (
    CommandResult,
    EventStream,
    GatheredValues,
    UpdateEvent,
    UpdateEventKind,
    ValueDrain,
    _require_value,
    drain_value_events,
    gather_event_streams,
)
from lib.update.flake import get_flake_input_node, nixpkgs_expr
from lib.update.net import fetch_url
from lib.update.nix_expr import compact_nix_expr
from lib.update.paths import get_repo_file, sources_file_for
from lib.update.process import convert_nix_hash_to_sri, run_command, run_nix_build
from lib.update.sources import load_source_entry, save_source_entry

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from lib.update.updaters.base import CargoLockGitDep


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
    result = _require_value(result_drain, "nix build did not return output")
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
        result = _require_value(result_drain, "nix build did not return output")
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
    result = _require_value(result_drain, "nix derivation show did not return output")
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


def _build_deno_deps_expr(source: str, platform: str) -> str:
    """Build a Nix expression that evaluates the overlay package for *platform*.

    Used by the deno deps flow which needs per-platform hash computation
    with the per-package ``sources.json`` written in-place.
    """
    return _build_overlay_expr(source, system=platform)


def _build_deno_hash_entries(
    *,
    platforms: Iterable[str],
    active_platform: str,
    existing_hashes: Mapping[str, str],
    computed_hashes: Mapping[str, str],
    fake_hash: str,
) -> list[HashEntry]:
    entries: list[HashEntry] = []
    for platform_name in platforms:
        if platform_name == active_platform:
            hash_value = fake_hash
        else:
            hash_value = computed_hashes.get(platform_name) or existing_hashes.get(
                platform_name,
                fake_hash,
            )
        entries.append(
            HashEntry.create(
                "denoDepsHash",
                hash_value,
                platform=platform_name,
            ),
        )
    return entries


def _build_deno_temp_entry(
    *,
    input_name: str,
    original_entry: SourceEntry | None,
    entries: list[HashEntry],
) -> SourceEntry:
    hash_collection = HashCollection.from_value(entries)
    if original_entry is not None:
        return original_entry.model_copy(
            update={"hashes": hash_collection, "input": input_name},
        )
    return SourceEntry(hashes=hash_collection, input=input_name)


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


_CARGO_LOCK_GIT_SOURCE_RE = re.compile(
    r'^source = "git\+(?P<url>[^?#]+)\?[^#]*#(?P<commit>[0-9a-f]+)"$',
)


def _parse_cargo_lock_git_sources(  # noqa: C901
    lockfile_content: str,
    git_deps: list[CargoLockGitDep],
) -> dict[str, tuple[str, str]]:
    """Parse a Cargo.lock and return ``{git_dep_name: (url, rev)}`` for each dep.

    Multiple crates may share the same git URL; we deduplicate by matching each
    ``CargoLockGitDep`` to the first ``[[package]]`` whose ``name`` starts with
    the dep's ``match_name``.
    """
    result: dict[str, tuple[str, str]] = {}
    unmatched = {dep.git_dep: dep for dep in git_deps}

    def _select_dep(dep_key: str, crate_name: str) -> CargoLockGitDep | None:
        direct = unmatched.get(dep_key)
        if direct is not None:
            return direct
        prefix_matches = [
            dep for dep in unmatched.values() if crate_name.startswith(dep.match_name)
        ]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        return None

    current_name: str | None = None
    current_version: str | None = None

    for raw_line in lockfile_content.splitlines():
        line = raw_line.strip()
        if line.startswith("name = "):
            current_name = line.split('"')[1]
            current_version = None
        elif line.startswith("version = ") and '"' in line:
            current_version = line.split('"')[1]
        elif line.startswith("source = ") and current_name is not None:
            match = _CARGO_LOCK_GIT_SOURCE_RE.match(line)
            if match is None:
                continue
            url, commit = match.group("url"), match.group("commit")
            dep_key = (
                f"{current_name}-{current_version}" if current_version else current_name
            )
            selected = _select_dep(dep_key, current_name)
            if selected is not None:
                result[selected.git_dep] = (url, commit)
                del unmatched[selected.git_dep]

    if unmatched:
        msg = f"Could not find git sources in Cargo.lock for: {list(unmatched)}"
        raise RuntimeError(
            msg,
        )
    return result


async def _prefetch_git_hash(
    source: str,
    url: str,
    rev: str,
    *,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Fetch a git repo and yield its SRI narHash via ``builtins.fetchGit``."""
    config = resolve_active_config(config)
    fetch_git = FunctionCall(
        name="builtins.fetchGit",
        argument=AttributeSet.from_dict(
            {
                "url": url,
                "rev": rev,
                "allRefs": True,
            },
        ),
    )
    expr = parse(f"({fetch_git.rebuild()}).narHash").expr.rebuild()
    args = ["nix", "eval", "--json", "--expr", expr]
    result_drain = ValueDrain[CommandResult]()
    async for event in drain_value_events(
        run_command(
            args,
            source=source,
            error="builtins.fetchGit failed",
            config=config,
        ),
        result_drain,
    ):
        yield event
    result = _require_value(result_drain, "builtins.fetchGit did not return output")
    if result.returncode != 0:
        msg = f"builtins.fetchGit failed:\n{result.stderr}"
        raise RuntimeError(msg)
    sri_hash = json.loads(result.stdout)
    if not isinstance(sri_hash, str) or not is_sri(sri_hash):
        msg = f"Unexpected hash format from builtins.fetchGit: {sri_hash}"
        raise RuntimeError(msg)
    yield UpdateEvent.value(source, sri_hash)


async def compute_import_cargo_lock_output_hashes(
    source: str,
    input_name: str,
    *,
    lockfile_path: str,
    git_deps: list[CargoLockGitDep],
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute ``importCargoLock`` output hashes via ``builtins.fetchGit``.

    Parses the upstream Cargo.lock to extract git dependency URLs and revisions,
    then prefetches each one directly.  This avoids evaluating nixpkgs entirely
    and works regardless of inter-repo workspace dependencies.
    """
    config = resolve_active_config(config)

    yield UpdateEvent.status(source, "Fetching upstream Cargo.lock...")
    node = get_flake_input_node(input_name)
    locked = node.locked
    if locked is None:
        msg = f"Flake input '{input_name}' has no locked info"
        raise RuntimeError(msg)
    owner = locked.owner
    repo = locked.repo
    rev = locked.rev
    if not all([owner, repo, rev]):
        msg = f"Flake input '{input_name}' missing owner/repo/rev in locked info"
        raise RuntimeError(
            msg,
        )

    lockfile_url = (
        f"https://raw.githubusercontent.com/{owner}/{repo}/{rev}/{lockfile_path}"
    )
    async with aiohttp.ClientSession() as session:
        payload = await fetch_url(
            session,
            lockfile_url,
            request_timeout=config.default_timeout,
            config=config,
            user_agent=config.default_user_agent,
        )
    lockfile_content = payload.decode(errors="replace")

    git_sources = _parse_cargo_lock_git_sources(lockfile_content, git_deps)

    streams = {
        dep.git_dep: _prefetch_git_hash(
            source,
            *git_sources[dep.git_dep],
            config=config,
        )
        for dep in git_deps
    }
    async for item in gather_event_streams(streams):
        if isinstance(item, GatheredValues):
            yield UpdateEvent.value(source, cast("dict[str, str]", item.values))
        else:
            yield item


async def _compute_deno_deps_hash_for_platform(
    source: str,
    input_name: str,  # noqa: ARG001 — part of public signature
    platform: str,
    *,
    config: UpdateConfig | None = None,
) -> EventStream:
    expr = _build_deno_deps_expr(source, platform)
    result_drain = ValueDrain[CommandResult]()
    async for event in drain_value_events(
        _run_fixed_output_build(
            f"{source}:{platform}",
            expr,
            success_error=(
                f"Expected nix build to fail with hash mismatch for {platform}, but it succeeded"
            ),
            config=config,
        ),
        result_drain,
    ):
        yield event
    result = _require_value(result_drain, "nix build did not return output")
    hash_drain = ValueDrain[str]()
    async for event in drain_value_events(
        _emit_sri_hash_from_build_result(source, result, config=config),
        hash_drain,
    ):
        yield event
    hash_value = _require_value(hash_drain, "Hash conversion failed")
    yield UpdateEvent.value(source, (platform, hash_value))


def _try_platform_hash_event(event: UpdateEvent) -> tuple[str, str] | None:
    if event.kind != UpdateEventKind.VALUE:
        return None
    payload = event.payload
    if (
        isinstance(payload, tuple)
        and len(payload) == _PLATFORM_HASH_PAYLOAD_SIZE
        and isinstance(payload[0], str)
        and isinstance(payload[1], str)
    ):
        return cast("tuple[str, str]", payload)
    return None


async def compute_deno_deps_hash(  # noqa: C901, PLR0912, PLR0915
    source: str,
    input_name: str,
    *,
    native_only: bool = False,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute Deno dependency hashes across configured platforms.

    Nix reads per-package ``sources.json`` files at eval time via path
    imports, so we write temporary hash entries directly to the real
    per-package file (with a file lock) before each platform build.
    """
    config = resolve_active_config(config)
    current_platform = get_current_nix_platform()
    platforms = config.deno_deps_platforms
    if current_platform not in platforms:
        msg = (
            f"Current platform {current_platform} not in supported platforms: "
            f"{platforms}"
        )
        raise RuntimeError(msg)

    pkg_sources_path = sources_file_for(source)
    if pkg_sources_path is None:
        msg = f"No sources.json found for '{source}'"
        raise RuntimeError(msg)
    lock_path = pkg_sources_path.with_suffix(".json.lock")

    with FileLock(lock_path):
        original_entry = load_source_entry(pkg_sources_path)

        existing_hashes: dict[str, str] = {}
        if original_entry:
            if entries := original_entry.hashes.entries:
                existing_hashes = {
                    entry.platform: entry.hash for entry in entries if entry.platform
                }
            elif mapping := original_entry.hashes.mapping:
                existing_hashes = dict(mapping)

        platforms_to_compute = (current_platform,) if native_only else platforms

        platform_hashes: dict[str, str] = {}
        failed_platforms: list[str] = []

        try:
            for platform_name in platforms_to_compute:
                yield UpdateEvent.status(
                    source,
                    f"Computing hash for {platform_name}...",
                )

                temp_entries = _build_deno_hash_entries(
                    platforms=platforms,
                    active_platform=platform_name,
                    existing_hashes=existing_hashes,
                    computed_hashes=platform_hashes,
                    fake_hash=config.fake_hash,
                )
                # Write temp hashes to the real per-package sources.json so
                # Nix can read them at eval time.
                temp_entry = _build_deno_temp_entry(
                    input_name=input_name,
                    original_entry=original_entry,
                    entries=temp_entries,
                )
                save_source_entry(pkg_sources_path, temp_entry)

                try:
                    async for event in _compute_deno_deps_hash_for_platform(
                        source,
                        input_name,
                        platform_name,
                        config=config,
                    ):
                        payload = _try_platform_hash_event(event)
                        if payload:
                            plat, hash_val = payload
                            platform_hashes[plat] = hash_val
                            continue
                        yield event
                except RuntimeError:
                    if platform_name != current_platform:
                        failed_platforms.append(platform_name)
                        if platform_name in existing_hashes:
                            yield UpdateEvent.status(
                                source,
                                f"Build failed for {platform_name}, preserving existing hash",
                            )
                            platform_hashes[platform_name] = existing_hashes[
                                platform_name
                            ]
                        else:
                            yield UpdateEvent.status(
                                source,
                                f"Build failed for {platform_name}, no existing hash to preserve",
                            )
                    else:
                        raise
        finally:
            # Always restore the original source file contents so a failed run
            # cannot leave fake placeholders behind.
            save_source_entry(pkg_sources_path, original_entry)

    if failed_platforms:
        yield UpdateEvent.status(
            source,
            f"Warning: {len(failed_platforms)} platform(s) failed, "
            f"preserved existing hashes: {', '.join(failed_platforms)}",
        )

    final_hashes = {**existing_hashes, **platform_hashes}
    yield UpdateEvent.value(source, final_hashes)


__all__ = [
    "_build_nix_expr",
    "compute_bun_node_modules_hash",
    "compute_cargo_vendor_hash",
    "compute_deno_deps_hash",
    "compute_drv_fingerprint",
    "compute_fixed_output_hash",
    "compute_go_vendor_hash",
    "compute_import_cargo_lock_output_hashes",
    "compute_npm_deps_hash",
    "compute_overlay_hash",
    "get_current_nix_platform",
]
