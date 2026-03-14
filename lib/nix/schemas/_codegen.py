"""Generate Pydantic models from vendored Nix JSON schemas.

Resolves all $ref cross-references using the `referencing` library,
then passes the fully-resolved schemas to `datamodel-code-generator`
as a library call to produce Pydantic v2 models.

Usage:
    python -m lib.nix.schemas._codegen
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeGuard

import yaml

SCHEMAS_DIR = Path(__file__).resolve().parent
MODELS_DIR = SCHEMAS_DIR.parent / "models"
OUTPUT_FILE = MODELS_DIR / "_generated.py"

# Schemas to generate models for (order matters for readability of output).
# We skip store-v1 as it's a composite test-only schema.
TOP_LEVEL_SCHEMAS = [
    "hash-v1",
    "store-path-v1",
    "content-address-v1",
    "file-system-object-v1",
    "build-trace-entry-v2",
    "build-result-v1",
    "deriving-path-v1",
    "derivation-v4",
    "derivation-options-v1",
    "store-object-info-v2",
]

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type ProgressReporter = Callable[[str], None]


def _as_object_dict(value: object, *, context: str) -> dict[str, object]:
    """Return *value* as ``dict[str, object]`` or raise ``TypeError``."""
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
    """Convert *value* to :data:`JsonValue` recursively."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_coerce_json_value(item, context=f"{context}[]") for item in value]
    obj = _as_object_dict(value, context=context)
    return {
        key: _coerce_json_value(item, context=f"{context}.{key}")
        for key, item in obj.items()
    }


def _coerce_json_object(value: object, *, context: str) -> JsonObject:
    """Convert *value* to :data:`JsonObject` or raise ``TypeError``."""
    json_value = _coerce_json_value(value, context=context)
    if not isinstance(json_value, dict):
        msg = f"Expected JSON object for {context}, got {type(json_value).__name__}"
        raise TypeError(msg)
    return json_value


def _emit_progress(progress: ProgressReporter | None, message: str) -> None:
    """Invoke the optional codegen progress reporter."""
    if progress is None:
        return
    progress(message)


def _is_registry_like(value: object) -> TypeGuard[_RegistryLike]:
    """Return ``True`` when *value* exposes a ``resolver()`` method."""
    return hasattr(value, "resolver")


class _ResolvedResourceLike(Protocol):
    contents: object


class _ResolverLike(Protocol):
    def lookup(self, uri: str) -> _ResolvedResourceLike: ...


class _RegistryLike(Protocol):
    def resolver(self) -> _ResolverLike: ...


def _load_yaml(name: str) -> JsonObject:
    """Load a YAML schema file by base name (without .yaml extension)."""
    path = SCHEMAS_DIR / f"{name}.yaml"
    with path.open() as f:
        return _coerce_json_object(yaml.safe_load(f), context=f"schema {path.name}")


def _build_registry() -> object:
    """Build a referencing.Registry containing all vendored schemas.

    Each schema is registered under its relative filename (e.g.,
    ``./hash-v1.yaml``) and its ``$id`` URI if present, so that both
    cross-file ``$ref`` styles resolve correctly.
    """
    referencing = importlib.import_module("referencing")
    referencing_jsonschema = importlib.import_module("referencing.jsonschema")

    resources: list[tuple[str, Any]] = []

    for yaml_file in sorted(SCHEMAS_DIR.glob("*.yaml")):
        schema = _load_yaml(yaml_file.stem)
        # Create a resource from the schema using JSON Schema Draft 4
        # (which the Nix schemas declare via "$schema")
        resource = referencing.Resource.from_contents(
            schema,
            default_specification=referencing_jsonschema.DRAFT4,
        )

        # Register under the relative path used in $ref values
        rel_name = f"./{yaml_file.name}"
        resources.append((rel_name, resource))

        # Also register under the bare filename (some refs omit ./)
        resources.append((yaml_file.name, resource))

        # Also register under the $id if present
        schema_id = schema.get("$id")
        if isinstance(schema_id, str) and schema_id:
            resources.append((schema_id, resource))

    return referencing.Registry().with_resources(resources)


def _walk_pointer(doc: object, fragment: str) -> object:
    """Walk a JSON pointer fragment (e.g., /$defs/foo) into a document."""
    parts = [p for p in fragment.split("/") if p]
    current = doc
    for part in parts:
        if isinstance(current, dict):
            current_dict = _as_object_dict(current, context=f"pointer {fragment}")
            current = current_dict[part]
            continue
        msg = f"Cannot walk pointer /{'/'.join(parts)}: hit non-dict at {part!r}"
        raise TypeError(msg)
    return current


