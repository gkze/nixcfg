{ config, pkgs, ... }:
{
  home.packages = with pkgs; [
    _1password-cli
    google-cloud-sdk
  ];
  xdg.configFile."opencode/work.json".text = builtins.toJSON {
    "$schema" = "https://opencode.ai/config.json";
    mcp = {
      convex = {
        type = "local";
        command = [
          "bunx"
          "--bun"
          "convex@latest"
          "mcp"
          "start"
        ];
      };
      linear = {
        type = "remote";
        url = "https://mcp.linear.app/mcp";
      };
      notion = {
        type = "remote";
        url = "https://mcp.notion.com/mcp";
        oauth = { };
      };
      sentry = {
        type = "remote";
        url = "https://mcp.sentry.dev/mcp";
      };
      slack =
        let
          slack-mcp-wrapper = pkgs.writeShellScript "slack-mcp" ''
            export SLACK_MCP_XOXP_TOKEN="$(security find-generic-password -s "slack-mcp-token" -a "$USER" -w)"
            export SLACK_MCP_ADD_MESSAGE_TOOL=true
            exec ${pkgs.lib.getExe' pkgs.bun "bunx"} --bun slack-mcp-server@latest --transport stdio
          '';
        in
        {
          type = "local";
          command = [ "${slack-mcp-wrapper}" ];
        };
      vanta = {
        type = "local";
        command = [
          "bunx"
          "--bun"
          "@vantasdk/vanta-mcp-server"
        ];
        environment.VANTA_ENV_FILE = "${config.home.homeDirectory}/.config/vanta-credentials.env";
      };
      vercel = {
        type = "remote";
        url = "https://mcp.vercel.com";
      };
      clerk = {
        type = "remote";
        url = "https://mcp.clerk.com/mcp";
      };
      # GitHub MCP for CI debugging, PR analysis, and workflow inspection
      # Token sources checked (in order): Keychain, $GITHUB_TOKEN, gh auth token, ~/.netrc
      #
      # To store token in Keychain (recommended):
      #   security add-generic-password -s "github-mcp-token" -a "$USER" -w "<your-token>"
      #
      # Token needs scopes: repo, workflow, read:org
      # Create at: https://github.com/settings/tokens/new
      github =
        let
          github-mcp-wrapper = pkgs.writeShellScript "github-mcp" ''
            # Try sources in order: Keychain > env var > gh CLI > netrc
            if TOKEN="$(security find-generic-password -s "github-mcp-token" -a "$USER" -w 2>/dev/null)"; then
              : # Token from Keychain
            elif [[ -n "''${GITHUB_TOKEN:-}" ]]; then
              TOKEN="$GITHUB_TOKEN"
            elif command -v gh &>/dev/null && TOKEN="$(gh auth token 2>/dev/null)"; then
              : # Token from gh CLI
            elif [[ -f "$HOME/.netrc" ]] && TOKEN="$(awk '/machine github.com/{getline; if($1=="password") print $2}' "$HOME/.netrc" 2>/dev/null)"; then
              : # Token from netrc
            else
              echo "ERROR: No GitHub token found. Options:" >&2
              echo "  1. Keychain: security add-generic-password -s github-mcp-token -a \$USER -w <token>" >&2
              echo "  2. Env var: export GITHUB_TOKEN=<token>" >&2
              echo "  3. gh CLI: gh auth login" >&2
              echo "  4. netrc: Add 'machine github.com password <token>' to ~/.netrc" >&2
              exit 1
            fi
            export GITHUB_PERSONAL_ACCESS_TOKEN="$TOKEN"
            export GITHUB_TOOLSETS="repos,pull_requests,actions"
            exec ${pkgs.lib.getExe' pkgs.bun "bunx"} --bun @anthropic-ai/github-mcp-server@latest
          '';
        in
        {
          type = "local";
          command = [ "${github-mcp-wrapper}" ];
        };
    };
  };
  programs = {
    topgrade.settings.misc.disable = [
      # "bun"
      "gcloud"
      # "jetbrains_datagrip"
      # "uv"
    ];
    zsh.plugins = with pkgs; [
      {
        name = "op";
        src = runCommand "_op" { buildInputs = [ _1password-cli ]; } ''
          mkdir $out
          HOME=$(mktemp -d) op completion zsh > $out/_op
        '';
        file = "_op";
      }
    ];
  };
}
