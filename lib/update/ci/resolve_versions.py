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
from typing import TYPE_CHECKING

import aiohttp
from pydantic import BaseModel

from lib.update.config import resolve_active_config
from lib.update.sources import load_all_sources
from lib.update.updaters import UPDATERS
from lib.update.updaters.base import VersionInfo

if TYPE_CHECKING:
    from collections.abc import Sequence


type _JsonSafe = (
    str | int | float | bool | None | dict[str, _JsonSafe] | list[_JsonSafe]
)
type _JsonObject = dict[str, _JsonSafe]


def _make_json_safe(obj: object) -> _JsonSafe:
    """Recursively convert *obj* to JSON-serializable types.

    Pydantic models are dumped via ``model_dump()``, dicts and sequences are
    traversed recursively, and unsupported values raise ``TypeError``.
    """
    if isinstance(obj, BaseModel):
        return _make_json_safe(obj.model_dump())
    if isinstance(obj, dict):
        return {str(k): _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    msg = f"Value is not JSON-serializable: {obj!r}"
    raise TypeError(msg)


def _serialize_version_info(info: VersionInfo) -> _JsonObject:
    """Serialize a VersionInfo to a JSON-safe dict."""
    return {
        "version": info.version,
        "metadata": _make_json_safe(info.metadata),
    }


def _deserialize_version_info(data: _JsonObject) -> VersionInfo:
    """Deserialize a VersionInfo from a JSON dict."""
    version_payload = data.get("version")
    if not isinstance(version_payload, str):
        msg = f"Pinned version entry missing string 'version': {data!r}"
        raise TypeError(msg)
    metadata_payload = data.get("metadata", {})
    if not isinstance(metadata_payload, dict):
        msg = f"Pinned version entry has invalid 'metadata': {data!r}"
        raise TypeError(msg)
    return VersionInfo(
        version=version_payload,
        metadata=dict(metadata_payload),
    )


def load_pinned_versions(path: Path) -> dict[str, VersionInfo]:
    """Load a pinned-versions manifest into a ``{name: VersionInfo}`` dict."""
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        msg = (
            f"Pinned versions file must be a JSON object, got {type(payload).__name__}"
        )
        raise TypeError(msg)
    results: dict[str, VersionInfo] = {}
    for name, entry in payload.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            msg = f"Invalid pinned versions entry: {name!r}: {entry!r}"
            raise TypeError(msg)
        results[name] = _deserialize_version_info(entry)
    return results


async def _resolve_all() -> dict[str, _JsonObject]:
    """Run ``fetch_latest()`` for every updater and return serialized results."""
    config = resolve_active_config(None)
    results: dict[str, _JsonObject] = {}

    async with aiohttp.ClientSession() as session:
        tasks: dict[str, asyncio.Task[VersionInfo]] = {}
        for name, updater_cls in UPDATERS.items():
            try:
                updater = updater_cls(config=config)
            except TypeError as exc:
                if "config" not in str(exc):
                    raise
                updater = updater_cls()
            tasks[name] = asyncio.create_task(updater.fetch_latest(session))

        outcomes = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for name, outcome in zip(tasks, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                sys.stderr.write(f"Warning: failed to resolve {name}: {outcome}\n")
                continue
            if not isinstance(outcome, VersionInfo):
                msg = f"unexpected version payload for {name}: {type(outcome)}"
                raise TypeError(msg)
            results[name] = _serialize_version_info(outcome)

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