@dataclass
class _SchemaRefResolver:
    schema: JsonObject
    registry: _RegistryLike
    file_cache: dict[str, JsonObject] = field(default_factory=dict)

    def _load_remote_schema(self, uri_part: str) -> JsonObject:
        cached = self.file_cache.get(uri_part)
        if cached is not None:
            return cached
        resolved_resource = self.registry.resolver().lookup(uri_part)
        loaded = _coerce_json_object(
            resolved_resource.contents,
            context=f"referenced schema {uri_part}",
        )
        self.file_cache[uri_part] = loaded
        return loaded

    @staticmethod
    def _split_ref(ref: str) -> tuple[str, str]:
        if "#" in ref:
            uri_part, fragment = ref.split("#", 1)
            return uri_part, fragment
        return ref, ""

    @staticmethod
    def _merge_ref_siblings(resolved: object, obj_dict: dict[str, object]) -> object:
        extra = {key: value for key, value in obj_dict.items() if key != "$ref"}
        if extra and isinstance(resolved, dict):
            return {**resolved, **extra}
        return resolved

    def _resolve_ref_target(
        self,
        ref: str,
        *,
        root: JsonObject,
    ) -> tuple[object, JsonObject] | None:
        uri_part, fragment = self._split_ref(ref)
        if uri_part:
            remote_schema = self._load_remote_schema(uri_part)
            resolved = (
                _walk_pointer(remote_schema, fragment) if fragment else remote_schema
            )
            return resolved, remote_schema
        if fragment:
            return _walk_pointer(root, fragment), root
        return None

    def _resolve_ref(
        self,
        *,
        ref: str,
        obj_dict: dict[str, object],
        seen: set[str],
        root: JsonObject,
    ) -> object:
        if ref in seen:
            return {"type": "object", "description": f"Circular ref: {ref}"}
        target = self._resolve_ref_target(ref, root=root)
        if target is None:
            return obj_dict
        resolved, new_root = target
        if isinstance(resolved, dict):
            resolved = dict(resolved)
        merged = self._merge_ref_siblings(resolved, obj_dict)
        return self.resolve(merged, seen=seen | {ref}, root=new_root)

    def resolve(self, obj: object, *, seen: set[str], root: JsonObject) -> object:
        if isinstance(obj, list):
            return [self.resolve(item, seen=seen, root=root) for item in obj]

        if not isinstance(obj, dict):
            return obj

        obj_dict = _as_object_dict(obj, context="schema object")
        ref_obj = obj_dict.get("$ref")
        if ref_obj is None:
            return {
                key: self.resolve(value, seen=seen, root=root)
                for key, value in obj_dict.items()
            }
        if not isinstance(ref_obj, str):
            msg = "$ref value must be a string"
            raise TypeError(msg)
        return self._resolve_ref(ref=ref_obj, obj_dict=obj_dict, seen=seen, root=root)


def _resolve_refs(schema: JsonObject, registry: object) -> JsonObject:
    """Recursively resolve all $ref pointers in a schema to produce a self-contained dict.

    Uses the registry for cross-file references and JSON pointer
    fragment resolution for intra-file references.
    """
    if not _is_registry_like(registry):
        msg = "Invalid schema registry instance"
        raise TypeError(msg)

    resolver = _SchemaRefResolver(schema=schema, registry=registry)
    resolved_root = resolver.resolve(schema, seen=set(), root=schema)
    return _coerce_json_object(resolved_root, context="resolved schema root")


_ALL_OF_EXCLUDED_KEYS = frozenset({
    "properties",
    "required",
    "type",
    "title",
    "description",
})


def _parse_mergeable_allof_branch(
    branch_obj: object,
) -> tuple[dict[str, object], dict[str, object], list[str]] | None:
    if not isinstance(branch_obj, dict):
        return None
    branch = _as_object_dict(branch_obj, context="allOf branch")
    props_obj = branch.get("properties")
    if not isinstance(props_obj, dict):
        return None
    props = _as_object_dict(props_obj, context="allOf branch properties")
    required_obj = branch.get("required")
    required = (
        [req for req in required_obj if isinstance(req, str)]
        if isinstance(required_obj, list)
        else []
    )
    return branch, props, required


def _merge_allof_extras(result: dict[str, object], branch: dict[str, object]) -> None:
    for key, value in branch.items():
        if key in _ALL_OF_EXCLUDED_KEYS or key in result:
            continue
        result[key] = value


