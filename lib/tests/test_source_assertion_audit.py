"""Audit tests for source-text substring assertions."""

import ast
import tomllib
from pathlib import Path

from lib.update.paths import REPO_ROOT

_REPO_ROOT = Path(REPO_ROOT)
_SELF_PATH = Path(__file__).resolve()
_EXCLUDED_PARTS = {".venv", "__pycache__", "mutants", "node_modules"}
_NIX_EVAL_HELPER_PATH = _REPO_ROOT / "lib/tests/_nix_eval.py"
_NIX_EVAL_TEST_ALLOWLIST = {
    "lib/tests/test_electron_runtime_nix.py::test_electron_overlay_backfills_a_new_artifact_for_an_existing_candidate": (
        "Only evaluation resolves the dynamic artifact backfill for an existing "
        "candidate version."
    ),
    "lib/tests/test_electron_runtime_nix.py::test_electron_overlay_reconstructs_the_updater_inventory": (
        "Only evaluation resolves dynamic runtime-version attribute grouping."
    ),
    "lib/tests/test_electron_runtime_nix.py::test_electron_overlay_rejects_a_url_outside_the_system_policy": (
        "Only evaluation forces the URL-policy assertion after decoding the "
        "runtime inventory."
    ),
    "lib/tests/test_electron_runtime_nix.py::test_electron_overlay_rejects_an_incomplete_runtime": (
        "Only evaluation proves the fail-closed inventory branch is forced."
    ),
    "lib/tests/test_electron_runtime_nix.py::test_electron_overlay_rejects_legacy_pin_metadata": (
        "Only evaluation forces the fail-closed legacy-pin branch in the "
        "dynamic source projection."
    ),
    "lib/tests/test_electron_runtime_nix.py::test_electron_overlay_synthesizes_only_update_candidate_versions": (
        "Only evaluation resolves the dynamic candidate version and its exact "
        "synthetic artifact set."
    ),
    "lib/tests/test_electron_runtime_nix.py::test_electron_runtime_build_uses_the_persisted_policy_url": (
        "Only evaluation resolves the selected runtime and header derivation "
        "URLs through the overlay."
    ),
    "lib/tests/test_goose_cli_package_nix.py::test_goose_cli_reviews_every_bitcoin_internals_version": (
        "Only evaluation resolves the locked crate graph across generated artifacts."
    ),
    "lib/tests/test_mac_apps_nix.py::test_guarded_bin_link_requires_an_executable": (
        "Only evaluation renders the interpolated shell program exercised by Bash."
    ),
    "lib/tests/test_mac_apps_nix.py::test_overlay_layer_merge_rejects_shadowed_fragment_outputs": (
        "Only evaluation proves that the merge helper throws before a later overlay "
        "can replace a fragment output."
    ),
    "lib/tests/test_mac_apps_nix.py::test_managed_app_overlap_assertion_accepts_context_carrying_output_paths": (
        "Only evaluation can create and compare context-carrying Nix paths."
    ),
    "lib/tests/test_mac_apps_nix.py::test_managed_app_overlap_assertion_allows_distinct_package_lists": (
        "Only evaluation establishes the assertion result produced by the Nix function."
    ),
    "lib/tests/test_mac_apps_nix.py::test_managed_app_overlap_assertion_ignores_unevaluable_package_outputs": (
        "Only evaluation proves that thrown package outputs remain guarded by tryEval."
    ),
    "lib/tests/test_mac_apps_nix.py::test_managed_app_overlap_assertion_keeps_unused_packages_lazy": (
        "Only evaluation proves unused package values remain lazy."
    ),
    "lib/tests/test_mac_apps_nix.py::test_managed_app_overlap_assertion_normalizes_each_package_once": (
        "Only evaluator traces expose repeated package coercion."
    ),
    "lib/tests/test_mac_apps_nix.py::test_managed_app_overlap_assertion_preserves_conflict_order": (
        "Only evaluation establishes the ordered conflicts produced by the Nix function."
    ),
    "lib/tests/test_mac_apps_nix.py::test_managed_app_overlap_assertion_reports_conflicting_package_lists": (
        "Only evaluation establishes the assertion message produced by the Nix function."
    ),
    "lib/tests/test_stylix_nix.py::test_default_base16_scheme_rejects_mismatched_source_metadata": (
        "Only evaluation reaches the throwing module branch."
    ),
    "lib/tests/test_stylix_nix.py::test_default_base16_scheme_uses_evaluator_visible_flake_input": (
        "Only evaluation resolves the default through the module system."
    ),
    "lib/tests/test_stylix_nix.py::test_explicit_base16_scheme_bypasses_default_source_metadata_check": (
        "Only evaluation proves the unused default remains lazy."
    ),
    "lib/tests/test_update_package_self_source_nix.py::test_package_probe_injects_overridden_self_source": (
        "Only evaluation proves callPackage argument injection."
    ),
}


def _iter_default_test_files(root: Path = _REPO_ROOT) -> list[Path]:
    """Return Python tests from every pytest root declared by the repository."""
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_config = config["tool"]["pytest"]["ini_options"]
    testpaths = pytest_config["testpaths"]
    python_files = pytest_config.get("python_files", ["test_*.py", "*_test.py"])
    test_files = {
        path
        for relative_root in testpaths
        for pattern in python_files
        for path in (root / relative_root).rglob(pattern)
    }
    return sorted(test_files)


def _iter_test_support_files(root: Path = _REPO_ROOT) -> list[Path]:
    """Return collected tests plus non-collected helpers under ``lib/tests``."""
    return sorted({
        *_iter_default_test_files(root),
        *(root / "lib/tests").rglob("*.py"),
    })


def _references_name(node: ast.AST | None, names: set[str]) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in names
    return node is not None and any(
        isinstance(item, ast.Name) and item.id in names for item in ast.walk(node)
    )


def _references_repo_root(node: ast.AST | None) -> bool:
    return _references_name(node, {"REPO_ROOT"})


