"""Config loading and generation runner for declarative schema codegen."""

from __future__ import annotations

import ast
import importlib
import io
import json
import re
import tokenize
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlparse

import httpx
import yaml

from lib.schema_codegen.config import (
    AliasStrategy,
    CodegenTarget,
    DereferenceMode,
    DirectorySource,
    LoadedSchemaCodegenConfig,
    PythonTransform,
    ResourceMode,
    RetrieveKind,
    SchemaCodegenConfig,
    SchemaFormat,
    SchemaSource,
    SchemaTransform,
    URLSource,
)
from lib.update.paths import REPO_ROOT


class _GenerateConfigModel(Protocol):
    @classmethod
    def model_validate(cls, obj: object) -> object: ...


class _DataModelCodeGeneratorConfigModule(Protocol):
    GenerateConfig: type[_GenerateConfigModel]


class _DataModelCodeGeneratorModule(Protocol):
    def generate(self, source: str, *, config: object) -> str | None | object: ...


class _ResourceLike(Protocol):
    contents: object

    def id(self) -> str | None: ...


class _ResolvedLike(Protocol):
    contents: object
    resolver: _ResolverLike


class _ResolverLike(Protocol):
    def lookup(self, ref: str) -> _ResolvedLike: ...


class _RegistryLike(Protocol):
    def __getitem__(self, uri: str) -> _ResourceLike: ...

    def crawl(self) -> _RegistryLike: ...

    def resolver_with_root(self, resource: _ResourceLike) -> _ResolverLike: ...

    def with_resources(self, pairs: Iterable[tuple[str, object]]) -> _RegistryLike: ...


class _ReferencingResourceFactory(Protocol):
    def from_contents(
        self,
        contents: object,
        default_specification: object | None = None,
    ) -> _ResourceLike: ...


class _ReferencingModule(Protocol):
    Resource: _ReferencingResourceFactory
    Registry: Callable[[], _RegistryLike]


class _ReferencingJsonSchemaModule(Protocol):
    def specification_with(self, dialect_id: str) -> object: ...


DEFAULT_CONFIG_PATH = REPO_ROOT / "schema_codegen.yaml"

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type ProgressReporter = Callable[[str], None]


@dataclass(frozen=True)
class SchemaTargetSummary:
    """A compact description of one configured generation target."""

    name: str
    output: Path


@dataclass(frozen=True)
class _LoadedSchemaDocument:
    """One schema document loaded from one configured source."""

    basename: str | None
    contents: JsonValue
    label: str
    relative_path: str | None
    source_uri: str | None


@dataclass(frozen=True)
class _ResolvedTarget:
    """A target together with its resolved defaults and registry profile."""

    generator_options: dict[str, object]
    name: str
    target: CodegenTarget


def _import_optional(module_name: str, *, feature: str) -> object:
    """Import an optional dependency or raise a helpful runtime error."""
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in live CLI
        msg = (
            f"{feature} requires optional dependency {module_name!r}. "
            "Install the `codegen` extra to use this command."
        )
        raise RuntimeError(msg) from exc


def _as_object_dict(value: object, *, context: str) -> dict[str, object]:
    """Return ``value`` as ``dict[str, object]`` or raise ``TypeError``."""
    if not isinstance(value, dict):
        msg = f"Expected object for {context}, got {type(value).__name__}"
        raise TypeError(msg)
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            msg = f"Expected string key in {context}, got {type(key).__name__}"
            raise TypeError(msg)
        result[key] = item
    return result


