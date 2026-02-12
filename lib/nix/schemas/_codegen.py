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
from pathlib import Path
from typing import Any, Protocol, cast

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
        return yaml.safe_load(f)


def _build_registry() -> object:
    """Build a referencing.Registry containing all vendored schemas.

    Each schema is registered under its relative filename (e.g.,
    ``./hash-v1.yaml``) and its ``$id`` URI if present, so that both
    cross-file ``$ref`` styles resolve correctly.
    """
    referencing = importlib.import_module("referencing")
    referencing_jsonschema = importlib.import_module("referencing.jsonschema")

    resources: list[tuple[str, object]] = []

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

    typed_resources = cast("list[tuple[str, Any]]", resources)
    return referencing.Registry().with_resources(typed_resources)


def _walk_pointer(doc: object, fragment: str) -> object:
    """Walk a JSON pointer fragment (e.g., /$defs/foo) into a document."""
    parts = [p for p in fragment.split("/") if p]
    current = doc
    for part in parts:
        if isinstance(current, dict):
            current_dict = cast("dict[str, object]", current)
            current = current_dict[part]
            continue
        msg = f"Cannot walk pointer /{'/'.join(parts)}: hit non-dict at {part!r}"
        raise TypeError(msg)
    return current


def _resolve_refs(schema: JsonObject, registry: object) -> JsonObject:  # noqa: C901, PLR0915
    """Recursively resolve all $ref pointers in a schema to produce a self-contained dict.

    Uses the registry for cross-file references and JSON pointer
    fragment resolution for intra-file references.
    """
    # Cache for loaded cross-file schemas (by URI part)
    file_cache: dict[str, JsonObject] = {}

    if not hasattr(registry, "resolver"):
        msg = "Invalid schema registry instance"
        raise TypeError(msg)
    typed_registry = cast("_RegistryLike", registry)

    def _load_remote(uri_part: str) -> JsonObject:
        """Load a cross-file schema by its URI part, with caching."""
        if uri_part not in file_cache:
            resolved_resource = typed_registry.resolver().lookup(uri_part)
            contents = resolved_resource.contents
            if not isinstance(contents, dict):
                msg = f"Referenced schema {uri_part!r} is not an object"
                raise TypeError(msg)
            file_cache[uri_part] = cast("JsonObject", contents)
        return file_cache[uri_part]

    def _resolve(  # noqa: C901, PLR0912
        obj: object,
        seen: set[str] | None = None,
        root: JsonObject | None = None,
    ) -> object:
        if seen is None:
            seen = set()
        if root is None:
            root = schema

        if isinstance(obj, list):
            return [_resolve(item, seen, root) for item in obj]

        if not isinstance(obj, dict):
            return obj

        obj_dict = cast("dict[str, object]", obj)

        if "$ref" in obj_dict:
            ref_obj = obj_dict["$ref"]
            if not isinstance(ref_obj, str):
                msg = "$ref value must be a string"
                raise TypeError(msg)
            ref = ref_obj

            # Prevent infinite recursion on circular refs
            if ref in seen:
                return {"type": "object", "description": f"Circular ref: {ref}"}
            seen = seen | {ref}

            # Split into URI part and fragment
            if "#" in ref:
                uri_part, fragment = ref.split("#", 1)
            else:
                uri_part = ref
                fragment = ""

            if uri_part:
                # Cross-file reference: load the remote schema
                remote_schema = _load_remote(uri_part)
                new_root = remote_schema  # switch root context
                resolved = (
                    _walk_pointer(remote_schema, fragment)
                    if fragment
                    else remote_schema
                )
            elif fragment:
                # Intra-file fragment reference (e.g., #/$defs/foo)
                # Use current root (which may be a remote schema we jumped into)
                resolved = _walk_pointer(root, fragment)
                new_root = root
            else:
                return obj

            # Ensure we have a mutable copy
            if isinstance(resolved, dict):
                resolved = dict(resolved)

            # Merge any sibling keys (e.g., title, description alongside $ref)
            extra = {k: v for k, v in obj_dict.items() if k != "$ref"}
            if extra and isinstance(resolved, dict):
                resolved = {**resolved, **extra}

            return _resolve(resolved, seen, new_root)

        # Recurse into all dict values
        return {k: _resolve(v, seen, root) for k, v in obj_dict.items()}

    resolved_root = _resolve(schema)
    if not isinstance(resolved_root, dict):
        msg = "Resolved schema root must be an object"
        raise TypeError(msg)
    return cast("JsonObject", resolved_root)


def _merge_allof_branches(result: dict[str, object]) -> None:  # noqa: C901, PLR0912
    """Inline ``allOf`` object branches into the parent schema object."""
    all_of = result.get("allOf")
    if not isinstance(all_of, list):
        return

    merged_props: dict[str, object] = {}
    merged_required: list[str] = []
    remaining: list[object] = []

    for branch_obj in all_of:
        if not isinstance(branch_obj, dict):
            remaining.append(branch_obj)
            continue
        branch = cast("dict[str, object]", branch_obj)

        if "properties" not in branch:
            remaining.append(branch)
            continue

        branch_props_obj = branch.get("properties")
        if isinstance(branch_props_obj, dict):
            merged_props.update(cast("dict[str, object]", branch_props_obj))

        branch_req_obj = branch.get("required")
        if isinstance(branch_req_obj, list):
            merged_required.extend(
                req for req in branch_req_obj if isinstance(req, str)
            )

        for key, value in branch.items():
            if (
                key not in ("properties", "required", "type", "title", "description")
                and key not in result
            ):
                result[key] = value

    if merged_props:
        existing_props_obj = result.get("properties")
        if isinstance(existing_props_obj, dict):
            existing_props = cast("dict[str, object]", existing_props_obj)
        else:
            existing_props = {}
        for key, value in merged_props.items():
            if key not in existing_props:
                existing_props[key] = value
        result["properties"] = existing_props

    if merged_required:
        existing_req_obj = result.get("required")
        if isinstance(existing_req_obj, list):
            existing_req = [req for req in existing_req_obj if isinstance(req, str)]
        else:
            existing_req = []
        seen = set(existing_req)
        for requirement in merged_required:
            if requirement not in seen:
                existing_req.append(requirement)
                seen.add(requirement)
        result["required"] = existing_req

    if remaining:
        result["allOf"] = remaining
    else:
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
    if not isinstance(fixed_schema, dict):
        msg = f"Fixed schema for {name} is not an object"
        raise TypeError(msg)
    resolved_schema = cast("JsonObject", fixed_schema)
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


def main() -> None:
    registry = _build_registry()

    all_imports: set[str] = set()
    all_bodies: list[str] = []

    for name in TOP_LEVEL_SCHEMAS:
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

    # Deduplicate imports by collecting imported names per module.
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
    for module in sorted(from_imports):
        names = ", ".join(sorted(from_imports[module]))
        import_lines.append(f"{module} import {names}")
    imports_block = "\n".join(import_lines)

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

    OUTPUT_FILE.write_text(final_code)


if __name__ == "__main__":
    main()