def _is_repo_relative_path_constructor(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call) or not node.args:
        return False
    constructor = node.func
    if not (isinstance(constructor, ast.Name) and constructor.id == "Path") and not (
        isinstance(constructor, ast.Attribute) and constructor.attr == "Path"
    ):
        return False
    first_argument = node.args[0]
    if _references_repo_root(first_argument) or _references_name(
        first_argument, {"__file__"}
    ):
        return True
    return (
        isinstance(first_argument, ast.Constant)
        and isinstance(first_argument.value, str)
        and not Path(first_argument.value).is_absolute()
    )


_REPO_PATH_TRANSFORM_METHODS = {
    "absolute",
    "expanduser",
    "joinpath",
    "readlink",
    "relative_to",
    "resolve",
    "with_name",
    "with_stem",
    "with_suffix",
}
_SOURCE_TEXT_TRANSFORM_METHODS = {
    "casefold",
    "expandtabs",
    "format",
    "format_map",
    "lower",
    "lstrip",
    "removeprefix",
    "removesuffix",
    "replace",
    "rstrip",
    "strip",
    "swapcase",
    "title",
    "translate",
    "upper",
    "zfill",
}
_SOURCE_TEXT_SEARCH_METHODS = {
    "count",
    "endswith",
    "find",
    "index",
    "rfind",
    "rindex",
    "startswith",
}


def _is_repo_source_path_expression(
    node: ast.AST | None,
    aliases: set[str] | None = None,
) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return (
            node.id == "REPO_ROOT"
            or node.id.endswith("_PATH")
            or node.id in (aliases or set())
        )
    if _references_repo_root(node) or _is_repo_relative_path_constructor(node):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _is_repo_source_path_expression(node.left, aliases)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return _is_repo_source_path_expression(node.value, aliases)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _REPO_PATH_TRANSFORM_METHODS
        and _is_repo_source_path_expression(node.func.value, aliases)
    )


def _receiver_is_repo_source_path(
    receiver: ast.expr,
    aliases: set[str] | None = None,
) -> bool:
    return _is_repo_source_path_expression(receiver, aliases)


def _is_repo_source_read(
    node: ast.AST | None,
    aliases: set[str] | None = None,
) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_text"
        and _receiver_is_repo_source_path(node.func.value, aliases)
    )


class _RepoSourceReaderNameVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()
        self._function_names: list[str] = []
        self._path_names: list[set[str]] = []
        self._text_names: list[set[str]] = []
        self._returns_repo_source_text: list[bool] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._function_names.append(node.name)
        self._path_names.append(set())
        self._text_names.append(set())
        self._returns_repo_source_text.append(False)
        self.generic_visit(node)
        returns_repo_source_text = self._returns_repo_source_text.pop()
        function_name = self._function_names.pop()
        self._text_names.pop()
        self._path_names.pop()
        if returns_repo_source_text:
            self.names.add(function_name)

    def visit_Assign(self, node: ast.Assign) -> None:
        if not self._path_names:
            self.generic_visit(node)
            return
        is_source_path = self._is_repo_source_path(node.value)
        is_source_text = self._is_repo_source_text(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._path_names[-1].discard(target.id)
                self._text_names[-1].discard(target.id)
                if is_source_path:
                    self._path_names[-1].add(target.id)
                if is_source_text:
                    self._text_names[-1].add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if not self._path_names:
            self.generic_visit(node)
            return
        if isinstance(node.target, ast.Name):
            self._path_names[-1].discard(node.target.id)
            self._text_names[-1].discard(node.target.id)
            if self._is_repo_source_path(node.value):
                self._path_names[-1].add(node.target.id)
            if self._is_repo_source_text(node.value):
                self._text_names[-1].add(node.target.id)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if self._is_repo_source_text(node.value):
            self._returns_repo_source_text[-1] = True
        self.generic_visit(node)

    def _is_repo_source_path(self, node: ast.AST | None) -> bool:
        if not self._path_names:
            return False
        return _is_repo_source_path_expression(node, self._path_names[-1])

    def _is_repo_source_text(self, node: ast.AST | None) -> bool:
        if not self._text_names or node is None:
            return False
        if isinstance(node, ast.Name):
            return node.id in self._text_names[-1]
        if _is_repo_source_read(node, self._path_names[-1]) or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.names
        ):
            return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            return self._is_repo_source_text(node.left) or self._is_repo_source_text(
                node.right
            )
        if isinstance(node, ast.JoinedStr):
            return any(self._is_repo_source_text(value) for value in node.values)
        if isinstance(node, ast.Subscript):
            return self._is_repo_source_text(node.value)
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SOURCE_TEXT_TRANSFORM_METHODS
            and self._is_repo_source_text(node.func.value)
        )


def _repo_source_reader_names(tree: ast.AST) -> set[str]:
    visitor = _RepoSourceReaderNameVisitor()
    while True:
        previous_names = set(visitor.names)
        visitor.visit(tree)
        if visitor.names == previous_names:
            break
    return visitor.names