def _coerce_json_value(value: object, *, context: str) -> JsonValue:
    """Convert arbitrary JSON-compatible data to ``JsonValue`` recursively."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_coerce_json_value(item, context=f"{context}[]") for item in value]
    obj = _as_object_dict(value, context=context)
    return {
        key: _coerce_json_value(item, context=f"{context}.{key}")
        for key, item in obj.items()
    }


def _emit_progress(progress: ProgressReporter | None, message: str) -> None:
    """Invoke the optional progress reporter."""
    if progress is None:
        return
    progress(message)


def _normalize_entrypoint_name(entrypoint: str) -> str:
    """Return a stable label for generated per-entrypoint sections."""
    candidate = entrypoint.rsplit("/", maxsplit=1)[-1].removesuffix(".yaml")
    candidate = candidate.removesuffix(".yml").removesuffix(".json")
    return candidate or "schema"


def _parse_schema_text(text: str, *, fmt: SchemaFormat, context: str) -> JsonValue:
    """Deserialize one schema document and coerce it to JSON-compatible data."""
    loaded = json.loads(text) if fmt is SchemaFormat.JSON else yaml.safe_load(text)
    return _coerce_json_value(loaded, context=context)


def _read_url_source(source: URLSource) -> str:
    """Fetch a remote schema document over HTTPS."""
    parsed = urlparse(source.uri)
    if parsed.scheme != "https" or not parsed.netloc:
        msg = f"Only absolute HTTPS schema URLs are supported, got {source.uri!r}"
        raise RuntimeError(msg)

    response: httpx.Response = httpx.get(
        source.uri,
        follow_redirects=True,
        timeout=30.0,
    )
    response.raise_for_status()
    return response.text


def _load_source_documents(source: SchemaSource) -> tuple[_LoadedSchemaDocument, ...]:
    """Load all schema documents for one configured source."""
    if isinstance(source, DirectorySource):
        documents: list[_LoadedSchemaDocument] = []
        for pattern in source.include:
            for path in sorted(source.path.glob(pattern)):
                if not path.is_file():
                    continue
                relative_path = f"./{path.relative_to(source.path).as_posix()}"
                documents.append(
                    _LoadedSchemaDocument(
                        basename=path.name,
                        contents=_parse_schema_text(
                            path.read_text(encoding="utf-8"),
                            fmt=source.format,
                            context=f"schema {path}",
                        ),
                        label=str(path),
                        relative_path=relative_path,
                        source_uri=None,
                    )
                )
        return tuple(documents)

    text = _read_url_source(source)
    return (
        _LoadedSchemaDocument(
            basename=Path(urlparse(source.uri).path).name or None,
            contents=_parse_schema_text(
                text,
                fmt=source.format,
                context=f"schema {source.uri}",
            ),
            label=source.uri,
            relative_path=None,
            source_uri=source.uri,
        ),
    )


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
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> LoadedSchemaCodegenConfig:
    """Load and validate the declarative schema codegen config file."""
    resolved_path = (
        config_path
        if config_path == DEFAULT_CONFIG_PATH
        else config_path.expanduser().resolve()
    )
    payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    config = SchemaCodegenConfig.model_validate(payload)
    _resolve_config_paths(config, base_dir=resolved_path.parent)
    return LoadedSchemaCodegenConfig(config=config, path=resolved_path)


def list_schema_codegen_targets(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
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


def _make_resource(
    contents: JsonValue,
    *,
    default_specification: str | None,
) -> _ResourceLike:
    """Create one ``referencing.Resource`` for one schema document."""
    referencing = cast(
        "_ReferencingModule",
        _import_optional("referencing", feature="schema code generation"),
    )
    resource_factory = referencing.Resource
    if default_specification is None:
        return resource_factory.from_contents(contents)

    referencing_jsonschema = cast(
        "_ReferencingJsonSchemaModule",
        _import_optional(
            "referencing.jsonschema",
            feature="schema code generation",
        ),
    )
    specification = referencing_jsonschema.specification_with(default_specification)
    return resource_factory.from_contents(
        contents,
        default_specification=specification,
    )


def _alias_uris_for_document(
    *,
    aliases: tuple[AliasStrategy, ...],
    document: _LoadedSchemaDocument,
    resource: _ResourceLike,
) -> tuple[str, ...]:
    """Return the configured alias URIs for one loaded document."""
    uris: list[str] = []
    for alias in aliases:
        if alias is AliasStrategy.SOURCE_URI and document.source_uri is not None:
            uris.append(document.source_uri)
        elif (
            alias is AliasStrategy.RELATIVE_PATH and document.relative_path is not None
        ):
            uris.append(document.relative_path)
        elif alias is AliasStrategy.BASENAME and document.basename is not None:
            uris.append(document.basename)
        elif alias is AliasStrategy.INTERNAL_ID:
            internal_id = resource.id()
            if internal_id:
                uris.append(cast("str", internal_id))
    return tuple(dict.fromkeys(uris))


def _build_registry(
    loaded: LoadedSchemaCodegenConfig,
    *,
    target: CodegenTarget,
) -> _RegistryLike:
    """Build the target-specific ``referencing.Registry``."""
    if target.registry_profile not in loaded.config.registry_profiles:
        msg = f"Unknown registry profile {target.registry_profile!r}"
        raise RuntimeError(msg)

    profile = loaded.config.registry_profiles[target.registry_profile]
    if profile.resource.mode is not ResourceMode.FROM_CONTENTS:
        msg = f"Unsupported resource mode {profile.resource.mode!r}"
        raise RuntimeError(msg)
    if profile.registry.retrieve.kind is not RetrieveKind.NONE:
        msg = f"Unsupported retrieve backend {profile.registry.retrieve.kind!r}"
        raise RuntimeError(msg)

    referencing = cast(
        "_ReferencingModule",
        _import_optional("referencing", feature="schema code generation"),
    )
    registry = referencing.Registry()
    resources: list[tuple[str, object]] = []
    seen_uris: dict[str, str] = {}

    for source_name in target.sources:
        if source_name not in loaded.config.sources:
            msg = f"Unknown schema source {source_name!r}"
            raise RuntimeError(msg)
        source = loaded.config.sources[source_name]
        for document in _load_source_documents(source):
            resource = _make_resource(
                document.contents,
                default_specification=profile.resource.default_specification,
            )
            uris = _alias_uris_for_document(
                aliases=profile.aliases,
                document=document,
                resource=resource,
            )
            if not uris:
                msg = f"Schema document {document.label} has no configured registry aliases"
                raise RuntimeError(msg)
            for uri in uris:
                existing = seen_uris.get(uri)
                if existing is not None and existing != document.label:
                    msg = (
                        f"Registry alias {uri!r} collides between "
                        f"{existing} and {document.label}"
                    )
                    raise RuntimeError(msg)
                seen_uris[uri] = document.label
                resources.append((uri, resource))

    registry = registry.with_resources(resources)
    return registry.crawl() if profile.registry.crawl else registry


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


def _merge_allof_extras(result: dict[str, object], branch: dict[str, object]) -> None:
    excluded = frozenset({"properties", "required", "type", "title", "description"})
    for key, value in branch.items():
        if key in excluded or key in result:
            continue
        result[key] = value


def _merge_allof_properties(
    result: dict[str, object], merged: dict[str, object]
) -> None:
    if not merged:
        return
    existing = result.get("properties")
    properties = (
        _as_object_dict(existing, context="schema properties")
        if isinstance(existing, dict)
        else {}
    )
    for key, value in merged.items():
        properties.setdefault(key, value)
    result["properties"] = properties


def _merge_allof_required(result: dict[str, object], merged: list[str]) -> None:
    if not merged:
        return
    existing = result.get("required")
    required = (
        [item for item in existing if isinstance(item, str)]
        if isinstance(existing, list)
        else []
    )
    seen = set(required)
    for item in merged:
        if item in seen:
            continue
        required.append(item)
        seen.add(item)
    result["required"] = required


def _merge_allof_branches(result: dict[str, object]) -> None:
    """Inline object-shaped ``allOf`` branches into their parent object."""
    all_of = result.get("allOf")
    if not isinstance(all_of, list):
        return

    merged_properties: dict[str, object] = {}
    merged_required: list[str] = []
    remaining: list[object] = []
    for branch in all_of:
        if not isinstance(branch, dict):
            remaining.append(branch)
            continue
        branch_dict = _as_object_dict(branch, context="allOf branch")
        props_obj = branch_dict.get("properties")
        if not isinstance(props_obj, dict):
            remaining.append(branch)
            continue
        merged_properties.update(_as_object_dict(props_obj, context="allOf properties"))
        required_obj = branch_dict.get("required")
        if isinstance(required_obj, list):
            merged_required.extend(
                item for item in required_obj if isinstance(item, str)
            )
        _merge_allof_extras(result, branch_dict)

    _merge_allof_properties(result, merged_properties)
    _merge_allof_required(result, merged_required)
    if remaining:
        result["allOf"] = remaining
    else:
        result.pop("allOf", None)


def _apply_schema_transforms(
    obj: JsonValue,
    *,
    transforms: tuple[SchemaTransform, ...],
) -> JsonValue:
    """Apply configured schema-shape transforms recursively."""
    if isinstance(obj, list):
        return [_apply_schema_transforms(item, transforms=transforms) for item in obj]
    if not isinstance(obj, dict):
        return obj

    drop_description = SchemaTransform.DROP_DESCRIPTION in transforms
    result: dict[str, JsonValue] = {
        key: _apply_schema_transforms(value, transforms=transforms)
        for key, value in obj.items()
        if isinstance(key, str) and not (drop_description and key == "description")
    }

    if SchemaTransform.INLINE_MERGEABLE_ALLOF in transforms:
        _merge_allof_branches(cast("dict[str, object]", result))
    if (
        SchemaTransform.CONST_NULL_TO_TYPE_NULL in transforms
        and result.get("const") is None
        and "const" in result
    ):
        result.pop("const", None)
        result["type"] = "null"
    return result


def _inline_references(
    obj: JsonValue,
    *,
    merge_ref_siblings: bool,
    resolver: _ResolverLike,
    stack: tuple[str, ...],
) -> JsonValue:
    """Inline ``$ref`` references using ``referencing``'s resolver primitives."""
    if isinstance(obj, list):
        return [
            _inline_references(
                item,
                merge_ref_siblings=merge_ref_siblings,
                resolver=resolver,
                stack=stack,
            )
            for item in obj
        ]
    if not isinstance(obj, dict):
        return obj

    ref_obj = obj.get("$ref")
    if not isinstance(ref_obj, str):
        return {
            key: _inline_references(
                value,
                merge_ref_siblings=merge_ref_siblings,
                resolver=resolver,
                stack=stack,
            )
            for key, value in obj.items()
            if isinstance(key, str)
        }

    resolved = resolver.lookup(ref_obj)
    resolved_contents = _coerce_json_value(
        resolved.contents,
        context=f"resolved schema {ref_obj}",
    )
    if ref_obj in stack:
        msg = f"Recursive $ref not supported during inline dereference: {ref_obj}"
        raise RuntimeError(msg)

    merged: JsonValue = resolved_contents
    if merge_ref_siblings and isinstance(resolved_contents, dict):
        extras = {key: value for key, value in obj.items() if key != "$ref"}
        merged = {**resolved_contents, **extras}

    return _inline_references(
        merged,
        merge_ref_siblings=merge_ref_siblings,
        resolver=resolved.resolver,
        stack=(*stack, ref_obj),
    )


