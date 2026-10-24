"""Internal schema loading, registry, and preparation helpers."""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlparse

import yaml

from lib import codegen_utils, json_utils
from lib.schema_codegen.config import (
    AliasStrategy,
    CodegenTarget,
    DereferenceMode,
    DirectorySource,
    LoadedSchemaCodegenConfig,
    RecursiveRefMode,
    ResourceMode,
    RetrieveKind,
    SchemaFormat,
    SchemaSource,
    SchemaTransform,
    URLSource,
)

type JsonValue = json_utils.JsonValue
type JsonObject = json_utils.JsonObject
type ReadURLSource = Callable[[URLSource], str]


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


type RegistryLike = _RegistryLike


@dataclass(frozen=True)
class _LoadedSchemaDocument:
    """One schema document loaded from one configured source."""

    basename: str | None
    contents: JsonValue
    label: str
    relative_path: str | None
    source_uri: str | None


_coerce_json_object = json_utils.coerce_json_object
_coerce_json_value = json_utils.coerce_json_value


_import_optional = codegen_utils.import_optional


def _parse_schema_text(text: str, *, fmt: SchemaFormat, context: str) -> JsonValue:
    """Deserialize one schema document and coerce it to JSON-compatible data."""
    loaded = json.loads(text) if fmt is SchemaFormat.JSON else yaml.safe_load(text)
    return _coerce_json_value(loaded, context=context)


def _load_source_documents(
    source: SchemaSource,
    *,
    read_url_source: ReadURLSource,
) -> tuple[_LoadedSchemaDocument, ...]:
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

    text = read_url_source(source)
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


