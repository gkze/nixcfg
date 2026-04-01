"""CLI entry point for update workflows."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import aiohttp
import typer
from rich.console import Console

from lib.update import updaters as updater_module

if TYPE_CHECKING:
    from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode

from lib.cli import HELP_CONTEXT_SETTINGS
from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.update.artifacts import GeneratedArtifact, save_generated_artifacts
from lib.update.ci.resolve_versions import load_pinned_versions
from lib.update.cli_inventory import (
    _build_inventory_summary,
    _classify_updater_kind,
    _flake_source_string,
    _inventory_classification,
    _inventory_sort_value,
    _InventoryHandles,
    _InventoryRefTarget,
    _InventorySourceTarget,
    _InventoryTarget,
    _ListRow,
    _row_sort_value,
    _source_backing_input_name,
    _source_hash_kinds,
)
from lib.update.cli_inventory import (
    _build_update_inventory as _build_update_inventory_impl,
)
from lib.update.cli_inventory import (
    _collect_flake_inputs_for_list as _collect_flake_inputs_for_list_impl,
)
from lib.update.cli_inventory import (
    _collect_source_entries_for_list as _collect_source_entries_for_list_impl,
)
from lib.update.cli_inventory import (
    _generated_artifact_paths as _generated_artifact_paths_impl,
)
from lib.update.cli_inventory import (
    _handle_list_targets_request as _handle_list_targets_request_impl,
)
from lib.update.cli_inventory import (
    _repo_relative_path as _repo_relative_path_impl,
)
from lib.update.cli_options import UpdateOptions, UpdateSortBy, UpdateTTYMode
from lib.update.cli_validation import (
    _handle_validate_request as _handle_validate_request_impl,
)
from lib.update.cli_validation import (
    _validate_list_sort_option as _validate_list_sort_option_impl,
)
from lib.update.config import (
    UpdateConfig,
    env_bool,
    resolve_active_config,
    resolve_config,
)
from lib.update.constants import ALL_TOOLS, NIX_BUILD_FAILURE_TAIL_LINES, REQUIRED_TOOLS
from lib.update.events import UpdateEvent
from lib.update.flake import (
    get_flake_input_version,
    load_flake_lock,
    resolve_root_input_node,
    update_flake_input,
)
from lib.update.paths import (
    get_repo_root,
    package_dir_for,
    package_file_map,
    sources_file_for,
)
from lib.update.process import run_queue_task
from lib.update.refs import (
    FlakeInputRef,
    RefTaskOptions,
    get_flake_inputs_with_refs,
    update_refs_task,
)
from lib.update.sources import (
    load_all_sources,
    save_sources,
    validate_source_discovery_consistency,
)
from lib.update.ui_consumer import ConsumeEventsOptions, consume_events
from lib.update.ui_state import ItemMeta, OperationKind, SummaryStatus
from lib.update.updaters import UPDATERS, ensure_updaters_loaded
from lib.update.updaters.base import FlakeInputHashUpdater, Updater, VersionInfo

_PRESERVED_INVENTORY_EXPORTS = (
    _InventoryHandles,
    _InventoryRefTarget,
    _InventorySourceTarget,
    _inventory_classification,
    _row_sort_value,
)


def _get_updaters() -> dict[str, type[Updater]]:
    return updater_module.resolve_registry_alias(UPDATERS, ensure_updaters_loaded)


def _build_update_options(values: dict[str, object]) -> UpdateOptions:
    """Compatibility wrapper for shared option construction."""
    return UpdateOptions.from_mapping(values)


def check_required_tools(
    *,
    include_flake_edit: bool = False,
    source: str | None = None,
    needs_sources: bool = True,
) -> list[str]:
    """Return names of required CLI tools that are missing from ``$PATH``."""
    updaters = _get_updaters()
    tools: list[str]
    if not needs_sources:
        # refs-only (or explicit --no-sources) mode: don't require hash tooling.
        tools = [str(tool) for tool in REQUIRED_TOOLS]
    elif source:
        if source in updaters:
            updater_cls = updaters[source]
            tools = [
                str(tool) for tool in getattr(updater_cls, "required_tools", ALL_TOOLS)
            ]
        else:
            # ref-only source - only needs nix (and possibly flake-edit)
            tools = [str(tool) for tool in REQUIRED_TOOLS]
    else:
        tools = [str(tool) for tool in ALL_TOOLS]
    if include_flake_edit:
        tools.append("flake-edit")
    return [tool for tool in tools if shutil.which(tool) is None]


def _resolve_full_output(*, full_output: bool | None = None) -> bool:
    if full_output is not None:
        return full_output
    return env_bool("UPDATE_LOG_FULL", default=False)


def _is_tty(
    *,
    force_tty: bool | None = None,
    no_tty: bool | None = None,
    zellij_guard: bool | None = None,
) -> bool:
    if force_tty is None:
        force_tty = env_bool("UPDATE_FORCE_TTY", default=False)
    if no_tty is None:
        no_tty = env_bool("UPDATE_NO_TTY", default=False)
    if zellij_guard is None:
        zellij_guard = env_bool("UPDATE_ZELLIJ_GUARD", default=False)
    if force_tty:
        return True
    if no_tty:
        return False
    if zellij_guard and (
        os.environ.get("ZELLIJ") or os.environ.get("ZELLIJ_SESSION_NAME")
    ):
        return False
    term = os.environ.get("TERM", "")
    return sys.stdout.isatty() and term.lower() not in {"", "dumb"}


@dataclass
class OutputOptions:
    """Console output helpers for human-readable and quiet/json modes."""

    json_output: bool = False
    quiet: bool = False
    _console: Console | None = field(default=None, repr=False, init=False)
    _err_console: Console | None = field(default=None, repr=False, init=False)

    @property
    def console(self) -> Console:
        """Lazily create stdout console on first access."""
        if self._console is None:
            no_color = not sys.stdout.isatty()
            self._console = Console(no_color=no_color, highlight=not no_color)
        return self._console

    @property
    def err_console(self) -> Console:
        """Lazily create stderr console on first access."""
        if self._err_console is None:
            no_color = not sys.stderr.isatty()
            self._err_console = Console(
                stderr=True, no_color=no_color, highlight=not no_color
            )
        return self._err_console

    def print(
        self,
        message: str,
        *,
        style: str | None = None,
        stderr: bool = False,
    ) -> None:
        """Print a message unless quiet or json mode is enabled."""
        if not self.quiet and not self.json_output:
            target = self.err_console if stderr else self.console
            target.print(message, style=style)

    def print_error(self, message: str) -> None:
        """Print an error message to stderr when not in json mode."""
        if not self.json_output:
            self.err_console.print(message, style="red")


_ORIGIN_FLAKE_ONLY = "(flake.nix)"
_ORIGIN_SOURCES_ONLY = "(sources.json)"
_ORIGIN_BOTH = "(flake.nix + sources.json)"


@dataclass
class UpdateSummary:
    """Aggregate final per-source update outcomes."""

    updated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    no_change: list[str] = field(default_factory=list)
    _status_by_name: dict[str, SummaryStatus] = field(default_factory=dict, repr=False)
    _order: list[str] = field(default_factory=list, repr=False)

    def _set_status(self, name: str, status: SummaryStatus) -> None:
        normalized = status if status in _SUMMARY_STATUS_PRIORITY else "no_change"
        if name not in self._status_by_name:
            self._order.append(name)
            self._status_by_name[name] = normalized
            return
        current = self._status_by_name[name]
        if _SUMMARY_STATUS_PRIORITY[normalized] > _SUMMARY_STATUS_PRIORITY[current]:
            self._status_by_name[name] = normalized

    def _rebuild_lists(self) -> None:
        self.updated = []
        self.errors = []
        self.no_change = []
        for name in self._order:
            status = self._status_by_name[name]
            if status == "updated":
                self.updated.append(name)
            elif status == "error":
                self.errors.append(name)
            else:
                self.no_change.append(name)

    def to_dict(self) -> dict[str, list[str] | bool]:
        """Return a JSON-serializable summary payload."""
        return {
            "updated": self.updated,
            "errors": self.errors,
            "noChange": self.no_change,
            "success": len(self.errors) == 0,
        }

    def accumulate(self, details: dict[str, SummaryStatus]) -> None:
        """Merge per-source statuses and rebuild summary lists."""
        for name, detail in details.items():
            self._set_status(name, detail)
        self._rebuild_lists()


_SUMMARY_STATUS_PRIORITY = {"no_change": 0, "updated": 1, "error": 2}


@dataclass(frozen=True)
class ResolvedTargets:
    """Resolved source/input targets and effective mode flags."""

    all_source_names: set[str]
    all_ref_inputs: list[FlakeInputRef]
    all_ref_names: set[str]
    all_known_names: set[str]
    do_refs: bool
    do_sources: bool
    do_input_refresh: bool
    dry_run: bool
    native_only: bool
    ref_inputs: list[FlakeInputRef]
    source_names: list[str]

    @classmethod
    def from_options(cls, opts: UpdateOptions) -> ResolvedTargets:
        """Resolve target sets and operational flags from update options."""
        updaters = _get_updaters()
        all_source_names = set(updaters.keys())
        all_ref_inputs = get_flake_inputs_with_refs()
        all_ref_names = {i.name for i in all_ref_inputs}
        all_known_names = all_source_names | all_ref_names

        # --native-only implies --no-refs: in CI, refs are managed by the
        # pipeline (nix flake update + create-pr).
        do_refs = not opts.no_refs and not opts.native_only
        do_sources = not opts.no_sources
        if opts.source:
            if opts.source not in all_ref_names:
                do_refs = False
            if opts.source not in all_source_names:
                do_sources = False

        ref_inputs = (
            [i for i in all_ref_inputs if i.name == opts.source]
            if opts.source
            else all_ref_inputs
        )
        source_names = (
            [opts.source]
            if opts.source in all_source_names
            else []
            if opts.source
            else list(updaters.keys())
        )
        if not do_refs:
            ref_inputs = []
        if not do_sources:
            source_names = []

        return cls(
            all_source_names=all_source_names,
            all_ref_inputs=all_ref_inputs,
            all_ref_names=all_ref_names,
            all_known_names=all_known_names,
            do_refs=do_refs,
            do_sources=do_sources,
            do_input_refresh=not opts.no_input,
            dry_run=opts.check,
            native_only=opts.native_only,
            ref_inputs=ref_inputs,
            source_names=source_names,
        )


def _build_item_meta(
    resolved: ResolvedTargets,
    sources: SourcesFile | None,
) -> tuple[dict[str, ItemMeta], list[str]]:
    flake_names = (
        {inp.name for inp in resolved.ref_inputs} if resolved.do_refs else set()
    )
    source_names = set(resolved.source_names) if resolved.do_sources else set()

    item_meta: dict[str, ItemMeta] = {}
    for name in flake_names | source_names:
        in_flake = name in flake_names
        in_sources = name in source_names
        entry = None if sources is None else sources.entries.get(name)
        updater_cls = _get_updaters().get(name)
        has_input_refresh = (
            _source_backing_input_name(name, updater_cls, entry) is not None
        )

        if in_flake and in_sources:
            origin = _ORIGIN_BOTH
            op_order = [OperationKind.CHECK_VERSION, OperationKind.UPDATE_REF]
            if has_input_refresh:
                op_order.append(OperationKind.REFRESH_LOCK)
            op_order.append(OperationKind.COMPUTE_HASH)
        elif in_sources and has_input_refresh:
            origin = _ORIGIN_SOURCES_ONLY
            op_order = (
                OperationKind.CHECK_VERSION,
                OperationKind.REFRESH_LOCK,
                OperationKind.COMPUTE_HASH,
            )
        elif in_sources:
            origin = _ORIGIN_SOURCES_ONLY
            op_order = (
                OperationKind.CHECK_VERSION,
                OperationKind.COMPUTE_HASH,
            )
        else:
            origin = _ORIGIN_FLAKE_ONLY
            op_order = (
                OperationKind.CHECK_VERSION,
                OperationKind.UPDATE_REF,
                OperationKind.REFRESH_LOCK,
            )
        item_meta[name] = ItemMeta(
            name=name,
            origin=origin,
            op_order=tuple(op_order),
        )

    order = sorted(item_meta, key=lambda name: f"{item_meta[name].origin} {name}")
    return item_meta, order


def _emit_summary(
    summary: UpdateSummary,
    *,
    had_errors: bool,
    out: OutputOptions,
    dry_run: bool,
) -> int:
    if out.json_output:
        sys.stdout.write(f"{json.dumps(summary.to_dict())}\n")
        return 1 if had_errors else 0

    if dry_run:
        if summary.updated:
            out.print(
                f"\nAvailable updates: {', '.join(summary.updated)}",
                style="green",
            )
        else:
            out.print("\nNo updates available.", style="dim")
    elif summary.updated:
        out.print(
            f"\n:heavy_check_mark: Updated: {', '.join(summary.updated)}",
            style="green",
        )
    else:
        out.print("\nNo updates needed.", style="dim")

    if summary.errors:
        out.print_error(f"\nFailed: {', '.join(summary.errors)}")

    return 1 if had_errors else 0


def _merge_source_updates(
    existing_entries: dict[str, SourceEntry],
    source_updates: dict[str, SourceEntry],
    *,
    native_only: bool,
) -> dict[str, SourceEntry]:
    if not native_only:
        return source_updates
    return {
        name: existing_entries[name].merge(entry) if name in existing_entries else entry
        for name, entry in source_updates.items()
    }


@dataclass(frozen=True)
class _SourceTaskContext:
    sources: SourcesFile
    update_input: bool
    native_only: bool
    session: aiohttp.ClientSession
    update_input_lock: asyncio.Lock
    queue: asyncio.Queue[UpdateEvent | None]
    config: UpdateConfig | None = None
    pinned_version: VersionInfo | None = None


@dataclass(frozen=True)
class _SourcesPhaseContext:
    source_names: list[str]
    sources: SourcesFile
    queue: asyncio.Queue[UpdateEvent | None]
    update_input: bool
    native_only: bool
    config: UpdateConfig
    pinned: dict[str, VersionInfo]


async def _update_source_task(
    name: str,
    *,
    context: _SourceTaskContext,
) -> None:
    async def _run() -> None:
        resolved_config = resolve_active_config(context.config)
        current = context.sources.entries.get(name)
        updater = _get_updaters()[name](config=resolved_config)
        if isinstance(updater, FlakeInputHashUpdater):
            updater.native_only = context.native_only
        input_name = getattr(updater, "input_name", None)
        put = context.queue.put

        await put(UpdateEvent.status(name, "Starting update"))
        if context.update_input and input_name:
            await put(
                UpdateEvent.status(
                    name,
                    f"Updating flake input '{input_name}'...",
                    operation="refresh_lock",
                    status="refresh_lock",
                    detail=input_name,
                ),
            )
            async with context.update_input_lock:
                async for event in update_flake_input(input_name, source=name):
                    await put(event)

        async for event in updater.update_stream(
            current,
            context.session,
            pinned_version=context.pinned_version,
        ):
            await put(event)

    await run_queue_task(source=name, queue=context.queue, task=_run)


def _resolve_runtime_config(opts: UpdateOptions) -> UpdateConfig:
    return resolve_config(
        http_timeout=opts.http_timeout,
        subprocess_timeout=opts.subprocess_timeout,
        log_tail_lines=opts.log_tail_lines,
        render_interval=opts.render_interval,
        user_agent=opts.user_agent,
        retries=opts.retries,
        retry_backoff=opts.retry_backoff,
        fake_hash=opts.fake_hash,
        max_nix_builds=opts.max_nix_builds,
        deno_platforms=opts.deno_platforms,
    )


def _handle_schema_request(opts: UpdateOptions) -> int | None:
    if not opts.schema:
        return None
    sys.stdout.write(f"{json.dumps(SourcesFile.json_schema())}\n")
    return 0


def _resolve_root_input_node(
    lock: FlakeLock,
    input_name: str,
) -> tuple[FlakeLockNode | None, str | None]:
    return resolve_root_input_node(lock, input_name)


def _repo_relative_path(path: Path | None) -> str | None:
    return _repo_relative_path_impl(path, repo_root=get_repo_root)


def _generated_artifact_paths(name: str, updater_cls: type[Updater]) -> tuple[str, ...]:
    return _generated_artifact_paths_impl(
        name,
        updater_cls,
        package_dir_for=package_dir_for,
        repo_relative_path=_repo_relative_path,
    )


def _build_update_inventory() -> list[_InventoryTarget]:
    return _build_update_inventory_impl(
        load_sources=load_all_sources,
        source_path_map=package_file_map,
        list_ref_inputs=get_flake_inputs_with_refs,
        load_lock=load_flake_lock,
        get_updaters=_get_updaters,
        source_file_for=sources_file_for,
        resolve_root_input_node=_resolve_root_input_node,
        source_backing_input_name=_source_backing_input_name,
        generated_artifact_paths=_generated_artifact_paths,
        source_hash_kinds=_source_hash_kinds,
        classify_updater_kind=_classify_updater_kind,
        repo_relative_path=_repo_relative_path,
    )


def _collect_flake_inputs_for_list() -> list[_ListRow]:
    return _collect_flake_inputs_for_list_impl(
        load_lock=load_flake_lock,
        resolve_root_input_node=_resolve_root_input_node,
        flake_source_string=_flake_source_string,
        get_flake_input_version=get_flake_input_version,
    )


def _collect_source_entries_for_list() -> list[_ListRow]:
    return _collect_source_entries_for_list_impl(
        load_sources=load_all_sources,
        source_path_map=package_file_map,
    )


def _handle_list_targets_request(opts: UpdateOptions) -> int | None:
    return _handle_list_targets_request_impl(
        opts,
        build_update_inventory=_build_update_inventory,
        inventory_sort_value=_inventory_sort_value,
        build_inventory_summary=_build_inventory_summary,
    )


def _handle_validate_request(opts: UpdateOptions, out: OutputOptions) -> int | None:
    return _handle_validate_request_impl(
        opts,
        out,
        load_sources=load_all_sources,
        validate_source_discovery_consistency=validate_source_discovery_consistency,
    )


def _validate_list_sort_option(opts: UpdateOptions, out: OutputOptions) -> int | None:
    return _validate_list_sort_option_impl(opts, out)


def _resolve_tty_settings(
    opts: UpdateOptions,
    resolved: ResolvedTargets,
) -> tuple[bool, bool]:
    tty_enabled = _is_tty(
        force_tty=True if opts.tty in ("force", "full") else None,
        no_tty=True if opts.tty == "off" else None,
        zellij_guard=opts.zellij_guard,
    )
    show_phase_headers = all(
        (
            not opts.json,
            not opts.quiet,
            not tty_enabled,
            resolved.do_refs,
            resolved.do_sources,
            bool(resolved.ref_inputs),
            bool(resolved.source_names),
        ),
    )
    return tty_enabled, show_phase_headers


def _load_sources_for_run(resolved: ResolvedTargets) -> SourcesFile:
    if resolved.do_sources and resolved.source_names:
        return load_all_sources()
    return SourcesFile(entries={})


def _load_pinned_versions(
    opts: UpdateOptions,
    out: OutputOptions,
) -> dict[str, VersionInfo]:
    if not opts.pinned_versions:
        return {}

    pinned = load_pinned_versions(Path(opts.pinned_versions))
    out.print(
        f"Loaded {len(pinned)} pinned versions from {opts.pinned_versions}",
        style="dim",
    )
    return pinned


async def _run_ref_phase(
    *,
    ref_inputs: list[FlakeInputRef],
    queue: asyncio.Queue[UpdateEvent | None],
    dry_run: bool,
    config: UpdateConfig,
) -> None:
    async with aiohttp.ClientSession() as session:
        flake_edit_lock = asyncio.Lock()
        async with asyncio.TaskGroup() as group:
            for inp in ref_inputs:
                group.create_task(
                    update_refs_task(
                        inp,
                        session,
                        queue,
                        options=RefTaskOptions(
                            dry_run=dry_run,
                            flake_edit_lock=flake_edit_lock,
                            config=config,
                        ),
                    ),
                )


async def _run_sources_phase(
    context: _SourcesPhaseContext,
) -> None:
    async with aiohttp.ClientSession() as session:
        update_input_lock = asyncio.Lock()
        async with asyncio.TaskGroup() as group:
            for name in context.source_names:
                group.create_task(
                    _update_source_task(
                        name,
                        context=_SourceTaskContext(
                            sources=context.sources,
                            update_input=context.update_input,
                            native_only=context.native_only,
                            session=session,
                            update_input_lock=update_input_lock,
                            queue=context.queue,
                            config=context.config,
                            pinned_version=context.pinned.get(name),
                        ),
                    )
                )


def _flatten_artifact_updates(
    artifact_updates: dict[str, tuple[GeneratedArtifact, ...]],
) -> list[GeneratedArtifact]:
    """Flatten per-source generated artifact updates into one list."""
    return [
        artifact
        for source in sorted(artifact_updates)
        for artifact in artifact_updates[source]
    ]


def _persist_generated_artifacts(
    *,
    resolved: ResolvedTargets,
    artifact_updates: dict[str, tuple[GeneratedArtifact, ...]],
    details: dict[str, SummaryStatus],
) -> None:
    """Persist generated artifacts emitted by source updaters."""
    if not (resolved.do_sources and resolved.source_names):
        return
    if resolved.dry_run or not artifact_updates:
        return
    successful_updates = {
        source: artifacts
        for source, artifacts in artifact_updates.items()
        if details.get(source) == "updated"
    }
    if not successful_updates:
        return
    save_generated_artifacts(_flatten_artifact_updates(successful_updates))


def _persist_source_updates(
    *,
    resolved: ResolvedTargets,
    sources: SourcesFile,
    source_updates: dict[str, SourceEntry],
    details: dict[str, SummaryStatus],
) -> None:
    if not (resolved.do_sources and resolved.source_names):
        return

    if source_updates:
        merged_updates = _merge_source_updates(
            sources.entries,
            source_updates,
            native_only=resolved.native_only,
        )
        sources.entries.update(merged_updates)

    if (
        not resolved.dry_run
        and source_updates
        and any(details.get(name) == "updated" for name in resolved.source_names)
    ):
        save_sources(sources)


def _persist_materialized_updates(
    *,
    resolved: ResolvedTargets,
    sources: SourcesFile,
    source_updates: dict[str, SourceEntry],
    artifact_updates: dict[str, tuple[GeneratedArtifact, ...]],
    details: dict[str, SummaryStatus],
) -> None:
    """Persist generated artifacts first, then update per-package sources."""
    _persist_generated_artifacts(
        resolved=resolved,
        artifact_updates=artifact_updates,
        details=details,
    )
    _persist_source_updates(
        resolved=resolved,
        sources=sources,
        source_updates=source_updates,
        details=details,
    )


@dataclass(frozen=True)
class _RunPlan:
    resolved: ResolvedTargets
    tty_enabled: bool
    show_phase_headers: bool
    sources: SourcesFile
    item_meta: dict[str, ItemMeta]
    order: list[str]


def _handle_preflight_requests(opts: UpdateOptions, out: OutputOptions) -> int | None:
    sort_validation = _validate_list_sort_option(opts, out)
    if sort_validation is not None:
        return sort_validation

    schema_result = _handle_schema_request(opts)
    if schema_result is not None:
        return schema_result

    list_result = _handle_list_targets_request(opts)
    if list_result is not None:
        return list_result

    return _handle_validate_request(opts, out)


def _build_run_plan(opts: UpdateOptions, out: OutputOptions) -> _RunPlan | int:
    resolved = ResolvedTargets.from_options(opts)
    tty_enabled, show_phase_headers = _resolve_tty_settings(opts, resolved)

    if opts.source and opts.source not in resolved.all_known_names:
        out.print_error(f"Error: Unknown source or input '{opts.source}'")
        out.print_error(f"Available: {', '.join(sorted(resolved.all_known_names))}")
        return 1

    if not resolved.ref_inputs and not resolved.source_names:
        return _emit_summary(
            UpdateSummary(),
            had_errors=False,
            out=out,
            dry_run=resolved.dry_run,
        )

    sources = _load_sources_for_run(resolved)
    item_meta, order = _build_item_meta(
        resolved,
        sources if resolved.do_sources else None,
    )
    if not order:
        return _emit_summary(
            UpdateSummary(),
            had_errors=False,
            out=out,
            dry_run=resolved.dry_run,
        )

    return _RunPlan(
        resolved=resolved,
        tty_enabled=tty_enabled,
        show_phase_headers=show_phase_headers,
        sources=sources,
        item_meta=item_meta,
        order=order,
    )


async def _execute_run_plan(
    opts: UpdateOptions,
    out: OutputOptions,
    config: UpdateConfig,
    plan: _RunPlan,
) -> int:
    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    is_tty = plan.tty_enabled and not opts.quiet and not opts.json
    full_output = _resolve_full_output(
        full_output=True if opts.tty == "full" else None,
    )
    consumer = asyncio.create_task(
        consume_events(
            queue,
            plan.order,
            plan.sources,
            options=ConsumeEventsOptions(
                item_meta=plan.item_meta,
                max_lines=config.default_log_tail_lines,
                is_tty=is_tty,
                full_output=full_output,
                verbose=opts.verbose,
                render_interval=config.default_render_interval,
                build_failure_tail_lines=NIX_BUILD_FAILURE_TAIL_LINES,
                quiet=opts.quiet or opts.json,
            ),
        ),
    )

    if plan.resolved.do_refs and plan.resolved.ref_inputs:
        if plan.show_phase_headers:
            out.print("\nPhase 1: flake input refs", style="dim")
        await _run_ref_phase(
            ref_inputs=plan.resolved.ref_inputs,
            queue=queue,
            dry_run=plan.resolved.dry_run,
            config=config,
        )

    if plan.resolved.do_sources and plan.resolved.source_names:
        if plan.show_phase_headers:
            out.print("\nPhase 2: sources.json updates", style="dim")
        pinned = _load_pinned_versions(opts, out)
        await _run_sources_phase(
            context=_SourcesPhaseContext(
                source_names=plan.resolved.source_names,
                sources=plan.sources,
                queue=queue,
                update_input=(
                    plan.resolved.do_input_refresh and not plan.resolved.dry_run
                ),
                native_only=plan.resolved.native_only,
                config=config,
                pinned=pinned,
            ),
        )

    await queue.put(None)
    consume_result = await consumer

    summary = UpdateSummary()
    summary.accumulate(consume_result.details)
    _persist_materialized_updates(
        resolved=plan.resolved,
        sources=plan.sources,
        source_updates=consume_result.source_updates,
        artifact_updates=consume_result.artifact_updates,
        details=consume_result.details,
    )

    return _emit_summary(
        summary,
        had_errors=consume_result.errors > 0,
        out=out,
        dry_run=plan.resolved.dry_run,
    )


async def run_updates(opts: UpdateOptions) -> int:
    """Core update workflow — accepts typed UpdateOptions, returns exit code."""
    out = OutputOptions(json_output=opts.json, quiet=opts.quiet)
    config = _resolve_runtime_config(opts)

    preflight_result = _handle_preflight_requests(opts, out)
    if preflight_result is not None:
        return preflight_result

    run_plan = _build_run_plan(opts, out)
    if isinstance(run_plan, int):
        return run_plan

    return await _execute_run_plan(opts, out, config, run_plan)


def run_update_command(  # noqa: PLR0913
    source: str | None = None,
    *,
    check: bool = False,
    deno_platforms: str | None = None,
    fake_hash: str | None = None,
    http_timeout: int | None = None,
    json_output: bool = False,
    list_targets: bool = False,
    log_tail_lines: int | None = None,
    max_nix_builds: int | None = None,
    native_only: bool = False,
    no_input: bool = False,
    no_refs: bool = False,
    no_sources: bool = False,
    pinned_versions: str | None = None,
    quiet: bool = False,
    render_interval: float | None = None,
    retries: int | None = None,
    retry_backoff: float | None = None,
    schema: bool = False,
    sort_by: UpdateSortBy = "name",
    subprocess_timeout: int | None = None,
    tty: UpdateTTYMode = "auto",
    user_agent: str | None = None,
    validate: bool = False,
    verbose: bool = False,
    zellij_guard: bool | None = None,
) -> int:
    """Run update workflow from typed CLI options."""
    opts = UpdateOptions.from_mapping(locals())

    if not (opts.list_targets or opts.schema or opts.validate):
        needs_flake_edit = not opts.no_refs and not opts.native_only
        if needs_flake_edit and opts.source:
            ref_names = {i.name for i in get_flake_inputs_with_refs()}
            needs_flake_edit = opts.source in ref_names

        missing = check_required_tools(
            include_flake_edit=needs_flake_edit,
            source=opts.source,
            needs_sources=not opts.no_sources,
        )
        if missing:
            sys.stderr.write(f"Error: Required tools not found: {', '.join(missing)}\n")
            sys.stderr.write("Please install them and ensure they are in your PATH.\n")
            return 1

    return asyncio.run(run_updates(opts))


app = typer.Typer(
    help="Update source versions/hashes and flake input refs.",
    add_completion=False,
    no_args_is_help=False,
    context_settings=HELP_CONTEXT_SETTINGS,
)


@app.callback(invoke_without_command=True)
def cli(  # noqa: PLR0913
    source: Annotated[
        str | None,
        typer.Argument(help="Source or flake input to update (default: all)."),
    ] = None,
    *,
    check: Annotated[
        bool,
        typer.Option("--check", "-c", help="Dry run: check without applying."),
    ] = False,
    deno_platforms: Annotated[
        str | None,
        typer.Option(
            "-d",
            "--deno-platforms",
            help="Comma-separated Deno platforms.",
        ),
    ] = None,
    fake_hash: Annotated[
        str | None,
        typer.Option("-f", "--fake-hash", help="Fake hash placeholder."),
    ] = None,
    http_timeout: Annotated[
        int | None,
        typer.Option("-H", "--http-timeout", help="HTTP timeout in seconds."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Output results as JSON."),
    ] = False,
    list_targets: Annotated[
        bool,
        typer.Option("--list", "-l", help="List update inventory."),
    ] = False,
    log_tail_lines: Annotated[
        int | None,
        typer.Option("-L", "--log-tail-lines", help="Log tail lines."),
    ] = None,
    max_nix_builds: Annotated[
        int | None,
        typer.Option(
            "-m",
            "--max-nix-builds",
            help="Max concurrent nix build processes.",
        ),
    ] = None,
    native_only: Annotated[
        bool,
        typer.Option(
            "--native-only",
            "-n",
            help="Only compute hashes for current platform (CI). Implies --no-refs.",
        ),
    ] = False,
    no_input: Annotated[
        bool,
        typer.Option(
            "--no-input",
            "-I",
            help="Skip flake input lock refresh before hashing.",
        ),
    ] = False,
    no_refs: Annotated[
        bool,
        typer.Option("--no-refs", "-R", help="Skip flake input ref updates."),
    ] = False,
    no_sources: Annotated[
        bool,
        typer.Option("--no-sources", "-S", help="Skip sources.json hash updates."),
    ] = False,
    pinned_versions: Annotated[
        str | None,
        typer.Option("-p", "--pinned-versions", help="Path to pinned-versions.json."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress progress output."),
    ] = False,
    render_interval: Annotated[
        float | None,
        typer.Option(
            "-r",
            "--render-interval",
            help="TTY render interval in seconds.",
        ),
    ] = None,
    retries: Annotated[
        int | None,
        typer.Option("-N", "--retries", help="HTTP retries."),
    ] = None,
    retry_backoff: Annotated[
        float | None,
        typer.Option("-b", "--retry-backoff", help="HTTP retry backoff seconds."),
    ] = None,
    schema: Annotated[
        bool,
        typer.Option("--schema", "-s", help="Output JSON schema for sources.json."),
    ] = False,
    sort_by: Annotated[
        UpdateSortBy,
        typer.Option(
            "--sort",
            "-o",
            help=(
                "Sort --list inventory by field: name, type/classification, "
                "source/input, ref/version, rev/commit, touches, or writes."
            ),
        ),
    ] = "name",
    subprocess_timeout: Annotated[
        int | None,
        typer.Option(
            "-T",
            "--subprocess-timeout",
            help="Subprocess timeout in seconds.",
        ),
    ] = None,
    tty: Annotated[
        UpdateTTYMode,
        typer.Option("--tty", "-t", help="TTY rendering mode."),
    ] = "auto",
    user_agent: Annotated[
        str | None,
        typer.Option("-u", "--user-agent", help="HTTP user agent."),
    ] = None,
    validate: Annotated[
        bool,
        typer.Option("--validate", "-v", help="Validate sources.json and exit."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-V", help="Stream build log lines to stdout."),
    ] = False,
    zellij_guard: Annotated[
        bool | None,
        typer.Option(
            "-z/-Z",
            "--zellij-guard/--no-zellij-guard",
            help="Disable live rendering under Zellij.",
        ),
    ] = None,
) -> None:
    """Update source versions/hashes and flake input refs."""
    raise typer.Exit(code=run_update_command(**locals()))
