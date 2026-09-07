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
  repoChecks = (import ./repo-checks.nix { inherit src lib lintFiles; }).checks;
  standardHooks = lib.mapAttrs (
    name: spec:
    let
      package = pkgs.writeShellScriptBin "check-${name}" ''
        set -euo pipefail
        ${spec.command { inherit lib pkgs nixcfgVenv; }}
      '';
    in
    {
      enable = true;
      inherit name package;
      entry = lib.getExe package;
      pass_filenames = false;
      always_run = true;
      priority = hookPriority;
    }
  ) repoChecks;

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
    ++ [
      nixcfgPkg
      pythonToolBins
    ]
    ++ lib.optional pkgs.stdenv.hostPlatform.isLinux dconf2nix
    ++ pre-commit-check.enabledPackages;

  devshell.startup.pre-commit.text = pre-commit-check.shellHook;
  devshell.startup.commitlint-node-modules.text = ''
    mkdir -p node_modules
    ln -sfn "${pkgs.commitlint}/lib/node_modules/@commitlint/root/node_modules/@commitlint" node_modules/@commitlint
    ln -sfn "${pkgs.typescript}/lib/node_modules/typescript" node_modules/typescript
  '';
}
