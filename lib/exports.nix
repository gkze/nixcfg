{
  src ? ../.,
}:
let
  modulesRoot = src + "/modules";
  apiVersion = 1;
  constructorNames = [
    "mkSystem"
    "mkDarwinHost"
    "mkHome"
    "mkHomeModules"
    "mkSetOpencodeEnvModule"
  ];

  moduleSets = {
    nixosModules = {
      nixcfgCommon = modulesRoot + "/common.nix";
      nixcfgBase = modulesRoot + "/nixos/base.nix";
      nixcfgProfiles = modulesRoot + "/nixos/profiles.nix";
    };

    darwinModules = {
      nixcfgCommon = modulesRoot + "/common.nix";
      nixcfgBase = modulesRoot + "/darwin/base.nix";
      nixcfgProfiles = modulesRoot + "/darwin/profiles.nix";
      nixcfgHomebrew = modulesRoot + "/darwin/homebrew.nix";
    };

    homeModules = {
      nixcfgBase = modulesRoot + "/home/base.nix";
      nixcfgGit = modulesRoot + "/home/git.nix";
      nixcfgProfiles = modulesRoot + "/home/profiles.nix";
      nixcfgPackages = modulesRoot + "/home/packages.nix";
      nixcfgOpencode = modulesRoot + "/home/opencode.nix";
      nixcfgTheme = modulesRoot + "/home/theme.nix";
      nixcfgFonts = modulesRoot + "/home/fonts.nix";
      nixcfgStylix = modulesRoot + "/home/stylix.nix";
      nixcfgZsh = modulesRoot + "/home/zsh.nix";
      nixcfgZen = modulesRoot + "/home/zen.nix";
      nixcfgDarwin = modulesRoot + "/home/darwin.nix";
      nixcfgLinux = modulesRoot + "/home/linux.nix";
      nixcfgLanguageBun = modulesRoot + "/home/languages/bun.nix";
      nixcfgLanguageGo = modulesRoot + "/home/languages/go.nix";
      nixcfgLanguagePython = modulesRoot + "/home/languages/python.nix";
      nixcfgLanguageRust = modulesRoot + "/home/languages/rust.nix";
    };
  };
  api = {
    version = apiVersion;
    inherit constructorNames moduleSets;
  };
in
{
  inherit api apiVersion constructorNames moduleSets;
  inherit (moduleSets)
    darwinModules
    homeModules
    nixosModules
    ;
}
