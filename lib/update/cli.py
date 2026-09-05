"""CLI entry point for update workflows."""

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Unpack, cast

import typer
from rich.console import Console

from lib.cli import HELP_CONTEXT_SETTINGS
from lib.nix.models.sources import SourcesFile
from lib.update import derivation_validation as update_derivation_validation
from lib.update import flake as update_flake
from lib.update import persistence as update_persistence
from lib.update import planner as update_planner
from lib.update import source_runner as update_source_runner
from lib.update import updaters as updater_module
from lib.update.cli_inventory import handle_list_targets_request
from lib.update.cli_options import (
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
from lib.update.paths import get_repo_root
from lib.update.refs import (
    FlakeInputRef,
    get_flake_inputs_with_refs,
)
from lib.update.sources import load_all_sources
from lib.update.ui_consumer import ConsumeEventsOptions, consume_events
from lib.update.ui_state import ItemMeta, OperationKind, SummaryStatus
from lib.update.updaters import UPDATERS, UpdaterClass, ensure_updaters_loaded

if TYPE_CHECKING:
    from collections.abc import Iterable

    from lib.update.events import UpdateEvent
    from lib.update.updaters.core import Updater

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
    "--validate": ("validate", True),
    "-v": ("validate", True),
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
    "UpdateSummary",
    "app",
    "check_required_tools",
    "cli",
    "run_update_command",
    "run_updates",
)

_REEXEC_ENV = "NIXCFG_UPDATE_REEXECED_FROM_CHECKOUT"
_EXECUTION_SOURCE_ENV = "NIXCFG_UPDATE_EXECUTION_SOURCE"


class _UpdateSourceChangedError(update_persistence.UpdateWorkspaceError):
    """The update runtime no longer matches the stable workspace snapshot."""


@dataclass(frozen=True, slots=True)
class _RuntimeSourcePolicy:
    root_paths: tuple[str, ...]
    library_extensions: tuple[str, ...]
    library_names: tuple[str, ...]
    excluded_library_paths: tuple[str, ...]
    dynamic_roots: tuple[str, ...]
    dynamic_extensions: tuple[str, ...]
    dynamic_excluded_file_suffixes: tuple[str, ...]


def _runtime_source_policy(root: Path) -> _RuntimeSourcePolicy:
    """Load the cross-language runtime source policy from project metadata."""
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    raw_value = project["tool"]["nixcfg"]["runtimeSource"]
    if not isinstance(raw_value, dict):
        msg = "Invalid nixcfg runtime source policy table"
        raise TypeError(msg)
    raw = cast("dict[str, object]", raw_value)
    schema_version = raw.get("schemaVersion")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        msg = "Unsupported nixcfg runtime source policy schema"
        raise RuntimeError(msg)

    def _strings(name: str) -> tuple[str, ...]:
        values = raw[name]
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value for value in values
        ):
            msg = f"Invalid nixcfg runtime source policy field: {name}"
            raise TypeError(msg)
        return tuple(values)

    def _paths(name: str) -> tuple[str, ...]:
        values = _strings(name)
        if any(
            Path(value).is_absolute() or ".." in Path(value).parts for value in values
        ):
            msg = f"Invalid nixcfg runtime source policy path field: {name}"
            raise TypeError(msg)
        return values

    return _RuntimeSourcePolicy(
        root_paths=_paths("rootPaths"),
        library_extensions=_strings("libraryExtensions"),
        library_names=_strings("libraryNames"),
        excluded_library_paths=_paths("excludedLibraryPaths"),
        dynamic_roots=_paths("dynamicRoots"),
        dynamic_extensions=_strings("dynamicExtensions"),
        dynamic_excluded_file_suffixes=_strings("dynamicExcludedFileSuffixes"),
    )


