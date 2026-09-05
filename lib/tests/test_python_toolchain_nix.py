"""Structural contracts for the repository Python formatting toolchain."""

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance, expect_not_none
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_source_fragment_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell


def test_dev_shell_pyupgrade_targets_python_314_directly() -> None:
    """The hook should not retain the pre-3.14 multi-except compatibility pass."""
    pyupgrade_check = nix_source_fragment_expr(
        "lib/dev-shell.nix",
        "  pythonPyupgradeCheck = ",
        ";\n\n  pythonCompileCheck",
    )

    script = expect_instance(pyupgrade_check, FunctionCall).argument
    shell = parse_shell(indented_string_body(script.rebuild()))
    assert command_texts(shell, "__NIX_INTERP__/bin/xargs") == [
        "__NIX_INTERP__/bin/xargs -0 -r __NIX_INTERP__ --py314-plus"
    ]


def test_dev_shell_pyupgrade_uses_the_shared_exclusion_inventory() -> None:
    """The working-tree hook should preserve runtime compatibility helpers."""
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "lib/dev-shell.nix",
            "  pythonPyupgradeFindPredicates = ",
            ";\n  oxfmtPatterns",
        ),
        """
        lib.concatMapStringsSep " " (
          path: "-o -path './${path}'"
        ) lintFiles.python.pythonPyupgradeExcludes
        """,
    )

    pyupgrade_check = nix_source_fragment_expr(
        "lib/dev-shell.nix",
        "  pythonPyupgradeCheck = ",
        ";\n\n  pythonCompileCheck",
    )
    script = expect_instance(pyupgrade_check, FunctionCall).argument
    shell = parse_shell(indented_string_body(script.rebuild()))
    find_commands = [
        " ".join(command.replace("\\\n", " ").split())
        for command in command_texts(shell, "find")
    ]
    assert find_commands == [
        "find . "
        "\\( -path './.claude/worktrees' -o -path './.direnv' -o -path './.git' "
        "-o -path './.pytest_cache' -o -path './.ruff_cache' -o -path './.venv' "
        "-o -path './node_modules' -o -path './result' -o -name '_generated.py' "
        "__NIX_INTERP__ \\) -prune -o -type f "
        "\\( -name '*.py' -o -name '*.pyi' __NIX_INTERP__ \\) -print0"
    ]


def test_dev_shell_preserves_unified_diff_payload_whitespace() -> None:
    """The mutating hook should leave whitespace-bearing patch payloads intact."""
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "lib/dev-shell.nix",
            "      trim-trailing-whitespace = ",
            ";\n    };\n  };\nin\npkgs.devshell",
        ),
        r"""
        {
          enable = true;
          id = "fix-trailing-whitespace";
          name = "fix-trailing-whitespace";
          excludes = [ "\\.patch$" ];
          priority = 2;
          stages = [
            "pre-commit"
            "manual"
          ];
        }
        """,
    )


def test_pyupgrade_exclusion_inventory_is_narrow_and_explicit() -> None:
    """Only the shared Python 3.12 patch helper needs pyupgrade immunity."""
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "lib/lint-files.nix",
            "  pythonPyupgradeExcludes = ",
            ";\n  pythonRuntimeSensitiveHelpers",
        ),
        '[ "lib/exact_text_patch.py" ]',
    )


def test_flake_pyupgrade_check_targets_python_314_directly() -> None:
    """The flake check should exercise the same Python 3.14 rewrite contract."""
    pyupgrade_check = nix_source_fragment_expr(
        "flake.nix",
        '            "format-python-pyupgrade" = ',
        ';\n\n            "lint-python-compile"',
    )

    attrs = expect_instance(pyupgrade_check, AttributeSet)
    command = expect_instance(
        expect_binding(attrs.values, "command").value,
        FunctionDefinition,
    )
    shell = parse_shell(indented_string_body(command.output.rebuild()))
    assert command_texts(shell, "__NIX_INTERP__/bin/xargs") == [
        "__NIX_INTERP__/bin/xargs -0 -r __NIX_INTERP__/bin/pyupgrade --py314-plus"
    ]


def test_flake_python_check_filesets_reuse_the_shared_lint_inventory() -> None:
    """Check sources should not duplicate extensionless-script or Ruff exclusions."""
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "flake.nix",
            "          pythonFiles = ",
            ";\n          generatedPythonFiles",
        ),
        """
        lib.fileset.unions [
          (filesWithExtensions [ "py" "pyi" ])
          (filesetFromPaths pythonScriptPaths)
        ]
        """,
    )
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "flake.nix",
            "          generatedPythonFiles = ",
            ";\n          pyupgradeExcludedFiles",
        ),
        """
        lib.fileset.fileFilter (
          file: (file.hasExt "py" || file.hasExt "pyi") && file.name == "_generated.py"
        ) ./.
        """,
    )
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "flake.nix",
            "          pyupgradeExcludedFiles = ",
            ";\n          pyupgradeFiles",
        ),
        "filesetFromPaths pythonPyupgradeExcludes",
    )
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "flake.nix",
            "          pyupgradeFiles = ",
            ";\n          ruffFormatFiles",
        ),
        """
        lib.fileset.unions [
          ./.gitignore
          (lib.fileset.difference pythonFiles (
            lib.fileset.unions [
              generatedPythonFiles
              pyupgradeExcludedFiles
            ]
          ))
        ]
        """,
    )
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "flake.nix",
            "          ruffFormatFiles = ",
            ";\n          pythonToolFiles",
        ),
        "lib.fileset.difference pythonFiles (filesetFromPaths ruffMutationExcludes)",
    )


def test_flake_pytest_source_excludes_local_node_modules() -> None:
    """The hermetic TypeScript fixture must not inherit ignored workspace installs."""
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "flake.nix",
            "          pytestFiles = ",
            ";\n          schemaVerificationFiles",
        ),
        "lib.fileset.difference ./. (lib.fileset.maybeMissing ./node_modules)",
    )

    pytest_check = expect_instance(
        nix_source_fragment_expr(
            "flake.nix",
            '            "test-python-pytest" = ',
            ";\n\n          };",
        ),
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(pytest_check.values, "source").value,
        "mkCheckSource pytestFiles",
    )


def test_flake_repo_check_only_resolves_inputs_that_are_actually_dynamic() -> None:
    """Repo-check sources and working directories are static by construction."""
    repo_check = expect_instance(
        nix_source_fragment_expr(
            "flake.nix",
            "          mkRepoCheck =\n",
            ";\n          mkEvalOnlyCheck",
        ),
        FunctionDefinition,
    )
    formals = [
        expect_instance(argument, Identifier) for argument in repo_check.argument_set
    ]
    assert [formal.name for formal in formals] == [
        "name",
        "runCommandAttrs",
        "repoWritable",
        "source",
        "setup",
        "command",
    ]
    expected_defaults = (None, "{ }", "false", None, '""', None)
    for formal, expected_default in zip(formals, expected_defaults, strict=True):
        if expected_default is None:
            assert formal.default_value is None
        else:
            assert_nix_ast_equal(
                expect_not_none(formal.default_value),
                expected_default,
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
