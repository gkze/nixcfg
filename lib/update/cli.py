"""CLI entry point for update workflows."""

import asyncio
import json
import os
import shutil
import sys
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Unpack, cast

import click
import typer
from dbos import DBOS
from pydantic import ValidationError
from rich.console import Console
from rich.text import Text
from typer import _click as typer_click

from lib.cli import HELP_CONTEXT_SETTINGS
from lib.diagnostics import redact_urls
from lib.nix.models.flake_lock import FlakeLock
from lib.nix.models.sources import SourcesFile
from lib.update import derivation_validation as update_derivation_validation
from lib.update import durable
from lib.update import flake as update_flake
from lib.update import lock_policy as update_lock_policy
from lib.update import persistence as update_persistence
from lib.update import planner as update_planner
from lib.update import runtime_source as update_runtime_source
from lib.update import source_runner as update_source_runner
from lib.update import updaters as updater_module
from lib.update.cli_inventory import handle_list_targets_request
from lib.update.cli_options import (
    RepairAgent,
    UpdateOptions,
    UpdateOptionsKwargs,
    UpdateSortBy,
    UpdateTTYMode,
)
from lib.update.cli_validation import (
    handle_validate_request,
    validate_list_sort_option,
)
from lib.update.config import (
    UpdateConfig,
    env_bool,
    resolve_config,
)
from lib.update.constants import ALL_TOOLS, NIX_BUILD_FAILURE_TAIL_LINES, REQUIRED_TOOLS
from lib.update.derivation_validation import (
    ValidationCommandFinished,
    ValidationCommandOutput,
    ValidationCommandStarted,
)
from lib.update.errors import format_exception
from lib.update.event_queue import EVENT_QUEUE_SIZE, run_event_pipeline
from lib.update.paths import get_repo_root
from lib.update.refs import (
    FlakeInputRef,
    get_flake_inputs_with_refs,
)
from lib.update.run_monitor import (
    RunMonitor,
    default_run_log_root,
    format_run_status,
    load_run_status,
    seconds_since,
)
from lib.update.runtime import active_runtime, runtime_scope
from lib.update.sources import load_all_sources
from lib.update.summary import UpdateSummary, emit_summary
from lib.update.ui_consumer import ConsumeEventsOptions, consume_events
from lib.update.ui_render import ValidationDisplay
from lib.update.ui_state import ItemMeta, OperationKind
from lib.update.updaters import UPDATERS, UpdaterClass, ensure_updaters_loaded

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from lib.nix.models.flake_lock import FlakeLockNode
    from lib.update.candidate import Preparation
    from lib.update.events import UpdateEvent
    from lib.update.updaters.core import Updater

_RUN_PHASE_COUNT = 4
_MAX_VALIDATION_ROUNDS = 3
_FLAKE_FILES = (Path("flake.nix"), Path("flake.lock"))

_TRAILING_TARGET_FLAG_OPTIONS: dict[str, tuple[str, bool]] = {
    "--check": ("check", True),
    "-c": ("check", True),
    "--json": ("json_output", True),
    "-j": ("json_output", True),
    "--list": ("list_targets", True),
    "-l": ("list_targets", True),
    "--native-only": ("native_only", True),
    "-n": ("native_only", True),
    "--no-input": ("no_input", True),
    "-I": ("no_input", True),
    "--no-refs": ("no_refs", True),
    "-R": ("no_refs", True),
    "--no-sources": ("no_sources", True),
    "-S": ("no_sources", True),
    "--quiet": ("quiet", True),
    "-q": ("quiet", True),
    "--schema": ("schema", True),
    "-s": ("schema", True),
    "--status": ("status", True),
    "--strict": ("strict", True),
    "--validate": ("validate", True),
    "-v": ("validate", True),
    "--timings": ("timings", True),
    "--verbose": ("verbose", True),
    "-V": ("verbose", True),
    "--zellij-guard": ("zellij_guard", True),
    "-z": ("zellij_guard", True),
    "--no-zellij-guard": ("zellij_guard", False),
    "-Z": ("zellij_guard", False),
}
_TRAILING_TARGET_VALUE_OPTIONS: dict[str, tuple[str, str]] = {
    "--deno-platforms": ("deno_platforms", "str"),
    "-d": ("deno_platforms", "str"),
    "--fake-hash": ("fake_hash", "str"),
    "-f": ("fake_hash", "str"),
    "--http-timeout": ("http_timeout", "int"),
    "-H": ("http_timeout", "int"),
    "--log-tail-lines": ("log_tail_lines", "int"),
    "-L": ("log_tail_lines", "int"),
    "--max-nix-builds": ("max_nix_builds", "int"),
    "-m": ("max_nix_builds", "int"),
    "--render-interval": ("render_interval", "float"),
    "-r": ("render_interval", "float"),
    "--retries": ("retries", "int"),
    "-N": ("retries", "int"),
    "--resume": ("resume", "str"),
    "--run-id": ("run_id", "str"),
    "--patch": ("patch", "str"),
    "--repair": ("repair", "repair_agent"),
    "--retry-backoff": ("retry_backoff", "float"),
    "-b": ("retry_backoff", "float"),
    "--sort": ("sort_by", "str"),
    "-o": ("sort_by", "str"),
    "--subprocess-timeout": ("subprocess_timeout", "int"),
    "-T": ("subprocess_timeout", "int"),
    "--tty": ("tty", "str"),
    "-t": ("tty", "str"),
    "--user-agent": ("user_agent", "str"),
    "-u": ("user_agent", "str"),
}


def _coerce_trailing_target_option(
    *,
    option: str,
    raw_value: str,
    value_kind: str,
) -> object:
    if value_kind == "int":
        try:
            return int(raw_value)
        except ValueError as exc:
            msg = f"{option} expects an integer value"
            raise typer.BadParameter(msg) from exc
    if value_kind == "float":
        try:
            return float(raw_value)
        except ValueError as exc:
            msg = f"{option} expects a numeric value"
            raise typer.BadParameter(msg) from exc
    if value_kind == "repair_agent":
        try:
            return RepairAgent(raw_value)
        except ValueError as exc:
            choices = ", ".join(sorted(RepairAgent))
            msg = f"{option} expects one of {choices}"
            raise typer.BadParameter(msg) from exc
    return raw_value


def _split_trailing_target_options(
    targets: list[str] | tuple[str, ...] | None,
) -> tuple[tuple[str, ...], dict[str, object]]:
    """Recover options that Click placed inside the variadic target argument."""
    normalized_targets: list[str] = []
    option_values: dict[str, object] = {}
    tokens = list(targets or ())
    index = 0
    while index < len(tokens):
        raw_arg = tokens[index]
        if raw_arg == "--":
            normalized_targets.extend(tokens[index + 1 :])
            break

        option = raw_arg
        inline_value: str | None = None
        if raw_arg.startswith("--") and "=" in raw_arg:
            option, inline_value = raw_arg.split("=", 1)

        flag_spec = _TRAILING_TARGET_FLAG_OPTIONS.get(option)
        if flag_spec is not None:
            if inline_value is not None:
                msg = f"{option} does not take a value"
                raise typer.BadParameter(msg)
            destination, value = flag_spec
            option_values[destination] = value
            index += 1
            continue

        value_spec = _TRAILING_TARGET_VALUE_OPTIONS.get(option)
        if value_spec is not None:
            destination, value_kind = value_spec
            if inline_value is None:
                index += 1
                if index >= len(tokens):
                    msg = f"{option} requires a value"
                    raise typer.BadParameter(msg)
                inline_value = tokens[index]
            option_values[destination] = _coerce_trailing_target_option(
                option=option,
                raw_value=inline_value,
                value_kind=value_kind,
            )
            index += 1
            continue

        normalized_targets.append(raw_arg)
        index += 1

    return tuple(normalized_targets), option_values