def _prepare_entrypoint_schema(
    *,
    entrypoint: str,
    registry: _RegistryLike,
    target: CodegenTarget,
) -> JsonValue:
    """Load, dereference, and transform one target entrypoint schema."""
    try:
        resource = registry[entrypoint]
    except Exception as exc:
        msg = f"Could not load schema entrypoint {entrypoint!r} from registry"
        raise RuntimeError(msg) from exc

    contents = _coerce_json_value(resource.contents, context=f"entrypoint {entrypoint}")
    if target.prepare.dereference is DereferenceMode.INLINE_REFS:
        contents = _inline_references(
            contents,
            merge_ref_siblings=target.prepare.merge_ref_siblings,
            resolver=registry.resolver_with_root(resource),
            stack=(),
        )
    if target.prepare.schema_transforms:
        contents = _apply_schema_transforms(
            contents,
            transforms=target.prepare.schema_transforms,
        )
    return contents


_CONSTR_PATTERN = re.compile(
    r"constr\(\s*pattern=(?P<literal>r?(?:'[^']*'|\"[^\"]*\"))\s*\)"
)


def _rewrite_constr_type_hints(code: str) -> str:
    """Rewrite ``constr(pattern=...)`` annotations to ``StringConstraints``."""

    def _replace(match: re.Match[str]) -> str:
        literal = match.group("literal")
        line_start = match.string.rfind("\n", 0, match.start()) + 1
        indent = match.string[line_start : match.start()]
        inner = f"{indent}    "
        return (
            "Annotated[\n"
            f"{inner}str,\n"
            f"{inner}StringConstraints(pattern={literal}),\n"
            f"{indent}]"
        )

    return _CONSTR_PATTERN.sub(_replace, code)