def _runtime_source_relpaths(
    root: Path,
    policy: _RuntimeSourcePolicy,
) -> set[Path]:
    """Return the source files admitted by the shared runtime policy."""
    runtime_paths: set[Path] = set()
    for raw_path in policy.root_paths:
        relative_root = Path(raw_path)
        source_root = root / relative_root
        if source_root.is_file() or source_root.is_symlink():
            runtime_paths.add(relative_root)
            continue
        if not source_root.is_dir():
            msg = f"Declared runtime source root does not exist: {raw_path}"
            raise RuntimeError(msg)
        runtime_paths.update(
            relative_root / path.relative_to(source_root)
            for path in source_root.rglob("*")
            if path.is_file() or path.is_symlink()
        )
    library_root = root / "lib"
    for path in library_root.rglob("*"):
        if not path.is_file() and not path.is_symlink():
            continue
        relative_path = path.relative_to(library_root)
        if any(
            relative_path.is_relative_to(excluded)
            for excluded in map(Path, policy.excluded_library_paths)
        ):
            continue
        if (
            path.suffix.removeprefix(".") in policy.library_extensions
            or path.name in policy.library_names
        ):
            runtime_paths.add(Path("lib") / relative_path)
    for raw_root in policy.dynamic_roots:
        relative_root = Path(raw_root)
        dynamic_root = root / relative_root
        if dynamic_root.is_file() or dynamic_root.is_symlink():
            dynamic_paths = (dynamic_root,)
        elif dynamic_root.is_dir():
            dynamic_paths = dynamic_root.rglob("*")
        else:
            msg = f"Declared dynamic runtime source root does not exist: {raw_root}"
            raise RuntimeError(msg)
        for path in dynamic_paths:
            if not path.is_file() and not path.is_symlink():
                continue
            if path.suffix.removeprefix(".") not in policy.dynamic_extensions:
                continue
            if any(
                path.name.endswith(suffix)
                for suffix in policy.dynamic_excluded_file_suffixes
            ):
                continue
            runtime_paths.add(
                relative_root
                if path == dynamic_root
                else relative_root / path.relative_to(dynamic_root)
            )
    return runtime_paths


def _same_source_path(left: Path, right: Path) -> bool:
    """Compare one packaged path without hiding symlink identity changes."""
    if left.is_symlink() or right.is_symlink():
        return (
            left.is_symlink()
            and right.is_symlink()
            and left.readlink() == right.readlink()
        )
    return (
        left.is_file() and right.is_file() and left.read_bytes() == right.read_bytes()
    )


def _update_library_matches_checkout(
    repo_root: Path,
    *,
    runtime_source_root: Path | None = None,
) -> bool:
    """Return whether the complete packaged runtime matches the checkout."""
    checkout_root = repo_root.expanduser().resolve()
    configured_runtime_root = os.environ.get(_EXECUTION_SOURCE_ENV)
    runtime_root = (
        Path(configured_runtime_root).expanduser().resolve()
        if runtime_source_root is None and configured_runtime_root
        else (
            Path(__file__).resolve().parents[2]
            if runtime_source_root is None
            else runtime_source_root.expanduser().resolve()
        )
    )
    try:
        if runtime_root.samefile(checkout_root):
            return True
    except OSError:
        return False

    try:
        repo_policy = _runtime_source_policy(checkout_root)
        runtime_policy = _runtime_source_policy(runtime_root)
    except KeyError, OSError, RuntimeError, TypeError, tomllib.TOMLDecodeError:
        return False
    if repo_policy != runtime_policy:
        return False

    try:
        repo_files = _runtime_source_relpaths(checkout_root, repo_policy)
        runtime_files = _runtime_source_relpaths(runtime_root, runtime_policy)
    except OSError, RuntimeError:
        return False
    try:
        return repo_files == runtime_files and all(
            _same_source_path(checkout_root / relpath, runtime_root / relpath)
            for relpath in repo_files
        )
    except OSError:
        return False


def _argv_runs_top_level_update(argv: list[str]) -> bool:
    return len(argv) > 1 and argv[1] == "update"


def _revalidate_runtime_source_snapshot(workspace_root: Path) -> None:
    """Reject a source race between runtime selection and workspace capture."""
    if not (
        _argv_runs_top_level_update(sys.argv) or os.environ.get(_EXECUTION_SOURCE_ENV)
    ):
        return
    if _update_library_matches_checkout(workspace_root):
        return
    msg = (
        "Update source changed while preparing the isolated workspace. "
        "No changes were applied; retry `nixcfg update`."
    )
    raise _UpdateSourceChangedError(msg)