__all__ = (
    "OutputOptions",
    "ResolvedTargets",
    "UpdateOptions",
    "app",
    "check_required_tools",
    "cli",
    "run_update_command",
    "run_updates",
)


def _get_updaters() -> dict[str, UpdaterClass]:
    return updater_module.resolve_registry_alias(UPDATERS, ensure_updaters_loaded)


def _shows_materialize_artifacts_phase(updater_cls: type[Updater] | None) -> bool:
    if updater_cls is None:
        return False
    return bool(getattr(updater_cls, "shows_materialize_artifacts_phase", False))


def _build_update_options(values: UpdateOptionsKwargs) -> UpdateOptions:
    """Compatibility wrapper for shared option construction."""
    return UpdateOptions.from_mapping(values)


def _needs_flake_edit(opts: UpdateOptions) -> bool:
    """Return whether the current option set needs flake-edit installed."""
    if opts.no_refs or opts.native_only:
        return False
    target_names = opts.target_names
    if not target_names:
        return True
    ref_names = {i.name for i in get_flake_inputs_with_refs()}
    return any(target in ref_names for target in target_names)


def check_required_tools(
    *,
    include_flake_edit: bool = False,
    source: str | None = None,
    targets: tuple[str, ...] | list[str] | None = None,
    needs_sources: bool = True,
) -> list[str]:
    """Return names of required CLI tools that are missing from ``$PATH``."""
    updaters = _get_updaters()
    target_names = tuple(targets) if targets is not None else ()
    if not target_names and source:
        target_names = (source,)
    tools: list[str]
    if not needs_sources:
        # refs-only (or explicit --no-sources) mode: don't require hash tooling.
        tools = [str(tool) for tool in REQUIRED_TOOLS]
    elif target_names:
        selected_sources = update_planner.select_target_source_names(
            target_names,
            updaters,
        )
        if selected_sources:
            target_ref_names = {i.name for i in get_flake_inputs_with_refs()}
            required_tools = {
                str(tool)
                for selected in selected_sources
                for tool in getattr(updaters[selected], "required_tools", ALL_TOOLS)
            }
            if any(target in target_ref_names for target in target_names):
                required_tools.update(str(tool) for tool in REQUIRED_TOOLS)
            tools = sorted(required_tools)
        else:
            # refs-only targets only need nix (and possibly flake-edit).
            tools = [str(tool) for tool in REQUIRED_TOOLS]
    else:
        tools = [str(tool) for tool in ALL_TOOLS]
    if include_flake_edit:
        tools.append("flake-edit")
    return [tool for tool in tools if shutil.which(tool) is None]


def _handle_required_tool_check(opts: UpdateOptions) -> int | None:
    """Validate required external tools for non-query update runs."""
    missing = check_required_tools(
        include_flake_edit=_needs_flake_edit(opts),
        targets=opts.target_names,
        needs_sources=not opts.no_sources,
    )
    if not missing:
        return None

    sys.stderr.write(f"Error: Required tools not found: {', '.join(missing)}\n")
    sys.stderr.write("Please install them and ensure they are in your PATH.\n")
    return 1


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
    timings: bool = False
    _console: Console | None = field(default=None, repr=False, init=False)
    _err_console: Console | None = field(default=None, repr=False, init=False)

    @property
    def console(self) -> Console:
        """Lazily create stdout console on first access."""
        if self._console is None:
            no_color = not sys.stdout.isatty()
            self._console = Console(
                no_color=no_color,
                highlight=not no_color,
                emoji=False,
            )
        return self._console

    @property
    def err_console(self) -> Console:
        """Lazily create stderr console on first access."""
        if self._err_console is None:
            no_color = not sys.stderr.isatty()
            self._err_console = Console(
                stderr=True,
                no_color=no_color,
                highlight=not no_color,
                emoji=False,
            )
        return self._err_console

    def print(
        self,
        message: str,
        *,
        style: str | None = None,
        stderr: bool = False,
    ) -> None:
        """Print a message unless quiet or json mode is enabled.

        Lines are never hard-wrapped: status lines and paths must survive
        intact in logs and pipes, so wrapping is left to the terminal.
        """
        if not self.quiet and not self.json_output:
            target = self.err_console if stderr else self.console
            target.print(redact_urls(message), style=style, soft_wrap=True)

    def print_error(self, message: str) -> None:
        """Print an error message to stderr when not in json mode."""
        if not self.json_output:
            self.err_console.print(redact_urls(message), style="red")