class _SourceSubstringVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str, reader_names: set[str]) -> None:
        self.path = path
        self.source = source
        self.reader_names = reader_names
        self.source_path_name_scopes: list[set[str]] = [set()]
        self.source_text_name_scopes: list[set[str]] = [set()]
        self.bound_name_scopes: list[set[str]] = [set()]
        self.violations: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        arguments = {
            argument.arg
            for argument in [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
        }
        if node.args.vararg is not None:
            arguments.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            arguments.add(node.args.kwarg.arg)
        self.source_path_name_scopes.append(set())
        self.source_text_name_scopes.append(set())
        self.bound_name_scopes.append(arguments)
        self.generic_visit(node)
        self.bound_name_scopes.pop()
        self.source_text_name_scopes.pop()
        self.source_path_name_scopes.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        is_source_path = self._is_repo_source_path(node.value)
        is_source_text = self._is_source_text(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._bind_name(target.id, is_source_path, is_source_text)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._bind_name(
                node.target.id,
                self._is_repo_source_path(node.value),
                self._is_source_text(node.value),
            )
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        for compare in ast.walk(node.test):
            if isinstance(compare, ast.Compare):
                self._visit_compare(node, compare)
        if any(
            self._is_source_text_search(call)
            for call in ast.walk(node.test)
            if isinstance(call, ast.Call)
        ):
            self._record_violation(node)
        self.generic_visit(node)

    def _bind_name(
        self,
        name: str,
        is_source_path: bool,
        is_source_text: bool,
    ) -> None:
        self.bound_name_scopes[-1].add(name)
        self.source_path_name_scopes[-1].discard(name)
        self.source_text_name_scopes[-1].discard(name)
        if is_source_path:
            self.source_path_name_scopes[-1].add(name)
        if is_source_text:
            self.source_text_name_scopes[-1].add(name)

    def _name_has_provenance(
        self,
        name: str,
        provenance_scopes: list[set[str]],
    ) -> bool:
        for bound_names, provenance_names in reversed(
            list(zip(self.bound_name_scopes, provenance_scopes, strict=True))
        ):
            if name in bound_names:
                return name in provenance_names
        return False

    def _visible_source_path_names(self) -> set[str]:
        names = set().union(*self.bound_name_scopes)
        return {
            name
            for name in names
            if self._name_has_provenance(name, self.source_path_name_scopes)
        }

    def _visit_compare(self, assert_node: ast.Assert, compare: ast.Compare) -> None:
        operands = [compare.left, *compare.comparators]
        for index, operator in enumerate(compare.ops):
            if not isinstance(operator, (ast.In, ast.NotIn)):
                continue
            left = operands[index]
            right = operands[index + 1]
            if _is_string_literal(left) and self._uses_source_text(right):
                self._record_violation(assert_node)
            if _is_string_literal(right) and self._uses_source_text(left):
                self._record_violation(assert_node)

    def _record_violation(self, node: ast.Assert) -> None:
        rel = self.path.relative_to(_REPO_ROOT)
        statement = ast.get_source_segment(self.source, node) or "assert ..."
        violation = f"{rel}:{node.lineno}: {statement.strip()}"
        if violation not in self.violations:
            self.violations.append(violation)

    def _is_source_text(self, node: ast.AST | None) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Name):
            return self._name_has_provenance(
                node.id,
                self.source_text_name_scopes,
            )
        if _is_repo_source_read(node, self._visible_source_path_names()) or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.reader_names
        ):
            return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            return self._uses_source_text(node.left) or self._uses_source_text(
                node.right
            )
        if isinstance(node, ast.JoinedStr):
            return any(self._uses_source_text(value) for value in node.values)
        if isinstance(node, ast.Subscript):
            return self._uses_source_text(node.value)
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SOURCE_TEXT_TRANSFORM_METHODS
            and self._uses_source_text(node.func.value)
        )

    def _is_repo_source_path(self, node: ast.AST | None) -> bool:
        return _is_repo_source_path_expression(
            node,
            self._visible_source_path_names(),
        )

    def _uses_source_text(self, node: ast.AST | None) -> bool:
        if node is None:
            return False
        if self._is_source_text(node):
            return True
        return any(
            self._uses_source_text(child) for child in ast.iter_child_nodes(node)
        )

    def _is_source_text_search(self, node: ast.Call) -> bool:
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in _SOURCE_TEXT_SEARCH_METHODS
            and self._uses_source_text(node.func.value)
            and any(
                _contains_string_literal(argument)
                for argument in [*node.args, *(item.value for item in node.keywords)]
            )
        )


class _DirectReadTextSubstringVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self.violations: list[str] = []

    def visit_Assert(self, node: ast.Assert) -> None:
        for compare in ast.walk(node.test):
            if isinstance(compare, ast.Compare):
                self._visit_compare(node, compare)
        self.generic_visit(node)

    def _visit_compare(self, assert_node: ast.Assert, compare: ast.Compare) -> None:
        operands = [compare.left, *compare.comparators]
        for index, operator in enumerate(compare.ops):
            if not isinstance(operator, (ast.In, ast.NotIn)):
                continue
            left = operands[index]
            right = operands[index + 1]
            if _is_string_literal(left) and _calls_read_text(right):
                self._record_violation(assert_node)
            if _is_string_literal(right) and _calls_read_text(left):
                self._record_violation(assert_node)

    def _record_violation(self, node: ast.Assert) -> None:
        rel = self.path.relative_to(_REPO_ROOT)
        statement = ast.get_source_segment(self.source, node) or "assert ..."
        self.violations.append(f"{rel}:{node.lineno}: {statement.strip()}")


