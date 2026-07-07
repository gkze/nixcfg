"""Merge per-package ``sources.json`` trees from multiple CI artifacts."""

import json
from pathlib import Path
from typing import Annotated

import typer

from lib.nix.models.sources import SourceEntry
from lib.update import io as update_io
from lib.update.ci._cli import (
    make_dual_typer_apps,
    make_main,
    register_dual_entrypoint,
)
from lib.update.ci.update_target_artifacts import STATUS_FILE_NAME
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


def _collect_merged_entries(
    roots: list[str],
    *,
    baseline: dict[str, SourceEntry] | None = None,
) -> tuple[dict[str, SourceEntry], int, list[str], list[str]]:
    merged: dict[str, SourceEntry] = dict(baseline or {})
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
            if (root / STATUS_FILE_NAME).is_file():
                continue
            empty_roots.append(root_arg)
            continue

        for name, path in source_files.items():
            entry = _load_entry(path)
            existing = merged.get(name)
            baseline_entry = None if baseline is None else baseline.get(name)
            try:
                merged[name] = (
                    entry
                    if existing is None
                    else existing.merge_artifact(
                        entry, platform=platform, baseline=baseline_entry
                    )
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


def _load_baseline_entries(output_root: Path) -> dict[str, SourceEntry]:
    source_files = package_file_map_in(output_root, SOURCES_FILE_NAME)
    return {name: _load_entry(path) for name, path in source_files.items()}


def run(*, roots: list[str], output_root: Path) -> int:
    """Merge sources from artifact roots into the repository tree."""
    merged, loaded, missing_roots, empty_roots = _collect_merged_entries(
        roots,
        baseline=_load_baseline_entries(output_root),
    )
    _validate_input_roots(missing_roots, empty_roots)

    if loaded == 0:
        return 0

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