def _normalize_pydantic_imports(code: str) -> str:
    """Replace ``constr`` imports with ``StringConstraints`` when needed."""
    lines: list[str] = []
    for raw_line in code.splitlines():
        line = raw_line
        if raw_line.startswith("from pydantic import ") and "constr" in raw_line:
            names = [
                name.strip()
                for name in raw_line.split("import ", maxsplit=1)[1].split(",")
            ]
            names = [name for name in names if name != "constr"]
            if "StringConstraints" not in names:
                names.append("StringConstraints")
            line = f"from pydantic import {', '.join(sorted(names))}"
        lines.append(line)
    return "\n".join(lines)


def _strip_generated_headers(code: str) -> str:
    """Remove datamodel-code-generator headers before composing outputs."""
    filtered: list[str] = []
    for line in code.splitlines():
        if line.startswith("# generated by datamodel-codegen"):
            continue
        if line.startswith("#   filename:"):
            continue
        if line.startswith("#   timestamp:"):
            continue
        filtered.append(line)
    return "\n".join(filtered)


def _collect_imports(code: str) -> tuple[set[str], str]:
    """Extract import statements and return them separately from the body."""
    tree = ast.parse(code)
    lines = code.splitlines()
    imports: set[str] = set()
    import_line_numbers: set[int] = set()

    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        start = node.lineno - 1
        end = node.end_lineno if node.end_lineno is not None else node.lineno
        imports.add("\n".join(lines[start:end]))
        import_line_numbers.update(range(start, end))

    body = [
        line for index, line in enumerate(lines) if index not in import_line_numbers
    ]
    return imports, "\n".join(body)