class _NixExprSubstringVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self.nix_expr_name_scopes: list[set[str]] = [set()]
        self.nix_text_name_scopes: list[set[str]] = [set()]
        self.violations: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.nix_expr_name_scopes.append(set())
        self.nix_text_name_scopes.append(set())
        self.generic_visit(node)
        self.nix_text_name_scopes.pop()
        self.nix_expr_name_scopes.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.nix_expr_name_scopes.append(set())
        self.nix_text_name_scopes.append(set())
        self.generic_visit(node)
        self.nix_text_name_scopes.pop()
        self.nix_expr_name_scopes.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._is_nix_expr_assignment(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.nix_expr_name_scopes[-1].add(target.id)
        if self._produces_nix_text(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.nix_text_name_scopes[-1].add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._is_nix_expr_assignment(node.value) and isinstance(
            node.target, ast.Name
        ):
            self.nix_expr_name_scopes[-1].add(node.target.id)
        if self._produces_nix_text(node.value) and isinstance(node.target, ast.Name):
            self.nix_text_name_scopes[-1].add(node.target.id)
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        for compare in ast.walk(node.test):
            if isinstance(compare, ast.Compare):
                self._visit_compare(node, compare)
        self.generic_visit(node)

    def _visit_compare(self, assert_node: ast.Assert, compare: ast.Compare) -> None:
        operands = [compare.left, *compare.comparators]
        for index, operator in enumerate(compare.ops):
            left = operands[index]
            right = operands[index + 1]
            if isinstance(operator, (ast.In, ast.NotIn)):
                if _is_string_literal(left) and self._uses_nix_expr_text(right):
                    self._record_violation(assert_node)
                if _is_string_literal(right) and self._uses_nix_expr_text(left):
                    self._record_violation(assert_node)
            elif isinstance(operator, (ast.Eq, ast.NotEq)) and (
                (self._uses_nix_expr_text(left) and _contains_string_literal(right))
                or (self._uses_nix_expr_text(right) and _contains_string_literal(left))
            ):
                self._record_violation(assert_node)

    def _is_nix_expr_assignment(self, node: ast.AST | None) -> bool:
        call_name = _call_name(node) if isinstance(node, ast.Call) else ""
        return isinstance(node, ast.Call) and (
            call_name == "parse_nix_expr"
            or call_name.endswith(("_expr", "_expression"))
        )

    def _is_rebuilt_nix_text(self, node: ast.AST | None) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "rebuild"
        )

    def _produces_nix_text(self, node: ast.AST | None) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Name) and any(
            node.id in names for names in self.nix_text_name_scopes
        ):
            return True
        if self._is_rebuilt_nix_text(node):
            return True
        if isinstance(
            node,
            ast.BinOp
            | ast.Dict
            | ast.DictComp
            | ast.GeneratorExp
            | ast.JoinedStr
            | ast.List
            | ast.ListComp
            | ast.Set
            | ast.SetComp
            | ast.Tuple,
        ):
            return any(
                self._uses_nix_expr_text(child) for child in ast.iter_child_nodes(node)
            )
        if not isinstance(node, ast.Call):
            return False
        text_transform = _call_name(node) in {"dedent", "indented_string_body"} or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr
            in {
                "join",
                "lstrip",
                "removeprefix",
                "removesuffix",
                "replace",
                "rstrip",
                "split",
                "strip",
            }
        )
        return text_transform and any(
            self._uses_nix_expr_text(child) for child in ast.iter_child_nodes(node)
        )

    def _uses_nix_expr_text(self, node: ast.AST | None) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Call) and _call_name(node) == "parse_shell":
            return False
        if isinstance(node, ast.Name) and any(
            node.id in names for names in self.nix_text_name_scopes
        ):
            return True
        if self._is_rebuilt_nix_text(node):
            return True
        return any(
            self._uses_nix_expr_text(child) for child in ast.iter_child_nodes(node)
        )

    def _record_violation(self, node: ast.Assert) -> None:
        rel = self.path.relative_to(_REPO_ROOT)
        statement = ast.get_source_segment(self.source, node) or "assert ..."
        self.violations.append(f"{rel}:{node.lineno}: {statement.strip()}")


def _calls_read_text(node: ast.AST) -> bool:
    return any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr == "read_text"
        for item in ast.walk(node)
    )


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        receiver = _dotted_name(node.value)
        return f"{receiver}.{node.attr}" if receiver else node.attr
    return ""


_PROCESS_CALL_NAMES = {
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "os.popen",
    "os.system",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "subprocess.run",
}


def _resolve_process_call_name(
    node: ast.expr,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
) -> str:
    name = _dotted_name(node)
    if name in callable_aliases:
        return callable_aliases[name]
    root, separator, remainder = name.partition(".")
    normalized_root = module_aliases.get(root, root)
    return f"{normalized_root}.{remainder}" if separator else normalized_root


def _process_aliases_from_tree(
    tree: ast.AST,
) -> tuple[dict[str, str], dict[str, str]]:
    module_aliases: dict[str, str] = {}
    callable_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"asyncio", "os", "subprocess"}:
                    module_aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module_prefix = f"{node.module}."
            for alias in node.names:
                process_call_name = f"{module_prefix}{alias.name}"
                if process_call_name in _PROCESS_CALL_NAMES:
                    callable_aliases[alias.asname or alias.name] = process_call_name

    assignments = [
        node for node in ast.walk(tree) if isinstance(node, ast.Assign | ast.AnnAssign)
    ]
    while True:
        discovered: dict[str, str] = {}
        for assignment in assignments:
            value = assignment.value
            if not isinstance(value, ast.Name | ast.Attribute):
                continue
            process_call_name = _resolve_process_call_name(
                value,
                module_aliases,
                callable_aliases,
            )
            if process_call_name not in _PROCESS_CALL_NAMES:
                continue
            targets = (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            discovered.update({
                target.id: process_call_name
                for target in targets
                if isinstance(target, ast.Name)
            })
        expanded = callable_aliases | discovered
        if expanded == callable_aliases:
            break
        callable_aliases = expanded
    return module_aliases, callable_aliases


def _function_call_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    process_module_aliases: dict[str, str],
    process_callable_aliases: dict[str, str],
) -> set[str]:
    pure_local_runners = {
        item.name
        for item in ast.walk(node)
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        if item is not node
        if not any(
            isinstance(call, ast.Call)
            and _resolve_process_call_name(
                call.func,
                process_module_aliases,
                process_callable_aliases,
            )
            in _PROCESS_CALL_NAMES
            for call in ast.walk(item)
        )
    }

    def uses_pure_local_runner(call: ast.Call) -> bool:
        return any(
            keyword.arg == "run"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id in pure_local_runners
            for keyword in call.keywords
        )

    return {
        name
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        if not uses_pure_local_runner(item)
        if (name := _dotted_name(item.func))
    }


def _nix_eval_imports(tree: ast.AST) -> tuple[set[str], set[str]]:
    callables: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "lib.tests._nix_eval":
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "lib.tests._nix_eval":
                for alias in node.names:
                    if alias.name.startswith("nix_eval_"):
                        callables.add(alias.asname or alias.name)
            elif node.module == "lib.tests":
                for alias in node.names:
                    if alias.name == "_nix_eval":
                        modules.add(alias.asname or alias.name)
    return callables, modules


