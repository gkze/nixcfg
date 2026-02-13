"""Deno dependency hash computation across platforms."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from filelock import FileLock

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.events import (
    CommandResult,
    EventStream,
    UpdateEvent,
    UpdateEventKind,
    ValueDrain,
    drain_value_events,
    require_value,
)
from lib.update.nix import (
    _PLATFORM_HASH_PAYLOAD_SIZE,
    _build_overlay_expr,
    _emit_sri_hash_from_build_result,
    _run_fixed_output_build,
    get_current_nix_platform,
)
from lib.update.paths import sources_file_for
from lib.update.sources import load_source_entry, save_source_entry

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


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
    result = require_value(result_drain, "nix build did not return output")
    hash_drain = ValueDrain[str]()
    async for event in drain_value_events(
        _emit_sri_hash_from_build_result(source, result, config=config),
        hash_drain,
    ):
        yield event
    hash_value = require_value(hash_drain, "Hash conversion failed")
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
    "compute_deno_deps_hash",
]
