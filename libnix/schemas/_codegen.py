"""Generate Pydantic models from vendored Nix JSON schemas.

Resolves all $ref cross-references using the `referencing` library,
then passes the fully-resolved schemas to `datamodel-code-generator`
as a library call to produce Pydantic v2 models.

Usage:
    python -m libnix.schemas._codegen
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import referencing
import referencing.jsonschema
import yaml
from datamodel_code_generator import DataModelType, generate
from datamodel_code_generator.config import GenerateConfig

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


def _load_yaml(name: str) -> dict[str, Any]:
    """Load a YAML schema file by base name (without .yaml extension)."""
    path = SCHEMAS_DIR / f"{name}.yaml"
    with path.open() as f:
        return yaml.safe_load(f)


def _build_registry() -> referencing.Registry[dict[str, Any]]:
    """Build a referencing.Registry containing all vendored schemas.

    Each schema is registered under its relative filename (e.g.,
    ``./hash-v1.yaml``) and its ``$id`` URI if present, so that both
    cross-file ``$ref`` styles resolve correctly.
    """
    resources: list[tuple[str, referencing.Resource[dict[str, Any]]]] = []

    for yaml_file in sorted(SCHEMAS_DIR.glob("*.yaml")):
        schema = _load_yaml(yaml_file.stem)
        # Create a resource from the schema using JSON Schema Draft 4
        # (which the Nix schemas declare via "$schema")
        resource = referencing.Resource.from_contents(
            schema, default_specification=referencing.jsonschema.DRAFT4
        )

        # Register under the relative path used in $ref values
        rel_name = f"./{yaml_file.name}"
        resources.append((rel_name, resource))

        # Also register under the bare filename (some refs omit ./)
        resources.append((yaml_file.name, resource))

        # Also register under the $id if present
        schema_id = schema.get("$id", "")
        if schema_id:
            resources.append((schema_id, resource))

    return referencing.Registry().with_resources(resources)


def _resolve_refs(
    schema: dict[str, Any], registry: referencing.Registry[dict[str, Any]]
) -> dict[str, Any]:
    """Recursively resolve all $ref pointers in a schema to produce a self-contained dict.

    Uses the registry for cross-file references and JSON pointer
    fragment resolution for intra-file references.
    """

    # Cache for loaded cross-file schemas (by URI part)
    file_cache: dict[str, dict[str, Any]] = {}

    def _load_remote(uri_part: str) -> dict[str, Any]:
        """Load a cross-file schema by its URI part, with caching."""
        if uri_part not in file_cache:
            resolved_resource = registry.resolver().lookup(uri_part)
            contents = resolved_resource.contents
            file_cache[uri_part] = (
                dict(contents) if isinstance(contents, dict) else contents
            )
        return file_cache[uri_part]

    def _walk_pointer(doc: Any, fragment: str) -> Any:
        """Walk a JSON pointer fragment (e.g., /$defs/foo) into a document."""
        parts = [p for p in fragment.split("/") if p]
        current = doc
        for part in parts:
            if isinstance(current, dict):
                current = current[part]
            else:
                msg = (
                    f"Cannot walk pointer /{'/'.join(parts)}: hit non-dict at {part!r}"
                )
                raise TypeError(msg)
        return current

    def _resolve(
        obj: Any, seen: set[str] | None = None, root: dict[str, Any] | None = None
    ) -> Any:
        if seen is None:
            seen = set()
        if root is None:
            root = schema

        if isinstance(obj, list):
            return [_resolve(item, seen, root) for item in obj]

        if not isinstance(obj, dict):
            return obj

        if "$ref" in obj:
            ref = obj["$ref"]

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
            extra = {k: v for k, v in obj.items() if k != "$ref"}
            if extra and isinstance(resolved, dict):
                resolved = {**resolved, **extra}

            return _resolve(resolved, seen, new_root)

        # Recurse into all dict values
        return {k: _resolve(v, seen, root) for k, v in obj.items()}

    return _resolve(schema)


def _fixup_schema(obj: Any) -> Any:
    """Fix schema patterns that datamodel-code-generator can't handle.

    - Replace {"const": null} with {"type": "null"} (codegen crashes on null literals)
    - Replace {"const": <value>} combined with a type with just the type + enum
    """
    if isinstance(obj, list):
        return [_fixup_schema(item) for item in obj]
    if not isinstance(obj, dict):
        return obj

    result = {k: _fixup_schema(v) for k, v in obj.items()}

    # Fix null const
    if result.get("const") is None and "const" in result:
        result.pop("const", None)
        result["type"] = "null"

    return result


def _generate_models(name: str, resolved_schema: dict[str, Any]) -> str:
    """Generate Pydantic v2 model code from a fully-resolved JSON schema."""
    resolved_schema = _fixup_schema(resolved_schema)
    schema_json = json.dumps(resolved_schema, indent=2)

    config = GenerateConfig(
        input_file_type="jsonschema",
        input_filename=f"{name}.yaml",
        output_model_type=DataModelType.PydanticV2BaseModel,
        use_annotated=True,
        use_standard_collections=True,
        use_union_operator=True,
        target_python_version="3.14",
        field_constraints=True,
        capitalise_enum_members=True,
        use_one_literal_as_default=True,
        use_default_kwarg=True,
    )
    result = generate(schema_json, config=config)
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
    print("Building schema registry...")
    registry = _build_registry()

    all_imports: set[str] = set()
    all_bodies: list[str] = []

    for name in TOP_LEVEL_SCHEMAS:
        print(f"  resolving {name}...")
        schema = _load_yaml(name)
        resolved = _resolve_refs(schema, registry)

        print(f"  generating models for {name}...")
        code = _generate_models(name, resolved)
        code = _strip_generated_headers(code)

        imports, body = _collect_imports(code)
        all_imports |= imports
        all_bodies.append(f"\n# === {name} ===\n{body}")

    # Compose final output
    header = '"""Auto-generated Pydantic models from Nix JSON schemas.\n\nDO NOT EDIT MANUALLY. Regenerate with:\n    python -m libnix.schemas._codegen\n"""\n\nfrom __future__ import annotations\n'

    # Deduplicate imports: merge "from X import A" and "from X import A, B"
    # by collecting all names per module
    from_imports: dict[str, set[str]] = {}
    bare_imports: set[str] = set()
    for imp in all_imports - {"from __future__ import annotations"}:
        if imp.startswith("from "):
            # "from X import A, B, C"
            parts = imp.split(" import ", 1)
            module = parts[0]  # "from X"
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
    print(f"\nWrote {OUTPUT_FILE}")
    print(f"  {len(TOP_LEVEL_SCHEMAS)} schemas processed")


if __name__ == "__main__":
    main()