def _maybe_reexec_checkout_update() -> int | None:
    """Run update commands with checkout-matching update code when needed."""
    if not _argv_runs_top_level_update(sys.argv):
        return None

    repo_root = get_repo_root()
    if _update_library_matches_checkout(repo_root):
        return None

    if os.environ.get(_REEXEC_ENV):
        sys.stderr.write(
            "Error: running nixcfg update code still differs from this checkout "
            "after re-exec. Run `nix run path:.#nixcfg -- update ...` directly.\n"
        )
        return 1

    nix = shutil.which("nix")
    if nix is None:
        sys.stderr.write(
            "Error: installed nixcfg update code differs from this checkout, "
            "but `nix` was not found for a checkout re-exec.\n"
        )
        return 1

    env = dict(os.environ)
    env[_REEXEC_ENV] = "1"
    env["REPO_ROOT"] = os.fspath(repo_root)
    with update_persistence.visible_source_snapshot(repo_root) as snapshot_root:
        result = subprocess.run(  # noqa: S603 -- fixed local Nix executable
            [
                nix,
                "run",
                f"path:{snapshot_root}#nixcfg",
                "--",
                *sys.argv[1:],
            ],
            cwd=repo_root,
            env=env,
            check=False,
        )
    return result.returncode


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


def _emit_summary(
    summary: UpdateSummary,
    *,
    had_errors: bool,
    out: OutputOptions,
    dry_run: bool,
    discarded_updates: tuple[str, ...] = (),
    indeterminate_updates: tuple[str, ...] = (),
) -> int:
    if out.json_output:
        payload = summary.to_dict()
        payload["success"] = not had_errors
        sys.stdout.write(f"{json.dumps(payload)}\n")
        return 1 if had_errors else 0

    if dry_run:
        if summary.updated:
            out.print(
                f"\nAvailable updates: {', '.join(summary.updated)}",
                style="green",
            )
        else:
            out.print("\nNo updates available.", style="dim")
    elif discarded_updates:
        out.print(
            f"\nCandidate updates discarded: {', '.join(discarded_updates)}",
            style="yellow",
        )
    elif indeterminate_updates:
        out.print(
            "\nCandidate updates have unknown promotion state: "
            f"{', '.join(indeterminate_updates)}",
            style="yellow",
        )
    elif summary.updated:
        out.print(
            f"\nUpdated: {', '.join(summary.updated)}",
            style="green",
        )
    else:
        out.print("\nNo updates needed.", style="dim")

    if summary.errors:
        out.print_error(f"\nFailed: {', '.join(summary.errors)}")

    return 1 if had_errors else 0


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


@dataclass(frozen=True)
class _RunPlan:
    resolved: ResolvedTargets
    tty_enabled: bool
    show_phase_headers: bool
    sources: SourcesFile
    item_meta: dict[str, ItemMeta]
    order: list[str]


@dataclass(frozen=True)
class _RunExecutionResult:
    """Validated phase result awaiting promotion into the live checkout."""

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


def _handle_preflight_requests(opts: UpdateOptions, out: OutputOptions) -> int | None:
    sort_validation = validate_list_sort_option(opts, out)
    if sort_validation is not None:
        return sort_validation

    schema_result = _handle_schema_request(opts)
    if schema_result is not None:
        return schema_result

    list_result = handle_list_targets_request(opts)
    if list_result is not None:
        return list_result

    return handle_validate_request(opts, out)


def _build_run_plan(opts: UpdateOptions) -> _RunPlan | _RunPlanError | None:
    resolved = ResolvedTargets.from_options(opts)
    tty_enabled, show_phase_headers = _resolve_tty_settings(opts, resolved)

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
        tty_enabled=tty_enabled,
        show_phase_headers=show_phase_headers,
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


