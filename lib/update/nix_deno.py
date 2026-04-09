"""Deno dependency hash computation across platforms."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.config import (
    UpdateConfig,
    hash_build_platforms_for,
    resolve_active_config,
)
from lib.update.events import (
    CommandResult,
    EventStream,
    UpdateEvent,
    UpdateEventKind,
    ValueDrain,
    drain_value_events,
    expect_command_result,
    expect_str,
    require_value,
)
from lib.update.nix import (
    _PLATFORM_HASH_PAYLOAD_SIZE,
    _build_overlay_expr,
    _emit_sri_hash_from_build_result,
    _FixedOutputBuildOptions,
    _run_fixed_output_build,
    get_current_nix_platform,
)
from lib.update.paths import sources_file_for
from lib.update.sources import load_source_entry

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


def _build_deno_deps_expr(source: str, platform: str) -> str:
    """Build a Nix expression that evaluates the overlay package for *platform*.

    Used by the deno deps flow which needs per-platform hash computation
    with per-run source entry overrides.
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


async def _compute_deno_deps_hash_for_platform(
    source: str,
    _input_name: str,
    platform: str,
    *,
    env: Mapping[str, str] | None = None,
    config: UpdateConfig | None = None,
) -> EventStream:
    expr = _build_deno_deps_expr(source, platform)
    result_drain = ValueDrain[CommandResult]()
    async for event in drain_value_events(
        _run_fixed_output_build(
            f"{source}:{platform}",
            expr,
            options=_FixedOutputBuildOptions(
                success_error=(
                    "Expected nix build to fail with hash mismatch "
                    f"for {platform}, but it succeeded"
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
    hash_drain = ValueDrain[str]()
    async for event in drain_value_events(
        _emit_sri_hash_from_build_result(source, result, config=config),
        hash_drain,
        parse=expect_str,
    ):
        yield event
    hash_value = require_value(hash_drain, "Hash conversion failed")
    yield UpdateEvent.value(source, (platform, hash_value))


def _try_platform_hash_event(event: UpdateEvent) -> tuple[str, str] | None:
    if event.kind != UpdateEventKind.VALUE:
        return None
    payload = event.payload
    if isinstance(payload, tuple) and len(payload) == _PLATFORM_HASH_PAYLOAD_SIZE:
        first = payload[0]
        second = payload[1]
        if isinstance(first, str) and isinstance(second, str):
            return first, second
    return None


def _build_source_override_env(source: str, entry: SourceEntry) -> dict[str, str]:
    payload = json.dumps({source: entry.to_dict()})
    return {"UPDATE_SOURCE_OVERRIDES_JSON": payload}


def _existing_platform_hashes(original_entry: SourceEntry | None) -> dict[str, str]:
    if original_entry is None:
        return {}
    if entries := original_entry.hashes.entries:
        return {entry.platform: entry.hash for entry in entries if entry.platform}
    if mapping := original_entry.hashes.mapping:
        return dict(mapping)
    return {}


@dataclass
class _PlatformHashContext:
    source: str
    input_name: str
    platforms: tuple[str, ...]
    current_platform: str
    original_entry: SourceEntry
    existing_hashes: dict[str, str]
    platform_hashes: dict[str, str]
    failed_platforms: list[str]
    config: UpdateConfig


async def _process_platform_hash(
    platform_name: str,
    *,
    context: _PlatformHashContext,
) -> EventStream:
    yield UpdateEvent.status(
        context.source,
        f"Computing hash for {platform_name}...",
        operation="compute_hash",
        status="computing_hash",
        detail=platform_name,
    )

    temp_entries = _build_deno_hash_entries(
        platforms=context.platforms,
        active_platform=platform_name,
        existing_hashes=context.existing_hashes,
        computed_hashes=context.platform_hashes,
        fake_hash=context.config.fake_hash,
    )
    temp_entry = _build_deno_temp_entry(
        input_name=context.input_name,
        original_entry=context.original_entry,
        entries=temp_entries,
    )

    try:
        async for event in _compute_deno_deps_hash_for_platform(
            context.source,
            context.input_name,
            platform_name,
            env=_build_source_override_env(context.source, temp_entry),
            config=context.config,
        ):
            payload = _try_platform_hash_event(event)
            if payload is None:
                yield event
                continue
            plat, hash_val = payload
            context.platform_hashes[plat] = hash_val
    except RuntimeError:
        if platform_name == context.current_platform:
            raise
        context.failed_platforms.append(platform_name)
        if platform_name in context.existing_hashes:
            context.platform_hashes[platform_name] = context.existing_hashes[
                platform_name
            ]
            yield UpdateEvent.status(
                context.source,
                f"Build failed for {platform_name}, preserving existing hash",
                operation="compute_hash",
            )
            return
        yield UpdateEvent.status(
            context.source,
            f"Build failed for {platform_name}, no existing hash to preserve",
            operation="compute_hash",
        )


async def compute_deno_deps_hash(
    source: str,
    input_name: str,
    *,
    native_only: bool = False,
    config: UpdateConfig | None = None,
) -> EventStream:
    """Compute Deno dependency hashes across configured platforms.

    Nix reads per-package ``sources.json`` values during evaluation, so we
    pass per-platform temporary overrides via ``UPDATE_SOURCE_OVERRIDES_JSON``
    instead of mutating tracked ``sources.json`` files on disk.
    """
    config = resolve_active_config(config)
    current_platform = get_current_nix_platform()
    platforms = hash_build_platforms_for(config)
    if current_platform not in platforms:
        msg = f"Current platform {current_platform} not in supported platforms: {platforms}"
        raise RuntimeError(msg)

    pkg_sources_path = sources_file_for(source)
    if pkg_sources_path is None:
        msg = f"No sources.json found for '{source}'"
        raise RuntimeError(msg)

    original_entry = load_source_entry(pkg_sources_path)

    existing_hashes = _existing_platform_hashes(original_entry)

    platforms_to_compute = (current_platform,) if native_only else platforms

    platform_hashes: dict[str, str] = {}
    failed_platforms: list[str] = []
    context = _PlatformHashContext(
        source=source,
        input_name=input_name,
        platforms=platforms,
        current_platform=current_platform,
        original_entry=original_entry,
        existing_hashes=existing_hashes,
        platform_hashes=platform_hashes,
        failed_platforms=failed_platforms,
        config=config,
    )

    for platform_name in platforms_to_compute:
        async for event in _process_platform_hash(
            platform_name=platform_name,
            context=context,
        ):
            yield event

    if failed_platforms:
        yield UpdateEvent.status(
            source,
            f"Warning: {len(failed_platforms)} platform(s) failed, "
            f"preserved existing hashes: {', '.join(failed_platforms)}",
            operation="compute_hash",
        )

    final_hashes = {**existing_hashes, **platform_hashes}
    yield UpdateEvent.value(source, final_hashes)


__all__ = [
    "compute_deno_deps_hash",
]
