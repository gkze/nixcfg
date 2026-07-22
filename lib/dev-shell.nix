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
  oxfmtPatterns = lintFiles.oxfmt.globs ++ map (glob: "!${glob}") lintFiles.oxfmt.excludeGlobs;

  # Keep the established pyupgrade floor until Python 3.14 rewrites are applied
  # as an intentional repo-wide formatting migration.
  pythonPyupgradeCheck = pkgs.writeShellScriptBin "check-python-pyupgrade" ''
    set -euo pipefail

    find . \
      \( -path './.claude/worktrees' -o -path './.direnv' -o -path './.git' -o -path './.pytest_cache' -o -path './.ruff_cache' -o -path './.venv' -o -path './node_modules' -o -path './result' -o -name '_generated.py' \) -prune -o \
      -type f \
      \( -name '*.py' -o -name '*.pyi' ${pythonScriptFindPredicates} \) \
      -print0 \
      | ${pkgs.findutils}/bin/xargs -0 -r ${pythonExe} -m lib.fix_python_multi_except --pyupgrade-exe ${pyupgradeExe} --pyupgrade-arg=--py313-plus
  '';

  pythonCompileCheck = pkgs.writeShellScriptBin "check-python-compile" ''
    set -euo pipefail

    ${pythonExe} ${./check_python_compile.py} ${lib.escapeShellArgs lintFiles.python.compilePaths}
  '';

  workflowActionlintCheck = pkgs.writeShellScriptBin "check-workflow-actionlint" ''
    set -euo pipefail

    ${pkgs.findutils}/bin/find .github/workflows -maxdepth 1 -type f ! -name '*.lock.yml' \( -name '*.yml' -o -name '*.yaml' \) -print0 \
      | ${pkgs.findutils}/bin/xargs -0 ${lib.getExe pkgs.actionlint}
  '';

  pre-commit-check = gitHooks.lib.${pkgs.system}.run {
    inherit src;
    package = pkgs.prek;
    hooks = {
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

      lint-editorconfig = {
        enable = true;
        name = "lint-editorconfig";
        package = pkgs."editorconfig-checker";
        entry = "editorconfig-checker -exclude ^\\.pre-commit-config\\.yaml$";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      format-yaml-yamlfmt = {
        enable = true;
        name = "format-yaml-yamlfmt";
        package = pkgs.yamlfmt;
        entry = "yamlfmt -lint -gitignore_excludes -conf .yamlfmt .";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      lint-yaml-yamllint = {
        enable = true;
        name = "lint-yaml-yamllint";
        package = pkgs.yamllint;
        entry = "yamllint -c .yamllint .";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      lint-agentic-workflows-gh-aw = {
        enable = true;
        name = "lint-agentic-workflows-gh-aw";
        package = pkgs.gh;
        entry = "gh aw compile update-self-heal --no-check-update --no-emit --approve";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      format-web-oxfmt = {
        enable = true;
        name = "format-web-oxfmt";
        package = pkgs.oxfmt;
        entry = "oxfmt --check --config .oxfmtrc.json --no-error-on-unmatched-pattern ${lib.escapeShellArgs oxfmtPatterns}";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      lint-web-oxlint = {
        enable = true;
        name = "lint-web-oxlint";
        package = pkgs.oxlint;
        entry = "env OXLINT_TSGOLINT_PATH=${lib.getExe pkgs.tsgolint} oxlint --config .oxlintrc.json --type-aware --quiet .";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      format-python-pyupgrade = {
        enable = true;
        name = "format-python-pyupgrade";
        package = pythonPyupgradeCheck;
        entry = "${pythonPyupgradeCheck}/bin/check-python-pyupgrade";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      format-python-ruff = {
        enable = true;
        name = "format-python-ruff";
        package = pythonToolBins;
        entry = "${ruffExe} format --check --config pyproject.toml .";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      lint-python-compile = {
        enable = true;
        name = "lint-python-compile";
        package = pythonCompileCheck;
        entry = "${pythonCompileCheck}/bin/check-python-compile";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      lint-python-ruff = {
        enable = true;
        name = "lint-python-ruff";
        package = pythonToolBins;
        entry = "${ruffExe} check --config pyproject.toml .";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      lint-python-ty = {
        enable = true;
        name = "lint-python-ty";
        package = pythonToolBins;
        entry = "${tyExe} check${tyPythonFlag} .";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      lint-workflows-actionlint = {
        enable = true;
        name = "lint-workflows-actionlint";
        package = workflowActionlintCheck;
        entry = "${workflowActionlintCheck}/bin/check-workflow-actionlint";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      lint-pins-pinact = {
        enable = true;
        name = "lint-pins-pinact";
        package = pkgs.pinact;
        entry = "pinact run --check";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
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
      pinact
      prek
      sops
      taplo
      uv
      yamlfmt
    ]
    ++ [ nixcfgPkg ]
    ++ lib.optional pkgs.stdenv.isLinux dconf2nix
    ++ pre-commit-check.enabledPackages;

  devshell.startup.pre-commit.text = pre-commit-check.shellHook;
  devshell.startup.commitlint-node-modules.text = ''
    mkdir -p node_modules
    ln -sfn "${pkgs.commitlint}/lib/node_modules/@commitlint/root/node_modules/@commitlint" node_modules/@commitlint
    ln -sfn "${pkgs.typescript}/lib/node_modules/typescript" node_modules/typescript
  '';
}
