"""Merge per-package ``sources.json`` trees from multiple CI artifacts."""

import json
from pathlib import Path
from typing import Annotated

import typer

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update import io as update_io
from lib.update.ci._cli import (
    make_dual_typer_apps,
    make_main,
    register_dual_entrypoint,
)
from lib.update.paths import (
    SOURCES_FILE_NAME,
    package_dir_for_in,
    package_file_map_in,
)

DEFAULT_OUTPUT_ROOT = Path()


def _load_entry(path: Path) -> SourceEntry:
    with path.open(encoding="utf-8") as f:
        return SourceEntry.model_validate(json.load(f))


def _save_entry(path: Path, entry: SourceEntry) -> None:
    update_io.atomic_write_json(path, entry.to_dict(), mkdir=True)


def _infer_platform_from_root_path(root: Path) -> str | None:
    name = root.name
    prefix = "sources-"
    if not name.startswith(prefix):
        return None
    platform = name.removeprefix(prefix)
    return platform or None


def _parse_root_spec(root_spec: str) -> tuple[str | None, Path]:
    if "=" in root_spec:
        platform, path = root_spec.split("=", 1)
        platform = platform.strip() or None
        return platform, Path(path)
    root = Path(root_spec)
    return _infer_platform_from_root_path(root), root


def _hash_entry_key(
    entry: HashEntry,
) -> tuple[
    str,
    str | None,
    str | None,
    str | None,
    tuple[tuple[str, str], ...] | None,
]:
    urls_key = tuple(sorted(entry.urls.items())) if entry.urls is not None else None
    return (
        entry.hash_type,
        entry.platform,
        entry.git_dep,
        entry.url,
        urls_key,
    )


def _merge_hash_entries(
    base: list[HashEntry],
    incoming: list[HashEntry],
    *,
    platform: str | None,
) -> list[HashEntry]:
    by_key: dict[
        tuple[
            str,
            str | None,
            str | None,
            str | None,
            tuple[tuple[str, str], ...] | None,
        ],
        HashEntry,
    ] = {_hash_entry_key(entry): entry for entry in base}

    for entry in incoming:
        if entry.hash.startswith(HashCollection.FAKE_HASH_PREFIX):
            continue
        key = _hash_entry_key(entry)

        if entry.platform is None:
            existing = by_key.get(key)
            if existing is not None and existing.hash != entry.hash:
                msg = (
                    "Conflicting non-platform hash entry for "
                    f"{entry.hash_type}: {existing.hash} vs {entry.hash}"
                )
                raise RuntimeError(msg)
            by_key[key] = entry
            continue

        if platform is None:
            by_key[key] = entry
            continue

        if entry.platform == platform:
            by_key[key] = entry

    return list(by_key.values())


def _merge_hash_mapping(
    base: dict[str, str],
    incoming: dict[str, str],
    *,
    platform: str | None,
) -> dict[str, str]:
    merged = dict(base)
    for incoming_platform, hash_value in incoming.items():
        if hash_value.startswith(HashCollection.FAKE_HASH_PREFIX):
            continue
        if platform is not None and incoming_platform != platform:
            continue
        existing = merged.get(incoming_platform)
        if (
            existing is not None
            and incoming_platform != platform
            and existing != hash_value
        ):
            msg = (
                "Conflicting non-platform hash mapping for "
                f"{incoming_platform}: {existing} vs {hash_value}"
            )
            raise RuntimeError(msg)
        merged[incoming_platform] = hash_value
    return merged


def _merge_optional_scalar(
    field_name: str,
    existing: str | None,
    incoming: str | None,
) -> str | None:
    if existing and incoming and existing != incoming:
        msg = f"Conflicting {field_name}: {existing!r} vs {incoming!r}"
        raise RuntimeError(msg)
    return incoming or existing


def _merge_urls(
    existing: dict[str, str] | None,
    incoming: dict[str, str] | None,
) -> dict[str, str] | None:
    if existing is None and incoming is None:
        return None
    merged: dict[str, str] = dict(existing or {})
    for url_key, url_value in (incoming or {}).items():
        existing_value = merged.get(url_key)
        if existing_value is not None and existing_value != url_value:
            msg = f"Conflicting urls entry for {url_key!r}: {existing_value!r} vs {url_value!r}"
            raise RuntimeError(msg)
        merged[url_key] = url_value
    return merged


