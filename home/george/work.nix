{
  config,
  lib,
  pkgs,
  ...
}:
let
  catalog = import ./mcp-catalog.nix { inherit config lib pkgs; };
  macApps = import ../../lib/mac-apps.nix { inherit lib pkgs; };
  routing = {
    cleanshot.package = pkgs.cleanshot;
    freelens.package = pkgs.freelens;
    onepassword = {
      package = pkgs.onepassword;
      scope = "system";
    };
    tailscale.package = pkgs.tailscale-app;
    "town-assistant".package = pkgs.town-assistant-nightly;
    "warp-preview".package = pkgs.warp-preview;
  };
  projection = macApps.managedMacAppRoutingProjection routing;
in
{
  home.packages = with pkgs; [
    _1password-cli
    anthropic-cli
    google-cloud-sdk
    linear-cli
    linearis
    openai-cli
    pscale
  ];

  nixcfg = {
    macApps.applications = projection.applications;
    packageSets = {
      inherit (projection) excludePackagesByName;
      extraPackages = [ pkgs.pants-preview ];
    };
    opencode = {
      activeProfile = lib.mkDefault "work";
      profiles.work.mcpServers = catalog.work;
    };
  };

  programs = {
    topgrade.settings.misc.disable = [ "gcloud" ];
    zsh.plugins = [
      {
        name = "op";
        src = pkgs.runCommand "_op" { buildInputs = [ pkgs._1password-cli ]; } ''
          mkdir "$out"
          HOME="$(mktemp -d)" op completion zsh > "$out/_op"
        '';
        file = "_op";
      }
    ];
  };
}
