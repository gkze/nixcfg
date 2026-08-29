{
  config,
  lib,
  pkgs,
  ...
}:
let
  catalog = import ./mcp-catalog.nix { inherit config lib pkgs; };
  macApps = import ../../lib/mac-apps.nix { inherit lib pkgs; };
  systemApp = package: {
    inherit package;
    scope = "system";
  };
  routing = {
    agentlog = systemApp pkgs.agentlog;
    aside = systemApp pkgs.aside;
    baseten-switch = systemApp pkgs.baseten-switch;
    bb = systemApp pkgs.bb;
    buzz = systemApp pkgs.buzz;
    "claude-code-url-handler".package = pkgs.claude-code-url-handler;
    cleanshot.package = pkgs.cleanshot;
    clearly = systemApp pkgs.clearly;
    coast-local = systemApp pkgs.coast-local;
    energy = systemApp pkgs.energy;
    executor = systemApp pkgs.executor;
    factory = systemApp pkgs.factory;
    freelens.package = pkgs.freelens;
    gemini = {
      package = pkgs.gemini;
      preventDowngrade = true;
      scope = "system";
    };
    github-copilot = systemApp pkgs.github-copilot-app;
    gooeypi = systemApp pkgs.gooeypi;
    grok-build.package = pkgs.grok-build;
    humanlayer = systemApp pkgs.humanlayer;
    hermes = systemApp pkgs.hermes-desktop;
    hq = systemApp pkgs.hq;
    mach-studio = systemApp pkgs.mach-studio;
    onepassword = systemApp pkgs.onepassword;
    openchamber = systemApp pkgs.openchamber;
    paseo = systemApp pkgs.paseo;
    reflect = systemApp pkgs.reflect-open;
    screen-studio = systemApp pkgs.screen-studio;
    tailscale.package = pkgs.tailscale-app;
    "town-assistant".package = pkgs.town-assistant-nightly;
    traycer = systemApp pkgs.traycer;
    unsloth = systemApp pkgs.unsloth;
    voiceos = systemApp pkgs.voiceos;
    waku = systemApp pkgs.waku;
    "warp-preview".package = pkgs.warp-preview;
    writer-computer = systemApp pkgs.writer-computer;
    zeron = systemApp pkgs.zeron;
    zo = systemApp pkgs.zo;
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
      extraPackages = [
        pkgs.baseten
        pkgs.baseten-switch.cliPackage
        pkgs.executor.cliPackage
        pkgs.pants-preview
        pkgs.traycer.cliPackage
        pkgs.writer-computer.cliPackage
      ];
    };
    opencode = {
      activeProfile = lib.mkDefault "work";
      profiles.work.mcpServers = catalog.work;
    };
  };

  launchd.agents."ai.traycer.host" = {
    enable = true;
    config = {
      Label = "ai.traycer.host";
      AssociatedBundleIdentifiers = [ "ai.traycer.desktop" ];
      ProgramArguments = [
        "${pkgs.traycer.cliPackage}/bin/traycer"
        "host"
        "start"
        "--service-label"
        "ai.traycer.host"
      ];
      RunAtLoad = true;
      KeepAlive = {
        SuccessfulExit = false;
        Crashed = true;
      };
      ThrottleInterval = 10;
      ProcessType = "Interactive";
      SoftResourceLimits = {
        NumberOfFiles = 8192;
      };
      EnvironmentVariables = {
        HOME = config.home.homeDirectory;
        NODE_OPTIONS = "--max-semi-space-size=16";
        PATH = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";
      };
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
