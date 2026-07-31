{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    mkIf
    mkOption
    types
    ;

  cfg = config.profiles.work;
  opencodeMcpLib = import ../../lib/opencode-mcp.nix { inherit lib; };
  inherit (opencodeMcpLib) mkLocalServer mkRemoteServer;
  mcpRemote = import ../../lib/mcp-remote-wrapper.nix { inherit lib pkgs; };
  workProfileSkeleton = import ../_profiles-work-skeleton.nix {
    enableDescription = "work profile — adds work packages, MCP servers, and shell integrations";
  };

  inherit (mcpRemote) bunxExe mkMcpRemoteWrapper;

  # GitHub MCP wrapper that reads PAT from sops secret and calls remote server.
  # This stays in the shared home module because it only depends on repo-managed
  # files plus bun, not on any Darwin-only runtime.
  github-mcp-wrapper = mkMcpRemoteWrapper {
    name = "github-mcp";
    tokenCommand = "cat ${config.sops.secrets.github_pat.path}";
    url = "https://api.githubcopilot.com/mcp/";
    extraHeaders = [ "X-MCP-Toolsets: repos,pull_requests,actions" ];
  };

  defaultOpencodeMcp = {
    axiom = mkRemoteServer "https://mcp.axiom.co/mcp";
    convex = mkLocalServer [
      "bunx"
      "--bun"
      "convex@latest"
      "mcp"
      "start"
    ];
    docusign = mkRemoteServer "https://mcp-d.docusign.com/mcp";
    figma = mkRemoteServer "https://mcp.figma.com/mcp";
    linear = mkRemoteServer "https://mcp.linear.app/mcp";
    notion = mkRemoteServer "https://mcp.notion.com/mcp" // {
      oauth = { };
    };
    # PlanetScale MCP (hosted remote server) - authenticates via `opencode mcp auth planetscale`
    planetscale = mkRemoteServer "https://mcp.pscale.dev/mcp/planetscale";
    sentry = mkRemoteServer "https://mcp.sentry.dev/mcp";
    vanta =
      mkLocalServer [
        "bunx"
        "--bun"
        "@vantasdk/vanta-mcp-server"
      ]
      // {
        environment.VANTA_ENV_FILE = "${config.home.homeDirectory}/.config/vanta-credentials.env";
      };
    vercel = mkRemoteServer "https://mcp.vercel.com";
    # GitHub MCP (remote via mcp-remote proxy) - uses PAT from sops secret
    # Toolsets: repos, pull_requests, actions
    github = mkLocalServer [ "${github-mcp-wrapper}" ];
  }
  // lib.optionalAttrs pkgs.stdenv.isDarwin (
    let
      # Render MCP wrapper that reads API key from macOS Keychain and calls the
      # remote server. Keep this Darwin-only because `security` is not portable.
      render-mcp-wrapper = mkMcpRemoteWrapper {
        name = "render-mcp";
        tokenCommand = ''security find-internet-password -s "mcp.render.com" -a "$USER" -r "htps" -w'';
        url = "https://mcp.render.com/mcp";
      };

      slack-mcp-wrapper = pkgs.writeShellScript "slack-mcp" ''
        set -euo pipefail

        token="$(security find-generic-password -s "slack-mcp-token" -a "$USER" -w)"
        if [ -z "$token" ]; then
          echo "slack-mcp: token lookup returned empty output" >&2
          exit 1
        fi

        export SLACK_MCP_XOXP_TOKEN="$token"
        export SLACK_MCP_ADD_MESSAGE_TOOL=true
        exec ${bunxExe} --bun slack-mcp-server@latest --transport stdio
      '';
    in
    {
      slack = mkLocalServer [ "${slack-mcp-wrapper}" ];
      # Render MCP (remote via mcp-remote proxy) - uses API key from macOS Keychain.
      render = mkLocalServer [ "${render-mcp-wrapper}" ];
    }
  );
in
{
  imports = [ workProfileSkeleton ];

  options.profiles.work = {
    packages = mkOption {
      type = types.listOf types.package;
      default = with pkgs; [
        _1password-cli
        anthropic-cli
        google-cloud-sdk
        linear-cli
        linearis
        openai-cli
        pscale
      ];
      description = "Packages installed when the work profile is enabled.";
    };

    opencodeMcp = mkOption {
      type = opencodeMcpLib.sparseMcpServerOverrideMapType;
      default = defaultOpencodeMcp;
      description = "MCP server map merged into nixcfg.opencode.profiles.work.mcpServers when the work profile is enabled.";
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

    nixcfg.opencode = {
      activeProfile = lib.mkDefault "work";
      profiles.work.mcpServers = cfg.opencodeMcp;
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