def _merge_entry(
    existing: SourceEntry,
    incoming: SourceEntry,
    *,
    platform: str | None,
) -> SourceEntry:
    if existing.hashes.entries is not None and incoming.hashes.entries is not None:
        merged_hashes = HashCollection(
            entries=_merge_hash_entries(
                existing.hashes.entries,
                incoming.hashes.entries,
                platform=platform,
            ),
        )
    elif existing.hashes.mapping is not None and incoming.hashes.mapping is not None:
        merged_hashes = HashCollection(
            mapping=_merge_hash_mapping(
                existing.hashes.mapping,
                incoming.hashes.mapping,
                platform=platform,
            ),
        )
    else:
        merged_hashes = existing.hashes.merge(incoming.hashes)

    return SourceEntry(
        hashes=merged_hashes,
        version=_merge_optional_scalar("version", existing.version, incoming.version),
        input=_merge_optional_scalar("input", existing.input, incoming.input),
        commit=_merge_optional_scalar("commit", existing.commit, incoming.commit),
        urls=_merge_urls(existing.urls, incoming.urls),
    )


def _collect_merged_entries(
    roots: list[str],
) -> tuple[dict[str, SourceEntry], int, list[str], list[str]]:
    merged: dict[str, SourceEntry] = {}
    loaded = 0
    missing_roots: list[str] = []
    empty_roots: list[str] = []

    for root_arg in roots:
        platform, root = _parse_root_spec(root_arg)
        if not root.exists():
            missing_roots.append(root_arg)
            continue

        source_files = package_file_map_in(root, SOURCES_FILE_NAME)
        if not source_files:
            empty_roots.append(root_arg)
            continue

        for name, path in source_files.items():
            entry = _load_entry(path)
            existing = merged.get(name)
            try:
                merged[name] = (
                    entry
                    if existing is None
                    else _merge_entry(existing, entry, platform=platform)
                )
            except RuntimeError as exc:
                msg = (
                    f"Failed to merge {name!r} from root {root_arg!r} "
                    f"(platform={platform!r}): {exc}"
                )
                raise RuntimeError(msg) from exc
            loaded += 1

    return merged, loaded, missing_roots, empty_roots


def _validate_input_roots(missing_roots: list[str], empty_roots: list[str]) -> None:
    if not (missing_roots or empty_roots):
        return

    issues: list[str] = []
    if missing_roots:
        missing = ", ".join(sorted(missing_roots))
        issues.append(f"missing roots: {missing}")
    if empty_roots:
        empty = ", ".join(sorted(empty_roots))
        issues.append(f"roots with no sources.json files: {empty}")
    raise RuntimeError("Invalid merge input roots: " + "; ".join(issues))


def _write_merged_entries(output_root: Path, merged: dict[str, SourceEntry]) -> None:
    output_paths = package_file_map_in(output_root, SOURCES_FILE_NAME)
    missing_output_paths: list[str] = []
    for name, entry in merged.items():
        path = output_paths.get(name)
        if path is None:
            package_dir = package_dir_for_in(output_root, name)
            if package_dir is None:
                missing_output_paths.append(name)
                continue
            path = package_dir / SOURCES_FILE_NAME
        _save_entry(path, entry)

    if not missing_output_paths:
        return

    missing = ", ".join(sorted(missing_output_paths))
    msg = (
        "Merged sources contain package names with no output destination under "
        f"{output_root}: {missing}"
    )
    raise RuntimeError(msg)


def run(*, roots: list[str], output_root: Path) -> int:
    """Merge sources from artifact roots into the repository tree."""
    merged, loaded, missing_roots, empty_roots = _collect_merged_entries(roots)
    _validate_input_roots(missing_roots, empty_roots)

    if loaded == 0:
        return 1

    _write_merged_entries(output_root, merged)

    return 0


_DUAL_APPS = make_dual_typer_apps(
    help_text="Merge per-package sources.json files from platform artifacts.",
    no_args_is_help=False,
)
app = _DUAL_APPS.app


@register_dual_entrypoint(_DUAL_APPS)
def cli(
    roots: Annotated[
        list[str],
        typer.Argument(
            help=(
                "Input artifact roots to merge. Supports either <path> "
                "(platform inferred from root name sources-<platform>) or "
                "<platform>=<path> (for example "
                "aarch64-darwin=sources-aarch64-darwin)."
            )
        ),
    ],
    *,
    output_root: Annotated[
        Path,
        typer.Option(
            "-o",
            "--output-root",
            help="Repository root to write merged files into.",
        ),
    ] = DEFAULT_OUTPUT_ROOT,
) -> None:
    """Merge platform artifact roots into repository sources.json files."""
    raise typer.Exit(code=run(roots=roots, output_root=output_root))


main = make_main(_DUAL_APPS.standalone_app, prog_name="pipeline sources")


if __name__ == "__main__":
    raise SystemExit(main())
