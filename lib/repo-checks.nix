# Commands and observed inputs shared by local hooks and Nix checks. The
# adapters own execution context: worktree mutations versus isolated snapshots.
{
  src ? ../.,
  lib,
  lintFiles ? import ./lint-files.nix,
}:
let
  inherit (lintFiles.python)
    compilePaths
    pythonPyupgradeExcludes
    pythonScriptPaths
    ruffMutationExcludes
    ;
  pythonScriptFindPredicates = lib.concatMapStringsSep " " (
    path: "-o -path './${path}'"
  ) pythonScriptPaths;
  pythonPyupgradeFindPredicates = lib.concatMapStringsSep " " (
    path: "-o -path './${path}'"
  ) pythonPyupgradeExcludes;
  oxfmtPatterns = lintFiles.oxfmt.globs ++ map (glob: "!${glob}") lintFiles.oxfmt.excludeGlobs;
  # Keep each check keyed to the files it can observe. Read-only checks
  # run from these immutable store paths; only mutation checks copy one.
  mkCheckSource =
    fileset:
    lib.fileset.toSource {
      root = src;
      inherit fileset;
    };
  # Prune local dependencies before selecting check inputs. Native fileset
  # intersection keeps excluded directory trees lazy in path: flakes too.
  repoFiles = lib.fileset.difference src (
    lib.fileset.unions (
      map (path: lib.fileset.maybeMissing (src + "/${path}")) [
        ".git"
        ".direnv"
        ".venv"
        ".pytest_cache"
        ".ruff_cache"
        ".claude/worktrees"
        ".opencode/node_modules"
        "node_modules"
        "result"
      ]
    )
  );
  filesMatching =
    predicate: lib.fileset.intersection repoFiles (lib.fileset.fileFilter predicate src);
  filesWithExtensions =
    extensions: filesMatching (file: lib.any (extension: file.hasExt extension) extensions);
  filesetFromPaths = paths: lib.fileset.unions (map (path: src + "/${path}") paths);
  yamlFiles = filesWithExtensions [
    "yaml"
    "yml"
  ];
  yamlLintFiles = lib.fileset.difference yamlFiles (
    lib.fileset.fileFilter (file: file.hasExt "yaml") (src + "/lib/nix/schemas")
  );
  webFormatFiles = lib.fileset.difference (filesWithExtensions [
    "cjs"
    "css"
    "js"
    "json"
    "jsonc"
    "ts"
  ]) (src + "/schemas/codegen/testdata/lockfile-golden/expected.codegen.lock.json");
  webLintFiles = filesWithExtensions [
    "cjs"
    "js"
    "ts"
  ];
  pythonFiles = lib.fileset.unions [
    (filesWithExtensions [
      "py"
      "pyi"
    ])
    (filesetFromPaths pythonScriptPaths)
  ];
  generatedPythonFiles = filesMatching (file: file.name == "_generated.py");
  pyupgradeExcludedFiles = filesetFromPaths pythonPyupgradeExcludes;
  pyupgradeFiles = lib.fileset.unions [
    (src + "/.gitignore")
    (lib.fileset.difference pythonFiles (
      lib.fileset.unions [
        generatedPythonFiles
        pyupgradeExcludedFiles
      ]
    ))
  ];
  ruffFormatFiles = lib.fileset.difference pythonFiles (filesetFromPaths ruffMutationExcludes);
  pythonToolFiles = lib.fileset.unions [
    (src + "/.gitignore")
    (src + "/pyproject.toml")
    pythonFiles
  ];
  ruffFormatToolFiles = lib.fileset.unions [
    (src + "/.gitignore")
    (src + "/pyproject.toml")
    ruffFormatFiles
  ];
  schemaVerificationFiles = lib.fileset.unions [
    (src + "/.root")
    (src + "/pyproject.toml")
    (src + "/schema_codegen.yaml")
    (src + "/nixcfg.py")
    (src + "/lib/nix/models/_generated.py")
    (src + "/lib/schema_codegen/models/_generated.py")
    (lib.fileset.fileFilter (file: file.hasExt "yaml") (src + "/lib/nix/schemas"))
    (lib.fileset.fileFilter (file: file.hasExt "json") (src + "/schemas/codegen"))
  ];
