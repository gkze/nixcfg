{
  src ? ../.,
  lib,
  gitHooks,
}:
pkgs:
let
  qualityPriority = 10;
  pythonQualityCheck = pkgs.writeShellScript "quality-python" ''
    set -euo pipefail
    ${lib.getExe pkgs.uv} run ruff check --config pyproject.toml .
    ${lib.getExe pkgs.uv} run ty check .
  '';
  pytestQualityCheck = pkgs.writeShellScript "quality-pytest" ''
    set -euo pipefail
    ${lib.getExe pkgs.uv} run coverage run -m pytest
    ${lib.getExe pkgs.uv} run coverage report
  '';
  ghawfrGoQualityCheck = pkgs.writeShellScript "quality-ghawfr-go" ''
    set -euo pipefail
    cd ghawfr
    ${lib.getExe pkgs.go} test ./...
  '';
  yamllintQualityCheck = pkgs.writeShellScript "quality-yamllint" ''
    set -euo pipefail
    ${lib.getExe pkgs.git} ls-files -z -- '*.yml' '*.yaml' \
      | ${lib.getExe' pkgs.findutils "xargs"} -0 -r ${lib.getExe pkgs.yamllint} -c .yamllint
  '';
  cssQualityCheck = pkgs.writeShellScript "quality-css" ''
    set -euo pipefail
    ${lib.getExe pkgs.git} ls-files -z -- '*.css' \
      | ${lib.getExe' pkgs.findutils "xargs"} -0 -r ${lib.getExe pkgs.biome} check \
          --config-path biome.jsonc \
          --error-on-warnings \
          --max-diagnostics=none
  '';

  pre-commit-check = gitHooks.lib.${pkgs.system}.run {
    inherit src;
    package = pkgs.prek;
    hooks = {
      quality-format = {
        enable = true;
        name = "quality-format";
        entry = "${lib.getExe pkgs.nix} fmt -- --ci";
        pass_filenames = false;
        always_run = true;
        priority = qualityPriority;
      };

      quality-editorconfig = {
        enable = true;
        name = "quality-editorconfig";
        entry = lib.getExe pkgs."editorconfig-checker";
        pass_filenames = false;
        always_run = true;
        priority = qualityPriority;
      };

      quality-python = {
        enable = true;
        name = "quality-python";
        entry = "${pythonQualityCheck}";
        pass_filenames = false;
        always_run = true;
        priority = qualityPriority;
      };

      quality-pytest = {
        enable = true;
        name = "quality-pytest";
        entry = "${pytestQualityCheck}";
        pass_filenames = false;
        always_run = true;
        priority = qualityPriority;
        stages = [
          "pre-commit"
          "manual"
        ];
      };

      quality-ghawfr-go = {
        enable = true;
        name = "quality-ghawfr-go";
        entry = "${ghawfrGoQualityCheck}";
        pass_filenames = false;
        always_run = true;
        priority = qualityPriority;
        stages = [
          "pre-commit"
          "manual"
        ];
      };

      commitlint = {
        enable = true;
        entry = "${lib.getExe pkgs.commitlint} --edit";
        pass_filenames = true;
        always_run = true;
        priority = qualityPriority;
        stages = [ "commit-msg" ];
      };

      quality-actionlint = {
        enable = true;
        name = "quality-actionlint";
        entry = "${lib.getExe pkgs.actionlint}";
        pass_filenames = false;
        always_run = true;
        priority = qualityPriority;
      };

      quality-pinact = {
        enable = true;
        name = "quality-pinact";
        entry = "${lib.getExe pkgs.pinact} run --check";
        pass_filenames = false;
        always_run = true;
        priority = qualityPriority;
      };

      quality-yamllint = {
        enable = true;
        name = "quality-yamllint";
        entry = "${yamllintQualityCheck}";
        pass_filenames = false;
        always_run = true;
        priority = qualityPriority;
      };

      quality-css = {
        enable = true;
        name = "quality-css";
        entry = "${cssQualityCheck}";
        pass_filenames = false;
        always_run = true;
        priority = qualityPriority;
      };

      check-merge-conflicts = {
        enable = true;
        priority = 0;
      };
      end-of-file-fixer = {
        enable = true;
        priority = 1;
      };
      trim-trailing-whitespace = {
        enable = true;
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
    ln -sfn "${pkgs.nodePackages.typescript}/lib/node_modules/typescript" node_modules/typescript
  '';
}