def _nix_eval_test_functions(tree: ast.AST) -> set[str]:
    """Return tests that directly or transitively call the real evaluator helper."""
    callables, modules = _nix_eval_imports(tree)
    process_module_aliases, process_callable_aliases = _process_aliases_from_tree(tree)
    functions = {
        node.name: node
        for node in getattr(tree, "body", [])
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    calls = {
        name: _function_call_names(
            node,
            process_module_aliases,
            process_callable_aliases,
        )
        for name, node in functions.items()
    }

    def calls_evaluator(call_names: set[str]) -> bool:
        return bool(call_names & callables) or any(
            any(
                call_name.startswith(f"{module}.")
                and call_name.rsplit(".", maxsplit=1)[-1].startswith("nix_eval_")
                for module in modules
            )
            for call_name in call_names
        )

    evaluator_functions = {
        name for name, call_names in calls.items() if calls_evaluator(call_names)
    }
    while True:
        callers = {
            name
            for name, call_names in calls.items()
            if call_names & evaluator_functions
        }
        expanded = evaluator_functions | callers
        if expanded == evaluator_functions:
            break
        evaluator_functions = expanded
    return {name for name in evaluator_functions if name.startswith("test_")}


def _nix_eval_test_inventory() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    inventory: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for path in _iter_default_test_files():
        if path.resolve() == _SELF_PATH:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        test_names = _nix_eval_test_functions(tree)
        relative_path = path.relative_to(_REPO_ROOT)
        for node in tree.body:
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in test_names
            ):
                inventory[f"{relative_path}::{node.name}"] = node
    return inventory


def _docstring_explains_ast_limit(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    docstring = (ast.get_docstring(node) or "").casefold()
    return "ast" in docstring and any(
        phrase in docstring
        for phrase in ("cannot", "can't", "insufficient", "not enough")
    )


def _contains_nix_process_command(
    node: ast.AST,
    command_names: set[str],
) -> bool:
    strings = {
        item.value.casefold()
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }
    names = {
        item.id.casefold() for item in ast.walk(node) if isinstance(item, ast.Name)
    }
    if names & command_names:
        return True
    operation = bool(strings & {"eval", "--eval", "--parse", "path-info"})
    literal_executable = any(
        value in {"nix", "nix-instantiate"}
        or value.endswith(("/nix", "/nix-instantiate"))
        or value.startswith(("nix ", "nix-instantiate "))
        for value in strings
    )
    if literal_executable:
        return True
    executable = any("nix" in name for name in names) or any(
        " nix " in f" {value} " for value in strings
    )
    return operation and executable


class _DirectNixProcessVisitor(ast.NodeVisitor):
    """Find test code that bypasses the shared Nix evaluation boundary."""

    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self.process_module_alias_scopes: list[dict[str, str]] = [{}]
        self.process_callable_alias_scopes: list[dict[str, str]] = [{}]
        self.command_name_scopes: list[set[str]] = [set()]
        self.command_function_names: set[str] = set()
        self.violations: list[str] = []

    def visit_Module(self, node: ast.Module) -> None:
        functions = [
            item
            for item in node.body
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        while True:
            discovered = {
                function.name.casefold()
                for function in functions
                if any(
                    isinstance(item, ast.Return)
                    and item.value is not None
                    and _contains_nix_process_command(
                        item.value,
                        self.command_function_names,
                    )
                    for item in ast.walk(function)
                )
            }
            expanded = self.command_function_names | discovered
            if expanded == self.command_function_names:
                break
            self.command_function_names = expanded
        self.generic_visit(node)

    def _known_command_names(self) -> set[str]:
        return self.command_name_scopes[-1] | self.command_function_names

    def _process_aliases(self, scopes: list[dict[str, str]]) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for scope in scopes:
            aliases.update(scope)
        return aliases

    def _normalized_process_call_name(self, node: ast.expr) -> str:
        return _resolve_process_call_name(
            node,
            self._process_aliases(self.process_module_alias_scopes),
            self._process_aliases(self.process_callable_alias_scopes),
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in {"asyncio", "os", "subprocess"}:
                self.process_module_alias_scopes[-1][alias.asname or alias.name] = (
                    alias.name
                )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module_prefix = f"{node.module}."
        for alias in node.names:
            process_call_name = f"{module_prefix}{alias.name}"
            if process_call_name in _PROCESS_CALL_NAMES:
                self.process_callable_alias_scopes[-1][alias.asname or alias.name] = (
                    process_call_name
                )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.process_module_alias_scopes.append({})
        self.process_callable_alias_scopes.append({})
        self.command_name_scopes.append(set())
        self.generic_visit(node)
        self.command_name_scopes.pop()
        self.process_callable_alias_scopes.pop()
        self.process_module_alias_scopes.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.process_module_alias_scopes.append({})
        self.process_callable_alias_scopes.append({})
        self.command_name_scopes.append(set())
        self.generic_visit(node)
        self.command_name_scopes.pop()
        self.process_callable_alias_scopes.pop()
        self.process_module_alias_scopes.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        process_call_name = (
            self._normalized_process_call_name(node.value)
            if isinstance(node.value, ast.Name | ast.Attribute)
            else ""
        )
        if _contains_nix_process_command(node.value, self._known_command_names()):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.command_name_scopes[-1].add(target.id.casefold())
        if process_call_name in _PROCESS_CALL_NAMES:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.process_callable_alias_scopes[-1][target.id] = (
                        process_call_name
                    )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            node.value is not None
            and isinstance(node.target, ast.Name)
            and _contains_nix_process_command(
                node.value,
                self._known_command_names(),
            )
        ):
            self.command_name_scopes[-1].add(node.target.id.casefold())
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        command = (
            node.args[0]
            if node.args
            else next(
                (keyword.value for keyword in node.keywords if keyword.arg == "args"),
                None,
            )
        )
        if (
            self._normalized_process_call_name(node.func) in _PROCESS_CALL_NAMES
            and command is not None
            and _contains_nix_process_command(
                command,
                self._known_command_names(),
            )
        ):
            rel = self.path.relative_to(_REPO_ROOT)
            statement = (
                ast.get_source_segment(self.source, node) or "subprocess.run(...)"
            )
            self.violations.append(f"{rel}:{node.lineno}: {statement.strip()}")
        self.generic_visit(node)


def _is_string_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _contains_string_literal(node: ast.AST) -> bool:
    return any(_is_string_literal(item) for item in ast.walk(node))


def test_source_substring_visitor_tracks_repo_path_aliases() -> None:
    """Repo-source substring audits should catch path aliases before read_text."""
    source = """
from lib.update.paths import REPO_ROOT

path = REPO_ROOT / "packages/demo/default.nix"
source_text = path.read_text(encoding="utf-8")
assert "demo" in source_text
"""
    tree = ast.parse(source)
    visitor = _SourceSubstringVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
        _repo_source_reader_names(tree),
    )

    visitor.visit(tree)

    assert visitor.violations == [
        'lib/tests/test_demo.py:6: assert "demo" in source_text'
    ]


def test_source_substring_visitor_tracks_generic_pathlib_paths() -> None:
    """Relative Path constructors must not bypass structured source assertions."""
    source = """
from pathlib import Path

source_file = Path("lib/demo.nix")
text = source_file.read_text(encoding="utf-8")
assert "forbidden" not in text
"""
    tree = ast.parse(source)
    visitor = _SourceSubstringVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
        _repo_source_reader_names(tree),
    )

    visitor.visit(tree)

    assert visitor.violations == [
        'lib/tests/test_demo.py:6: assert "forbidden" not in text'
    ]