def build_registry(
    loaded: LoadedSchemaCodegenConfig,
    *,
    target: CodegenTarget,
    read_url_source: ReadURLSource,
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
        for document in _load_source_documents(
            source,
            read_url_source=read_url_source,
        ):
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
                    msg = f"Registry alias {uri!r} collides between {existing} and {document.label}"
                    raise RuntimeError(msg)
                seen_uris[uri] = document.label
                resources.append((uri, resource))

    registry = registry.with_resources(resources)
    return registry.crawl() if profile.registry.crawl else registry


def _merge_allof_extras(result: JsonObject, branch: JsonObject) -> None:
    excluded = frozenset({"properties", "required", "title", "description"})
    for key, value in branch.items():
        if key in excluded or key in result:
            continue
        result[key] = value


def _merge_allof_properties(result: JsonObject, merged: JsonObject) -> bool:
    """Merge properties only when their intersection needs no schema rewriting."""
    if not merged:
        return True
    existing = result.get("properties")
    if existing is not None and not isinstance(existing, dict):
        return False
    properties: JsonObject = (
        _coerce_json_object(existing, context="schema properties")
        if isinstance(existing, dict)
        else {}
    )
    for key, value in merged.items():
        if key not in properties or properties[key] == {}:
            properties[key] = value
        elif value == {} or value == properties[key]:
            continue
        else:
            return False
    result["properties"] = properties
    return True


def _merge_allof_required(result: JsonObject, merged: list[str]) -> None:
    if not merged:
        return
    existing = result.get("required")
    required: list[JsonValue] = []
    if isinstance(existing, list):
        required.extend(item for item in existing if isinstance(item, str))
    seen = {item for item in required if isinstance(item, str)}
    for item in merged:
        if item in seen:
            continue
        required.append(item)
        seen.add(item)
    result["required"] = required


def _collect_allof_merge(
    all_of: list[JsonValue],
) -> tuple[JsonObject, list[str], list[JsonValue]] | None:
    """Collect a flat merge plan, or return ``None`` when it is not provably safe."""
    mergeable_keys = frozenset({
        "description",
        "properties",
        "required",
        "title",
        "type",
    })
    merged_properties: JsonObject = {}
    merged_required: list[str] = []
    merged_types: list[JsonValue] = []
    for branch in all_of:
        if not isinstance(branch, dict):
            return None
        branch_dict = _coerce_json_object(branch, context="allOf branch")
        if any(key not in mergeable_keys for key in branch_dict):
            return None
        props_obj = branch_dict.get("properties")
        if not isinstance(props_obj, dict):
            return None
        required_obj = branch_dict.get("required", [])
        if not isinstance(required_obj, list) or any(
            not isinstance(item, str) for item in required_obj
        ):
            return None
        if "type" in branch_dict:
            merged_types.append(branch_dict["type"])
        merged_required.extend(item for item in required_obj if isinstance(item, str))
        for key, value in props_obj.items():
            if key not in merged_properties or merged_properties[key] == {}:
                merged_properties[key] = value
            elif value == {} or value == merged_properties[key]:
                continue
            else:
                return None
    return merged_properties, merged_required, merged_types


def _merge_allof_branches(result: JsonObject) -> None:
    """Inline only ``allOf`` intersections with a semantics-preserving flat form."""
    all_of = result.get("allOf")
    if not isinstance(all_of, list):
        return

    existing_required = result.get("required")
    if existing_required is not None and (
        not isinstance(existing_required, list)
        or any(not isinstance(item, str) for item in existing_required)
    ):
        return

    merge_plan = _collect_allof_merge(all_of)
    if merge_plan is None:
        return
    merged_properties, merged_required, merged_types = merge_plan
    if merged_types and (
        any(value != merged_types[0] for value in merged_types[1:])
        or ("type" in result and result["type"] != merged_types[0])
    ):
        return

    existing_properties = result.get("properties")
    if isinstance(existing_properties, dict):
        new_properties = merged_properties.keys() - existing_properties.keys()
    else:
        new_properties = merged_properties.keys()
    property_sensitive_keys = frozenset({
        "additionalProperties",
        "patternProperties",
        "unevaluatedProperties",
    })
    if new_properties and any(key in result for key in property_sensitive_keys):
        return

    if not _merge_allof_properties(result, merged_properties):
        return
    _merge_allof_required(result, merged_required)
    for branch in all_of:
        _merge_allof_extras(
            result,
            _coerce_json_object(branch, context="allOf branch"),
        )
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
        if not (drop_description and key == "description")
    }

    if SchemaTransform.INLINE_MERGEABLE_ALLOF in transforms:
        _merge_allof_branches(result)
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
    preserve_root_ref: bool,
    recursive_ref: RecursiveRefMode,
    resolver: _ResolverLike,
    stack: tuple[str, ...],
) -> JsonValue:
    """Inline ``$ref`` references using ``referencing``'s resolver primitives."""
    if isinstance(obj, list):
        return [
            _inline_references(
                item,
                merge_ref_siblings=merge_ref_siblings,
                preserve_root_ref=preserve_root_ref,
                recursive_ref=recursive_ref,
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
                preserve_root_ref=preserve_root_ref,
                recursive_ref=recursive_ref,
                resolver=resolver,
                stack=stack,
            )
            for key, value in obj.items()
        }
    if ref_obj == "#" and preserve_root_ref:
        return _coerce_json_value(obj, context="preserved recursive schema ref")

    resolved = resolver.lookup(ref_obj)
    resolved_contents = _coerce_json_value(
        resolved.contents,
        context=f"resolved schema {ref_obj}",
    )
    if ref_obj in stack:
        if recursive_ref is RecursiveRefMode.AS_OBJECT:
            return {"type": "object"}
        msg = f"Recursive $ref not supported during inline dereference: {ref_obj}"
        raise RuntimeError(msg)

    merged: JsonValue = resolved_contents
    if merge_ref_siblings and isinstance(resolved_contents, dict):
        extras = {key: value for key, value in obj.items() if key != "$ref"}
        merged = {**resolved_contents, **extras}

    return _inline_references(
        merged,
        merge_ref_siblings=merge_ref_siblings,
        preserve_root_ref=preserve_root_ref,
        recursive_ref=recursive_ref,
        resolver=resolved.resolver,
        stack=(*stack, ref_obj),
    )


def prepare_entrypoint_schema(
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
            preserve_root_ref=target.prepare.preserve_root_ref,
            recursive_ref=target.prepare.recursive_ref,
            resolver=registry.resolver_with_root(resource),
            stack=(),
        )
    if target.prepare.schema_transforms:
        contents = _apply_schema_transforms(
            contents,
            transforms=target.prepare.schema_transforms,
        )
    return contents