_ORIGIN_FLAKE_ONLY = "(flake.nix)"
_ORIGIN_SOURCES_ONLY = "(sources.json)"
_ORIGIN_BOTH = "(flake.nix + sources.json)"


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
        return update_planner.resolve_update_targets(
            opts,
            updaters=_get_updaters(),
            ref_inputs=get_flake_inputs_with_refs(),
            result_type=cls,
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
        has_materialize_artifacts_phase = _shows_materialize_artifacts_phase(
            updater_cls
        )
        has_input_refresh = update_planner.source_backing_input_name(
            name, updater_cls, entry
        ) is not None or bool(update_planner.source_additional_input_names(updater_cls))

        if in_flake and in_sources:
            origin = _ORIGIN_BOTH
            op_order = [OperationKind.CHECK_VERSION, OperationKind.UPDATE_REF]
            if has_input_refresh:
                op_order.append(OperationKind.REFRESH_LOCK)
            if has_materialize_artifacts_phase:
                op_order.append(OperationKind.MATERIALIZE_ARTIFACTS)
            op_order.append(OperationKind.COMPUTE_HASH)
        elif in_sources and has_input_refresh:
            origin = _ORIGIN_SOURCES_ONLY
            op_order = [
                OperationKind.CHECK_VERSION,
                OperationKind.REFRESH_LOCK,
            ]
            if has_materialize_artifacts_phase:
                op_order.append(OperationKind.MATERIALIZE_ARTIFACTS)
            op_order.append(OperationKind.COMPUTE_HASH)
        elif in_sources:
            origin = _ORIGIN_SOURCES_ONLY
            op_order = [
                OperationKind.CHECK_VERSION,
            ]
            if has_materialize_artifacts_phase:
                op_order.append(OperationKind.MATERIALIZE_ARTIFACTS)
            op_order.append(OperationKind.COMPUTE_HASH)
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


def _resolve_runtime_config(opts: UpdateOptions) -> UpdateConfig:
    if (run := durable.current_run()) is not None:
        return run.config
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


def _resolve_tty_settings(opts: UpdateOptions) -> tuple[bool, bool]:
    tty_enabled = _is_tty(
        force_tty=True if opts.tty in ("force", "full") else None,
        no_tty=True if opts.tty == "off" else None,
        zellij_guard=opts.zellij_guard,
    )
    # Plain output always names the phase it is in; the live panel shows it
    # in the status line instead.
    show_phase_headers = not opts.json and not opts.quiet and not tty_enabled
    return tty_enabled, show_phase_headers


def _load_sources_for_run(resolved: ResolvedTargets) -> SourcesFile:
    if resolved.do_sources and resolved.source_names:
        return load_all_sources()
    return SourcesFile(entries={})


@dataclass(frozen=True)
class _RunPlan:
    resolved: ResolvedTargets
    sources: SourcesFile
    item_meta: dict[str, ItemMeta]
    order: list[str]


@dataclass(frozen=True)
class _RunExecutionResult:
    """Materialized phase result awaiting snapshot validation and promotion."""

    summary: UpdateSummary
    candidate_updates: tuple[str, ...]
    had_errors: bool
    written_paths: tuple[Path, ...]


@dataclass(frozen=True)
class _RunPlanError:
    """Target-selection failure discovered inside the isolated checkout."""

    message: str
    unknown_targets: tuple[str, ...]
    available_targets: tuple[str, ...]


@dataclass
class _RunOutcome:
    """Final update result emitted only after workspace teardown."""

    summary: UpdateSummary = field(default_factory=UpdateSummary)
    candidate_updates: tuple[str, ...] = ()
    had_errors: bool = False
    promoted: bool = False
    promotion_state: update_persistence.UpdatePromotionState | None = None
    plan_error: _RunPlanError | None = None
    workspace_error: str | None = None
    validation_error: str | None = None
    # Candidates withheld because a coupled target failed, with the reason.
    dropped: dict[str, str] = field(default_factory=dict)
    written_paths: tuple[Path, ...] = ()
    run_dir: Path | None = None
    timings: list[dict[str, str | int | float]] | None = None
    patch: bytes = b""


def _record_workspace_failure(
    outcome: _RunOutcome,
    error: update_persistence.UpdateWorkspaceError,
) -> None:
    """Merge one workspace failure and its recovered promotion state."""
    outcome.summary.accumulate({"workspace": "error"})
    outcome.had_errors = True
    outcome.promotion_state = error.promotion_state
    if error.promotion_state is update_persistence.UpdatePromotionState.PROMOTED:
        outcome.promoted = True
    outcome.workspace_error = str(error)


def _run_log_root(config: UpdateConfig) -> Path | None:
    """Return where new run directories go, or ``None`` when logging is off."""
    if not config.run_log:
        return None
    return config.run_log_dir or default_run_log_root()


def _handle_status_request(
    opts: UpdateOptions,
    out: OutputOptions,
    config: UpdateConfig,
) -> int | None:
    """Handle ``--status [RUN_ID]`` from the persisted run state."""
    if not opts.status:
        return None
    run_id = opts.target_names[0] if opts.target_names else None
    run_root = config.run_log_dir or default_run_log_root()
    try:
        run_dir, status, updated_at = load_run_status(run_root, run_id)
    except FileNotFoundError as error:
        if opts.json:
            sys.stdout.write(f"{json.dumps({'success': False, 'error': str(error)})}\n")
        else:
            out.print_error(f"Error: {error}")
        return 1
    age = seconds_since(updated_at)
    if opts.json:
        payload = {
            "success": True,
            "runDir": str(run_dir),
            "updatedAt": updated_at,
            "status": asdict(status),
        }
        sys.stdout.write(f"{json.dumps(payload)}\n")
        return 0
    out.print(format_run_status(status, updated_seconds_ago=age))
    out.print(f"Run log: {run_dir}", style="dim")
    return 0


def _handle_preflight_requests(
    opts: UpdateOptions,
    out: OutputOptions,
    config: UpdateConfig,
) -> int | None:
    sort_validation = validate_list_sort_option(opts, out)
    if sort_validation is not None:
        return sort_validation

    schema_result = _handle_schema_request(opts)
    if schema_result is not None:
        return schema_result

    status_result = _handle_status_request(opts, out, config)
    if status_result is not None:
        return status_result

    list_result = handle_list_targets_request(opts)
    if list_result is not None:
        return list_result

    return handle_validate_request(opts, out)


def _build_run_plan(opts: UpdateOptions) -> _RunPlan | _RunPlanError | None:
    resolved = ResolvedTargets.from_options(opts)

    unknown_targets = [
        target for target in opts.target_names if target not in resolved.all_known_names
    ]
    if unknown_targets:
        if len(unknown_targets) == 1:
            message = f"Unknown source or input '{unknown_targets[0]}'"
        else:
            message = "Unknown sources or inputs: " + ", ".join(unknown_targets)
        return _RunPlanError(
            message=message,
            unknown_targets=tuple(unknown_targets),
            available_targets=tuple(sorted(resolved.all_known_names)),
        )

    if not resolved.ref_inputs and not resolved.source_names:
        return None

    sources = _load_sources_for_run(resolved)
    item_meta, order = _build_item_meta(
        resolved,
        sources if resolved.do_sources else None,
    )
    if not order:
        return None

    return _RunPlan(
        resolved=resolved,
        sources=sources,
        item_meta=item_meta,
        order=order,
    )


def _record_derivation_validation_failures(
    summary: UpdateSummary,
    out: OutputOptions,
    failures: Iterable[update_derivation_validation.DerivationValidationFailure],
) -> bool:
    """Record and report derivation failures through one consistent boundary."""
    materialized_failures = tuple(failures)
    if not materialized_failures:
        return False
    summary.accumulate({failure.source: "error" for failure in materialized_failures})
    for failure in materialized_failures:
        out.print_error(
            f"[{failure.source}] Derivation validation failed for "
            f"{failure.installable}:\n{failure.message}"
        )
    return True


def _live_ui_enabled(opts: UpdateOptions) -> bool:
    """Return whether the live terminal panel owns progress output."""
    tty_enabled, _ = _resolve_tty_settings(opts)
    return tty_enabled and not opts.quiet and not opts.json


def _start_run_monitor(
    plan: _RunPlan,
    opts: UpdateOptions,
    out: OutputOptions,
    config: UpdateConfig,
) -> RunMonitor:
    """Create the run monitor, its run directory, and the heartbeat."""
    run_root = _run_log_root(config)
    try:
        run = durable.current_run()
        monitor = (
            RunMonitor(
                targets=plan.order,
                phase_count=_RUN_PHASE_COUNT,
                run_dir=run.store.path.parent,
                run_id=run.store.path.parent.name,
                diagnostics=config.run_log,
                inactivity_warning_seconds=config.inactivity_warning_seconds,
            )
            if run is not None
            else RunMonitor.start(
                targets=plan.order,
                phase_count=_RUN_PHASE_COUNT,
                run_root=run_root,
                inactivity_warning_seconds=config.inactivity_warning_seconds,
            )
        )
    except OSError as error:
        if durable.current_run() is not None:
            raise
        out.print_error(
            f"Warning: run log unavailable under {run_root} ({error}); "
            "continuing without it"
        )
        monitor = RunMonitor.start(
            targets=plan.order,
            phase_count=_RUN_PHASE_COUNT,
            run_root=None,
            inactivity_warning_seconds=config.inactivity_warning_seconds,
        )
    printer = None
    if not _live_ui_enabled(opts) and not opts.quiet and not opts.json:

        def printer(line: str) -> None:
            out.print(line, style="dim")

    monitor.start_heartbeat(config.heartbeat_interval, printer)
    if monitor.run_dir is not None:
        out.print(f"Run log: {monitor.run_dir}", style="dim")
    return monitor


async def _execute_run_plan_result(
    opts: UpdateOptions,
    out: OutputOptions,
    config: UpdateConfig,
    plan: _RunPlan,
    monitor: RunMonitor | None = None,
    *,
    preparation: Preparation | None = None,
) -> _RunExecutionResult:
    # Every plan runs in a disposable workspace. ``resolved.dry_run`` controls
    # reporting and live promotion, not whether the candidate is materialized.
    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue(maxsize=EVENT_QUEUE_SIZE)
    is_tty = _live_ui_enabled(opts)
    _, show_phase_headers = _resolve_tty_settings(opts)
    full_output = _resolve_full_output(
        full_output=True if opts.tty == "full" else None,
    )

    async def consume() -> None:
        await consume_events(
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
                monitor=monitor,
            ),
        )

    async def produce() -> update_source_runner.UpdatePhaseResult:
        phase_result = update_source_runner.UpdatePhaseResult()
        if plan.resolved.do_refs and plan.resolved.ref_inputs:
            if show_phase_headers:
                out.print("\nPhase 1: flake input refs", style="dim")
            if monitor is not None:
                monitor.begin_phase("flake input refs", 1)

            async def replay_refs(
                result: update_source_runner.UpdatePhaseResult,
            ) -> None:
                for event in result.terminal_events:
                    await queue.put(event)

            ref_result = await durable.phase(
                "refs",
                lambda: update_source_runner.run_ref_phase(
                    ref_inputs=plan.resolved.ref_inputs, queue=queue, config=config
                ),
                replay=replay_refs,
            )
            phase_result = phase_result.merged(ref_result)

        if plan.resolved.do_sources and plan.resolved.source_names:
            if show_phase_headers:
                out.print("\nPhase 2: sources.json updates", style="dim")
            if monitor is not None:
                monitor.begin_phase("sources", 2)
            source_result = await update_source_runner.run_sources_phase(
                update_source_runner.SourcesPhaseContext(
                    source_names=plan.resolved.source_names,
                    sources=plan.sources,
                    queue=queue,
                    update_input=plan.resolved.do_input_refresh,
                    native_only=plan.resolved.native_only or preparation is not None,
                    config=config,
                    input_refreshes=phase_result.input_refreshes,
                    preparation=preparation,
                ),
            )
            phase_result = phase_result.merged(source_result)

        return phase_result

    phase_result = await run_event_pipeline(queue, produce=produce, consume=consume)

    async def persist() -> tuple[Path, ...]:
        paths = (
            update_persistence.persist_materialized_updates(
                do_sources=plan.resolved.do_sources,
                source_names=plan.resolved.source_names,
                native_only=plan.resolved.native_only or preparation is not None,
                sources=plan.sources,
                source_updates=phase_result.source_updates,
                artifact_updates=phase_result.artifact_updates,
                details=phase_result.details,
                pins_only=preparation is not None and preparation.pins_only,
            )
            or ()
        )
        return tuple(
            path.relative_to(get_repo_root())
            if durable.current_run() is not None and path.is_absolute()
            else path
            for path in paths
        )

    written_paths = await durable.phase("materialized", persist)

    summary = UpdateSummary()
    summary.accumulate(phase_result.details)
    candidate_updates = tuple(summary.updated)

    return _RunExecutionResult(
        summary=summary,
        candidate_updates=candidate_updates,
        had_errors=phase_result.errors > 0,
        written_paths=tuple(written_paths or ()),
    )


