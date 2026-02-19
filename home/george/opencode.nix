{ config, lib, ... }:
let
  inherit (lib) mkOption types;
  cfg = config.opencode;
in
{
  options.opencode = {
    mcpServers = mkOption {
      type = types.attrsOf types.anything;
      default = { };
      description = "Base MCP server definitions shared across all opencode contexts.";
    };
    plugins = mkOption {
      type = types.listOf types.str;
      default = [
        "@franlol/opencode-md-table-formatter"
        "@mohak34/opencode-notifier@latest"
        "opencode-beads"
      ];
      description = "OpenCode plugins to install.";
    };
  };

  config = {
    # Default base MCP servers
    opencode.mcpServers = {
      # aws-knowledge = {
      #   type = "remote";
      #   url = "https://knowledge-mcp.global.api.aws";
      # };
      # aws-mcp = {
      #   type = "local";
      #   command = [
      #     "uvx"
      #     "mcp-proxy-for-aws@latest"
      #     "https://aws-mcp.us-east-1.api.aws/mcp"
      #   ];
      #   environment.AWS_PROFILE = "default";
      # };
      # chrome-devtools = {
      #   type = "local";
      #   command = [
      #     "bunx"
      #     "--bun"
      #     "chrome-devtools-mcp@latest"
      #     "--executablePath=${config.home.homeDirectory}/Applications/Home Manager Apps/Google Chrome.app/Contents/MacOS/Google Chrome"
      #   ];
      # };
      # firefox-devtools = {
      #   type = "local";
      #   command = [
      #     "bunx"
      #     "--bun"
      #     "@padenot/firefox-devtools-mcp@latest"
      #     "--firefoxPath=/Applications/Twilight.app/Contents/MacOS/zen"
      #   ];
      # };
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

    programs.opencode = {
      enable = true;
      # Base config: shared settings for both work and personal contexts
      # Machine-specific MCP servers are in:
      #   modules/home/profiles.nix (work.json for argus via profiles.work.enable)
      #   darwin/rocinante.nix (personal.json for rocinante)
      # Context is selected via OPENCODE_CONFIG env var
      settings = {
        theme = config.theme.slug;
        plugin = cfg.plugins;
        tui.scroll_acceleration.enabled = true;
        # Base MCP servers shared across all machines
        mcp = cfg.mcpServers;
      };
    };
  };
}
