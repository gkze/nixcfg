{
  src ? ../.,
  lib,
  gitHooks,
  lintFiles,
  mkNixcfgPackage ? null,
}:
pkgs:
let
  hookPriority = 10;
  nixcfgPkg =
    if mkNixcfgPackage == null then
      throw "lib/dev-shell.nix: mkNixcfgPackage is required for uv2nix-managed Python tooling."
    else
      mkNixcfgPackage pkgs;
  nixcfgVenv = nixcfgPkg.passthru.venv;
  pythonToolBins = pkgs.runCommand "nixcfg-python-tool-bins" { } ''
    mkdir -p "$out/bin"
    for tool in ${nixcfgVenv}/bin/*; do
      name="$(basename "$tool")"
      [ "$name" = nixcfg ] && continue
      ln -s "$tool" "$out/bin/$name"
    done
  '';
  pythonExe = "${nixcfgVenv}/bin/python";
  pyupgradeExe = "${pythonToolBins}/bin/pyupgrade";
  ruffExe = "${pythonToolBins}/bin/ruff";
  tyExe = "${pythonToolBins}/bin/ty";
  tyPythonFlag = " --python ${pythonExe}";
  pythonScriptFindPredicates = lib.concatMapStringsSep " " (
    path: "-o -path './${path}'"
  ) lintFiles.python.pythonScriptPaths;
  pythonPyupgradeFindPredicates = lib.concatMapStringsSep " " (
    path: "-o -path './${path}'"
  ) lintFiles.python.pythonPyupgradeExcludes;
  oxfmtPatterns = lintFiles.oxfmt.globs ++ map (glob: "!${glob}") lintFiles.oxfmt.excludeGlobs;

  pythonPyupgradeCheck = pkgs.writeShellScriptBin "check-python-pyupgrade" ''
    set -euo pipefail

    find . \
      \( -path './.claude/worktrees' -o -path './.direnv' -o -path './.git' -o -path './.pytest_cache' -o -path './.ruff_cache' -o -path './.venv' -o -path './node_modules' -o -path './result' -o -name '_generated.py' ${pythonPyupgradeFindPredicates} \) -prune -o \
      -type f \
      \( -name '*.py' -o -name '*.pyi' ${pythonScriptFindPredicates} \) \
      -print0 \
      | ${pkgs.findutils}/bin/xargs -0 -r ${pyupgradeExe} --py314-plus
  '';

  pythonCompileCheck = pkgs.writeShellScriptBin "check-python-compile" ''
    set -euo pipefail

    ${pythonExe} ${./check_python_compile.py} ${lib.escapeShellArgs lintFiles.python.compilePaths}
  '';

  standardHookSpecs = {
    lint-editorconfig = {
      package = pkgs."editorconfig-checker";
      entry = "editorconfig-checker -exclude ^\\.pre-commit-config\\.yaml$";
    };
    format-yaml-yamlfmt = {
      package = pkgs.yamlfmt;
      entry = "yamlfmt -lint -gitignore_excludes -conf .yamlfmt .";
    };
    lint-yaml-yamllint = {
      package = pkgs.yamllint;
      entry = "yamllint -c .yamllint .";
    };
    format-web-oxfmt = {
      package = pkgs.oxfmt;
      entry = "oxfmt --check --config .oxfmtrc.json --no-error-on-unmatched-pattern ${lib.escapeShellArgs oxfmtPatterns}";
    };
    lint-web-oxlint = {
      package = pkgs.oxlint;
      entry = "env OXLINT_TSGOLINT_PATH=${lib.getExe pkgs.tsgolint} oxlint --config .oxlintrc.json --type-aware --quiet .";
    };
    format-python-pyupgrade = {
      package = pythonPyupgradeCheck;
      entry = "${pythonPyupgradeCheck}/bin/check-python-pyupgrade";
    };
    format-python-ruff = {
      package = pythonToolBins;
      entry = "${ruffExe} format --check --config pyproject.toml .";
    };
    lint-python-compile = {
      package = pythonCompileCheck;
      entry = "${pythonCompileCheck}/bin/check-python-compile";
    };
    lint-python-ruff = {
      package = pythonToolBins;
      entry = "${ruffExe} check --config pyproject.toml .";
    };
    lint-python-ty = {
      package = pythonToolBins;
      entry = "${tyExe} check${tyPythonFlag} .";
    };
    verify-python-generated = {
      package = pythonToolBins;
      entry = "${pythonExe} ./nixcfg.py schema verify";
    };
  };
  standardHooks = lib.mapAttrs (
    name: spec:
    {
      enable = true;
      inherit name;
      pass_filenames = false;
      always_run = true;
      priority = hookPriority;
    }
    // spec
  ) standardHookSpecs;

  pre-commit-check = gitHooks.lib.${pkgs.system}.run {
    inherit src;
    package = pkgs.prek;
    hooks = standardHooks // {
      format-repo = {
        enable = true;
        name = "format-repo";
        package = pkgs.nix;
        entry = "nix fmt -- --ci";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
        stages = [ "manual" ];
      };

      commit-message-commitlint = {
        enable = true;
        name = "commit-message-commitlint";
        package = pkgs.commitlint;
        entry = "commitlint --edit";
        pass_filenames = true;
        always_run = true;
        priority = hookPriority;
        stages = [ "commit-msg" ];
      };

      check-merge-conflicts = {
        enable = true;
        id = "guard-merge-conflicts";
        name = "guard-merge-conflicts";
        priority = 0;
      };

      end-of-file-fixer = {
        enable = true;
        id = "fix-end-of-file";
        name = "fix-end-of-file";
        priority = 1;
      };

      trim-trailing-whitespace = {
        enable = true;
        id = "fix-trailing-whitespace";
        name = "fix-trailing-whitespace";
        excludes = [ "\\.patch$" ];
        priority = 2;
        stages = [
          "pre-commit"
          "manual"
        ];
      };
    };
  };
in
pkgs.devshell.mkShell {
  name = "nixcfg";

  packages =
    with pkgs;
    [
      flake-edit
      go
      nh
      nil
      nix-init
      nixos-generators
      oxfmt
      oxlint
      tsgolint
      nurl
      prek
      taplo
      uv
      yamlfmt
    ]
    ++ [ nixcfgPkg ]
    ++ lib.optional pkgs.stdenv.hostPlatform.isLinux dconf2nix
    ++ pre-commit-check.enabledPackages;

  devshell.startup.pre-commit.text = pre-commit-check.shellHook;
  devshell.startup.commitlint-node-modules.text = ''
    mkdir -p node_modules
    ln -sfn "${pkgs.commitlint}/lib/node_modules/@commitlint/root/node_modules/@commitlint" node_modules/@commitlint
    ln -sfn "${pkgs.typescript}/lib/node_modules/typescript" node_modules/typescript
  '';
}