def test_source_substring_visitor_tracks_transformed_paths_and_text() -> None:
    """Path and text transformations must preserve source provenance per scope."""
    source = """
from pathlib import Path

def test_source():
    source_file = (Path("lib") / "demo.nix").resolve()
    text = source_file.read_text(encoding="utf-8").strip()
    assert text.startswith("forbidden")

def test_generated_output():
    text = render_demo()
    assert text.startswith("allowed")
"""
    tree = ast.parse(source)
    visitor = _SourceSubstringVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
        _repo_source_reader_names(tree),
    )

    visitor.visit(tree)

    assert visitor.violations == [
        'lib/tests/test_demo.py:7: assert text.startswith("forbidden")'
    ]


def test_repo_source_reader_names_tracks_function_path_aliases() -> None:
    """Reader helper detection should handle aliases inside helper functions."""
    source = """
from lib.update.paths import REPO_ROOT

def read_source():
    path = REPO_ROOT / "packages/demo/default.nix"
    return path.read_text(encoding="utf-8")
"""

    assert _repo_source_reader_names(ast.parse(source)) == {"read_source"}


def test_source_substring_visitor_tracks_helpers_returning_text_aliases() -> None:
    """Reader helpers must preserve transformed source text through return aliases."""
    source = """
from pathlib import Path

def read_source():
    source_file = (Path("lib") / "demo.nix").resolve()
    contents = source_file.read_text(encoding="utf-8").strip()
    return contents

def test_source():
    text = read_source()
    assert text.endswith("forbidden")
"""
    tree = ast.parse(source)
    visitor = _SourceSubstringVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
        _repo_source_reader_names(tree),
    )

    visitor.visit(tree)

    assert visitor.violations == [
        'lib/tests/test_demo.py:11: assert text.endswith("forbidden")'
    ]


def test_default_test_file_discovery_follows_declared_pytest_roots(
    tmp_path: Path,
) -> None:
    """Semantic audits must cover every root collected by default pytest runs."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'testpaths = ["lib", "packages"]\n'
        'python_files = ["test_*.py", "*_test.py"]\n',
        encoding="utf-8",
    )
    shared = tmp_path / "lib/tests/test_shared.py"
    sibling = tmp_path / "packages/demo/updater_test.py"
    ignored = tmp_path / "overlays/demo/updater_test.py"
    for path in (shared, sibling, ignored):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "def test_sibling():\n    pass\n",
            encoding="utf-8",
        )

    assert _iter_default_test_files(tmp_path) == [shared, sibling]


def test_tests_do_not_assert_substrings_in_repo_source_text() -> None:
    """Source-file tests should assert parsed behavior or AST structure instead."""
    violations: list[str] = []
    for path in _iter_default_test_files():
        if path.resolve() == _SELF_PATH:
            continue
        if any(part in _EXCLUDED_PARTS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        if "read_text" not in source:
            continue
        tree = ast.parse(source)
        visitor = _SourceSubstringVisitor(
            path,
            source,
            _repo_source_reader_names(tree),
        )
        visitor.visit(tree)
        violations.extend(visitor.violations)

    assert violations == [], "\n".join(violations)


def test_tests_do_not_assert_substrings_directly_against_file_text() -> None:
    """Structured file outputs should be parsed before behavior assertions."""
    violations: list[str] = []
    for path in _iter_default_test_files():
        if path.resolve() == _SELF_PATH:
            continue
        if any(part in _EXCLUDED_PARTS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        visitor = _DirectReadTextSubstringVisitor(path, source)
        visitor.visit(ast.parse(source))
        violations.extend(visitor.violations)

    assert violations == [], "\n".join(violations)


def test_tests_assert_nix_expr_builders_with_ast_checks() -> None:
    """Python-to-Nix boundary tests should compare parsed Nix structure."""
    violations: list[str] = []
    for path in _iter_default_test_files():
        if path.resolve() == _SELF_PATH:
            continue
        if any(part in _EXCLUDED_PARTS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        visitor = _NixExprSubstringVisitor(path, source)
        visitor.visit(ast.parse(source))
        violations.extend(visitor.violations)

    assert violations == [], "\n".join(violations)


def test_real_nix_eval_tests_match_the_reviewed_inventory() -> None:
    """Eval-based tests must remain an exact, centrally reviewed exception set."""
    inventory = _nix_eval_test_inventory()
    actual = set(inventory)
    expected = set(_NIX_EVAL_TEST_ALLOWLIST)

    unexpected = sorted(actual - expected)
    missing = sorted(expected - actual)
    assert unexpected == [], "Unexpected evaluator tests:\n" + "\n".join(unexpected)
    assert missing == [], "Missing evaluator tests:\n" + "\n".join(missing)

    weak_justifications = sorted(
        node_id
        for node_id, node in inventory.items()
        if not _docstring_explains_ast_limit(node)
    )
    assert weak_justifications == [], (
        "Evaluator test docstrings must explicitly explain why AST checks are "
        "insufficient:\n" + "\n".join(weak_justifications)
    )


def test_python_tests_do_not_construct_nix_processes_directly() -> None:
    """All real Nix evaluator and parser processes must use reviewed boundaries."""
    violations: list[str] = []
    for path in _iter_test_support_files():
        if path.resolve() == _SELF_PATH:
            continue
        if path.resolve() == _NIX_EVAL_HELPER_PATH:
            continue
        if any(part in _EXCLUDED_PARTS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        visitor = _DirectNixProcessVisitor(path, source)
        visitor.visit(ast.parse(source))
        violations.extend(visitor.violations)

    assert violations == [], "\n".join(violations)


def test_nix_eval_inventory_follows_local_wrappers_and_qualified_imports() -> None:
    """The reviewed evaluator inventory must include transitive test callers."""
    source = '''
import lib.tests._nix_eval as qualified_eval
from lib.tests._nix_eval import nix_eval_raw as evaluate

def direct_case():
    return evaluate(expression)

def wrapped_case():
    return direct_case()

def test_wrapped_case():
    """AST checks cannot prove evaluation-time behavior."""
    assert wrapped_case()

def test_qualified_case():
    """AST checks cannot prove evaluation-time behavior."""
    assert qualified_eval.nix_eval_json(expression)

def test_injected_case():
    def fake_run(*args, **kwargs):
        return completed
    assert qualified_eval.nix_eval_result(expression, raw=True, run=fake_run)

def test_explicit_real_runner():
    assert qualified_eval.nix_eval_result(
        expression,
        raw=True,
        run=subprocess.run,
    )
'''

    assert _nix_eval_test_functions(ast.parse(source)) == {
        "test_explicit_real_runner",
        "test_qualified_case",
        "test_wrapped_case",
    }


def test_direct_nix_process_visitor_follows_command_variables() -> None:
    """Evaluator subprocess construction must remain in the shared helper."""
    source = """
