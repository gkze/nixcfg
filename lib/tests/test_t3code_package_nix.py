"""AST-level checks for the T3 Code standalone package."""

from __future__ import annotations

from functools import cache

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.inherit import Inherit
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.update.paths import REPO_ROOT


@cache
def _t3code_derivation() -> FunctionCall:
    """Return the top-level derivation from ``packages/t3code/default.nix``."""
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/t3code/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, FunctionCall)


@cache
def _t3code_derivation_args() -> AttributeSet:
    """Return the attrset passed to the standalone T3 derivation."""
    return expect_instance(_t3code_derivation().argument, AttributeSet)


@cache
def _shared_output() -> AttributeSet:
    """Return the exported attrset from ``packages/t3code/_shared.nix``."""
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/t3code/_shared.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, AttributeSet)


def _assert_shared_binding(name: str, expected: str) -> None:
    assert_nix_ast_equal(expect_scope_binding(_shared_output(), name).value, expected)


def test_t3code_package_wraps_the_bun_runtime_entrypoint() -> None:
    """The package should expose ``t3`` by wrapping Bun around the built dist."""
    assert_nix_ast_equal(_t3code_derivation().name, "stdenvNoCC.mkDerivation")
    node_modules = expect_instance(
        expect_scope_binding(_t3code_derivation(), "node_modules").value,
        FunctionCall,
    )
    node_modules_args = expect_instance(node_modules.argument, AttributeSet)
    node_modules_build = expect_instance(
        expect_binding(node_modules_args.values, "buildPhase").value,
        IndentedString,
    )
    assert "--server-only" in node_modules_build.value
    assert 'exports["./utils/*"]' in node_modules_build.value
    assert "if [ -d node_modules/.bun ]; then" in node_modules_build.value
    assert "find node_modules/.bun -path '*/node_modules/.bin'" in (
        node_modules_build.value
    )

    install_phase = expect_instance(
        expect_binding(_t3code_derivation_args().values, "installPhase").value,
        IndentedString,
    )

    assert (
        'cp -R ${workspaceBuild}/apps/server/dist "$out/libexec/${pname}/dist"'
        in install_phase.value
    )
    assert (
        'cp -R ${node_modules}/node_modules "$out/libexec/${pname}/node_modules"'
        in install_phase.value
    )
    assert 'makeWrapper ${lib.getExe bun} "$out/bin/t3"' in install_phase.value
    assert '--add-flags "$out/libexec/${pname}/dist/bin.mjs"' in install_phase.value