async def _execute_run_plan_result(
    opts: UpdateOptions,
    out: OutputOptions,
    config: UpdateConfig,
    plan: _RunPlan,
) -> _RunExecutionResult:
    # Every plan runs in a disposable workspace. ``resolved.dry_run`` controls
    # reporting and live promotion, not whether the candidate is materialized.
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

    phase_result = update_source_runner.UpdatePhaseResult()
    validation_failures = ()
    try:
        if plan.resolved.do_refs and plan.resolved.ref_inputs:
            if plan.show_phase_headers:
                out.print("\nPhase 1: flake input refs", style="dim")
            ref_result = await update_source_runner.run_ref_phase(
                ref_inputs=plan.resolved.ref_inputs,
                queue=queue,
                dry_run=False,
                config=config,
            )
            phase_result = phase_result.merged(ref_result)

        if plan.resolved.do_sources and plan.resolved.source_names:
            if plan.show_phase_headers:
                out.print("\nPhase 2: sources.json updates", style="dim")
            source_result = await update_source_runner.run_sources_phase(
                update_source_runner.SourcesPhaseContext(
                    source_names=plan.resolved.source_names,
                    sources=plan.sources,
                    queue=queue,
                    update_input=plan.resolved.do_input_refresh,
                    native_only=plan.resolved.native_only,
                    config=config,
                    dry_run=False,
                ),
            )
            phase_result = phase_result.merged(source_result)

        await queue.put(None)
        await consumer

        written_paths = update_persistence.persist_materialized_updates(
            do_sources=plan.resolved.do_sources,
            source_names=plan.resolved.source_names,
            dry_run=False,
            native_only=plan.resolved.native_only,
            sources=plan.sources,
            source_updates=phase_result.source_updates,
            artifact_updates=phase_result.artifact_updates,
            details=phase_result.details,
        )

        if phase_result.errors == 0:
            completed = [
                name for name in plan.order if phase_result.details.get(name) != "error"
            ]
            validation_failures = update_derivation_validation.validate_derivations(
                completed,
                updaters=_get_updaters(),
                timeout=config.default_subprocess_timeout,
                all_declared_systems=True,
            )
    except BaseException:
        await queue.put(None)
        with contextlib.suppress(BaseException):
            await consumer
        raise

    summary = UpdateSummary()
    summary.accumulate(phase_result.details)
    candidate_updates = tuple(summary.updated)
    validation_failed = _record_derivation_validation_failures(
        summary,
        out,
        validation_failures,
    )

    return _RunExecutionResult(
        summary=summary,
        candidate_updates=candidate_updates,
        had_errors=phase_result.errors > 0 or validation_failed,
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
    updaters: dict[str, UpdaterClass],
) -> bool:
    """Return whether selected source tasks invoke any input refresh."""
    return any(
        update_planner.source_backing_input_name(name, updaters.get(name))
        or update_planner.source_additional_input_names(updaters.get(name))
        for name in source_names
    )


def _requires_root_closure_validation(
    opts: UpdateOptions,
    changed_paths: Iterable[Path],
) -> bool:
    """Return whether this transaction can affect configured root closures."""
    return not opts.target_names or bool(tuple(changed_paths))


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
    if out.json_output:
        payload = cast("dict[str, object]", outcome.summary.to_dict())
        payload["success"] = not outcome.had_errors
        if discarded_updates:
            payload["candidateUpdatesDiscarded"] = list(discarded_updates)
            payload["updated"] = []
        elif indeterminate_updates:
            payload["candidateUpdatesIndeterminate"] = list(indeterminate_updates)
            payload["updated"] = []
        if candidate_state_known:
            payload["candidatePromotionState"] = outcome.promotion_state.value
        if plan_error is not None:
            payload.update({
                "unknownTargets": list(plan_error.unknown_targets),
                "availableTargets": list(plan_error.available_targets),
            })
            error_key = "planError" if workspace_error is not None else "error"
            payload[error_key] = plan_error.message
        if workspace_error is not None:
            payload["error"] = workspace_error
        sys.stdout.write(f"{json.dumps(payload)}\n")
        return 1 if outcome.had_errors else 0

    if plan_error is not None:
        out.print_error(f"Error: {plan_error.message}")
        out.print_error(
            f"Available: {', '.join(plan_error.available_targets)}",
        )
    if workspace_error is not None:
        out.print_error(f"Error: {workspace_error}")
    if plan_error is not None:
        return 1
    return _emit_summary(
        outcome.summary,
        had_errors=outcome.had_errors,
        out=out,
        dry_run=dry_run,
        discarded_updates=discarded_updates,
        indeterminate_updates=indeterminate_updates,
    )


