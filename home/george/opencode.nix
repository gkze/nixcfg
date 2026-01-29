{ config, ... }:
{
  programs.opencode = {
    enable = true;
    # Base config: shared settings for both work and personal contexts
    # Machine-specific MCP servers are in:
    #   modules/home/town.nix (work.json for argus)
    #   darwin/rocinante.nix (personal.json for rocinante)
    # Context is selected via OPENCODE_CONFIG env var
    settings = {
      theme = "catppuccin-frappe";
      plugin = [
        "opencode-beads"
        "@franlol/opencode-md-table-formatter"
      ];
      tui.scroll_acceleration.enabled = true;
      # Base MCP servers shared across all machines
      mcp = {
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
          environment.AWS_PROFILE = "default";
        };
        axiom = {
          type = "remote";
          url = "https://mcp.axiom.co/mcp";
        };
        chrome-devtools = {
          type = "local";
          command = [
            "bunx"
            "--bun"
            "chrome-devtools-mcp@latest"
            "--executablePath=${config.home.homeDirectory}/Applications/Home Manager Apps/Google Chrome.app/Contents/MacOS/Google Chrome"
          ];
        };
        firefox-devtools = {
          type = "local";
          command = [
            "bunx"
            "--bun"
            "@padenot/firefox-devtools-mcp@latest"
            "--firefoxPath=/Applications/Twilight.app/Contents/MacOS/zen"
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
    };
  };
}