def test_t3code_shared_build_keeps_workspace_and_hash_contracts() -> None:
    """The shared helper should keep runtime versioning separate from dependency FODs."""
    _assert_shared_binding(
        "rootPackageJson", 'builtins.fromJSON (builtins.readFile "${src}/package.json")'
    )
    _assert_shared_binding(
        "serverPackageJson",
        'builtins.fromJSON (builtins.readFile "${src}/apps/server/package.json")',
    )
    _assert_shared_binding("baseVersion", "serverPackageJson.version")
    _assert_shared_binding("version", '"${baseVersion}-main-${revSuffix}"')
    _assert_shared_binding("nodeModulesVersion", '"deps"')
    _assert_shared_binding("pnpm", "pnpm_10.override { inherit nodejs; }")
    _assert_shared_binding(
        "workspaceParentNames",
        """
        [
          "apps"
          "infra"
          "packages"
        ]
        """,
    )
    _assert_shared_binding(
        "workspaceParentDirs",
        """
        builtins.filter (
          parent: builtins.pathExists (src + "/${parent}")
        ) workspaceParentNames
        """,
    )
    _assert_shared_binding("rootWorkspaces", "rootPackageJson.workspaces or { }")
    _assert_shared_binding(
        "rootWorkspacePackagePatterns",
        """
        if builtins.isList rootWorkspaces then rootWorkspaces else rootWorkspaces.packages or [ ]
        """,
    )
    _assert_shared_binding(
        "explicitRootWorkspaceDirs",
        """
        builtins.filter (
          dir: !lib.hasInfix "*" dir && builtins.pathExists (src + "/${dir}/package.json")
        ) rootWorkspacePackagePatterns
        """,
    )
    _assert_shared_binding(
        "topLevelWorkspaceNames",
        """
        [
          "oxlint-plugin-t3code"
          "scripts"
        ]
        """,
    )
    _assert_shared_binding(
        "topLevelWorkspaceDirs",
        """
        builtins.filter (
          dir: builtins.pathExists (src + "/${dir}/package.json")
        ) topLevelWorkspaceNames
        """,
    )
    _assert_shared_binding("mobileModuleRoot", '"apps/mobile/modules"')
    _assert_shared_binding(
        "mobileModulePackageDirs",
        """
        lib.optionals (builtins.pathExists (src + "/${mobileModuleRoot}")) (
          map (name: "${mobileModuleRoot}/${name}") (childDirectoryNames (src + "/${mobileModuleRoot}"))
        )
        """,
    )
    _assert_shared_binding(
        "workspaceDirs",
        """
        lib.unique (
          nestedWorkspaceDirs
          ++ explicitRootWorkspaceDirs
          ++ topLevelWorkspaceDirs
        )
        """,
    )
    _assert_shared_binding(
        "workspaceBuildShellDirs", "lib.escapeShellArgs workspaceBuildDirectories"
    )
    _assert_shared_binding(
        "workspaceBuildDirectories",
        """
        lib.unique (
          workspaceParentDirs
          ++ explicitRootWorkspaceDirs
          ++ topLevelWorkspaceDirs
        )
        """,
    )
    dependency_source = expect_instance(
        expect_scope_binding(_shared_output(), "dependencySource").value,
        FunctionCall,
    )
    dependency_source_args = expect_instance(dependency_source.argument, AttributeSet)
    assert_nix_ast_equal(dependency_source.name, "builtins.path")
    assert_nix_ast_equal(
        expect_binding(dependency_source_args.values, "name").value,
        '"${pname}-dependency-source"',
    )
    _assert_shared_binding(
        "dependencySourceDirectories",
        """
        [
          ""
        ]
        ++ workspaceParentDirs
        ++ workspaceDirs
        ++ lib.optional (builtins.pathExists (src + "/${mobileModuleRoot}")) mobileModuleRoot
        ++ mobileModulePackageDirs
        ++ lib.optional (builtins.pathExists (src + "/patches")) "patches"
        """,
    )
    assert_nix_ast_equal(
        expect_binding(dependency_source_args.values, "filter").value,
        """
        path: type:
        let
          pathString = toString path;
          srcString = toString src;
          relativePath = if pathString == srcString then "" else lib.removePrefix "${srcString}/" pathString;
        in
        (type == "directory" && builtins.elem relativePath dependencySourceDirectories)
        || lib.hasPrefix "patches/" relativePath
        || builtins.elem relativePath (
          [
            "package.json"
            "pnpm-lock.yaml"
            "pnpm-workspace.yaml"
          ]
          ++ map (dir: "${dir}/package.json") workspaceDirs
          ++ map (dir: "${dir}/package.json") mobileModulePackageDirs
        )
        """,
    )

    node_modules = expect_instance(
        expect_scope_binding(_shared_output(), "node_modules").value,
        IfExpression,
    )
    assert_nix_ast_equal(node_modules.condition, "fetchPnpmDeps != null")
    assert_nix_ast_equal(node_modules.consequence, "fetchPnpmDeps args")
    assert_nix_ast_equal(node_modules.alternative, "pnpm.fetchDeps args")
    node_modules_args = expect_instance(
        expect_scope_binding(node_modules, "args").value,
        AttributeSet,
    )
    assert str(expect_binding(node_modules_args.values, "pname").value) == (
        '"${sourceHashPackageName}-node_modules"'
    )
    assert_nix_ast_equal(
        expect_binding(node_modules_args.values, "version").value,
        "nodeModulesVersion",
    )
    assert_nix_ast_equal(
        expect_binding(node_modules_args.values, "src").value,
        "dependencySource",
    )
    assert [
        [str(name) for name in item.names]
        for item in node_modules_args.values
        if isinstance(item, Inherit)
    ] == [["pnpm"]]
    assert_nix_ast_equal(
        expect_binding(node_modules_args.values, "fetcherVersion").value,
        "3",
    )
    assert_nix_ast_equal(
        expect_binding(node_modules_args.values, "hash").value,
        'outputs.lib.sourceHashForPlatform sourceHashPackageName "nodeModulesHash" system',
    )

    workspace_build = expect_instance(
        expect_scope_binding(_shared_output(), "workspaceBuild").value,
        FunctionCall,
    )
    workspace_build_args = expect_instance(workspace_build.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(workspace_build_args.values, "pnpmDeps").value,
        "node_modules",
    )
    workspace_build_phase = expect_instance(
        expect_binding(workspace_build_args.values, "buildPhase").value,
        IndentedString,
    )
    assert "chmod -R u+w node_modules ${workspaceBuildShellDirs}" in (
        workspace_build_phase.value
    )
    assert "find ${workspaceBuildShellDirs} -type d -name node_modules -print" in (
        workspace_build_phase.value
    )
    assert "pnpm run build:desktop" in workspace_build_phase.value
    workspace_install = expect_instance(
        expect_binding(workspace_build_args.values, "installPhase").value,
        IndentedString,
    )
    for snippet in (
        'cp -R apps/server/dist "$out/apps/server/dist"',
        'cp -R apps/web/dist "$out/apps/web/dist"',
        'cp -R apps/desktop/dist-electron "$out/apps/desktop/dist-electron"',
        'cp -R apps/desktop/resources "$out/apps/desktop/resources"',
    ):
        assert snippet in workspace_install.value
