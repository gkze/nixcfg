"""CLI entry point for update workflows."""

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from rich.columns import Columns
from rich.console import Console

from libnix.models.sources import SourceEntry, SourcesFile
from update.config import (
    UpdateConfig,
    _env_bool,
    _resolve_active_config,
    _resolve_config,
    default_max_nix_builds,
)
from update.constants import ALL_TOOLS, NIX_BUILD_FAILURE_TAIL_LINES, REQUIRED_TOOLS
from update.events import UpdateEvent
from update.flake import update_flake_input
from update.paths import SOURCES_FILE
from update.process import _run_queue_task
from update.refs import FlakeInputRef, _update_refs_task, get_flake_inputs_with_refs
from update.ui import ItemMeta, OperationKind, SummaryStatus, consume_events
from update.updaters import UPDATERS
from update.updaters.base import DenoDepsHashUpdater


def _check_required_tools(
    *,
    include_flake_edit: bool = False,
    source: str | None = None,
) -> list[str]:
    if source:
        if source in UPDATERS:
            updater_cls = UPDATERS[source]
            tools = list(getattr(updater_cls, "required_tools", REQUIRED_TOOLS))
        else:
            # ref-only source — only needs nix (and possibly flake-edit)
            tools = list(REQUIRED_TOOLS)
    else:
        tools = list(ALL_TOOLS)
    if include_flake_edit:
        tools.append("flake-edit")
    return [tool for tool in tools if shutil.which(tool) is None]


def _resolve_full_output(*, full_output: bool | None = None) -> bool:
    if full_output is not None:
        return full_output
    return _env_bool("UPDATE_LOG_FULL", default=False)


