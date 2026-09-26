{
  config,
  lib,
  pkgs,
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
    theme = if config.theme.name == "catppuccin" then "catppuccin-system" else config.theme.slug;
    scroll_acceleration.enabled = true;
  };

  # OpenCode 2 ships standalone, single-mode palettes. Pair their native
  # version-2 mode trees so system appearance selects Latte or Frappé.
  catppuccinTheme =
    let
      themeDir = "${config.programs.opencode.package.src}/packages/tui/src/theme/assets";
      light = lib.importJSON "${themeDir}/catppuccin-latte.json";
      dark = lib.importJSON "${themeDir}/catppuccin-frappe.json";
    in
    dark
    // {
      inherit (light) light;
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
      # OpenCode 2 reads cli.json; tui.json is only imported during migration.
      # Merge appearance into the mutable file so unrelated CLI preferences survive.
      activation.opencodeSystemAppearance = lib.hm.dag.entryAfter [ "linkGeneration" ] ''
        run ${pkgs.writeShellScript "opencode-system-appearance" ''
          set -eu
          appearance_config=${lib.escapeShellArg "${config.xdg.configHome}/opencode/cli.json"}
          # Leave first-run migration to OpenCode so it can import all legacy settings.
          if [ ! -f "$appearance_config" ]; then
            exit 0
          fi
          appearance_tmp=$(mktemp "$appearance_config.XXXXXX")
          trap 'rm -f "$appearance_tmp"' EXIT
          ${lib.getExe pkgs.jq} --arg name ${lib.escapeShellArg baseOpencodeTui.theme} \
            '.theme = {name: $name, mode: "system"}' \
            "$appearance_config" > "$appearance_tmp"
          chmod 600 "$appearance_tmp"
          mv "$appearance_tmp" "$appearance_config"
        ''}
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
    xdg.configFile."opencode/themes/catppuccin-system.json" = mkIf (config.theme.name == "catppuccin") {
      text = builtins.toJSON catppuccinTheme;
    };
  };
}
