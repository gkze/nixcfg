"""Deno dependency hash computation across platforms."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.config import (
    UpdateConfig,
    hash_build_platforms_for,
    resolve_active_config,
)
from lib.update.events import (
    EventSink,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    ignore_event,
)
from lib.update.nix import (
    _build_overlay_expr,
    compute_fixed_output_hash,
    get_current_nix_platform,
)
from lib.update.paths import sources_file_for
from lib.update.platform_hashes import (
    PlatformHashFailure,
    platform_hash_failure_status,
    require_complete_platform_hashes,
)
from lib.update.sources import load_source_entry

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


def _build_deno_deps_expr(
    source: str,
    platform: str,
    source_override: SourceEntry | None = None,
) -> str:
    """Build a Nix expression that evaluates the overlay package for *platform*.

    Used by the deno deps flow which needs per-platform hash computation
    with per-run source entry overrides.
    """
    return _build_overlay_expr(
        source,
        system=platform,
        source_overrides={source: source_override} if source_override else None,
    )


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
    source_override: SourceEntry | None = None,
    config: UpdateConfig | None = None,
    emit: EventSink = ignore_event,
) -> tuple[str, str]:
    expr = _build_deno_deps_expr(source, platform, source_override)
    hash_value = await compute_fixed_output_hash(
        f"{source}:{platform}",
        expr,
        isolate_by_drv_hash=True,
        config=config,
        emit=emit,
    )
    return (platform, hash_value)


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
    failed_platforms: list[PlatformHashFailure]
    config: UpdateConfig


async def _process_platform_hash(
    platform_name: str, *, context: _PlatformHashContext, emit: EventSink = ignore_event
) -> None:
    await emit(
        UpdateEvent.status(
            context.source,
            f"Computing hash for {platform_name}...",
            operation="compute_hash",
            status=StatusInfo(kind=StatusKind.COMPUTING_HASH, value=platform_name),
        )
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
        platform, hash_value = await _compute_deno_deps_hash_for_platform(
            context.source,
            context.input_name,
            platform_name,
            source_override=temp_entry,
            config=context.config,
            emit=emit,
        )
        context.platform_hashes[platform] = hash_value
    except RuntimeError as exc:
        if platform_name == context.current_platform:
            raise
        failure = PlatformHashFailure(platform_name, str(exc))
        context.failed_platforms.append(failure)
        await emit(platform_hash_failure_status(context.source, failure))


async def compute_deno_deps_hash(
    source: str,
    input_name: str,
    *,
    native_only: bool = False,
    config: UpdateConfig | None = None,
    source_override: SourceEntry | None = None,
    emit: EventSink = ignore_event,
) -> dict[str, str]:
    """Compute Deno dependency hashes across configured platforms.

    Nix reads per-package ``sources.json`` values during evaluation, so each
    expression receives a temporary source override without mutating tracked
    files on disk.
    """
    config = resolve_active_config(config)
    current_platform = get_current_nix_platform()
    platforms = hash_build_platforms_for(config)
    if current_platform not in platforms:
        msg = f"Current platform {current_platform} not in supported platforms: {platforms}"
        raise RuntimeError(msg)

    if source_override is None:
        pkg_sources_path = sources_file_for(source)
        if pkg_sources_path is None:
            msg = f"No sources.json found for '{source}'"
            raise RuntimeError(msg)
        original_entry = load_source_entry(pkg_sources_path)
    else:
        original_entry = source_override

    existing_hashes = _existing_platform_hashes(original_entry)

    platforms_to_compute = (current_platform,) if native_only else platforms

    platform_hashes: dict[str, str] = {}
    failed_platforms: list[PlatformHashFailure] = []
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
        await _process_platform_hash(
            platform_name=platform_name, context=context, emit=emit
        )

    require_complete_platform_hashes(source, failed_platforms)

    return {**existing_hashes, **platform_hashes}


__all__ = [
    "compute_deno_deps_hash",
]
