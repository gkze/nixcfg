"""Config loading and generation runner for declarative schema codegen."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from lib import http_utils
from lib.schema_codegen.config import (
    CodegenTarget,
    DirectorySource,
    LoadedSchemaCodegenConfig,
    SchemaCodegenConfig,
    URLSource,
)
from lib.update.paths import get_repo_root

from . import _prepare, _render

type ProgressReporter = Callable[[str], None]


def default_config_path() -> Path:
    """Return the checked-in schema codegen config path."""
    return get_repo_root() / "schema_codegen.yaml"


@dataclass(frozen=True)
class SchemaTargetSummary:
    """A compact description of one configured generation target."""

    name: str
    output: Path


@dataclass(frozen=True)
class _ResolvedTarget:
    """A target together with its resolved defaults and registry profile."""

    generator_options: dict[str, object]
    name: str
    target: CodegenTarget


def _emit_progress(progress: ProgressReporter | None, message: str) -> None:
    """Invoke the optional progress reporter."""
    if progress is None:
        return
    progress(message)


def _read_url_source(source: URLSource) -> str:
    """Fetch a remote schema document over HTTPS."""
    try:
        payload, _headers = http_utils.fetch_url_bytes(
            source.uri,
            headers=http_utils.build_github_headers(
                source.uri,
                token=http_utils.resolve_github_token(
                    allow_keyring=True,
                    allow_netrc=True,
                ),
                user_agent="nixcfg-schema-codegen",
            ),
            timeout=30.0,
        )
    except ValueError as exc:
        msg = f"Only absolute HTTPS schema URLs are supported, got {source.uri!r}"
        raise RuntimeError(msg) from exc
    except http_utils.RequestError as exc:
        msg = f"Failed to fetch schema URL {source.uri!r}: {exc.detail}"
        raise RuntimeError(msg) from exc
    return payload.decode()


def _resolve_config_paths(config: SchemaCodegenConfig, *, base_dir: Path) -> None:
    """Resolve repo-relative paths in-place against the config file directory."""
    for source in config.sources.values():
        if isinstance(source, DirectorySource) and not source.path.is_absolute():
            source.path = (base_dir / source.path).resolve()

    for target in config.targets.values():
        output = target.generator.output
        if output is not None and not output.is_absolute():
            target.generator.output = (base_dir / output).resolve()


def load_schema_codegen_config(
    *,
    config_path: Path | None = None,
) -> LoadedSchemaCodegenConfig:
    """Load and validate the declarative schema codegen config file."""
    resolved_path = (
        default_config_path()
        if config_path is None
        else config_path.expanduser().resolve()
    )
    payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    config = SchemaCodegenConfig.model_validate(payload)
    _resolve_config_paths(config, base_dir=resolved_path.parent)
    return LoadedSchemaCodegenConfig(config=config, path=resolved_path)


def list_schema_codegen_targets(
    *,
    config_path: Path | None = None,
) -> tuple[SchemaTargetSummary, ...]:
    """Return configured targets sorted by name."""
    loaded = load_schema_codegen_config(config_path=config_path)
    summaries: list[SchemaTargetSummary] = []
    for name, target in sorted(loaded.config.targets.items()):
        output = target.generator.output
        if output is None:
            msg = f"Target {name!r} is missing a generator output path"
            raise RuntimeError(msg)
        summaries.append(SchemaTargetSummary(name=name, output=output))
    return tuple(summaries)


def _build_registry(
    loaded: LoadedSchemaCodegenConfig,
    *,
    target: CodegenTarget,
) -> _prepare.RegistryLike:
    """Build the target-specific ``referencing.Registry``."""
    return _prepare.build_registry(
        loaded,
        target=target,
        read_url_source=_read_url_source,
    )


def _resolve_target(
    loaded: LoadedSchemaCodegenConfig,
    *,
    target_name: str,
) -> _ResolvedTarget:
    """Resolve one configured target together with inherited generator options."""
    target = loaded.config.targets.get(target_name)
    if target is None:
        msg = f"Unknown schema codegen target {target_name!r}"
        raise RuntimeError(msg)

    generator_options = loaded.config.defaults.generator.merged_with(
        target.generator
    ).model_dump(exclude_none=True)
    output = generator_options.get("output")
    if not isinstance(output, Path):
        msg = f"Target {target_name!r} is missing a generator output path"
        raise TypeError(msg)

    return _ResolvedTarget(
        generator_options=generator_options,
        name=target_name,
        target=target,
    )


# Preserve runner.py as the facade for the private helpers imported in tests.
_prepare_entrypoint_schema = _prepare.prepare_entrypoint_schema
_entrypoint_class_suffix = _render.entrypoint_class_suffix
_resolve_body_class_conflicts = _render.resolve_body_class_conflicts


def generate_schema_codegen_target(
    *,
    config_path: Path | None = None,
    progress: ProgressReporter | None = None,
    target_name: str,
) -> Path:
    """Render one configured target to its declared output path."""
    loaded = load_schema_codegen_config(config_path=config_path)
    resolved_target = _resolve_target(loaded, target_name=target_name)
    output_path = cast("Path", resolved_target.generator_options["output"])

    _emit_progress(progress, f"Building schema registry for target {target_name}.")
    registry = _build_registry(loaded, target=resolved_target.target)

    all_imports: set[str] = set()
    bodies: list[str] = []
    seen_class_signatures: dict[str, str] = {}
    used_class_names: set[str] = set()
    total = len(resolved_target.target.entrypoints)
    for index, entrypoint in enumerate(resolved_target.target.entrypoints, start=1):
        _emit_progress(progress, f"Preparing schema {index}/{total}: {entrypoint}")
        schema = _prepare_entrypoint_schema(
            entrypoint=entrypoint,
            registry=registry,
            target=resolved_target.target,
        )
        code = _render.generate_models(
            entrypoint=entrypoint,
            generator_options=resolved_target.generator_options,
            schema=schema,
        )
        imports, body = _render.collect_imports(_render.strip_generated_headers(code))
        body = _resolve_body_class_conflicts(
            body,
            entrypoint=entrypoint,
            seen_signatures=seen_class_signatures,
            used_names=used_class_names,
        )
        all_imports |= imports
        bodies.append(
            f"\n# === {_render.normalize_entrypoint_name(entrypoint)} ===\n{body}"
        )

    header = (
        '"""Auto-generated Pydantic models from JSON schemas.\n\n'
        "DO NOT EDIT MANUALLY. Regenerate with:\n"
        f"    nixcfg schema generate {target_name}\n"
        '"""\n\n'
        "from __future__ import annotations\n"
    )
    rendered = (
        f"{header}\n{_render.compose_imports_block(all_imports)}\n{''.join(bodies)}\n"
    )
    rendered = _render.dedupe_classes(rendered)
    rendered = _render.collapse_excess_blank_lines(rendered)
    rendered = _render.apply_python_transforms(rendered, target=resolved_target.target)
    if not rendered.endswith("\n"):
        rendered = f"{rendered}\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    _emit_progress(progress, f"Wrote generated models to {output_path}.")
    return output_path
