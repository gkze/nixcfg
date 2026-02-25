{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    mkEnableOption
    mkIf
    mkOption
    types
    ;

  cfg = config.profiles.work;

  bunxExe = pkgs.lib.getExe' pkgs.bun "bunx";

  mkMcpRemoteWrapper =
    {
      name,
      tokenCommand,
      url,
      extraHeaders ? [ ],
    }:
    pkgs.writeShellScript name ''
      TOKEN="$(${tokenCommand})"
      args=(
        "${url}"
        --header "Authorization: Bearer $TOKEN"
      )
      ${lib.concatMapStringsSep "\n      " (
        header: "args+=(--header ${lib.escapeShellArg header})"
      ) extraHeaders}
      exec ${bunxExe} --bun mcp-remote@latest "''${args[@]}"
    '';

  # Render MCP wrapper that reads API key from macOS Keychain and calls remote server
  render-mcp-wrapper = mkMcpRemoteWrapper {
    name = "render-mcp";
    tokenCommand = ''security find-internet-password -s "mcp.render.com" -a "$USER" -r "htps" -w'';
    url = "https://mcp.render.com/mcp";
  };

  # GitHub MCP wrapper that reads PAT from sops secret and calls remote server
  github-mcp-wrapper = mkMcpRemoteWrapper {
    name = "github-mcp";
    tokenCommand = "cat ${config.sops.secrets.github_pat.path}";
    url = "https://api.githubcopilot.com/mcp/";
    extraHeaders = [ "X-MCP-Toolsets: repos,pull_requests,actions" ];
  };

  slack-mcp-wrapper = pkgs.writeShellScript "slack-mcp" ''
    export SLACK_MCP_XOXP_TOKEN="$(security find-generic-password -s "slack-mcp-token" -a "$USER" -w)"
    export SLACK_MCP_ADD_MESSAGE_TOOL=true
    exec ${bunxExe} --bun slack-mcp-server@latest --transport stdio
  '';

  defaultOpencodeMcp = {
    axiom = {
      type = "remote";
      url = "https://mcp.axiom.co/mcp";
    };
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
    slack = {
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
    # Render MCP (remote via mcp-remote proxy) - uses API key from macOS Keychain
    render = {
      type = "local";
      command = [ "${render-mcp-wrapper}" ];
    };
    # GitHub MCP (remote via mcp-remote proxy) - uses PAT from sops secret
    # Toolsets: repos, pull_requests, actions
    github = {
      type = "local";
      command = [ "${github-mcp-wrapper}" ];
    };
  };
in
{
  options.profiles.work = {
    enable = mkEnableOption "work profile — adds work packages, MCP servers, and shell integrations";

    packages = mkOption {
      type = types.listOf types.package;
      default = with pkgs; [
        _1password-cli
        google-cloud-sdk
      ];
      description = "Packages installed when the work profile is enabled.";
    };

    opencodeMcp = mkOption {
      type = types.attrsOf types.anything;
      default = defaultOpencodeMcp;
      description = "MCP server map written to opencode/work.json when the work profile is enabled.";
    };

    topgradeDisable = mkOption {
      type = types.listOf types.str;
      default = [ "gcloud" ];
      description = "Entries added to topgrade.settings.misc.disable in the work profile.";
    };

    enableOnePasswordZshPlugin = mkOption {
      type = types.bool;
      default = true;
      description = "Enable zsh completion integration for 1Password CLI.";
    };
  };

  config = mkIf cfg.enable {
    home.packages = cfg.packages;

    xdg.configFile."opencode/work.json".text = builtins.toJSON {
      "$schema" = "https://opencode.ai/config.json";
      mcp = cfg.opencodeMcp;
    };

    programs = {
      topgrade.settings.misc.disable = cfg.topgradeDisable;

      zsh.plugins = lib.optionals cfg.enableOnePasswordZshPlugin (
        with pkgs;
        [
          {
            name = "op";
            src = runCommand "_op" { buildInputs = [ _1password-cli ]; } ''
              mkdir $out
              HOME=$(mktemp -d) op completion zsh > $out/_op
            '';
            file = "_op";
          }
        ]
      );
    };
  };
}