import subprocess

def test_direct_eval():
    nix = find_tool("nix")
    command = [nix, "eval", "--raw"]
    subprocess.run([*command, "--expr", expression])
"""
    visitor = _DirectNixProcessVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
    )

    visitor.visit(ast.parse(source))

    assert visitor.violations == [
        'lib/tests/test_demo.py:7: subprocess.run([*command, "--expr", expression])'
    ]


def test_direct_nix_process_visitor_follows_command_helpers() -> None:
    """Local command factories must not hide direct evaluator subprocesses."""
    source = """
import subprocess

def nix_command():
    return ["nix", "eval", "--raw"]

def test_direct_eval():
    subprocess.run(nix_command())
"""
    visitor = _DirectNixProcessVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
    )

    visitor.visit(ast.parse(source))

    assert visitor.violations == [
        "lib/tests/test_demo.py:8: subprocess.run(nix_command())"
    ]


def test_direct_nix_process_visitor_normalizes_assigned_process_aliases() -> None:
    """Process-module and callable aliases must not bypass the evaluator boundary."""
    source = """
import subprocess as sp

def test_direct_eval():
    command = ["nix", "eval", "--raw"]
    launch = sp.run
    launch(args=command)
"""
    visitor = _DirectNixProcessVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
    )

    visitor.visit(ast.parse(source))

    assert visitor.violations == ["lib/tests/test_demo.py:7: launch(args=command)"]


def test_nix_eval_inventory_rejects_process_backed_injected_runners() -> None:
    """An injected runner is pure only when it cannot launch an evaluator process."""
    source = """
import subprocess
from lib.tests._nix_eval import nix_eval_result

def test_injected_real_runner():
    def runner(command, **kwargs):
        return subprocess.check_output(["nix", *command])
    return nix_eval_result(expression, raw=True, run=runner)
"""

    assert _nix_eval_test_functions(ast.parse(source)) == {"test_injected_real_runner"}


def test_nix_eval_inventory_normalizes_aliased_injected_process_runners() -> None:
    """Aliased process APIs still make an injected evaluator runner real."""
    source = """
import subprocess as sp
from lib.tests._nix_eval import nix_eval_result

def test_injected_real_runner():
    def runner(command, **kwargs):
        launch = sp.check_output
        return launch(args=["nix", *command])
    return nix_eval_result(expression, raw=True, run=runner)
"""

    assert _nix_eval_test_functions(ast.parse(source)) == {"test_injected_real_runner"}


def test_direct_nix_process_visitor_rejects_dynamic_check_output_wrappers() -> None:
    """Alternative subprocess APIs and dynamic Nix arguments cannot bypass policy."""
    source = """
import subprocess

def test_direct_eval(args):
    subprocess.check_output(["nix", *args])
"""
    visitor = _DirectNixProcessVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
    )

    visitor.visit(ast.parse(source))

    assert visitor.violations == [
        'lib/tests/test_demo.py:5: subprocess.check_output(["nix", *args])'
    ]


def test_nix_expr_substring_visitor_tracks_rebuilt_parsed_expressions() -> None:
    """Parsed Nix must be asserted structurally after it is rebuilt or aliased."""
    source = """
expression = parse_nix_expr("{ enabled = true; }")
rendered = expression.rebuild()
assert "enabled" in rendered
"""
    visitor = _NixExprSubstringVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
    )

    visitor.visit(ast.parse(source))

    assert visitor.violations == [
        'lib/tests/test_demo.py:4: assert "enabled" in rendered'
    ]


def test_nix_expr_substring_visitor_rejects_rebuilt_ast_string_equality() -> None:
    """Nix AST equality must compare semantic nodes, not rendered strings."""
    source = """
expression = parse_nix_expr("{ enabled = true; }")
assert expression.rebuild() == "{ enabled = true; }"
"""
    visitor = _NixExprSubstringVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
    )

    visitor.visit(ast.parse(source))

    assert visitor.violations == [
        'lib/tests/test_demo.py:3: assert expression.rebuild() == "{ enabled = true; }"'
    ]


def test_nix_expr_substring_visitor_allows_parsed_shell_output_contracts() -> None:
    """Rendered Nix shell is safe after a shell parser establishes its structure."""
    source = """