def _merge_allof_properties(
    result: dict[str, object],
    merged_props: dict[str, object],
) -> None:
    if not merged_props:
        return
    existing_props_obj = result.get("properties")
    existing_props = (
        _as_object_dict(existing_props_obj, context="schema properties")
        if isinstance(existing_props_obj, dict)
        else {}
    )
    for key, value in merged_props.items():
        if key not in existing_props:
            existing_props[key] = value
    result["properties"] = existing_props


def _merge_allof_required(
    result: dict[str, object], merged_required: list[str]
) -> None:
    if not merged_required:
        return
    existing_req_obj = result.get("required")
    existing_req = (
        [req for req in existing_req_obj if isinstance(req, str)]
        if isinstance(existing_req_obj, list)
        else []
    )
    seen = set(existing_req)
    for requirement in merged_required:
        if requirement in seen:
            continue
        existing_req.append(requirement)
        seen.add(requirement)
    result["required"] = existing_req


def _merge_allof_branches(result: dict[str, object]) -> None:
    """Inline ``allOf`` object branches into the parent schema object."""
    all_of = result.get("allOf")
    if not isinstance(all_of, list):
        return

    merged_props: dict[str, object] = {}
    merged_required: list[str] = []
    remaining: list[object] = []

    for branch_obj in all_of:
        parsed = _parse_mergeable_allof_branch(branch_obj)
        if parsed is None:
            remaining.append(branch_obj)
            continue
        branch, branch_props, branch_required = parsed
        merged_props.update(branch_props)
        merged_required.extend(branch_required)
        _merge_allof_extras(result, branch)

    _merge_allof_properties(result, merged_props)
    _merge_allof_required(result, merged_required)

    if remaining:
        result["allOf"] = remaining
        return
    result.pop("allOf", None)


def _fixup_schema(obj: object) -> object:
    """Fix schema patterns that datamodel-code-generator can't handle.

    - Inline ``allOf`` branches into the parent object to avoid duplicate fields
    - Replace {"const": null} with {"type": "null"} (codegen crashes on null literals)
    - Replace {"const": <value>} combined with a type with just the type + enum
    - Drop schema descriptions to keep generated models lint-friendly
    """
    if isinstance(obj, list):
        return [_fixup_schema(item) for item in obj]
    if not isinstance(obj, dict):
        return obj

    result: dict[str, object] = {
        k: _fixup_schema(v)
        for k, v in obj.items()
        if isinstance(k, str) and k != "description"
    }

    # Merge allOf branches into the parent object so datamodel-code-generator
    # does not see duplicated property definitions.
    _merge_allof_branches(result)

    # Fix null const
    if result.get("const") is None and "const" in result:
        result.pop("const", None)
        result["type"] = "null"

    return result


def _generate_models(name: str, resolved_schema: JsonObject) -> str:
    """Generate Pydantic v2 model code from a fully-resolved JSON schema."""
    datamodel_code_generator = importlib.import_module("datamodel_code_generator")
    datamodel_code_generator_config = importlib.import_module(
        "datamodel_code_generator.config",
    )

    fixed_schema = _fixup_schema(resolved_schema)
    resolved_schema = _coerce_json_object(fixed_schema, context=f"fixed schema {name}")
    schema_json = json.dumps(resolved_schema, indent=2)

    datamodel_code_generator_format = importlib.import_module(
        "datamodel_code_generator.format",
    )
    formatter_cls = datamodel_code_generator_format.Formatter

    config = datamodel_code_generator_config.GenerateConfig(
        input_file_type=datamodel_code_generator.InputFileType.JsonSchema,
        input_filename=f"{name}.yaml",
        output_model_type=datamodel_code_generator.DataModelType.PydanticV2BaseModel,
        use_annotated=True,
        use_standard_collections=True,
        use_union_operator=True,
        target_python_version=datamodel_code_generator.PythonVersion.PY_314,
        field_constraints=True,
        snake_case_field=True,
        use_field_description=False,
        use_schema_description=False,
        capitalise_enum_members=True,
        use_one_literal_as_default=True,
        use_default_kwarg=True,
        formatters=[formatter_cls.RUFF_FORMAT, formatter_cls.RUFF_CHECK],
    )
    result = datamodel_code_generator.generate(schema_json, config=config)
    if result is None:
        msg = f"codegen returned None for {name}"
        raise RuntimeError(msg)
    return result if isinstance(result, str) else str(result)


def _strip_generated_headers(code: str) -> str:
    """Remove per-schema headers so we can combine into one file."""
    lines = code.splitlines()
    filtered = []
    for line in lines:
        if line.startswith("# generated by datamodel-codegen"):
            continue
        if line.startswith("#   filename:"):
            continue
        if line.startswith("#   timestamp:"):
            continue
        filtered.append(line)
    return "\n".join(filtered)


