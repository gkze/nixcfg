{ config, pkgs, ... }:
let
  # GitHub MCP wrapper that reads PAT from sops secret and calls remote server
  github-mcp-wrapper = pkgs.writeShellScript "github-mcp" ''
    TOKEN="$(cat ${config.sops.secrets.github_pat.path})"
    exec ${pkgs.lib.getExe' pkgs.bun "bunx"} --bun mcp-remote@latest \
      "https://api.githubcopilot.com/mcp/" \
      --header "Authorization: Bearer $TOKEN" \
      --header "X-MCP-Toolsets: repos,pull_requests,actions"
  '';
in
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
      # GitHub MCP (remote via mcp-remote proxy) - uses PAT from sops secret
      # Toolsets: repos, pull_requests, actions
      github = {
        type = "local";
        command = [ "${github-mcp-wrapper}" ];
      };
    };
  };
  programs = {
    topgrade.settings.misc.disable = [
      # "bun"
      # "gcloud"
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