in
{
  checks = {
    "lint-update-ci" = {
      source = mkCheckSource (src + "/.github");
      command =
        { lib, pkgs, ... }:
        ''
          ${lib.getExe pkgs.actionlint} .github/workflows/*.yml
        '';
    };

    "lint-editorconfig" = {
      source = mkCheckSource src;
      command =
        { lib, pkgs, ... }:
        ''
          ${lib.getExe pkgs."editorconfig-checker"} -exclude '^\.pre-commit-config\.yaml$'
        '';
    };

    "format-yaml-yamlfmt" = {
      source = mkCheckSource (
        lib.fileset.unions [
          (src + "/.gitignore")
          (src + "/.yamlfmt")
          yamlFiles
        ]
      );
      command =
        { lib, pkgs, ... }:
        ''
          ${lib.getExe pkgs.yamlfmt} -lint -gitignore_excludes -conf .yamlfmt .
        '';
    };

    "lint-yaml-yamllint" = {
      source = mkCheckSource (
        lib.fileset.unions [
          (src + "/.yamllint")
          yamlLintFiles
        ]
      );
      command =
        { lib, pkgs, ... }:
        ''
          ${lib.getExe pkgs.yamllint} -c .yamllint .
        '';
    };

    "format-web-oxfmt" = {
      source = mkCheckSource (
        lib.fileset.unions [
          (src + "/.editorconfig")
          (src + "/.oxfmtrc.json")
          (src + "/.gitignore")
          (src + "/flake.lock")
          webFormatFiles
        ]
      );
      command =
        { lib, pkgs, ... }:
        ''
          ${lib.getExe pkgs.oxfmt} --check --config .oxfmtrc.json --no-error-on-unmatched-pattern ${lib.escapeShellArgs oxfmtPatterns}
        '';
    };

    "lint-web-oxlint" = {
      source = mkCheckSource (
        lib.fileset.unions [
          (src + "/.gitignore")
          (src + "/.oxlintrc.json")
          webLintFiles
        ]
      );
      command =
        { lib, pkgs, ... }:
        ''
          OXLINT_TSGOLINT_PATH=${lib.getExe pkgs.tsgolint} ${lib.getExe pkgs.oxlint} --config .oxlintrc.json --type-aware --quiet .
        '';
    };

    "format-python-pyupgrade" = {
      repoWritable = true;
      nixcfg = true;
      source = mkCheckSource pyupgradeFiles;
      beforeCheck = { lib, pkgs, ... }: ''
        ${lib.getExe pkgs.git} init -q .
        ${lib.getExe pkgs.git} add -A
      '';
      afterCheck = { lib, pkgs, ... }: ''
        ${lib.getExe pkgs.git} diff --exit-code -- .
      '';
      command =
        {
          pkgs,
          nixcfgVenv,
          ...
        }:
        ''
          ${pkgs.findutils}/bin/find . \
            \( -path './.claude/worktrees' -o -path './.direnv' -o -path './.git' -o -path './.pytest_cache' -o -path './.ruff_cache' -o -path './.venv' -o -path './node_modules' -o -path './result' -o -name '_generated.py' ${pythonPyupgradeFindPredicates} \) -prune -o \
            -type f \
            \( -name '*.py' -o -name '*.pyi' ${pythonScriptFindPredicates} \) \
            -print0 \
            | ${pkgs.findutils}/bin/xargs -0 -r ${nixcfgVenv}/bin/pyupgrade --py314-plus
        '';
    };

    "lint-python-compile" = {
      nixcfg = true;
      source = mkCheckSource pythonFiles;
      command =
        { lib, nixcfgVenv, ... }:
        ''
          ${nixcfgVenv}/bin/python ${./check_python_compile.py} ${lib.escapeShellArgs compilePaths}
        '';
    };

    "format-python-ruff" = {
      nixcfg = true;
      source = mkCheckSource ruffFormatToolFiles;
      setup = ''
        export RUFF_CACHE_DIR="$TMPDIR/.ruff_cache"
      '';
      command =
        { nixcfgVenv, ... }:
        ''
          ${nixcfgVenv}/bin/ruff format --check --config pyproject.toml .
        '';
    };

    "lint-python-ruff" = {
      nixcfg = true;
      source = mkCheckSource pythonToolFiles;
      setup = ''
        export RUFF_CACHE_DIR="$TMPDIR/.ruff_cache"
      '';
      command =
        { nixcfgVenv, ... }:
        ''
          ${nixcfgVenv}/bin/ruff check --config pyproject.toml .
        '';
    };

    "lint-python-ty" = {
      nixcfg = true;
      source = mkCheckSource pythonToolFiles;
      command =
        { nixcfgVenv, ... }:
        ''
          ${nixcfgVenv}/bin/ty check --python ${nixcfgVenv}/bin/python .
        '';
    };

    "verify-python-generated" = {
      nixcfg = true;
      source = mkCheckSource schemaVerificationFiles;
      command =
        { nixcfgVenv, ... }:
        ''
          ${nixcfgVenv}/bin/python ./nixcfg.py schema verify
        '';
    };

  };
}