def _collect_imports(code: str) -> tuple[set[str], str]:
    """Extract import lines and return (imports, remaining_code)."""
    imports: set[str] = set()
    body_lines: list[str] = []
    for line in code.splitlines():
        if line.startswith(("from ", "import ")):
            imports.add(line)
        else:
            body_lines.append(line)
    return imports, "\n".join(body_lines)


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


def _import_module_sort_key(module: str) -> tuple[int, str]:
    """Sort stdlib imports before third-party imports."""
    module_name = module.removeprefix("from ").split(" import", maxsplit=1)[0]
    if module_name.startswith("pydantic"):
        return (1, module_name)
    return (0, module_name)


def _compose_imports_block(all_imports: set[str]) -> str:
    """Build a deduplicated import block with grouped stdlib/third-party imports."""
    from_imports: dict[str, set[str]] = {}
    bare_imports: set[str] = set()
    for imp in all_imports - {"from __future__ import annotations"}:
        if imp.startswith("from "):
            parts = imp.split(" import ", 1)
            module = parts[0]
            names = {n.strip() for n in parts[1].split(",")}
            from_imports.setdefault(module, set()).update(names)
        else:
            bare_imports.add(imp)

    import_lines: list[str] = sorted(bare_imports)
    previous_group: int | None = None
    for module in sorted(from_imports, key=_import_module_sort_key):
        group, _module_name = _import_module_sort_key(module)
        if (
            previous_group is not None
            and group != previous_group
            and import_lines
            and import_lines[-1] != ""
        ):
            import_lines.append("")
        names = ", ".join(sorted(from_imports[module]))
        import_lines.append(f"{module} import {names}")
        previous_group = group
    return "\n".join(import_lines)


def main(*, progress: ProgressReporter | None = None) -> None:
    _emit_progress(progress, "Building schema registry.")
    registry = _build_registry()

    all_imports: set[str] = set()
    all_bodies: list[str] = []
    total = len(TOP_LEVEL_SCHEMAS)
    _emit_progress(progress, f"Generating models for {total} top-level schema(s).")

    for index, name in enumerate(TOP_LEVEL_SCHEMAS, start=1):
        _emit_progress(progress, f"Processing {index}/{total}: {name}")
        schema = _load_yaml(name)
        resolved = _resolve_refs(schema, registry)

        code = _generate_models(name, resolved)
        code = _strip_generated_headers(code)

        imports, body = _collect_imports(code)
        all_imports |= imports
        all_bodies.append(f"\n# === {name} ===\n{body}")

    # Compose final output
    header = (
        '"""Auto-generated Pydantic models from Nix JSON schemas.\n\n'
        "DO NOT EDIT MANUALLY. Regenerate with:\n"
        "    python -m lib.nix.schemas._codegen\n"
        '"""\n\n'
        "from __future__ import annotations\n"
    )

    imports_block = _compose_imports_block(all_imports)

    final_code = f"{header}\n{imports_block}\n{''.join(all_bodies)}\n"

    # Deduplicate class definitions: keep first occurrence of each class name.
    # This handles cases like Method being generated from multiple schemas.
    seen_classes: set[str] = set()
    deduped_lines: list[str] = []
    skip_until_next_class = False
    for line in final_code.splitlines():
        class_match = re.match(r"^class (\w+)\(", line)
        if class_match:
            class_name = class_match.group(1)
            if class_name in seen_classes:
                skip_until_next_class = True
                continue
            seen_classes.add(class_name)
            skip_until_next_class = False
        elif skip_until_next_class:
            # Skip body lines of duplicate class; stop at next blank or class line
            if line.strip() == "" or (line and not line[0].isspace()):
                skip_until_next_class = False
                # Don't skip this line (it's the boundary)
            else:
                continue
        deduped_lines.append(line)
    final_code = "\n".join(deduped_lines)

    # Clean up excessive blank lines
    final_code = re.sub(r"\n{4,}", "\n\n\n", final_code)

    # Ty does not permit call expressions in type positions.
    # Convert generated ``constr(pattern=...)`` annotations to
    # ``Annotated[str, StringConstraints(...)]``.
    final_code = _rewrite_constr_type_hints(final_code)
    final_code = _normalize_pydantic_imports(final_code)
    if not final_code.endswith("\n"):
        final_code = f"{final_code}\n"

    _emit_progress(progress, f"Writing generated models to {OUTPUT_FILE}.")
    OUTPUT_FILE.write_text(final_code)
    _emit_progress(progress, "Schema codegen complete.")


if __name__ == "__main__":  # pragma: no cover
    main()