def _workspace_relative_paths(
    root: Path,
    paths: Iterable[Path],
) -> tuple[Path, ...]:
    """Normalize absolute update outputs to paths owned by one workspace."""
    root = root.resolve()
    relative_paths: list[Path] = []
    for raw_path in paths:
        path = (raw_path if raw_path.is_absolute() else root / raw_path).resolve()
        try:
            relative_paths.append(path.relative_to(root))
        except ValueError as error:
            msg = f"Update output escapes isolated workspace: {path}"
            raise update_persistence.UpdateWorkspaceError(msg) from error
    return tuple(relative_paths)


def _workspace_allowed_paths(
    root: Path,
    declared_paths: Iterable[Path],
    written_paths: Iterable[Path],
    explicit_phase_outputs: Iterable[Path],
) -> tuple[Path, ...]:
    """Return exact write authority within the predeclared upper bound."""
    declared = _workspace_relative_paths(root, declared_paths)
    authorized = _workspace_relative_paths(
        root,
        (*written_paths, *explicit_phase_outputs),
    )
    declared_set = set(declared)
    if unexpected := tuple(path for path in authorized if path not in declared_set):
        raise update_persistence.UpdateWorkspaceUnexpectedPathsError(unexpected)
    return tuple(sorted(set(authorized)))


def _sources_refresh_flake_lock(
    source_names: Iterable[str],
    updaters: Mapping[str, UpdaterClass],
) -> bool:
    """Return whether selected source tasks invoke any input refresh."""
    return any(
        update_planner.source_backing_input_name(name, updaters.get(name))
        or update_planner.source_additional_input_names(updaters.get(name))
        for name in source_names
    )


def _requires_root_closure_validation(changed_paths: Iterable[Path]) -> bool:
    """Return whether this transaction can affect configured root closures.

    Root closures gate changes to the checkout; a candidate identical to the
    baseline cannot change what the roots build, so it skips the gate.
    """
    return bool(tuple(changed_paths))


def _emit_run_outcome(
    outcome: _RunOutcome,
    *,
    out: OutputOptions,
    dry_run: bool,
) -> int:
    """Emit exactly one final result after isolated workspace teardown."""
    plan_error = outcome.plan_error
    workspace_error = outcome.workspace_error
    discarded_updates = (
        outcome.candidate_updates
        if (
            not dry_run
            and outcome.had_errors
            and not outcome.promoted
            and outcome.promotion_state
            is not update_persistence.UpdatePromotionState.UNKNOWN
        )
        else ()
    )
    indeterminate_updates = (
        outcome.candidate_updates
        if (
            not dry_run
            and outcome.promotion_state
            is update_persistence.UpdatePromotionState.UNKNOWN
        )
        else ()
    )
    candidate_state_known = not dry_run and outcome.promotion_state is not None
    runtime = active_runtime()
    timings = outcome.timings
    if out.timings and runtime is not None:
        timings = runtime.report()
    if out.json_output:
        payload = dict[str, object](outcome.summary.to_dict())
        if timings is not None:
            payload["timings"] = timings
        payload["success"] = not outcome.had_errors
        payload["withheld"] = {
            redact_urls(name): redact_urls(reason)
            for name, reason in outcome.dropped.items()
        }
        if outcome.run_dir is not None:
            payload["runLog"] = str(outcome.run_dir)
        if discarded_updates:
            payload["candidateUpdatesDiscarded"] = [
                redact_urls(name) for name in discarded_updates
            ]
            payload["updated"] = []
        elif indeterminate_updates:
            payload["candidateUpdatesIndeterminate"] = [
                redact_urls(name) for name in indeterminate_updates
            ]
            payload["updated"] = []
        if candidate_state_known:
            payload["candidatePromotionState"] = outcome.promotion_state.value
        if plan_error is not None:
            payload.update({
                "unknownTargets": [
                    redact_urls(name) for name in plan_error.unknown_targets
                ],
                "availableTargets": [
                    redact_urls(name) for name in plan_error.available_targets
                ],
            })
            error_key = "planError" if workspace_error is not None else "error"
            payload[error_key] = redact_urls(plan_error.message)
        if outcome.validation_error is not None:
            payload["validationIncomplete"] = redact_urls(outcome.validation_error)
        if workspace_error is not None:
            payload["error"] = redact_urls(workspace_error)
        sys.stdout.write(f"{json.dumps(payload)}\n")
        return 1 if outcome.had_errors else 0

    if timings is not None:
        out.print(
            "\nOperation timings (active seconds / waiting seconds):", style="dim"
        )
        for row in timings:
            out.print(
                f"{row['source']} {row['operation']}: "
                f"{row['elapsed_seconds']:.3f} / {row['wait_seconds']:.3f} "
                f"({row['count']} operations, {row['cache_hits']} cache hits)"
            )
    if plan_error is not None:
        out.print_error(f"Error: {plan_error.message}")
        out.print_error(
            f"Available: {', '.join(plan_error.available_targets)}",
        )
    if outcome.validation_error is not None:
        out.print_error(outcome.validation_error)
    if workspace_error is not None:
        out.print_error(f"Error: {workspace_error}")
    if plan_error is not None:
        return 1
    exit_code = emit_summary(
        outcome.summary,
        had_errors=outcome.had_errors,
        out=out,
        dry_run=dry_run,
        discarded_updates=discarded_updates,
        indeterminate_updates=indeterminate_updates,
        dropped=outcome.dropped,
    )
    if outcome.run_dir is not None:
        out.print(f"Run log: {outcome.run_dir}", style="dim")
    return exit_code