def _format_import_alias(alias: ast.alias) -> str:
    """Render one import alias back to source form."""
    return alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"


def _import_module_sort_key(module: str) -> tuple[int, str]:
    """Sort stdlib imports ahead of third-party imports."""
    return (1 if module.startswith("pydantic") else 0, module)


def _compose_imports_block(all_imports: set[str]) -> str:
    """Compose one deduplicated import block."""
    from_imports: dict[str, set[str]] = {}
    bare_imports: set[str] = set()
    for import_stmt in all_imports:
        node = ast.parse(import_stmt).body[0]
        if isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if module_name == "__future__":
                continue
            from_imports.setdefault(module_name, set()).update(
                _format_import_alias(alias) for alias in node.names
            )
        elif isinstance(node, ast.Import):
            bare_imports.update(_format_import_alias(alias) for alias in node.names)

    lines: list[str] = [f"import {name}" for name in sorted(bare_imports)]
    previous_group: int | None = None
    for module_name in sorted(from_imports, key=_import_module_sort_key):
        group, _ = _import_module_sort_key(module_name)
        if (
            previous_group is not None
            and group != previous_group
            and lines
            and lines[-1] != ""
        ):
            lines.append("")
        lines.append(
            f"from {module_name} import {', '.join(sorted(from_imports[module_name]))}"
        )
        previous_group = group
    return "\n".join(lines)


def _entrypoint_class_suffix(entrypoint: str) -> str:
    """Return a stable CamelCase suffix derived from one entrypoint label."""
    parts = re.findall(r"[A-Za-z0-9]+", _normalize_entrypoint_name(entrypoint))
    return "".join(part.capitalize() for part in parts) or "Schema"


def _class_signature(node: ast.ClassDef) -> str:
    """Return one class definition's semantic signature for dedupe checks."""
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _rename_name_tokens(code: str, *, rename_map: dict[str, str]) -> str:
    """Rename Python identifiers without touching strings or comments."""
    if not rename_map:
        return code
    tokens: list[tokenize.TokenInfo] = []
    for token_info in tokenize.generate_tokens(io.StringIO(code).readline):
        replacement = token_info
        if token_info.type == tokenize.NAME and token_info.string in rename_map:
            replacement = tokenize.TokenInfo(
                type=token_info.type,
                string=rename_map[token_info.string],
                start=token_info.start,
                end=token_info.end,
                line=token_info.line,
            )
        tokens.append(replacement)
    return tokenize.untokenize(tokens)


