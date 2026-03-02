{ config, lib, ... }:
let
  inherit (lib)
    filterAttrs
    mapAttrs
    mapAttrsToList
    mkEnableOption
    mkIf
    mkOption
    optionalAttrs
    types
    ;

  cfg = config.nixcfg.opencode;

  mcpServerType = types.submodule {
    freeformType = types.attrsOf types.anything;

    options = {
      enable = mkEnableOption "MCP server" // {
        default = true;
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

  mkServerConfig =
    server:
    {
      inherit (server) type;
    }
    // optionalAttrs (server.command != [ ]) { inherit (server) command; }
    // optionalAttrs (server.url != null) { inherit (server) url; }
    // optionalAttrs (server.environment != { }) { inherit (server) environment; };
in
{
  options.nixcfg.opencode = {
    enable = mkEnableOption "OpenCode client configuration" // {
      default = true;
    };

    mcpServers = mkOption {
      type = types.attrsOf mcpServerType;
      default = {
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
      description = "Base MCP server definitions shared across all OpenCode contexts.";
    };

    plugins = mkOption {
      type = types.listOf types.str;
      default = [
        "@franlol/opencode-md-table-formatter"
        "@mohak34/opencode-notifier@latest"
      ];
      description = "OpenCode plugins to install.";
    };
  };

  config = mkIf cfg.enable {
    assertions = mapAttrsToList (
      name: server:
      let
        isValid = if server.type == "local" then server.command != [ ] else server.url != null;
      in
      {
        assertion = isValid;
        message =
          if server.type == "local" then
            "nixcfg.opencode.mcpServers.${name}: local servers require a non-empty command."
          else
            "nixcfg.opencode.mcpServers.${name}: remote servers require a non-null url.";
      }
    ) cfg.mcpServers;

    programs.opencode = {
      enable = true;
      settings = {
        theme = config.theme.slug;
        plugin = cfg.plugins;
        tui.scroll_acceleration.enabled = true;
        mcp = mapAttrs (_: mkServerConfig) (filterAttrs (_: server: server.enable) cfg.mcpServers);
      };
    };
  };
}