def _validation_progress_output(
    opts: UpdateOptions,
    out: OutputOptions,
    source: str,
    monitor: RunMonitor | None = None,
) -> update_derivation_validation.ValidationProgress | None:
    """Feed validation output to the run monitor and, when verbose, the console."""
    verbose = opts.verbose and not out.quiet and not out.json_output
    if monitor is None and not verbose:
        return None

    def progress(
        message: update_derivation_validation.ValidationProgressEvent,
    ) -> None:
        if monitor is not None:
            match message:
                case ValidationCommandStarted(command=command):
                    monitor.validation_started(f"{source}: {command}")
                case ValidationCommandOutput(command=command, line=line):
                    monitor.validation_output(f"{source}: {command}", line)
                case ValidationCommandFinished(command=command, succeeded=succeeded):
                    monitor.validation_finished(
                        f"{source}: {command}", succeeded=succeeded
                    )
                case _:
                    monitor.validation_output(None, message)
        if verbose:
            match message:
                case ValidationCommandStarted(command=command):
                    text = f"$ {command}"
                case ValidationCommandOutput(line=line):
                    text = line
                case ValidationCommandFinished():
                    return
                case _:
                    text = message
            rendered = Text(f"[{source}] ")
            rendered.append_text(Text.from_ansi(redact_urls(text).replace("\r", "")))
            out.console.print(rendered, soft_wrap=True)
            out.console.file.flush()

    return progress


def _validation_display(
    plan: _RunPlan | _RunPlanError | None,
    opts: UpdateOptions,
    config: UpdateConfig,
    monitor: RunMonitor | None,
) -> ValidationDisplay | nullcontext[None]:
    """Show a live status block for validation only where the panel was live."""
    if monitor is None or not isinstance(plan, _RunPlan):
        return nullcontext()
    if not _live_ui_enabled(opts):
        return nullcontext()

    def tail() -> tuple[str, ...]:
        validations = monitor.snapshot().validations
        return () if not validations else validations[-1].tail

    return ValidationDisplay(
        status_line=monitor.status_line,
        tail=tail,
        render_interval=config.default_render_interval,
    )


def _flake_input_lock_node(
    lock_bytes: bytes | None,
    input_name: str,
) -> FlakeLockNode | None:
    """Resolve one root input's locked node from raw lock bytes."""
    if lock_bytes is None:
        return None
    lock = FlakeLock.from_dict(json.loads(lock_bytes))
    node, _follows = update_flake.resolve_root_input_node(lock, input_name)
    return node


def _target_input_names(
    name: str,
    plan: _RunPlan,
    updaters: Mapping[str, UpdaterClass],
) -> set[str]:
    """Return every flake input whose state one target's files depend on."""
    inputs: set[str] = set()
    if name in {ref.name for ref in plan.resolved.all_ref_inputs}:
        inputs.add(name)
    updater_cls = updaters.get(name)
    backing = update_planner.source_backing_input_name(
        name, updater_cls, plan.sources.entries.get(name)
    )
    if backing is not None:
        inputs.add(backing)
    inputs.update(update_planner.source_additional_input_names(updater_cls))
    return inputs


def _failed_targets_touched_flake(
    workspace: update_persistence.IsolatedUpdateWorkspace,
    plan: _RunPlan,
    failed: Iterable[str],
    updaters: Mapping[str, UpdaterClass],
) -> tuple[str, ...]:
    """Return failed targets whose flake input moved during this run.

    Such a target's files still describe the baseline input while flake.nix or
    flake.lock already describe the new one, so the two cannot be promoted apart.
    """
    baseline_refs = {ref.name: ref.ref for ref in plan.resolved.all_ref_inputs}
    current_refs: dict[str, str] | None = None
    baseline_lock = workspace.baseline_content(Path("flake.lock"))
    lock_path = workspace.root / "flake.lock"
    current_lock = lock_path.read_bytes() if lock_path.is_file() else None
    touched: list[str] = []
    for name in failed:
        for input_name in sorted(_target_input_names(name, plan, updaters)):
            if input_name in baseline_refs:
                if current_refs is None:
                    current_refs = {
                        ref.name: ref.ref for ref in get_flake_inputs_with_refs()
                    }
                if current_refs.get(input_name) != baseline_refs[input_name]:
                    touched.append(name)
                    break
            if _flake_input_lock_node(
                baseline_lock, input_name
            ) != _flake_input_lock_node(current_lock, input_name):
                touched.append(name)
                break
    return tuple(touched)


def _rollback_failed_input_locks(
    workspace: update_persistence.IsolatedUpdateWorkspace,
    plan: _RunPlan,
    failed: Iterable[str],
    updaters: Mapping[str, UpdaterClass],
) -> update_lock_policy.LockRollback | update_lock_policy.RollbackUnavailable:
    """Restore failed targets' lock-only input nodes in flake.lock.

    Ref-backed inputs are pinned by a URL in flake.nix, which cannot be rolled
    back per-input, so those stay on the whole-file revert path. The graph
    policy lives in :mod:`lib.update.lock_policy`; this function owns only the
    reads and the write.
    """
    ref_names = {ref.name for ref in plan.resolved.all_ref_inputs}
    candidates: set[str] = set()
    for name in failed:
        candidates.update(_target_input_names(name, plan, updaters))
    candidates -= ref_names
    if not candidates:
        return update_lock_policy.RollbackUnavailable(
            "failed targets have no lock-only inputs"
        )
    baseline_lock = workspace.baseline_content(Path("flake.lock"))
    if baseline_lock is None:
        return update_lock_policy.RollbackUnavailable("no baseline flake.lock")
    lock_path = workspace.root / "flake.lock"
    try:
        baseline = FlakeLock.model_validate_json(baseline_lock)
        current = FlakeLock.model_validate_json(lock_path.read_bytes())
    except (OSError, ValidationError) as exc:
        return update_lock_policy.RollbackUnavailable(
            f"flake.lock is unreadable: {type(exc).__name__}"
        )
    result = update_lock_policy.rollback_input_locks(
        baseline, current, frozenset(candidates)
    )
    if isinstance(result, update_lock_policy.LockRollback):
        payload = result.lock.model_dump(mode="json", by_alias=True, exclude_unset=True)
        # Nix writes flake.lock as sorted, two-space-indented JSON.
        lock_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return result