def _resolve_body_class_conflicts(
    body: str,
    *,
    entrypoint: str,
    seen_signatures: dict[str, str],
    used_names: set[str],
) -> str:
    """Rename conflicting helper classes and drop byte-identical duplicates."""
    tree = ast.parse(body)
    drop_lines: set[int] = set()
    rename_map: dict[str, str] = {}
    suffix = _entrypoint_class_suffix(entrypoint)

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        signature = _class_signature(node)
        previous = seen_signatures.get(node.name)
        if previous is None:
            seen_signatures[node.name] = signature
            used_names.add(node.name)
            continue
        if previous == signature:
            end = node.end_lineno if node.end_lineno is not None else node.lineno
            drop_lines.update(range(node.lineno - 1, end))
            continue

        candidate = f"{node.name}{suffix}"
        counter = 2
        while candidate in used_names:
            candidate = f"{node.name}{suffix}{counter}"
            counter += 1
        rename_map[node.name] = candidate
        used_names.add(candidate)
        seen_signatures[candidate] = signature

    rewritten = _rename_name_tokens(body, rename_map=rename_map)
    if not drop_lines:
        return rewritten
    return "\n".join(
        line
        for index, line in enumerate(rewritten.splitlines())
        if index not in drop_lines
    )


def _dedupe_classes(code: str) -> str:
    """Keep only the first generated definition for duplicate class names."""
    seen_classes: set[str] = set()
    deduped: list[str] = []
    skip_body = False
    for line in code.splitlines():
        class_match = re.match(r"^class (\w+)\(", line)
        if class_match:
            class_name = class_match.group(1)
            if class_name in seen_classes:
                skip_body = True
                continue
            seen_classes.add(class_name)
            skip_body = False
        elif skip_body:
            if line.strip() == "" or (line and not line[0].isspace()):
                skip_body = False
            else:
                continue
        deduped.append(line)
    return "\n".join(deduped)


def _apply_python_transforms(code: str, *, target: CodegenTarget) -> str:
    """Apply configured Python post-processing transforms to generated code."""
    transforms = set(target.prepare.python_transforms)
    if PythonTransform.REWRITE_CONSTR_ANNOTATIONS in transforms:
        code = _rewrite_constr_type_hints(code)
    if PythonTransform.NORMALIZE_PYDANTIC_IMPORTS in transforms:
        code = _normalize_pydantic_imports(code)
    return code


def _map_generator_options(options: dict[str, object]) -> object:
    """Validate config values with upstream ``GenerateConfig`` directly."""
    config_mod = cast(
        "_DataModelCodeGeneratorConfigModule",
        _import_optional(
            "datamodel_code_generator.config",
            feature="schema code generation",
        ),
    )
    mapped = dict(options)
    mapped.pop("output", None)
    return config_mod.GenerateConfig.model_validate(mapped)


def _generate_models(
    *,
    entrypoint: str,
    generator_options: dict[str, object],
    schema: JsonValue,
) -> str:
    """Generate Python models for one fully-prepared schema entrypoint."""
    datamodel_code_generator = cast(
        "_DataModelCodeGeneratorModule",
        _import_optional("datamodel_code_generator", feature="schema code generation"),
    )
    config = _map_generator_options({
        **generator_options,
        "input_filename": f"{_normalize_entrypoint_name(entrypoint)}.json",
    })
    schema_json = json.dumps(schema, indent=2)
    result = datamodel_code_generator.generate(schema_json, config=config)
    if result is None:
        msg = f"codegen returned None for {entrypoint}"
        raise RuntimeError(msg)
    return result if isinstance(result, str) else str(result)


def generate_schema_codegen_target(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
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
        code = _generate_models(
            entrypoint=entrypoint,
            generator_options=resolved_target.generator_options,
            schema=schema,
        )
        imports, body = _collect_imports(_strip_generated_headers(code))
        body = _resolve_body_class_conflicts(
            body,
            entrypoint=entrypoint,
            seen_signatures=seen_class_signatures,
            used_names=used_class_names,
        )
        all_imports |= imports
        bodies.append(f"\n# === {_normalize_entrypoint_name(entrypoint)} ===\n{body}")

    header = (
        '"""Auto-generated Pydantic models from JSON schemas.\n\n'
        "DO NOT EDIT MANUALLY. Regenerate with:\n"
        f"    nixcfg schema generate {target_name}\n"
        '"""\n\n'
        "from __future__ import annotations\n"
    )
    rendered = f"{header}\n{_compose_imports_block(all_imports)}\n{''.join(bodies)}\n"
    rendered = _dedupe_classes(rendered)
    rendered = re.sub(r"\n{4,}", "\n\n\n", rendered)
    rendered = _apply_python_transforms(rendered, target=resolved_target.target)
    if not rendered.endswith("\n"):
        rendered = f"{rendered}\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    _emit_progress(progress, f"Wrote generated models to {output_path}.")
    return output_path
