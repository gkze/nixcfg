"""Contracts for shared check commands and formatter-specific behavior."""

from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_binding_expr, nix_source_fragment_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell


def test_shared_pyupgrade_targets_python_314_and_preserves_exclusions() -> None:
    """Both execution adapters use the same bounded Python 3.14 rewrite."""
    spec = expect_instance(
        nix_file_binding_expr("lib/repo-checks.nix", '"format-python-pyupgrade"'),
        AttributeSet,
    )
    command = expect_instance(
        expect_binding(spec.values, "command").value, FunctionDefinition
    )
    shell = parse_shell(indented_string_body(command.output.rebuild()))
    assert command_texts(shell, "__NIX_INTERP__/bin/xargs") == [
        "__NIX_INTERP__/bin/xargs -0 -r __NIX_INTERP__/bin/pyupgrade --py314-plus"
    ]
    find_commands = [
        " ".join(command.replace("\\\n", " ").split())
        for command in command_texts(shell, "__NIX_INTERP__/bin/find")
    ]
    assert find_commands == [
        "__NIX_INTERP__/bin/find . "
        "\\( -path './.claude/worktrees' -o -path './.direnv' -o -path './.git' "
        "-o -path './.pytest_cache' -o -path './.ruff_cache' -o -path './.venv' "
        "-o -path './node_modules' -o -path './result' -o -name '_generated.py' "
        "__NIX_INTERP__ \\) -prune -o -type f "
        "\\( -name '*.py' -o -name '*.pyi' __NIX_INTERP__ \\) -print0"
    ]
    assert_nix_ast_equal(
        nix_file_binding_expr("lib/repo-checks.nix", "pythonPyupgradeFindPredicates"),
        """
        lib.concatMapStringsSep " " (
          path: "-o -path './${path}'"
        ) pythonPyupgradeExcludes
        """,
    )


def test_pyupgrade_snapshot_verification_is_separate_from_the_mutation() -> None:
    """Nix snapshots must detect rewrites without resetting the real worktree."""
    spec = expect_instance(
        nix_file_binding_expr("lib/repo-checks.nix", '"format-python-pyupgrade"'),
        AttributeSet,
    )
    commands = {}
    for name in ("beforeCheck", "command", "afterCheck"):
        command = expect_instance(
            expect_binding(spec.values, name).value, FunctionDefinition
        )
        shell = parse_shell(indented_string_body(command.output.rebuild()))
        commands[name] = command_texts(shell, "__NIX_INTERP__")
    assert commands == {
        "beforeCheck": ["__NIX_INTERP__ init -q .", "__NIX_INTERP__ add -A"],
        "command": [],
        "afterCheck": ["__NIX_INTERP__ diff --exit-code -- ."],
    }


def test_dev_shell_preserves_unified_diff_payload_whitespace() -> None:
    """The mutating hook should leave whitespace-bearing patch payloads intact."""
    assert_nix_ast_equal(
        nix_file_binding_expr("lib/dev-shell.nix", "trim-trailing-whitespace"),
        r"""
        {
          enable = true;
          id = "fix-trailing-whitespace";
          name = "fix-trailing-whitespace";
          excludes = [ "\\.patch$" ];
          priority = 2;
          stages = [ "pre-commit" "manual" ];
        }
        """,
    )


def test_pyupgrade_exclusion_inventory_is_narrow_and_explicit() -> None:
    """Helpers executed before the project runtime retain Python 3.12 syntax."""
    assert_nix_ast_equal(
        nix_file_binding_expr("lib/lint-files.nix", "pythonPyupgradeExcludes"),
        '[ "lib/exact_text_patch.py" "lib/update/ci/jobs.py" ]',
    )


def test_flake_pytest_source_excludes_local_node_modules() -> None:
    """The hermetic TypeScript fixture must not inherit ignored workspace installs."""
    assert_nix_ast_equal(
        nix_file_binding_expr("flake.nix", "pytestFiles"),
        "lib.fileset.difference ./. (lib.fileset.maybeMissing ./node_modules)",
    )
    pytest_check = expect_instance(
        nix_file_binding_expr("flake.nix", '"test-python-pytest"'), AttributeSet
    )
    assert_nix_ast_equal(
        expect_binding(pytest_check.values, "source").value,
        "mkCheckSource pytestFiles",
    )


def test_treefmt_pyupgrade_targets_python_314_directly() -> None:
    """The write-mode formatter should use pyupgrade's native 3.14 mode."""
    pyupgrade_formatter = nix_source_fragment_expr(
        "flake.nix",
        "                      python-pyupgrade = ",
        ";\n                      ruff-check",
    )

    assert_nix_ast_equal(
        pyupgrade_formatter,
        """
        {
          command = pyupgradeExe;
          options = [
            "--py314-plus"
            "--exit-zero-even-if-changed"
          ];
          includes = pyupgradePaths;
          excludes = [ "**/_generated.py" ] ++ pythonPyupgradeExcludes;
        }
        """,
    )


def test_treefmt_markdown_tables_use_supported_gfm_plugin() -> None:
    """The Markdown formatter should avoid nixpkgs' archived tables plugin."""
    markdown_formatter = nix_source_fragment_expr(
        "flake.nix",
        '                      "markdown-table-formatter" = ',
        ";\n                      twilight-autoconfig-format",
    )

    assert_nix_ast_equal(
        markdown_formatter,
        """
        {
          command = lib.getExe' (pkgs.python3.withPackages (
            ps: with ps; [
              mdformat
              mdformat-gfm
            ]
          )) "mdformat";
          includes = lintFiles.markdown.globs;
          excludes = lintFiles.markdown.excludeGlobs;
        }
        """,
    )