def _input_backed_targets(
    plan: _RunPlan,
    updaters: Mapping[str, UpdaterClass],
) -> frozenset[str]:
    """Return every ref target and every source that hashes against an input."""
    return frozenset(
        name for name in plan.order if _target_input_names(name, plan, updaters)
    )


def _withhold_failed_clusters(
    workspace: update_persistence.IsolatedUpdateWorkspace,
    plan: _RunPlan,
    outcome: _RunOutcome,
    updaters: Mapping[str, UpdaterClass],
    out: OutputOptions,
    monitor: RunMonitor | None,
) -> None:
    """Revert candidates coupled to failed targets so the rest can be promoted.

    Coupling follows companion, aggregate, and flake-input edges. When a failure
    left flake.lock changed, the failed targets' lock-only input nodes are
    rolled back per-input so unrelated input-backed candidates survive. Ref
    inputs pinned in flake.nix cannot be rolled back per-input; a failure on
    one of those still reverts every input-backed candidate together with the
    flake files.
    """
    statuses = outcome.summary.statuses
    failed = [name for name, status in statuses.items() if status == "error"]
    if not failed:
        return
    clusters = update_planner.dependency_clusters(
        plan.order,
        updaters=updaters,
        entries=plan.sources.entries,
        input_names=[ref.name for ref in plan.resolved.ref_inputs],
    )
    closure = set(update_planner.failure_closure(failed, clusters))
    touched = _failed_targets_touched_flake(workspace, plan, failed, updaters)
    rolled_back_inputs: frozenset[str] = frozenset()
    global_flake_revert = False
    rollback_unavailable: str | None = None
    if touched:
        rollback = _rollback_failed_input_locks(workspace, plan, failed, updaters)
        if isinstance(rollback, update_lock_policy.LockRollback):
            rolled_back_inputs = frozenset(rollback.inputs)
        else:
            rollback_unavailable = rollback.reason
        global_flake_revert = any(
            bool(_target_input_names(name, plan, updaters) - rolled_back_inputs)
            for name in touched
        )
        if global_flake_revert:
            closure.update(_input_backed_targets(plan, updaters))
    dropped = update_planner.withheld_targets(
        plan.order,
        statuses=statuses,
        failed=failed,
        clusters=clusters,
        closure=frozenset(closure),
        target_inputs={
            name: frozenset(_target_input_names(name, plan, updaters))
            for name in plan.order
        }
        if rolled_back_inputs
        else {},
        rolled_back_inputs=rolled_back_inputs,
        flake_culprit=touched[0] if touched else None,
    )
    outcome.summary.accumulate(dict.fromkeys(dropped, "dropped"))
    outcome.dropped.update(dropped)

    # Only files owned by a target that is still being promoted may change.
    # Everything else written during the run, including the declared paths of
    # failed and withheld sources, returns to baseline.
    source_names = set(plan.resolved.source_names)
    kept_sources = [
        name for name in plan.resolved.source_names if statuses.get(name) == "updated"
    ]
    owned: set[Path] = set()
    if kept_sources:
        owned.update(
            _workspace_relative_paths(
                workspace.root,
                update_persistence.planned_update_paths(kept_sources, updaters),
            )
        )
    if not global_flake_revert:
        owned.update(_FLAKE_FILES)
    restore: set[Path] = {
        path
        for path in _workspace_relative_paths(workspace.root, outcome.written_paths)
        if path not in owned
    }
    revert_sources = [name for name in (*dropped, *failed) if name in source_names]
    if revert_sources:
        restore.update(
            _workspace_relative_paths(
                workspace.root,
                update_persistence.planned_update_paths(revert_sources, updaters),
            )
        )
    if touched and global_flake_revert:
        restore.update(_FLAKE_FILES)
    if restore:
        workspace.restore_baseline(sorted(restore))
        update_flake.invalidate_flake_lock()
    if dropped:
        rendered = ", ".join(f"{name} ({reason})" for name, reason in dropped.items())
        out.print(f"Continuing without: {rendered}", style="yellow")
    if monitor is not None:
        monitor.note(
            "withheld coupled candidates",
            failed=failed,
            withheld=sorted(dropped),
            flakeReverted=bool(touched),
        )
        if rollback_unavailable is not None:
            monitor.note(
                f"rollback unavailable: {rollback_unavailable}",
                failed=failed,
            )


def _update_cancellation_check() -> (
    update_derivation_validation.ValidationCancellationCheck
):
    """Capture the async owner before entering synchronous validation."""
    owning_task = asyncio.current_task()

    def check_cancelled() -> None:
        if owning_task is not None and owning_task.cancelling():
            raise asyncio.CancelledError

    return check_cancelled


@dataclass(frozen=True)
class _ValidationContext:
    """Everything the validation rounds need besides the workspace and plan."""

    opts: UpdateOptions
    out: OutputOptions
    config: UpdateConfig
    updaters: Mapping[str, UpdaterClass]
    check_cancelled: update_derivation_validation.ValidationCancellationCheck
    monitor: RunMonitor | None = None


def _validate_round(
    plan: _RunPlan | _RunPlanError | None,
    snapshot: update_persistence.UpdateValidationSnapshot,
    outcome: _RunOutcome,
    context: _ValidationContext,
    *,
    round_index: int,
) -> tuple[bool, bool]:
    """Run one derivation round and, when clean, the root closure gate.

    Returns ``(derivations_failed, roots_failed)``.
    """
    opts, out, config = context.opts, context.out, context.config
    verbose = opts.verbose and not out.quiet and not out.json_output
    if isinstance(plan, _RunPlan) or opts.validate_all_packages:
        kept = None
        if not opts.validate_all_packages and isinstance(plan, _RunPlan):
            kept = [
                name
                for name in plan.order
                if outcome.summary.statuses.get(name) not in {"error", "dropped"}
            ]
        suffix = f" (round {round_index + 1})" if round_index else ""
        out.print(f"\nPhase 3: derivation validation{suffix}", style="dim")
        if context.monitor is not None:
            context.monitor.begin_phase("derivation validation", 3)
        failures = update_derivation_validation.validate_derivations(
            kept,
            updaters=context.updaters,
            flake_root=snapshot.root,
            timeout=config.default_subprocess_timeout,
            all_declared_systems=True,
            progress=_validation_progress_output(
                opts, out, "derivations", context.monitor
            ),
            check_cancelled=context.check_cancelled,
            print_build_logs=verbose,
            max_eval_workers=config.max_nix_evaluations,
            max_build_workers=config.max_nix_builds,
        )
        if _record_derivation_validation_failures(outcome.summary, out, failures):
            return True, False
    if not opts.validate_all_packages and not _requires_root_closure_validation(
        snapshot.changed_paths
    ):
        return False, False
    out.print("\nPhase 4: root closure builds", style="dim")
    if context.monitor is not None:
        context.monitor.begin_phase("root closures", 4)
    root_failures = update_derivation_validation.validate_root_closures(
        flake_root=snapshot.root,
        timeout=config.subprocess_timeout_override,
        progress=_validation_progress_output(
            opts, out, "root-closures", context.monitor
        ),
        check_cancelled=context.check_cancelled,
        print_build_logs=verbose,
    )
    return False, _record_derivation_validation_failures(
        outcome.summary, out, root_failures
    )


