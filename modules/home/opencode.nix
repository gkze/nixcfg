{ config, lib, ... }:
let
  inherit (lib)
    concatLists
    mapAttrs
    mapAttrs'
    mapAttrsToList
    mkEnableOption
    mkIf
    mkOption
    nameValuePair
    optionalAttrs
    recursiveUpdate
    types
    ;

  cfg = config.nixcfg.opencode;

  mcpServerType = types.submodule {
    freeformType = types.attrsOf types.anything;

    options = {
      enable = mkEnableOption "MCP server" // {
        default = false;
      };

      type = mkOption {
        type = types.enum [
          "local"
          "remote"
        ];
        default = "local";
        description = "MCP server transport type.";
      };

      command = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "Command argv for local MCP servers.";
      };

      url = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Endpoint URL for remote MCP servers.";
      };

      environment = mkOption {
        type = types.attrsOf types.str;
        default = { };
        description = "Environment variables for local MCP server processes.";
      };
    };
  };

  profileType = types.submodule {
    options = {
      mcpServers = mkOption {
        type = types.attrsOf (types.attrsOf types.anything);
        default = { };
        description = "Per-profile MCP server overrides merged over nixcfg.opencode.mcpServers.";
      };

      settings = mkOption {
        type = types.attrsOf types.anything;
        default = { };
        description = "Additional top-level settings merged over the shared OpenCode config.";
      };
    };
  };

  mkServerConfig =
    server:
    let
      enabled = server.enabled or (server.enable or false);
      serverType = server.type or "local";
      command = server.command or [ ];
      url = server.url or null;
      environment = server.environment or { };
      extras = removeAttrs server [
        "command"
        "enable"
        "enabled"
        "environment"
        "type"
        "url"
      ];
    in
    extras
    // {
      inherit enabled;
      type = serverType;
    }
    // optionalAttrs (command != [ ]) { inherit command; }
    // optionalAttrs (url != null) { inherit url; }
    // optionalAttrs (environment != { }) { inherit environment; };

  renderMcpServers = servers: mapAttrs (_: mkServerConfig) servers;

  mergeProfileMcpServers = profileMcpServers: recursiveUpdate cfg.mcpServers profileMcpServers;

  mkServerAssertions =
    optionPath: servers:
    mapAttrsToList (
      name: server:
      let
        isLocal = (server.type or "local") == "local";
        command = server.command or [ ];
        url = server.url or null;
        isValid = if isLocal then command != [ ] else url != null;
      in
      {
        assertion = isValid;
        message =
          if isLocal then
            "${optionPath}.${name}: local servers require a non-empty command."
          else
            "${optionPath}.${name}: remote servers require a non-null url.";
      }
    ) servers;

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

  mkProfileConfig =
    profile:
    let
      mergedSettings = recursiveUpdate (
        baseOpencodeSettings // { tui = baseOpencodeTui; }
      ) profile.settings;
      mergedMcpServers = mergeProfileMcpServers profile.mcpServers;
    in
    mergedSettings
    // {
      "$schema" = profile.settings."$schema" or "https://opencode.ai/config.json";
      mcp = renderMcpServers mergedMcpServers;
    };

  activeProfileConfig = cfg.profiles.${cfg.activeProfile} or emptyProfile;
in
{
  options.nixcfg.opencode = {
    enable = mkEnableOption "OpenCode client configuration" // {
      default = true;
    };

    activeProfile = mkOption {
      type = types.str;
      default = "personal";
      description = "Named OpenCode profile materialized to opencode/active.json for GUI launches.";
    };

    profiles = mkOption {
      type = types.attrsOf profileType;
      default = {
        personal = { };
      };
      description = "Named OpenCode profile overrides materialized to opencode/<name>.json.";
    };

    mcpServers = mkOption {
      type = types.attrsOf mcpServerType;
      default = {
        chrome-devtools = {
          enable = false;
          type = "local";
          command = [
            "npx"
            "-y"
            "chrome-devtools-mcp@latest"
            "--autoConnect"
            "--channel=stable"
          ];
        };

        macos-automator = {
          type = "local";
          command = [
            "bunx"
            "--bun"
            "@steipete/macos-automator-mcp@latest"
          ];
        };

        next-devtools = {
          type = "local";
          command = [
            "bunx"
            "--bun"
            "next-devtools-mcp@latest"
          ];
        };
      };
      description = "Base MCP server definitions written to the global OpenCode config; servers default to disabled and can be enabled on demand.";
    };

    plugins = mkOption {
      type = types.listOf types.str;
      default = [
        # "@mohak34/opencode-notifier@latest"
      ];
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
    ++ mkServerAssertions "nixcfg.opencode.mcpServers" cfg.mcpServers
    ++ concatLists (
      mapAttrsToList (
        profileName: profile:
        mkServerAssertions "nixcfg.opencode.profiles.${profileName}.mcpServers" (
          mergeProfileMcpServers profile.mcpServers
        )
      ) cfg.profiles
    );

    programs.opencode = {
      enable = true;
      settings = baseOpencodeSettings // {
        mcp = renderMcpServers cfg.mcpServers;
      };
      tui = baseOpencodeTui;
    };

    xdg.configFile =
      mapAttrs' (
        profileName: profile:
        nameValuePair "opencode/${profileName}.json" {
          text = builtins.toJSON (mkProfileConfig profile);
        }
      ) cfg.profiles
      // {
        "opencode/active.json".text = builtins.toJSON (mkProfileConfig activeProfileConfig);
      };
  };
}
