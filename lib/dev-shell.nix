{
  src ? ../.,
  lib,
  gitHooks,
  lintFiles ? import ./lint-files.nix,
}:
pkgs:
let
  nixcfgScript = if builtins.hasAttr "nixcfg" pkgs then pkgs.nixcfg else null;

  tyPythonFlag = if nixcfgScript != null then " --python ${nixcfgScript}/bin/python" else "";

  pre-commit-check = gitHooks.lib.${pkgs.system}.run {
    inherit src;
    package = pkgs.prek;
    hooks = {
      nixfmt.enable = true;
      deadnix.enable = true;
      statix.enable = true;

      ruff = {
        enable = true;
        files = lintFiles.ruff.regex;
        entry = "${lib.getExe pkgs.ruff} check --config pyproject.toml .";
        pass_filenames = false;
        always_run = true;
      };

      ruff-format = {
        enable = true;
        files = lintFiles.ruff.regex;
      };

      ty = {
        enable = true;
        files = lintFiles.ruff.regex;
        package = pkgs.ty;
        entry = "${lib.getExe pkgs.ty} check${tyPythonFlag} .";
        pass_filenames = false;
        always_run = true;
      };

      pytest-coverage = {
        enable = true;
        entry = "${lib.getExe pkgs.uv} run ${lib.getExe pkgs.bash} -c 'coverage run -m pytest && coverage report'";
        pass_filenames = false;
        always_run = true;
        stages = [
          "pre-commit"
          "manual"
        ];
      };

      commitlint = {
        enable = true;
        package = pkgs.commitlint;
        entry = "${lib.getExe pkgs.commitlint} --edit";
        pass_filenames = true;
        always_run = true;
        stages = [ "commit-msg" ];
      };

      shellcheck = {
        enable = true;
        files = lintFiles.shell.regex;
        excludes = lintFiles.shell.excludeRegex;
      };

      shfmt = {
        enable = true;
        files = lintFiles.shell.regex;
        excludes = lintFiles.shell.excludeRegex;
        args = [
          "-i"
          "2"
          "-s"
        ];
      };

      mdformat = {
        enable = true;
        package = pkgs.python3.withPackages (
          ps: with ps; [
            mdformat
            mdformat-tables
          ]
        );
      };

      yamlfmt = {
        enable = true;
        args = [
          "-conf"
          ".yamlfmt"
        ];
      };

      yamllint = {
        enable = true;
        args = [
          "-c"
          ".yamllint"
        ];
      };

      actionlint = {
        enable = true;
        files = "^\\.github/workflows/.*\\.ya?ml$";
      };

      taplo = {
        enable = true;
        files = lintFiles.toml.regex;
        entry = "${lib.getExe pkgs.taplo} format --config .taplo.toml";
      };

      check-merge-conflicts.enable = true;
      end-of-file-fixer.enable = true;
      trim-trailing-whitespace = {
        enable = true;
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
      nh
      nil
      nix-init
      nixos-generators
      nurl
      prek
      sops
      taplo
      yamlfmt
    ]
    ++ lib.optional (pkgs ? nix-manipulator) nix-manipulator
    ++ lib.optional pkgs.stdenv.isLinux dconf2nix
    ++ pre-commit-check.enabledPackages;

  devshell.startup.pre-commit.text = pre-commit-check.shellHook;
  devshell.startup.commitlint-node-modules.text = ''
    mkdir -p node_modules
    ln -sfn "${pkgs.commitlint}/lib/node_modules/@commitlint/root/node_modules/@commitlint" node_modules/@commitlint
    ln -sfn "${pkgs.nodePackages.typescript}/lib/node_modules/typescript" node_modules/typescript
  '';
}