async def _validate_and_gate(
    workspace: update_persistence.IsolatedUpdateWorkspace,
    plan: _RunPlan | _RunPlanError | None,
    outcome: _RunOutcome,
    context: _ValidationContext,
) -> bool:
    """Validate the candidate, withholding coupled failures, then gate on roots.

    Each round validates the derivations still in the candidate. Failures are
    withheld with their coupled targets and the smaller candidate is validated
    again, bounded by a few rounds. Root closures build once, on the candidate
    that will actually be promoted. Returns ``True`` when nothing may be
    promoted.
    """
    check_cancelled = context.check_cancelled
    for round_index in range(_MAX_VALIDATION_ROUNDS):
        check_cancelled()
        with (
            _validation_display(plan, context.opts, context.config, context.monitor),
            workspace.validation_snapshot() as snapshot,
        ):

            def validate(
                index: int = round_index,
            ) -> tuple[tuple[bool, bool], UpdateSummary]:
                result = _validate_round(
                    plan, snapshot, outcome, context, round_index=index
                )
                return result, outcome.summary

            (derivations_failed, roots_failed), outcome.summary = (
                durable.checkpoint_sync(
                    f"validation:{round_index}:{snapshot.identity or 'ephemeral'}",
                    validate,
                )
            )
        await asyncio.sleep(0)
        check_cancelled()
        if not derivations_failed:
            return roots_failed
        if (
            context.opts.strict
            or context.opts.validate_all_packages
            or not isinstance(plan, _RunPlan)
        ):
            return True
        _withhold_failed_clusters(
            workspace, plan, outcome, context.updaters, context.out, context.monitor
        )
    context.out.print_error(
        "Error: derivation validation kept failing after "
        f"{_MAX_VALIDATION_ROUNDS} rounds; no changes were applied"
    )
    return True


async def _gate_candidate(
    workspace: update_persistence.IsolatedUpdateWorkspace,
    run_plan: _RunPlan | _RunPlanError | None,
    outcome: _RunOutcome,
    context: _ValidationContext,
    *,
    allowed_paths: tuple[Path, ...],
    preparation: Preparation | None = None,
) -> None:
    """Withhold coupled failures, validate what remains, then promote or check.

    Strict mode keeps the whole run as one unit: any failure discards every
    candidate. A plan error never reaches validation.
    """
    opts = context.opts
    if preparation is not None:
        # Preparation exports even failed bytes, but cannot promote. Native CI
        # jobs validate the final assembled tree in a separate command.
        preparation.capture(
            workspace,
            sources=(
                tuple(run_plan.resolved.source_names)
                if isinstance(run_plan, _RunPlan)
                else ()
            ),
            allowed_paths=tuple(dict.fromkeys((*allowed_paths, *_FLAKE_FILES))),
            succeeded=not outcome.had_errors,
        )
        return
    if outcome.plan_error is not None or (opts.strict and outcome.had_errors):
        return
    if outcome.had_errors and isinstance(run_plan, _RunPlan):
        _withhold_failed_clusters(
            workspace,
            run_plan,
            outcome,
            context.updaters,
            context.out,
            context.monitor,
        )
    blocked = await _validate_and_gate(workspace, run_plan, outcome, context)
    # Withheld failures still fail the run; promotion of the remainder does
    # not hide them from the exit status.
    outcome.had_errors = outcome.had_errors or blocked or bool(outcome.summary.errors)
    if blocked:
        return
    # Store the validated diff with DBOS's result, including checks. Re-exporting
    # a completed run must never pick up later edits from the live checkout.
    patch = (
        workspace.patch(allowed_paths)
        if not outcome.had_errors and durable.current_run() is not None
        else b""
    )
    if opts.check:
        workspace.validate_changes(allowed_paths)
    else:
        # Reconcile the filesystem on every replay until the workflow commits.
        # SQLite cannot atomically acknowledge an external multi-file write.
        outcome.promoted = bool(workspace.promote(allowed_paths))
    outcome.patch = patch


def _restore_preparation(
    workspace: update_persistence.IsolatedUpdateWorkspace,
    preparation: Preparation | None,
) -> tuple[Path, ...]:
    """Import declared outputs and retain their authority across native no-ops."""
    if preparation is None or preparation.previous is None:
        return ()
    previous = preparation.previous
    previous.apply(workspace.root)
    prior_paths = update_persistence.planned_update_paths(
        list(previous.sources), _get_updaters()
    )
    return workspace.validate_changes((
        *_workspace_relative_paths(workspace.root, prior_paths),
        *_FLAKE_FILES,
    ))


def _declared_phase_outputs(
    plan: _RunPlan,
    updaters: Mapping[str, UpdaterClass],
    root: Path,
) -> tuple[list[Path], list[Path]]:
    """Declare source ownership and the flake writes this plan can perform."""
    declared = list(
        update_persistence.planned_update_paths(plan.resolved.source_names, updaters)
    )
    phase_outputs: list[Path] = []
    updates_refs = bool(plan.resolved.do_refs and plan.resolved.ref_inputs)
    refreshes_inputs = bool(
        plan.resolved.do_sources
        and plan.resolved.source_names
        and plan.resolved.do_input_refresh
        and _sources_refresh_flake_lock(plan.resolved.source_names, updaters)
    )
    if updates_refs:
        phase_outputs.append(root / "flake.nix")
    if updates_refs or refreshes_inputs:
        phase_outputs.append(root / "flake.lock")
    return [*declared, *phase_outputs], phase_outputs


async def _run_updates(
    opts: UpdateOptions,
    *,
    check_tools: bool = False,
    preparation: Preparation | None = None,
) -> _RunOutcome | int:
    """Core update workflow — accepts typed UpdateOptions, returns exit code."""
    out = OutputOptions(json_output=opts.json, quiet=opts.quiet, timings=opts.timings)
    config = _resolve_runtime_config(opts)
    check_cancelled = _update_cancellation_check()

    preflight_result = _handle_preflight_requests(opts, out, config)
    if preflight_result is not None:
        return preflight_result

    outcome = _RunOutcome()
    monitor: RunMonitor | None = None
    updaters: Mapping[str, UpdaterClass] = {}
    try:
        with durable.workspace(get_repo_root()) as workspace:
            restored_paths = _restore_preparation(workspace, preparation)
            update_runtime_source.revalidate_runtime_source_snapshot(workspace.root)
            if (
                check_tools
                and (tool_check := _handle_required_tool_check(opts)) is not None
            ):
                return tool_check
            if opts.validate_all_packages:
                updaters = _get_updaters()
            update_flake.invalidate_flake_lock()
            allowed_paths: tuple[Path, ...] = ()
            run_plan = durable.checkpoint_sync("plan", lambda: _build_run_plan(opts))
            if isinstance(run_plan, _RunPlanError):
                outcome.plan_error = run_plan
                outcome.summary.accumulate(
                    dict.fromkeys(run_plan.unknown_targets, "error")
                )
                outcome.had_errors = True
            elif run_plan is not None:
                updaters = _get_updaters()
                monitor = _start_run_monitor(run_plan, opts, out, config)
                outcome.run_dir = monitor.run_dir
                declared_paths, explicit_phase_outputs = _declared_phase_outputs(
                    run_plan, updaters, workspace.root
                )
                result = await _execute_run_plan_result(
                    opts, out, config, run_plan, monitor, preparation=preparation
                )
                outcome.summary = result.summary
                outcome.candidate_updates = result.candidate_updates
                outcome.had_errors = result.had_errors
                outcome.written_paths = tuple(result.written_paths)
                allowed_paths = _workspace_allowed_paths(
                    workspace.root,
                    declared_paths,
                    result.written_paths,
                    explicit_phase_outputs,
                )
            await _gate_candidate(
                workspace,
                run_plan,
                outcome,
                _ValidationContext(
                    opts=opts,
                    out=out,
                    config=config,
                    updaters=updaters,
                    check_cancelled=check_cancelled,
                    monitor=monitor,
                ),
                allowed_paths=(*restored_paths, *allowed_paths),
                preparation=preparation,
            )
    except update_derivation_validation.ValidationIncompleteError as error:
        outcome.had_errors = True
        outcome.validation_error = redact_urls(str(error))
        outcome.summary.accumulate({"validation": "error"})
        if monitor is not None:
            monitor.validation_output(None, outcome.validation_error)
    except update_persistence.UpdateWorkspaceError as error:
        _record_workspace_failure(outcome, error)
    finally:
        update_flake.invalidate_flake_lock()
        if monitor is not None:
            monitor.close(
                summary={
                    **outcome.summary.to_dict(),
                    "promoted": outcome.promoted,
                    "withheld": dict(outcome.dropped),
                    **(
                        {"validationIncomplete": outcome.validation_error}
                        if outcome.validation_error is not None
                        else {}
                    ),
                }
            )

    return outcome


