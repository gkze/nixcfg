{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    concatLists
    mapAttrs
    mapAttrsToList
    mkEnableOption
    mkIf
    mkOption
    optionalAttrs
    types
    ;

  cfg = config.nixcfg.opencode;
  opencodeMcpLib = import ../../lib/opencode-mcp.nix { inherit lib; };
  mcpRemote = import ../../lib/mcp-remote-wrapper.nix { inherit lib pkgs; };
  phoneMcpWrapper = mcpRemote.mkMcpRemoteWrapper {
    name = "phone-mcp";
    tokenEnv = "PHONE_AGENT_API_KEY";
    url = "https://phone.kote.fyi/mcp";
  };

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

  # Keep overlays sparse for shared/global servers, but preserve the historical
  # disabled-by-default behavior for profile-only servers added outside the
  # shared global config.
  mkSparseServerOverride =
    name: server:
    let
      extras = removeAttrs server [
        "enable"
        "enabled"
      ];
      enabled =
        server.enabled or (server.enable or (if builtins.hasAttr name cfg.mcpServers then null else false));
    in
    extras // optionalAttrs (enabled != null) { inherit enabled; };

  renderMcpServers = servers: mapAttrs (_: mkServerConfig) servers;
  renderSparseMcpServerOverrides = servers: mapAttrs mkSparseServerOverride servers;

  mergeProfileMcpServers =
    profileMcpServers: opencodeMcpLib.resolveSparseMcpServerOverrides cfg.mcpServers profileMcpServers;

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

  mkProfileOverlayConfig =
    profile:
    profile.settings
    // {
      "$schema" = profile.settings."$schema" or "https://opencode.ai/config.json";
    }
    // optionalAttrs (profile.mcpServers != { }) {
      mcp = renderSparseMcpServerOverrides profile.mcpServers;
    };

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
      type = types.attrsOf mcpServerType;
      default = {
        aws-knowledge = {
          type = "remote";
          url = "https://knowledge-mcp.global.api.aws";
        };

        aws-mcp = {
          type = "local";
          command = [
            "uvx"
            "mcp-proxy-for-aws@latest"
            "https://aws-mcp.us-east-1.api.aws/mcp"
          ];
        };

        chrome-devtools = {
          enable = false;
          type = "local";
          # Use Node's npx here instead of bunx. Hard-verified on argus: the
          # same chrome-devtools-mcp --autoConnect --channel=stable command
          # succeeds via npx but fails via bunx after MCP init/tool discovery on
          # the first browser attach/list_pages call. The bundled Puppeteer/ws
          # transport inside chrome-devtools-mcp hits Bun websocket upgrade
          # compatibility issues and can surface misleading
          # "Could not find DevToolsActivePort" errors even when the file
          # exists. Relevant upstream Bun threads: #5951, #28114, #25777,
          # #8320, #27859, and #28828.
          command = [
            "npx"
            "-y"
            "chrome-devtools-mcp@latest"
            "--autoConnect"
            "--channel=stable"
          ];
        };

        firefox-devtools = {
          type = "local";
          command = [
            "npx"
            "-y"
            "@padenot/firefox-devtools-mcp@latest"
            "--firefoxPath=/Applications/Twilight.app/Contents/MacOS/zen"
          ];
        };

        figma = {
          type = "remote";
          url = "https://mcp.figma.com/mcp";
        };

        macos-automator = {
          type = "local";
          command = [
            "bunx"
            "--bun"
            "@steipete/macos-automator-mcp@latest"
          ];
        };

        markitdown = {
          type = "local";
          command = [
            "uvx"
            "markitdown-mcp@0.0.1a4"
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

        phone = {
          type = "local";
          command = [ "${phoneMcpWrapper}" ];
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
        mcp = renderMcpServers cfg.mcpServers;
      };
      tui = baseOpencodeTui;
    };

    xdg.configFile."opencode/${cfg.activeProfile}.json".text = builtins.toJSON (
      mkProfileOverlayConfig selectedProfileConfig
    );
  };
}
