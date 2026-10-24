"""Shared helpers for schema code generation."""

import importlib
from collections.abc import Callable
from typing import override

import isort
import libcst as cst
from libcst.codemod import CodemodContext
from libcst.codemod.visitors import AddImportsVisitor

type ProgressReporter = Callable[[str], None]


def emit_progress(progress: ProgressReporter | None, message: str) -> None:
    """Invoke an optional schema-codegen progress reporter."""
    if progress is not None:
        progress(message)


def import_optional(module_name: str, *, feature: str) -> object:
    """Import an optional codegen dependency or raise a helpful runtime error."""
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        msg = (
            f"{feature} requires optional dependency {module_name!r}. "
            "Install the `codegen` extra to use this command."
        )
        raise RuntimeError(msg) from exc


class _ConstrAnnotationTransformer(cst.CSTTransformer):
    """Rewrite Pydantic ``constr(pattern=...)`` calls to ``Annotated``."""

    def __init__(self) -> None:
        self.rewritten = False

    @override
    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        """Replace simple ``constr(pattern=...)`` calls with v2 constraints."""
        _ = original_node
        if (
            not isinstance(updated_node.func, cst.Name)
            or updated_node.func.value != "constr"
        ):
            return updated_node
        if len(updated_node.args) != 1:
            return updated_node
        [arg] = updated_node.args
        if arg.keyword is None or arg.keyword.value != "pattern":
            return updated_node
        self.rewritten = True
        return cst.Subscript(
            value=cst.Name("Annotated"),
            slice=[
                cst.SubscriptElement(slice=cst.Index(cst.Name("str"))),
                cst.SubscriptElement(
                    slice=cst.Index(
                        cst.Call(
                            func=cst.Name("StringConstraints"),
                            args=[arg],
                        )
                    )
                ),
            ],
        )


class _PydanticImportTransformer(cst.CSTTransformer):
    """Replace imported ``constr`` names with ``StringConstraints``."""

    @override
    def leave_ImportFrom(
        self,
        original_node: cst.ImportFrom,
        updated_node: cst.ImportFrom,
    ) -> cst.ImportFrom:
        """Normalize imports from ``pydantic`` after annotation rewriting."""
        _ = original_node
        if not isinstance(updated_node.module, cst.Name):
            return updated_node
        if updated_node.module.value != "pydantic":
            return updated_node
        if not isinstance(updated_node.names, tuple):
            return updated_node

        aliases: list[cst.ImportAlias] = []
        for alias in updated_node.names:
            if not isinstance(alias, cst.ImportAlias):
                return updated_node
            if _import_alias_name(alias) != "constr":
                aliases.append(alias)
        if len(aliases) == len(updated_node.names):
            return updated_node
        if not any(
            _import_alias_name(alias) == "StringConstraints" for alias in aliases
        ):
            aliases.append(cst.ImportAlias(name=cst.Name("StringConstraints")))
        aliases = sorted(aliases, key=_import_alias_name)
        return updated_node.with_changes(
            names=tuple(
                alias.with_changes(comma=cst.MaybeSentinel.DEFAULT) for alias in aliases
            )
        )


def _import_alias_name(alias: cst.ImportAlias) -> str:
    """Return a sortable import alias name."""
    name = alias.name
    if isinstance(name, cst.Name):
        return name.value
    return name.attr.value


def rewrite_constr_type_hints(code: str) -> str:
    """Rewrite ``constr`` annotations and import ``Annotated`` when used."""
    transformer = _ConstrAnnotationTransformer()
    rewritten = cst.parse_module(code).visit(transformer)
    if not transformer.rewritten:
        return rewritten.code
    context = CodemodContext()
    AddImportsVisitor.add_needed_import(context, "typing", "Annotated")
    return rewritten.visit(AddImportsVisitor(context)).code


def normalize_pydantic_imports(code: str) -> str:
    """Replace ``constr`` imports with ``StringConstraints`` when needed."""
    normalized = cst.parse_module(code).visit(_PydanticImportTransformer()).code
    return isort.code(normalized).rstrip("\n")
