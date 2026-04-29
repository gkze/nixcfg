{
  config,
  lib,
  pkgs,
  ...
}:
let
  mcpRemote = import ../../lib/mcp-remote-wrapper.nix { inherit lib pkgs; };
  githubMcpWrapper = mcpRemote.mkMcpRemoteWrapper {
    name = "zed-github-mcp";
    tokenCommand = "gh auth token";
    url = "https://api.githubcopilot.com/mcp/";
    extraHeaders = [ "X-MCP-Toolsets: repos,pull_requests,actions" ];
  };
  phoneMcpWrapper = mcpRemote.mkMcpRemoteWrapper {
    name = "zed-phone-mcp";
    tokenEnv = "PHONE_AGENT_API_KEY";
    url = "https://phone.kote.fyi/mcp";
  };
  renderMcpWrapper = mcpRemote.mkMcpRemoteWrapper {
    name = "zed-render-mcp";
    tokenCommand = ''security find-internet-password -s "mcp.render.com" -a "$USER" -r "htps" -w'';
    url = "https://mcp.render.com/mcp";
  };

  disabledLocalMcp = command: args: {
    enabled = false;
    inherit args command;
  };

  disabledRemoteMcp = url: {
    enabled = false;
    inherit url;
  };
in
{
  programs.zed-editor = {
    enable = true;
    package = pkgs.zed-editor-nightly;
    userSettings = {
      agent_servers = {
        qwen-code.type = "registry";
        mistral-vibe.type = "registry";
        factory-droid.type = "registry";
        github-copilot.type = "registry";
        auggie.type = "registry";
        opencode.type = "registry";
      };
      agent = {
        default_model = {
          model = "claude-opus-4-5-20251101";
          provider = "anthropic";
        };
        dock = "left";
        inline_assistant_model = {
          model = "claude-opus-4-5-20251101";
          provider = "anthropic";
        };
        model_parameters = [ ];
        sidebar_side = "left";
      };
      buffer_font_family = config.fonts.monospace.name;
      buffer_font_size = 12.0;
      font_family = config.fonts.monospace.name;
      context_servers = {
        aws-knowledge = disabledRemoteMcp "https://knowledge-mcp.global.api.aws";
        aws-mcp = disabledLocalMcp "uvx" [
          "mcp-proxy-for-aws@latest"
          "https://aws-mcp.us-east-1.api.aws/mcp"
        ];
        axiom = disabledRemoteMcp "https://mcp.axiom.co/mcp";
        chrome-devtools = disabledLocalMcp "npx" [
          "-y"
          "chrome-devtools-mcp@latest"
          "--autoConnect"
          "--channel=stable"
        ];
        clerk = disabledRemoteMcp "https://mcp.clerk.com/mcp";
        convex = disabledLocalMcp "bunx" [
          "--bun"
          "convex@latest"
          "mcp"
          "start"
        ];
        figma = disabledRemoteMcp "https://mcp.figma.com/mcp";
        firefox-devtools = disabledLocalMcp "npx" [
          "-y"
          "@padenot/firefox-devtools-mcp@latest"
          "--firefoxPath=/Applications/Twilight.app/Contents/MacOS/zen"
        ];
        github = disabledLocalMcp "${githubMcpWrapper}" [ ];
        linear = disabledRemoteMcp "https://mcp.linear.app/mcp";
        macos-automator = disabledLocalMcp "bunx" [
          "--bun"
          "@steipete/macos-automator-mcp@latest"
        ];
        markitdown = disabledLocalMcp "uvx" [
          "markitdown-mcp@0.0.1a4"
        ];
        next-devtools = disabledLocalMcp "bunx" [
          "--bun"
          "next-devtools-mcp@latest"
        ];
        notion = disabledRemoteMcp "https://mcp.notion.com/mcp";
        phone = disabledLocalMcp "${phoneMcpWrapper}" [ ];
        planetscale = disabledRemoteMcp "https://mcp.pscale.dev/mcp/planetscale";
        render = disabledLocalMcp "${renderMcpWrapper}" [ ];
        sentry = disabledRemoteMcp "https://mcp.sentry.dev/mcp";
        slack = disabledLocalMcp "${config.home.homeDirectory}/.local/bin/slack-mcp-wrapper" [ ];
        supabase = disabledRemoteMcp "https://mcp.supabase.com/mcp?project_ref=xfgralojsgvvibogtjxo";
        vanta =
          (disabledLocalMcp "bunx" [
            "--bun"
            "@vantasdk/vanta-mcp-server"
          ])
          // {
            env.VANTA_ENV_FILE = "${config.home.homeDirectory}/.config/vanta-credentials.env";
          };
        vercel = disabledRemoteMcp "https://mcp.vercel.com";
      };
      file_types = {
        "Shell Script" = [ ".envrc" ];
      };
      format_on_save = "on";
      git_panel.dock = "right";
      icon_theme = config.theme.displayNameAccented;
      minimap.show = "always";
      outline_panel.dock = "right";
      project_panel.dock = "right";
      show_whitespaces = "all";
      theme = {
        dark = config.theme.displayNameAccented;
        light = "One Light";
        mode = "system";
      };
      ui_font_family = config.fonts.sansSerif.name;
      ui_font_size = 15.0;
      vim_mode = true;
      wrap_guides = [
        80
        100
      ];
    };
    userKeymaps = [
      {
        context = "Terminal";
        bindings = {
          "shift-enter" = [
            "terminal::SendText"
            "\u001b\r"
          ];
        };
      }
      {
        bindings = {
          "alt-~" = "terminal_panel::ToggleFocus";
        };
      }
    ];
  };
}