async def run_updates(
    opts: UpdateOptions,
    *,
    check_tools: bool = False,
) -> int:
    """Core update workflow — accepts typed UpdateOptions, returns exit code."""
    out = OutputOptions(json_output=opts.json, quiet=opts.quiet)
    config = _resolve_runtime_config(opts)

    preflight_result = _handle_preflight_requests(opts, out)
    if preflight_result is not None:
        return preflight_result

    outcome = _RunOutcome()
    try:
        with update_persistence.IsolatedUpdateWorkspace(
            get_repo_root(),
        ) as workspace:
            _revalidate_runtime_source_snapshot(workspace.root)
            if (
                check_tools
                and (tool_check := _handle_required_tool_check(opts)) is not None
            ):
                return tool_check
            update_flake.invalidate_flake_lock()
            allowed_paths: tuple[Path, ...] = ()
            if isinstance(run_plan := _build_run_plan(opts), _RunPlanError):
                outcome.plan_error = run_plan
                outcome.summary.accumulate(
                    dict.fromkeys(run_plan.unknown_targets, "error")
                )
                outcome.had_errors = True
            elif run_plan is not None:
                updaters = _get_updaters()
                declared_paths = list(
                    update_persistence.planned_update_paths(
                        run_plan.resolved.source_names,
                        updaters,
                    )
                )
                explicit_phase_outputs: list[Path] = []
                updates_refs = bool(
                    run_plan.resolved.do_refs and run_plan.resolved.ref_inputs
                )
                refreshes_source_inputs = bool(
                    run_plan.resolved.do_sources
                    and run_plan.resolved.source_names
                    and run_plan.resolved.do_input_refresh
                    and _sources_refresh_flake_lock(
                        run_plan.resolved.source_names,
                        updaters,
                    )
                )
                if updates_refs:
                    flake_nix = workspace.root / "flake.nix"
                    declared_paths.append(flake_nix)
                    explicit_phase_outputs.append(flake_nix)
                if updates_refs or refreshes_source_inputs:
                    flake_lock = workspace.root / "flake.lock"
                    declared_paths.append(flake_lock)
                    explicit_phase_outputs.append(flake_lock)
                result = await _execute_run_plan_result(opts, out, config, run_plan)
                outcome.summary = result.summary
                outcome.candidate_updates = result.candidate_updates
                outcome.had_errors = result.had_errors
                allowed_paths = _workspace_allowed_paths(
                    workspace.root,
                    declared_paths,
                    result.written_paths,
                    explicit_phase_outputs,
                )
            if not outcome.had_errors:
                with workspace.validation_snapshot() as snapshot:
                    if _requires_root_closure_validation(
                        opts,
                        snapshot.changed_paths,
                    ):
                        outcome.had_errors = (
                            _record_derivation_validation_failures(
                                outcome.summary,
                                out,
                                update_derivation_validation.validate_root_closures(
                                    flake_root=snapshot.root,
                                    timeout=config.subprocess_timeout_override,
                                ),
                            )
                            or outcome.had_errors
                        )
                if not outcome.had_errors:
                    if opts.check:
                        workspace.validate_changes(allowed_paths)
                    else:
                        workspace.promote(allowed_paths)
                        outcome.promoted = True
    except update_persistence.UpdateWorkspaceError as error:
        _record_workspace_failure(outcome, error)
    finally:
        update_flake.invalidate_flake_lock()

    return _emit_run_outcome(
        outcome,
        out=out,
        dry_run=opts.check,
    )


def run_update_command(
    options: UpdateOptions | None = None,
    /,
    **overrides: Unpack[UpdateOptionsKwargs],
) -> int:
    """Run the update workflow from one options object or keyword overrides."""
    reexec_result = _maybe_reexec_checkout_update()
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

    return asyncio.run(run_updates(opts, check_tools=True))


app = typer.Typer(
    help="Update source versions/hashes and flake input refs.",
    add_completion=False,
    no_args_is_help=False,
    context_settings=HELP_CONTEXT_SETTINGS,
)


@app.callback(invoke_without_command=True)
def cli(
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
    normalized_targets, trailing_options = _split_trailing_target_options(targets)
    values["targets"] = normalized_targets
    values.update(trailing_options)
    raise typer.Exit(code=run_update_command(**cast("UpdateOptionsKwargs", values)))
