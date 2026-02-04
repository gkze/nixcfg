{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.opencode;
  opencodeServerScript = pkgs.writeShellApplication {
    name = "opencode-server";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      set -euo pipefail

      secret="${config.sops.secrets.opencode_server_password.path}"
      if [ ! -s "$secret" ]; then
        echo "opencode-server: missing secret at $secret" >&2
        exit 1
      fi

      password="$(cat "$secret")"
      export OPENCODE_SERVER_PASSWORD="$password"

      exec ${lib.getExe cfg.package} serve
    '';
  };
in
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
      server = {
        hostname = "0.0.0.0";
        port = 4096;
        mdns = true;
      };
      tui.scroll_acceleration.enabled = true;
      # Base MCP servers shared across all machines
      mcp = {
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

  launchd.agents.opencode-server = lib.mkIf pkgs.stdenv.hostPlatform.isDarwin {
    enable = true;
    config = {
      Label = "ai.opencode.server";
      ProgramArguments = [
        "${opencodeServerScript}/bin/opencode-server"
      ];
      KeepAlive = {
        Crashed = true;
        SuccessfulExit = false;
      };
      ProcessType = "Background";
      RunAtLoad = true;
    };
  };
}
