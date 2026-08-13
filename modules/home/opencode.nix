{
  config,
  lib,
  ...
}:
let
  inherit (lib)
    concatLists
    mapAttrsToList
    mkEnableOption
    mkIf
    mkOption
    optionalAttrs
    types
    ;

  cfg = config.nixcfg.opencode;
  opencodeMcpLib = import ../../lib/opencode-mcp.nix { inherit lib; };

  profileType = types.submodule {
    options = {
      mcpServers = mkOption {
        type = opencodeMcpLib.sparseMcpServerOverrideMapType;
        default = { };
        description = "Per-profile MCP server overrides layered over nixcfg.opencode.mcpServers.";
      };

      settings = mkOption {
        type = types.attrsOf types.anything;
        default = { };
        description = "Additional top-level runtime settings layered over the shared OpenCode config.";
      };
    };
  };

  mergeProfileMcpServers =
    profileMcpServers: opencodeMcpLib.resolveSparseMcpServerOverrides cfg.mcpServers profileMcpServers;

  baseOpencodeTui = {
    theme = config.theme.slug;
    scroll_acceleration.enabled = true;
  };

  baseOpencodeSettings = optionalAttrs (cfg.plugins != [ ]) {
    plugin = cfg.plugins;
  };

  emptyProfile = {
    settings = { };
    mcpServers = { };
  };

  mkProfileOverlayConfig = opencodeMcpLib.mkProfileOverlayConfig cfg.mcpServers;

  selectedProfileConfig = cfg.profiles.${cfg.activeProfile} or emptyProfile;
  selectedProfilePath = "${config.home.homeDirectory}/.config/opencode/${cfg.activeProfile}.json";
  staleProfileJsonPaths =
    map (fileName: "${config.home.homeDirectory}/.config/opencode/${fileName}")
      (
        (builtins.map (profileName: "${profileName}.json") (
          builtins.filter (profileName: profileName != cfg.activeProfile) (builtins.attrNames cfg.profiles)
        ))
        ++ [ "active.json" ]
      );
in
{
  options.nixcfg.opencode = {
    enable = mkEnableOption "OpenCode client configuration" // {
      default = true;
    };

    activeProfile = mkOption {
      type = types.str;
      default = "personal";
      description = "Named OpenCode profile materialized to opencode/<name>.json and selected via OPENCODE_CONFIG.";
    };

    profiles = mkOption {
      type = types.attrsOf profileType;
      default = {
        personal = { };
      };
      description = "Named OpenCode profile overrides layered over the shared global config.";
    };

    mcpServers = mkOption {
      type = opencodeMcpLib.mcpServerMapType;
      default = { };
      description = "Base MCP server definitions written to the global OpenCode config; servers default to disabled and can be enabled on demand.";
    };

    plugins = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "OpenCode plugins to install.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = builtins.hasAttr cfg.activeProfile cfg.profiles;
        message = "nixcfg.opencode.activeProfile must match a key in nixcfg.opencode.profiles.";
      }
    ]
    ++ opencodeMcpLib.mkServerAssertions "nixcfg.opencode.mcpServers" cfg.mcpServers
    ++ concatLists (
      mapAttrsToList (
        profileName: profile:
        opencodeMcpLib.mkServerAssertions "nixcfg.opencode.profiles.${profileName}.mcpServers" (
          mergeProfileMcpServers profile.mcpServers
        )
      ) cfg.profiles
    );

    home = {
      activation.removeStaleOpencodeProfiles = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        ${builtins.concatStringsSep "\n        " (
          map (path: "run rm -f ${lib.escapeShellArg path}") staleProfileJsonPaths
        )}
      '';
      sessionVariables.OPENCODE_CONFIG = selectedProfilePath;
    };

    programs.opencode = {
      enable = true;
      settings = baseOpencodeSettings // {
        mcp = opencodeMcpLib.renderMcpServers cfg.mcpServers;
      };
      tui = baseOpencodeTui;
    };

    xdg.configFile."opencode/${cfg.activeProfile}.json".text = builtins.toJSON (
      mkProfileOverlayConfig selectedProfileConfig
    );
  };
}
