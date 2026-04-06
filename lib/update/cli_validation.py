"""Validation helpers for the update CLI."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from lib.nix.models.sources import SourcesFile
    from lib.update.cli import OutputOptions
    from lib.update.cli_options import UpdateOptions


@dataclass(frozen=True)
class ValidationDependencies:
    """Collaborators required to validate discovered sources."""

    load_sources: Callable[[], SourcesFile]
    validate_source_discovery_consistency: Callable[[], None]


def handle_validate_request(
    opts: UpdateOptions,
    out: OutputOptions,
    *,
    dependencies: ValidationDependencies,
) -> int | None:
    """Handle ``--validate`` using one explicit dependency bundle."""
    if not opts.validate:
        return None

    try:
        sources = dependencies.load_sources()
        dependencies.validate_source_discovery_consistency()
        if opts.json:
            sys.stdout.write(
                f"{json.dumps({'valid': True, 'sources': len(sources.entries)})}\n",
            )
        else:
            out.print(
                ":heavy_check_mark: Validated sources.json entries: "
                f"{len(sources.entries)} sources OK",
                style="green",
            )
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        if opts.json:
            sys.stdout.write(
                f"{json.dumps({'valid': False, 'error': str(exc)})}\n",
            )
        else:
            out.print_error(f":x: Validation failed: {exc}")
        return 1
    return 0


def _handle_validate_request(
    opts: UpdateOptions,
    out: OutputOptions,
    *,
    load_sources: Callable[[], SourcesFile],
    validate_source_discovery_consistency: Callable[[], None],
) -> int | None:
    return handle_validate_request(
        opts,
        out,
        dependencies=ValidationDependencies(
            load_sources=load_sources,
            validate_source_discovery_consistency=validate_source_discovery_consistency,
        ),
    )


def validate_list_sort_option(opts: UpdateOptions, out: OutputOptions) -> int | None:
    """Reject ``--sort`` unless inventory output was explicitly requested."""
    if opts.list_targets or opts.sort_by == "name":
        return None
    message = "--sort/-o is only valid with --list/-l"
    if opts.json:
        sys.stdout.write(f"{json.dumps({'success': False, 'error': message})}\n")
    else:
        out.print_error(f"Error: {message}")
    return 1


def _validate_list_sort_option(opts: UpdateOptions, out: OutputOptions) -> int | None:
    return validate_list_sort_option(opts, out)


__all__ = [
    "ValidationDependencies",
    "_handle_validate_request",
    "_validate_list_sort_option",
    "handle_validate_request",
    "validate_list_sort_option",
]
