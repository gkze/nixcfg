{
  config,
  lib,
  pkgs,
}:
let
  opencode = import ../../lib/opencode-mcp.nix { inherit lib; };
  remoteWrapper = import ../../lib/mcp-remote-wrapper.nix { inherit lib pkgs; };
  inherit (opencode) mkLocalServer mkRemoteServer;

  local = command: {
    opencode = mkLocalServer command;
    zed = {
      enabled = false;
      command = builtins.head command;
      args = builtins.tail command;
    };
  };
  remote = url: {
    opencode = mkRemoteServer url;
    zed = {
      enabled = false;
      inherit url;
    };
  };
  zedOnly = zed: { inherit zed; };
  withEnvironment =
    environment: server:
    server
    // {
      opencode = server.opencode // {
        inherit environment;
      };
      zed = server.zed // {
        env = environment;
      };
    };
  github = remoteWrapper.mkMcpRemoteWrapper {
    name = "github-mcp";
    tokenCommand = "gh auth token";
    url = "https://api.githubcopilot.com/mcp/";
    extraHeaders = [ "X-MCP-Toolsets: repos,pull_requests,actions" ];
  };
  phone = remoteWrapper.mkMcpRemoteWrapper {
    name = "phone-mcp";
    tokenEnv = "PHONE_AGENT_API_KEY";
    url = "https://phone.kote.fyi/mcp";
  };
  render = remoteWrapper.mkMcpRemoteWrapper {
    name = "render-mcp";
    tokenCommand = ''security find-internet-password -s "mcp.render.com" -a "$USER" -r "htps" -w'';
    url = "https://mcp.render.com/mcp";
  };
  slack = pkgs.writeShellScript "slack-mcp" ''
    set -euo pipefail
    token="$(security find-generic-password -s "slack-mcp-token" -a "$USER" -w)"
    if [ -z "$token" ]; then
      echo "slack-mcp: token lookup returned empty output" >&2
      exit 1
    fi
    export SLACK_MCP_XOXP_TOKEN="$token"
    export SLACK_MCP_ADD_MESSAGE_TOOL=true
    exec ${remoteWrapper.bunxExe} --bun slack-mcp-server@1.3.0 --transport stdio
  '';
  twilight = lib.attrByPath [
    "nixcfg"
    "macApps"
    "resolved"
    "zen-twilight"
    "path"
  ] "/Applications/Twilight.app" config;

  baseServers = {
    aws-knowledge = remote "https://knowledge-mcp.global.api.aws";
    aws-mcp = local [
      "uvx"
      "mcp-proxy-for-aws==1.6.4"
      "https://aws-mcp.us-east-1.api.aws/mcp"
    ];
    chrome-devtools = local [
      "npx"
      "-y"
      "chrome-devtools-mcp@1.7.0"
      "--autoConnect"
      "--channel=stable"
    ];
    firefox-devtools = local [
      "npx"
      "-y"
      "@padenot/firefox-devtools-mcp@0.7.5"
      "--firefoxPath=${twilight}/Contents/MacOS/zen"
    ];
    macos-automator = local [
      "bunx"
      "--bun"
      "@steipete/macos-automator-mcp@0.4.6"
    ];
    markitdown = local [
      "uvx"
      "markitdown-mcp@0.0.1a4"
    ];
    next-devtools = local [
      "bunx"
      "--bun"
      "next-devtools-mcp@0.4.0"
    ];
  };

  workServers = {
    axiom = remote "https://mcp.axiom.co/mcp";
    convex = local [
      "bunx"
      "--bun"
      "convex@1.43.0"
      "mcp"
      "start"
    ];
    docusign = remote "https://mcp-d.docusign.com/mcp";
    figma = remote "https://mcp.figma.com/mcp";
    github = local [ "${github}" ];
    linear = remote "https://mcp.linear.app/mcp";
    notion = (remote "https://mcp.notion.com/mcp") // {
      opencode = (mkRemoteServer "https://mcp.notion.com/mcp") // {
        oauth = { };
      };
    };
    planetscale = remote "https://mcp.pscale.dev/mcp/planetscale";
    render = local [ "${render}" ];
    sentry = remote "https://mcp.sentry.dev/mcp";
    slack = local [ "${slack}" ];
    vanta =
      withEnvironment
        {
          VANTA_ENV_FILE = "${config.home.homeDirectory}/.config/vanta-credentials.env";
        }
        (local [
          "bunx"
          "--bun"
          "@vantasdk/vanta-mcp-server@1.2.0"
        ]);
    vercel = remote "https://mcp.vercel.com";
  };

  zedServers = {
    browser-tools-context-server = zedOnly {
      enabled = true;
      remote = false;
      settings = { };
    };
    clerk = zedOnly {
      enabled = false;
      url = "https://mcp.clerk.com/mcp";
    };
    mcp-server-exa-search = zedOnly {
      enabled = false;
      remote = false;
      settings = { };
    };
    phone = zedOnly {
      enabled = false;
      command = "${phone}";
      args = [ ];
    };
    planetscale-context-server = zedOnly {
      enabled = false;
      remote = false;
      settings = { };
    };
    supabase = zedOnly {
      enabled = false;
      url = "https://mcp.supabase.com/mcp?project_ref=xfgralojsgvvibogtjxo";
    };
  };

  project =
    field: servers:
    builtins.mapAttrs (_: server: server.${field}) (
      lib.filterAttrs (_: builtins.hasAttr field) servers
    );
in
{
  base = project "opencode" baseServers;
  work = project "opencode" workServers;
  zed = project "zed" (baseServers // workServers // zedServers);
}
