"""Resolve upstream versions for all updaters and write a pinned-versions manifest.

This runs ``fetch_latest()`` for every updater once, producing a JSON file
that the per-platform ``compute-hashes`` jobs consume via ``--pinned-versions``.
By resolving versions in a single job we eliminate the race condition where
different CI runners see different upstream versions for the same package.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

from lib.update.config import resolve_active_config
from lib.update.sources import load_all_sources
from lib.update.updaters import UPDATERS
from lib.update.updaters.base import VersionInfo

if TYPE_CHECKING:
    from collections.abc import Sequence


def _serialize_version_info(info: VersionInfo) -> dict[str, Any]:
    """Serialize a VersionInfo to a JSON-safe dict."""
    return {
        "version": info.version,
        "metadata": info.metadata,
    }


def _deserialize_version_info(data: dict[str, Any]) -> VersionInfo:
    """Deserialize a VersionInfo from a JSON dict."""
    return VersionInfo(
        version=data["version"],
        metadata=data.get("metadata", {}),
    )


def load_pinned_versions(path: Path) -> dict[str, VersionInfo]:
    """Load a pinned-versions manifest into a ``{name: VersionInfo}`` dict."""
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    return {name: _deserialize_version_info(entry) for name, entry in payload.items()}


async def _resolve_all() -> dict[str, dict[str, Any]]:
    """Run ``fetch_latest()`` for every updater and return serialized results."""
    config = resolve_active_config(None)
    results: dict[str, dict[str, Any]] = {}

    async with aiohttp.ClientSession() as session:
        tasks: dict[str, asyncio.Task[VersionInfo]] = {}
        for name, updater_cls in UPDATERS.items():
            updater = updater_cls(config=config)
            tasks[name] = asyncio.create_task(updater.fetch_latest(session))

        for name, task in tasks.items():
            try:
                info = await task
                results[name] = _serialize_version_info(info)
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"Warning: failed to resolve {name}: {exc}\n")

    return results


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve upstream versions for all updaters",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="pinned-versions.json",
        help="Path to write the pinned versions manifest (default: pinned-versions.json)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI entrypoint."""
    args = _parse_args(argv)
    # Ensure updater modules are imported (triggers discovery).
    load_all_sources()
    results = asyncio.run(_resolve_all())

    if not results:
        sys.stderr.write("Error: no versions resolved\n")
        return 1

    output = Path(args.output)
    with output.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True)
        f.write("\n")

    sys.stderr.write(f"Resolved {len(results)} versions -> {output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
