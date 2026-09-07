"""Normalize generated crate2nix output and repair reviewed GitButler graph edges."""

from typing import TYPE_CHECKING, cast

from nix_manipulator import parse
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.cargo_nix_normalizer import normalize as normalize_cargo_nix

if TYPE_CHECKING:
    from tree_sitter import Node


_REGISTRY_SOURCE_DISAMBIGUATOR = "crate2nix-source-registry"
_REGISTRY_PACKAGES = (
    "registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18",
    "registry+https://github.com/rust-lang/crates.io-index#gix-validate@0.11.2",
)


def _binding(attributes: AttributeSet, name: str) -> Binding | None:
    return next(
        (
            item
            for item in attributes.values
            if isinstance(item, Binding) and item.name in {name, f'"{name}"'}
        ),
        None,
    )


def _attribute_set(binding: Binding) -> AttributeSet:
    if not isinstance(binding.value, AttributeSet):
        msg = f"Expected GitButler {binding.name} to be an attribute set"
        raise TypeError(msg)
    return binding.value


def _string_list(binding: Binding) -> NixList:
    if not isinstance(binding.value, NixList) or not all(
        isinstance(item, StringPrimitive) for item in binding.value.value
    ):
        msg = f"Expected GitButler {binding.name} to be a string list"
        raise TypeError(msg)
    return binding.value


def _add_feature(attributes: AttributeSet, name: str) -> bool:
    binding = _binding(attributes, name)
    if binding is None:
        attributes[name] = NixList(
            value=[StringPrimitive(value=_REGISTRY_SOURCE_DISAMBIGUATOR)]
        )
        return True
    values = _string_list(binding).value
    if any(
        isinstance(item, StringPrimitive)
        and item.value == _REGISTRY_SOURCE_DISAMBIGUATOR
        for item in values
    ):
        return False
    values.insert(0, StringPrimitive(value=_REGISTRY_SOURCE_DISAMBIGUATOR))
    return True


def _dependencies(crate: AttributeSet, name: str) -> list[AttributeSet]:
    binding = _binding(crate, name)
    if binding is None:
        return []
    if not isinstance(binding.value, NixList) or not all(
        isinstance(item, AttributeSet) for item in binding.value.value
    ):
        msg = f"Expected GitButler {name} to be a list of dependency sets"
        raise ValueError(msg)
    return cast("list[AttributeSet]", binding.value.value)


def _is_package(dependency: AttributeSet, package_id: str) -> bool:
    binding = _binding(dependency, "packageId")
    return (
        binding is not None
        and isinstance(binding.value, StringPrimitive)
        and binding.value.value == package_id
    )


def _repair_graph(crates: AttributeSet) -> bool:
    changed = False
    for package_id in _REGISTRY_PACKAGES:
        package = _binding(crates, package_id)
        if package is None:
            continue
        attributes = _attribute_set(package)
        features = _binding(attributes, "features")
        if features is None:
            feature_set = AttributeSet(values=[])
            attributes["features"] = feature_set
        else:
            feature_set = _attribute_set(features)
        marker = _binding(feature_set, _REGISTRY_SOURCE_DISAMBIGUATOR)
        if marker is None:
            feature_set[f'"{_REGISTRY_SOURCE_DISAMBIGUATOR}"'] = NixList(value=[])
            changed = True
        elif _string_list(marker).value:
            msg = "GitButler source-disambiguation feature must have no dependencies"
            raise ValueError(msg)
        changed = _add_feature(attributes, "resolvedDefaultFeatures") or changed

        for crate in crates.values:
            if not isinstance(crate, Binding):
                continue
            for name in ("dependencies", "buildDependencies", "devDependencies"):
                for dependency in _dependencies(_attribute_set(crate), name):
                    if _is_package(dependency, package_id):
                        changed = _add_feature(dependency, "features") or changed

    tauri = _binding(crates, "gitbutler-tauri")
    if tauri is not None:
        attributes = _attribute_set(tauri)
        dependencies = _binding(attributes, "dependencies")
        if dependencies is None:
            msg = "GitButler gitbutler-tauri is missing its dependencies list"
            raise ValueError(msg)
        items = _dependencies(attributes, "dependencies")
        if not any(_is_package(item, "but") for item in items):
            items.insert(
                0,
                AttributeSet(
                    values=[
                        Binding(name="name", value=StringPrimitive(value="but")),
                        Binding(name="packageId", value=StringPrimitive(value="but")),
                        Binding(name="optional", value=Primitive(value=True)),
                    ]
                ),
            )
            changed = True
    return changed


def _binding_node(container: Node, name: str) -> Node | None:
    if container.type not in {"attrset_expression", "rec_attrset_expression"}:
        msg = "Expected GitButler Cargo.nix to return an attribute set"
        raise ValueError(msg)
    bindings = next(
        (child for child in container.named_children if child.type == "binding_set"),
        None,
    )
    if bindings is None:
        return None
    for child in bindings.named_children:
        attribute = child.child_by_field_name("attrpath")
        if attribute is not None and attribute.text in {
            name.encode(),
            f'"{name}"'.encode(),
        }:
            return child.child_by_field_name("expression")
    return None


def _graph_node(source: Node) -> Node:
    function = next(
        (
            child
            for child in source.named_children
            if child.type == "function_expression"
        ),
        None,
    )
    body = function.child_by_field_name("body") if function is not None else None
    if body is None:
        msg = "Expected GitButler Cargo.nix to have a function body"
        raise ValueError(msg)
    graph = _binding_node(body, "crates") or _binding_node(body, "internal.crates")
    if graph is None:
        internal = _binding_node(body, "internal")
        graph = _binding_node(internal, "crates") if internal is not None else None
    if graph is None:
        msg = "GitButler Cargo.nix is missing its crates graph"
        raise ValueError(msg)
    return graph


def normalize(text: str) -> tuple[str, int, bool]:
    """Repair the crate graph without parsing unrelated generator helper functions.

    The pinned Nix parser cannot decode some crate2nix helper formals. Select
    the graph by its syntax-tree path, then parse and edit that complete set.
    """
    normalized, path_rewrites, added_root_src = normalize_cargo_nix(
        text,
        local_path_prefixes=("crates",),
    )
    graph_node = _graph_node(parse(normalized).node)
    graph_source = (graph_node.text or b"").decode()
    parsed_graph = parse(graph_source)
    crates = parsed_graph.expr
    if (
        graph_node.has_error
        or parsed_graph.contains_error
        or not isinstance(crates, AttributeSet)
    ):
        msg = "Expected GitButler crates graph to be a valid attribute set"
        raise ValueError(msg)
    if _repair_graph(crates):
        # Replace the selected syntax node as a unit; helper code and comments
        # outside the graph retain their original bytes.
        source = normalized.encode()
        normalized = (
            source[: graph_node.start_byte]
            + crates.rebuild(inline=True).encode()
            + source[graph_node.end_byte :]
        ).decode()
    return normalized, path_rewrites, added_root_src