async def collect_run_outcome(
    opts: UpdateOptions,
    *,
    check_tools: bool = False,
    preparation: Preparation | None = None,
) -> _RunOutcome | int:
    """Own shared resources and return the serializable domain result."""
    async with runtime_scope(_resolve_runtime_config(opts)) as runtime:
        result = await _run_updates(
            opts, check_tools=check_tools, preparation=preparation
        )
        if isinstance(result, _RunOutcome) and opts.timings:
            result.timings = runtime.report()
        return result


@DBOS.workflow(name="nixcfg.update")
async def _durable_update() -> _RunOutcome | int:
    """Execute the admitted SQLite request under DBOS recovery supervision."""

    async def collect() -> _RunOutcome | int:
        run = durable.current_run()
        assert run is not None  # noqa: S101 -- supervise admits an active session
        return await collect_run_outcome(run.options, check_tools=True)

    return await durable.supervise(collect)


def emit_run_result(result: _RunOutcome | int, opts: UpdateOptions) -> int:
    """Render original and recovered results after workspace teardown."""
    if isinstance(result, int):
        return result
    if opts.patch is not None:
        Path(opts.patch).write_bytes(result.patch)
    return _emit_run_outcome(
        result,
        out=OutputOptions(
            json_output=opts.json, quiet=opts.quiet, timings=opts.timings
        ),
        dry_run=opts.check,
    )


async def run_updates(opts: UpdateOptions, *, check_tools: bool = False) -> int:
    """Run a library-owned update without starting a DBOS service."""
    return emit_run_result(
        await collect_run_outcome(opts, check_tools=check_tools), opts
    )


def run_update_command(
    options: UpdateOptions | None = None,
    /,
    **overrides: Unpack[UpdateOptionsKwargs],
) -> int:
    """Run the update workflow from one options object or keyword overrides."""
    reexec_result = update_runtime_source.maybe_reexec_checkout_update()
    if reexec_result is not None:
        return reexec_result

    if options is not None and overrides:
        msg = "run_update_command accepts either UpdateOptions or keyword overrides"
        raise TypeError(msg)

    override_values: UpdateOptionsKwargs = {**overrides}
    opts = options if options is not None else _build_update_options(override_values)
    if not isinstance(opts, UpdateOptions):
        msg = f"Expected UpdateOptions, got {type(opts)!r}"
        raise TypeError(msg)

    config = _resolve_runtime_config(opts)
    out = OutputOptions(json_output=opts.json, quiet=opts.quiet, timings=opts.timings)
    if (
        opts.resume is None
        and (result := _handle_preflight_requests(opts, out, config)) is not None
    ):
        return result
    try:
        if opts.repair is not None:
            from lib.update.repair import (  # noqa: PLC0415 -- optional supervisor avoids import cycle
                run_repairing_update,
            )

            return run_repairing_update(opts, get_repo_root())
        return asyncio.run(durable.execute(opts, get_repo_root(), config))
    except (
        durable.ResumeError,
        update_persistence.UpdateWorkspaceError,
        OSError,
    ) as error:
        if opts.json:
            sys.stdout.write(json.dumps({"success": False, "error": str(error)}) + "\n")
        else:
            out.print_error(f"Error: {error}")
        return 1


app = typer.Typer(
    help="Update source versions/hashes and flake input refs.",
    add_completion=False,
    no_args_is_help=False,
    context_settings=HELP_CONTEXT_SETTINGS,
)


@app.callback(invoke_without_command=True)
def cli(  # noqa: PLR0913 -- Typer requires an explicit parameter for each public option
    targets: Annotated[
        list[str] | None,
        typer.Argument(help="Sources or flake inputs to update (default: all)."),
    ] = None,
    *,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            "-c",
            help="Validate a prospective update in isolation without applying.",
        ),
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
            help="Only compute hashes for the current platform. Implies --no-refs.",
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
    status: Annotated[
        bool,
        typer.Option(
            "--status",
            help=(
                "Show the recorded state of the latest run, or of the run id "
                "given as the first target, and exit."
            ),
        ),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option(
            "--resume", help="Resume an interrupted run using its original inputs."
        ),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            help="Start or resume a named run; repeated invocations must use the same inputs.",
        ),
    ] = None,
    patch: Annotated[
        str | None,
        typer.Option(
            "--patch", help="Export the successful run's validated Git patch."
        ),
    ] = None,
    repair: Annotated[
        RepairAgent | None,
        typer.Option(
            "--repair",
            help="Allow one isolated agent repair and a fresh validated attempt.",
        ),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help=(
                "Treat the run as one unit: any failed target discards every "
                "candidate instead of promoting the unaffected ones."
            ),
        ),
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
            help=(
                "Per-subprocess timeout in seconds, including root validation. "
                "Defaults to 40 minutes for package commands and 6 hours for roots."
            ),
        ),
    ] = None,
    timings: Annotated[
        bool,
        typer.Option(
            "--timings",
            help="Report per-operation active time, wait time, and cache reuse.",
        ),
    ] = False,
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
    values = dict(locals())
    try:
        normalized_targets, trailing_options = _split_trailing_target_options(targets)
        values["targets"] = normalized_targets
        values.update(trailing_options)
        exit_code = run_update_command(**cast("UpdateOptionsKwargs", values))
    except (
        click.ClickException,
        click.exceptions.Exit,
        click.Abort,
        typer_click.ClickException,
        typer.Exit,
        typer.Abort,
    ):
        raise
    except Exception as error:  # noqa: BLE001 -- CLI errors must cross the redaction boundary
        message = format_exception(error, include_traceback=bool(values["verbose"]))
        if values["json_output"]:
            sys.stdout.write(f"{json.dumps({'success': False, 'error': message})}\n")
        else:
            OutputOptions().print_error(message)
        exit_code = 1
    raise typer.Exit(code=exit_code)
