{
  lib,
  src ? ../..,
}:
let
  projectionLib = lib // {
    fileset = lib.fileset // {
      toSource = { fileset, ... }: lib.fileset.toList fileset;
    };
  };
  # Observe the real fileset projection without copying its contents to the store.
  inherit
    (
      (import (src + "/lib/repo-checks.nix") {
        inherit src;
        lib = projectionLib;
      })
    )
    checks
    ;
  fixtureSrc = src + "/tests/nix/fixtures/repo_checks";
  fixtureChecks =
    (import (src + "/lib/repo-checks.nix") {
      src = fixtureSrc;
      lib = projectionLib // {
        fileset = projectionLib.fileset // {
          # Excluded trees must stay lazy, not merely disappear from the result.
          fileFilter =
            predicate:
            lib.fileset.fileFilter (
              file:
              assert file.name != "dependency.py";
              predicate file
            );
        };
      };
      lintFiles.python.pythonScriptPaths = [ ];
    }).checks;
  includes = check: path: builtins.elem (src + "/${path}") checks.${check}.source;
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";
  results = [
    (assertEq "local dependency trees are excluded from Python inputs" [
      (fixtureSrc + "/__init__.py")
    ] fixtureChecks.lint-python-compile.source)
    (assertEq "editorconfig retains its complete source contract" true (
      builtins.elem (fixtureSrc + "/.direnv/dependency.py") fixtureChecks.lint-editorconfig.source
      && builtins.elem (fixtureSrc + "/node_modules/dependency.py") fixtureChecks.lint-editorconfig.source
    ))
    (assertEq "compile checks extensionless Python" true (
      includes "lint-python-compile" "home/george/bin/zentool"
    ))
    (assertEq "pyupgrade checks maintained Python" true (
      includes "format-python-pyupgrade" "nixcfg.py"
    ))
    (assertEq "pyupgrade preserves compatibility helpers" false (
      includes "format-python-pyupgrade" "lib/exact_text_patch.py"
    ))
    (assertEq "pyupgrade preserves generated Python" false (
      includes "format-python-pyupgrade" "lib/nix/models/_generated.py"
    ))
    (assertEq "Ruff formatting preserves runtime-sensitive helpers" false (
      includes "format-python-ruff" "lib/update/persistence.py"
    ))
    (assertEq "Ruff still lints runtime-sensitive helpers" true (
      includes "lint-python-ruff" "lib/update/persistence.py"
    ))
    (assertEq "Ruff source includes its configuration" true (
      includes "lint-python-ruff" "pyproject.toml"
    ))
    (assertEq "web formatting preserves golden output" false (
      includes "format-web-oxfmt" "schemas/codegen/testdata/lockfile-golden/expected.codegen.lock.json"
    ))
    (assertEq "schema verification observes its manifest" true (
      includes "verify-python-generated" "schema_codegen.yaml"
    ))
    (assertEq "schema verification observes generated output" true (
      includes "verify-python-generated" "lib/nix/models/_generated.py"
    ))
    (assertEq "Python lint excludes unrelated Nix sources" false (
      includes "lint-python-ruff" "flake.nix"
    ))
  ];
in
# Actual fileset inclusion, not the spelling of the selectors, is the cache contract.
builtins.deepSeq results true
