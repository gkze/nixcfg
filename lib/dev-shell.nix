{
  src ? ../.,
  lib,
  gitHooks,
}:
pkgs:
let
  hookPriority = 10;

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

      format-web-biome = {
        enable = true;
        name = "format-web-biome";
        package = pkgs.biome;
        entry = "biome check --config-path biome.jsonc --diagnostic-level=error .";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      format-python-ruff = {
        enable = true;
        name = "format-python-ruff";
        package = pkgs.ruff;
        entry = "ruff format --check --config pyproject.toml .";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      lint-python-ruff = {
        enable = true;
        name = "lint-python-ruff";
        package = pkgs.ruff;
        entry = "ruff check --config pyproject.toml .";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      lint-python-ty = {
        enable = true;
        name = "lint-python-ty";
        package = pkgs.ty;
        entry = "ty check .";
        pass_filenames = false;
        always_run = true;
        priority = hookPriority;
      };

      lint-workflows-actionlint = {
        enable = true;
        name = "lint-workflows-actionlint";
        package = pkgs.actionlint;
        entry = "actionlint";
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
      biome
      flake-edit
      go
      nh
      nil
      nix-init
      nixos-generators
      nurl
      pinact
      prek
      sops
      taplo
      uv
      yamlfmt
    ]
    ++ lib.optional (pkgs ? nix-manipulator) nix-manipulator
    ++ lib.optional pkgs.stdenv.isLinux dconf2nix
    ++ pre-commit-check.enabledPackages;

  devshell.startup.pre-commit.text = pre-commit-check.shellHook;
  devshell.startup.commitlint-node-modules.text = ''
    mkdir -p node_modules
    ln -sfn "${pkgs.commitlint}/lib/node_modules/@commitlint/root/node_modules/@commitlint" node_modules/@commitlint
    ln -sfn "${pkgs.typescript}/lib/node_modules/typescript" node_modules/typescript
  '';
}