phase = expect_indented_string(expression)
assert command_texts(parse_shell(indented_string_body(phase.rebuild())), "tool") == [
    "tool --flag"
]
"""
    visitor = _NixExprSubstringVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
    )

    visitor.visit(ast.parse(source))

    assert visitor.violations == []


def test_nix_expr_substring_visitor_tracks_rebuilt_collection_aliases() -> None:
    """Collections of rendered Nix nodes must not hide string comparisons."""
    source = """
formals = [argument.rebuild() for argument in function.argument_set]
assert formals == ["source", "..."]
"""
    visitor = _NixExprSubstringVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
    )

    visitor.visit(ast.parse(source))

    assert visitor.violations == [
        'lib/tests/test_demo.py:3: assert formals == ["source", "..."]'
    ]


def test_nix_expr_substring_visitor_does_not_leak_aliases_between_tests() -> None:
    """A rendered-name alias in one test must not taint another test's value."""
    source = """
def test_rendered():
    result = expression.rebuild()
    assert result == "true"

def test_process():
    result = subprocess.run(command)
    assert result.stderr == ""
"""
    visitor = _NixExprSubstringVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
    )

    visitor.visit(ast.parse(source))

    assert visitor.violations == ['lib/tests/test_demo.py:4: assert result == "true"']


def test_nix_expr_substring_visitor_does_not_taint_process_results() -> None:
    """Passing rendered shell to a process must not taint its completed result."""
    source = """
def test_shell():
    rendered = expression.rebuild()
    result = subprocess.run(command, input=rendered)
    assert result.stderr == ""
"""
    visitor = _NixExprSubstringVisitor(
        _REPO_ROOT / "lib/tests/test_demo.py",
        source,
    )

    visitor.visit(ast.parse(source))

    assert visitor.violations == []


def _updater_class(tree: ast.Module, name: str) -> ast.ClassDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _class_attribute(updater: ast.ClassDef, name: str) -> ast.expr:
    for statement in updater.body:
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
            and statement.value is not None
        ):
            return statement.value
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            return statement.value
    msg = f"{updater.name}.{name} is not declared in class scope"
    raise AssertionError(msg)


def _literal_string(node: ast.expr) -> str:
    assert isinstance(node, ast.Constant)
    assert isinstance(node.value, str)
    return node.value


def _literal_string_mapping(node: ast.expr) -> dict[str, str]:
    assert isinstance(node, ast.Dict)
    return {
        _literal_string(key): _literal_string(value)
        for key, value in zip(node.keys, node.values, strict=True)
        if key is not None
    }


def _name_mapping(node: ast.expr) -> dict[str, str]:
    assert isinstance(node, ast.Dict)
    result: dict[str, str] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        assert key is not None
        assert isinstance(value, ast.Name)
        result[_literal_string(key)] = value.id
    return result


def _updater_method(
    updater: ast.ClassDef,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in updater.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _literal_getter_keys(node: ast.AST, getter: str) -> set[str]:
    keys: set[str] = set()
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == getter
            and child.args
            and isinstance(child.args[0], ast.Constant)
            and isinstance(child.args[0].value, str)
        ):
            keys.add(child.args[0].value)
    return keys


def _parse_updater(path: str) -> tuple[ast.Module, ast.ClassDef]:
    source_path = _REPO_ROOT / path
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    class_name = f"{source_path.parent.name.title()}Updater"
    return tree, _updater_class(tree, class_name)


def test_buzz_declares_and_consumes_its_reviewed_compatibility_contracts() -> None:
    """Buzz's intentional scalar and byte constraints remain explicit policy."""
    tree, updater = _parse_updater("packages/buzz/updater.py")

    assert _literal_string_mapping(_class_attribute(updater, "compatibility_pins")) == {
        "onnxruntimeVersion": "1.27.0",
    }
    assert _literal_string(
        _class_attribute(updater, "compatibility_pin_rationale")
    ).strip()
    assert _name_mapping(_class_attribute(updater, "compatibility_source_digests")) == {
        "meshLlm": "_MESH_SOURCE_DIGESTS",
        "onnxruntime": "_ONNX_SOURCE_DIGESTS",
        "sherpaOnnx": "_SHERPA_SOURCE_DIGESTS",
    }
    assert _literal_string(
        _class_attribute(updater, "compatibility_source_digest_rationale")
    ).strip()
    assert _literal_getter_keys(
        _updater_method(updater, "_required_metadata"),
        "get_compatibility_pin",
    ) == {"onnxruntimeVersion"}
    assert _literal_getter_keys(
        _updater_method(updater, "fetch_latest"),
        "get_compatibility_source_digest_contract",
    ) == {"meshLlm", "onnxruntime", "sherpaOnnx"}
    assert _literal_getter_keys(
        tree,
        "get_compatibility_source_digest_contract",
    ) == {"meshLlm", "onnxruntime", "sherpaOnnx"}


def test_paseo_declares_and_consumes_its_reviewed_compatibility_contracts() -> None:
    """Paseo's version-specific patches and inventories remain fail-closed policy."""
    tree, updater = _parse_updater("packages/paseo/updater.py")

    assert _literal_string_mapping(_class_attribute(updater, "compatibility_pins")) == {
        "appBuilderLibBackportCommit": "2ff9190aadc791503a6e62cdcbfa975448bc49bf",
        "appBuilderLibVersion": "26.8.1",
        "nodeAddonApiVersion": "8.3.0",
        "npmFetcherVersion": "2",
        "onnxruntimeVersion": "1.23.2",
        "sherpaVersion": "1.12.28",
    }
    assert _literal_string(
        _class_attribute(updater, "compatibility_pin_rationale")
    ).strip()
    assert _literal_getter_keys(
        _updater_method(updater, "_validate_manifests"),
        "get_compatibility_pin",
    ) == {
        "appBuilderLibVersion",
        "nodeAddonApiVersion",
        "onnxruntimeVersion",
        "sherpaVersion",
    }
    assert _literal_getter_keys(tree, "get_compatibility_pin") == {
        "appBuilderLibBackportCommit",
        "appBuilderLibVersion",
        "nodeAddonApiVersion",
        "npmFetcherVersion",
        "onnxruntimeVersion",
        "sherpaVersion",
    }