def _is_tty(
    *,
    force_tty: bool | None = None,
    no_tty: bool | None = None,
    zellij_guard: bool | None = None,
) -> bool:
    if force_tty is None:
        force_tty = _env_bool("UPDATE_FORCE_TTY", default=False)
    if no_tty is None:
        no_tty = _env_bool("UPDATE_NO_TTY", default=False)
    if zellij_guard is None:
        zellij_guard = _env_bool("UPDATE_ZELLIJ_GUARD", default=False)
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
    _console: Any = field(default=None, repr=False)
    _err_console: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Initialize stdout/stderr rich consoles."""
        self._console = Console()
        self._err_console = Console(stderr=True)

    def print(
        self,
        message: str,
        *,
        style: str | None = None,
        stderr: bool = False,
    ) -> None:
        """Print a message unless quiet or json mode is enabled."""
        if not self.quiet and not self.json_output:
            console = self._err_console if stderr else self._console
            if console is not None:
                console.print(message, style=style)

    def print_error(self, message: str) -> None:
        """Print an error message to stderr when not in json mode."""
        if not self.json_output and self._err_console is not None:
            self._err_console.print(message, style="red")


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

    def to_dict(self) -> dict[str, Any]:
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
    def from_args(cls, args: argparse.Namespace) -> ResolvedTargets:
        """Resolve target sets and operational flags from CLI args."""
        all_source_names = set(UPDATERS.keys())
        all_ref_inputs = get_flake_inputs_with_refs()
        all_ref_names = {i.name for i in all_ref_inputs}
        all_known_names = all_source_names | all_ref_names

        # --native-only implies --no-refs: in CI, refs are managed by the
        # pipeline (nix flake update + create-pr).  If compute-hashes workers
        # update refs locally the changes are not propagated to the merged
        # artifact, causing flake.lock / sources.json version desync.
        do_refs = not args.no_refs and not args.native_only
        do_sources = not args.no_sources
        if args.source:
            if args.source not in all_ref_names:
                do_refs = False
            if args.source not in all_source_names:
                do_sources = False

        ref_inputs = (
            [i for i in all_ref_inputs if i.name == args.source]
            if args.source
            else all_ref_inputs
        )
        source_names = (
            [args.source]
            if args.source in all_source_names
            else []
            if args.source
            else list(UPDATERS.keys())
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
            do_input_refresh=not args.no_input,
            dry_run=args.check,
            native_only=args.native_only,
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
    sources_with_input: set[str] = set()
    if sources is not None:
        sources_with_input = {
            name for name, entry in sources.entries.items() if entry.input
        }
    sources_with_input &= source_names

    both = flake_names & sources_with_input
    flake_only = flake_names - both
    sources_only = source_names - both

    item_meta: dict[str, ItemMeta] = {}
    for name in both:
        item_meta[name] = ItemMeta(
            name=name,
            origin=_ORIGIN_BOTH,
            op_order=(
                OperationKind.CHECK_VERSION,
                OperationKind.UPDATE_REF,
                OperationKind.REFRESH_LOCK,
                OperationKind.COMPUTE_HASH,
            ),
        )
    for name in flake_only:
        item_meta[name] = ItemMeta(
            name=name,
            origin=_ORIGIN_FLAKE_ONLY,
            op_order=(
                OperationKind.CHECK_VERSION,
                OperationKind.UPDATE_REF,
                OperationKind.REFRESH_LOCK,
            ),
        )
    for name in sources_only:
        if name in sources_with_input:
            op_order = (
                OperationKind.CHECK_VERSION,
                OperationKind.REFRESH_LOCK,
                OperationKind.COMPUTE_HASH,
            )
        else:
            op_order = (
                OperationKind.CHECK_VERSION,
                OperationKind.COMPUTE_HASH,
            )
        item_meta[name] = ItemMeta(
            name=name,
            origin=_ORIGIN_SOURCES_ONLY,
            op_order=op_order,
        )

    order = sorted(item_meta, key=lambda name: f"{item_meta[name].origin} {name}")
    return item_meta, order


def _emit_summary(
    args: argparse.Namespace,
    summary: UpdateSummary,
    *,
    had_errors: bool,
    out: OutputOptions,
    dry_run: bool,
) -> int:
    if args.json:
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


async def _update_source_task(  # noqa: PLR0913
    name: str,
    sources: SourcesFile,
    *,
    update_input: bool,
    native_only: bool,
    session: aiohttp.ClientSession,
    update_input_lock: asyncio.Lock,
    queue: asyncio.Queue[UpdateEvent | None],
    config: UpdateConfig | None = None,
) -> None:
    async def _run() -> None:
        resolved_config = _resolve_active_config(config)
        current = sources.entries.get(name)
        updater = UPDATERS[name](config=resolved_config)
        if isinstance(updater, DenoDepsHashUpdater):
            updater.native_only = native_only
        input_name = getattr(updater, "input_name", None)
        put = queue.put

        await put(UpdateEvent.status(name, "Starting update"))
        if update_input and input_name:
            await put(
                UpdateEvent.status(name, f"Updating flake input '{input_name}'..."),
            )
            async with update_input_lock:
                async for event in update_flake_input(input_name, source=name):
                    await put(event)

        async for event in updater.update_stream(current, session):
            await put(event)

    await _run_queue_task(source=name, queue=queue, task=_run)


async def _run_updates(args: argparse.Namespace) -> int:  # noqa: C901, PLR0911, PLR0912, PLR0915
    out = OutputOptions(json_output=args.json, quiet=args.quiet)
    config = _resolve_config(args)

    if args.schema:
        return 0

    if args.list:
        if args.json:
            sorted(UPDATERS.keys())
            ref_inputs = [i.name for i in get_flake_inputs_with_refs()]
            return 0

        console = Console()
        console.print("[bold]Available sources (sources.json):[/bold]")
        console.print(Columns(sorted(UPDATERS.keys()), padding=(0, 2)))
        console.print()
        ref_inputs = get_flake_inputs_with_refs()
        if ref_inputs:
            console.print("[bold]Flake inputs with version refs:[/bold]")
            for inp in ref_inputs:
                console.print(f"  {inp.name}: {inp.owner}/{inp.repo} @ {inp.ref}")
        return 0

    if args.validate:
        try:
            sources = SourcesFile.load(SOURCES_FILE)
            if args.json:
                pass
            else:
                out.print(
                    f":heavy_check_mark: Validated {SOURCES_FILE}: "
                    f"{len(sources.entries)} sources OK",
                    style="green",
                )
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            if args.json:
                pass
            else:
                out.print_error(f":x: Validation failed: {exc}")
            return 1
        else:
            return 0

    resolved = ResolvedTargets.from_args(args)
    tty_mode = getattr(args, "tty", "auto")
    tty_enabled = _is_tty(
        force_tty=True if tty_mode in ("force", "full") else None,
        no_tty=True if tty_mode == "off" else None,
        zellij_guard=args.zellij_guard,
    )
    show_phase_headers = (
        not args.json
        and not args.quiet
        and not tty_enabled
        and resolved.do_refs
        and resolved.do_sources
        and resolved.ref_inputs
        and resolved.source_names
    )

    if args.source and args.source not in resolved.all_known_names:
        out.print_error(f"Error: Unknown source or input '{args.source}'")
        out.print_error(f"Available: {', '.join(sorted(resolved.all_known_names))}")
        return 1

    summary = UpdateSummary()
    had_errors = False

    if not resolved.ref_inputs and not resolved.source_names:
        return _emit_summary(
            args,
            summary,
            had_errors=False,
            out=out,
            dry_run=resolved.dry_run,
        )

    sources = (
        SourcesFile.load(SOURCES_FILE)
        if resolved.do_sources and resolved.source_names
        else SourcesFile(entries={})
    )
    item_meta, order = _build_item_meta(
        resolved,
        sources if resolved.do_sources else None,
    )

    if not order:
        return _emit_summary(
            args,
            summary,
            had_errors=False,
            out=out,
            dry_run=resolved.dry_run,
        )

    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    max_lines = config.default_log_tail_lines
    is_tty = tty_enabled and not args.quiet and not args.json
    full_output = _resolve_full_output(
        full_output=True if tty_mode == "full" else None,
    )
    consumer = asyncio.create_task(
        consume_events(
            queue,
            order,
            sources,
            item_meta=item_meta,
            max_lines=max_lines,
            is_tty=is_tty,
            full_output=full_output,
            verbose=getattr(args, "verbose", False),
            render_interval=config.default_render_interval,
            build_failure_tail_lines=NIX_BUILD_FAILURE_TAIL_LINES,
            quiet=args.quiet or args.json,
        ),
    )

    if resolved.do_refs and resolved.ref_inputs:
        if show_phase_headers:
            out.print("\nPhase 1: flake input refs", style="dim")
        async with aiohttp.ClientSession() as session:
            flake_edit_lock = asyncio.Lock()
            tasks = [
                asyncio.create_task(
                    _update_refs_task(
                        inp,
                        session,
                        queue,
                        dry_run=resolved.dry_run,
                        flake_edit_lock=flake_edit_lock,
                        config=config,
                    ),
                )
                for inp in resolved.ref_inputs
            ]
            await asyncio.gather(*tasks)

    if resolved.do_sources and resolved.source_names:
        if show_phase_headers:
            out.print("\nPhase 2: sources.json updates", style="dim")
        async with aiohttp.ClientSession() as session:
            update_input_lock = asyncio.Lock()
            tasks = [
                asyncio.create_task(
                    _update_source_task(
                        name,
                        sources,
                        update_input=resolved.do_input_refresh,
                        native_only=resolved.native_only,
                        session=session,
                        update_input_lock=update_input_lock,
                        queue=queue,
                        config=config,
                    ),
                )
                for name in resolved.source_names
            ]
            await asyncio.gather(*tasks)

    await queue.put(None)
    _updated, error_count, details, source_updates = await consumer
    summary.accumulate(details)
    had_errors = error_count > 0

    if resolved.do_sources and resolved.source_names:
        if source_updates:
            source_updates = _merge_source_updates(
                sources.entries,
                source_updates,
                native_only=resolved.native_only,
            )
            sources.entries.update(source_updates)
        if any(details.get(name) == "updated" for name in resolved.source_names):
            sources.save(SOURCES_FILE)

    return _emit_summary(
        args,
        summary,
        had_errors=had_errors,
        out=out,
        dry_run=resolved.dry_run,
    )


def main() -> None:
    """Parse arguments, validate tools, and run updates."""
    parser = argparse.ArgumentParser(
        description="Update source versions/hashes and flake input refs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available sources: {', '.join(UPDATERS.keys())}",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Source or flake input to update (default: all)",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List available sources and inputs",
    )
    parser.add_argument(
        "-R",
        "--no-refs",
        action="store_true",
        help="Skip flake input ref updates",
    )
    parser.add_argument(
        "-S",
        "--no-sources",
        action="store_true",
        help="Skip sources.json hash updates",
    )
    parser.add_argument(
        "-I",
        "--no-input",
        action="store_true",
        help="Skip flake input lock refresh before hashing",
    )
    parser.add_argument(
        "-c",
        "--check",
        action="store_true",
        help="Dry run: check for updates without applying",
    )
    parser.add_argument(
        "-v",
        "--validate",
        action="store_true",
        help="Validate sources.json and exit",
    )
    parser.add_argument(
        "-s",
        "--schema",
        action="store_true",
        help="Output JSON schema for sources.json and exit",
    )
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="Output results as JSON (for scripting/automation)",
    )
    parser.add_argument(
        "-V",
        "--verbose",
        action="store_true",
        help="Stream build log lines to stdout (useful in non-TTY mode)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress progress output, only show errors and final summary",
    )
    parser.add_argument(
        "-t",
        "--tty",
        choices=["auto", "force", "off", "full"],
        default="auto",
        help=(
            "TTY rendering: auto=detect, force=always, off=disable, "
            "full=force+verbose output "
            "(env: UPDATE_FORCE_TTY, UPDATE_NO_TTY, UPDATE_LOG_FULL)"
        ),
    )
    parser.add_argument(
        "--zellij-guard",
        dest="zellij_guard",
        action="store_true",
        default=None,
        help="Disable live rendering under Zellij (env: UPDATE_ZELLIJ_GUARD)",
    )
    parser.add_argument(
        "--no-zellij-guard",
        dest="zellij_guard",
        action="store_false",
        help="Allow live rendering under Zellij (env: UPDATE_ZELLIJ_GUARD)",
    )
    parser.add_argument(
        "--http-timeout",
        type=int,
        default=None,
        help="HTTP timeout seconds (env: UPDATE_HTTP_TIMEOUT)",
    )
    parser.add_argument(
        "--subprocess-timeout",
        type=int,
        default=None,
        help="Subprocess timeout seconds (env: UPDATE_SUBPROCESS_TIMEOUT)",
    )
    parser.add_argument(
        "--max-nix-builds",
        type=int,
        default=None,
        help=(
            "Max concurrent nix build processes "
            f"(default: {default_max_nix_builds()} ~= 70 percent of CPU cores, "
            "env: UPDATE_MAX_NIX_BUILDS)"
        ),
    )
    parser.add_argument(
        "--log-tail-lines",
        type=int,
        default=None,
        help="Log tail lines (env: UPDATE_LOG_TAIL_LINES)",
    )
    parser.add_argument(
        "--render-interval",
        type=float,
        default=None,
        help="TTY render interval seconds (env: UPDATE_RENDER_INTERVAL)",
    )
    parser.add_argument(
        "--user-agent",
        type=str,
        default=None,
        help="HTTP user agent (env: UPDATE_USER_AGENT)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=None,
        help="HTTP retries (env: UPDATE_RETRIES)",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=None,
        help="HTTP retry backoff seconds (env: UPDATE_RETRY_BACKOFF)",
    )
    parser.add_argument(
        "--fake-hash",
        type=str,
        default=None,
        help="Fake hash placeholder (env: UPDATE_FAKE_HASH)",
    )
    parser.add_argument(
        "--deno-platforms",
        type=str,
        default=None,
        help="Comma-separated Deno platforms (env: UPDATE_DENO_DEPS_PLATFORMS)",
    )
    parser.add_argument(
        "-n",
        "--native-only",
        action="store_true",
        help="Only compute platform-specific hashes for current platform"
        " (for CI). Implies --no-refs.",
    )
    args = parser.parse_args()

    if args.list or args.schema or args.validate:
        raise SystemExit(asyncio.run(_run_updates(args)))

    needs_flake_edit = not args.no_refs and not args.native_only
    if needs_flake_edit and args.source:
        ref_names = {i.name for i in get_flake_inputs_with_refs()}
        needs_flake_edit = args.source in ref_names

    missing = _check_required_tools(
        include_flake_edit=needs_flake_edit,
        source=args.source,
    )
    if missing:
        sys.stderr.write(f"Error: Required tools not found: {', '.join(missing)}\n")
        sys.stderr.write("Please install them and ensure they are in your PATH.\n")
        raise SystemExit(1)

    raise SystemExit(asyncio.run(_run_updates(args)))
