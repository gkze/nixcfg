"""Resolve upstream versions for all updaters and write a pinned-versions manifest.

This runs ``fetch_latest()`` for each independently resolvable updater once,
producing a JSON file that the per-platform ``compute-hashes`` jobs consume via
``--pinned-versions``. By resolving versions in a single job we eliminate the
race condition where different CI runners see different upstream versions for
the same package. Companion updaters are intentionally left unpinned because
their versions may depend on artifacts materialized by their primary source
during the source update waves.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path
from typing import Annotated

import aiohttp
import typer
from pydantic import BaseModel

from lib.update import io as update_io
from lib.update import updaters as updater_module
from lib.update.ci._cli import make_main, make_typer_app
from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.sources import load_all_sources
from lib.update.updaters import UPDATERS, UpdaterClass, ensure_updaters_loaded
from lib.update.updaters.base import Updater, VersionInfo
from lib.update.updaters.metadata import deserialize_metadata, serialize_metadata

type _JsonSafe = (
    str | int | float | bool | None | dict[str, _JsonSafe] | list[_JsonSafe]
)
type _JsonObject = dict[str, _JsonSafe]

DEFAULT_PINNED_VERSIONS_PATH = Path("pinned-versions.json")


def _get_updaters() -> dict[str, UpdaterClass]:
    return updater_module.resolve_registry_alias(UPDATERS, ensure_updaters_loaded)


def _instantiate_updater(updater_cls: UpdaterClass, *, config: UpdateConfig) -> Updater:
    init_params = inspect.signature(updater_cls.__init__).parameters
    if "config" in init_params:
        return updater_cls(config=config)
    return updater_cls()


def _is_companion_updater(updater_cls: UpdaterClass) -> bool:
    """Return whether an updater should resolve after its primary source."""
    companion_of = getattr(updater_cls, "companion_of", None)
    return isinstance(companion_of, str) and bool(companion_of)


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
        "metadata": _make_json_safe(serialize_metadata(info.metadata)),
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
        metadata=deserialize_metadata(dict(metadata_payload)),
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


async def _resolve_all() -> tuple[dict[str, _JsonObject], list[str]]:
    """Run ``fetch_latest()`` for every updater and return results and failures."""
    config = resolve_active_config(None)
    results: dict[str, _JsonObject] = {}
    failures: list[str] = []

    async with aiohttp.ClientSession() as session:

        async def _resolve_one(name: str, updater_cls: UpdaterClass) -> None:
            updater: Updater = _instantiate_updater(updater_cls, config=config)
            try:
                outcome = await updater.fetch_latest(session)
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"Warning: failed to resolve {name}: {exc}\n")
                failures.append(name)
                return
            if not isinstance(outcome, VersionInfo):
                msg = f"unexpected version payload for {name}: {type(outcome)}"
                raise TypeError(msg)
            results[name] = _serialize_version_info(outcome)

        try:
            async with asyncio.TaskGroup() as group:
                for name, updater_cls in _get_updaters().items():
                    if _is_companion_updater(updater_cls):
                        continue
                    group.create_task(_resolve_one(name, updater_cls))
        except* Exception as exc_group:
            if len(exc_group.exceptions) == 1:
                raise exc_group.exceptions[0] from None
            raise

    return results, failures


def run(*, output: Path, strict: bool = True) -> int:
    """Resolve versions and write a pinned-versions manifest."""
    load_all_sources()
    _get_updaters()
    results, failures = asyncio.run(_resolve_all())

    if strict and failures:
        sys.stderr.write(
            f"Error: some updaters failed to resolve: {', '.join(sorted(failures))}\n"
        )
        return 1

    if failures:
        sys.stderr.write(
            "Warning: writing partial pinned versions manifest for: "
            f"{', '.join(sorted(failures))}\n"
        )

    if not results:
        sys.stderr.write("Error: no versions resolved\n")
        return 1

    update_io.atomic_write_json(output, results)

    sys.stderr.write(f"Resolved {len(results)} versions -> {output}\n")
    return 0


app = make_typer_app(
    help_text="Resolve upstream versions for all updaters.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def cli(
    *,
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Path to write the pinned versions manifest.",
        ),
    ] = DEFAULT_PINNED_VERSIONS_PATH,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict/--allow-partial",
            "-s/-p",
            help="Fail on any updater error unless partial output is explicitly allowed.",
        ),
    ] = True,
) -> None:
    """Resolve upstream versions and write a manifest."""
    raise typer.Exit(code=run(output=output, strict=strict))


main = make_main(app, prog_name="pipeline versions")


if __name__ == "__main__":
    raise SystemExit(main())
