{ outputs, ... }:
let
  inherit (outputs) lib;
in
lib.mkDarwinHost {
  user = "george";
  work = true;
  brewAppsModule = "${lib.modulesPath}/darwin/george/brew-apps.nix";
  extraHomeModules = [
    "${lib.modulesPath}/home/darwin-closure-priority.nix"
    (
      { pkgs, ... }:
      {
        # Install a few large host-specific tools on argus without opting this
        # host into the full
        # heavyOptional package set.
        nixcfg.packageSets.extraPackages = [
          pkgs.goose-cli
          pkgs.gws
        ];
      }
    )
  ];
  extraSystemModules = [
    {
      # Preserve any pre-existing app/editor settings the first time Home Manager
      # takes over files like ~/.gemini/settings.json and VS Code Insiders
      # settings.json.
      home-manager.backupFileExtension = "backup";

      # The default 6 GiB Rosetta Linux builder VM is too small for some of
      # the heavier x86_64-linux Rust builds we run from argus.
      nix-rosetta-builder.memory = "16GiB";
    }
    "${lib.modulesPath}/darwin/george/town-dock-apps.nix"
    (lib.mkSetOpencodeEnvModule "work.json")
  ];
}
