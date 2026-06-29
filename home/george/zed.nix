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
  twilightAppPath = lib.attrByPath [
    "nixcfg"
    "macApps"
    "resolved"
    "zen-twilight"
    "path"
  ] "/Applications/Twilight.app" config;

  disabledLocalMcp = command: args: {
    enabled = false;
    inherit args command;
  };

  disabledRemoteMcp = url: {
    enabled = false;
    inherit url;
  };

  extensionMcp = enabled: {
    inherit enabled;
    remote = false;
    settings = { };
  };
in
{
  programs.zed-editor = {
    enable = true;
    package = null;
    userSettings = {
      agent_servers = {
        amp-acp.type = "registry";
        auggie.type = "registry";
        claude-acp.type = "registry";
        codex-acp.type = "registry";
        cursor.type = "registry";
        factory-droid.type = "registry";
        gemini.type = "registry";
        github-copilot.type = "registry";
        github-copilot-cli.type = "registry";
        goose.type = "registry";
        kimi.type = "registry";
        mistral-vibe.type = "registry";
        opencode.type = "registry";
        pi-acp.type = "registry";
        qwen-code.type = "registry";
      };
      agent = {
        button = true;
        default_model = {
          effort = "medium";
          enable_thinking = true;
          model = "gpt-5.5";
          provider = "zed.dev";
        };
        dock = "left";
        expand_edit_card = false;
        expand_terminal_card = false;
        inline_assistant_model = {
          model = "claude-opus-4-5-20251101";
          provider = "anthropic";
        };
        model_parameters = [ ];
        sidebar_side = "left";
        thinking_display = "always_expanded";
        tool_permissions = {
          default = "confirm";
          tools = {
            edit_file = {
              always_allow = [
                {
                  pattern = "^town/\\.zed/";
                }
              ];
              default = "allow";
            };
            fetch.default = "allow";
            search_web.default = "allow";
            terminal.default = "allow";
          };
        };
      };
      buffer_font_family = config.fonts.monospace.name;
      buffer_font_size = 12.0;
      collaboration_panel.dock = "left";
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
        browser-tools-context-server = extensionMcp true;
        clerk = disabledRemoteMcp "https://mcp.clerk.com/mcp";
        convex = disabledLocalMcp "bunx" [
          "--bun"
          "convex@latest"
          "mcp"
          "start"
        ];
        docusign = disabledRemoteMcp "https://mcp-d.docusign.com/mcp";
        figma = disabledRemoteMcp "https://mcp.figma.com/mcp";
        firefox-devtools = disabledLocalMcp "npx" [
          "-y"
          "@padenot/firefox-devtools-mcp@latest"
          "--firefoxPath=${twilightAppPath}/Contents/MacOS/zen"
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
        mcp-server-exa-search = extensionMcp false;
        next-devtools = disabledLocalMcp "bunx" [
          "--bun"
          "next-devtools-mcp@latest"
        ];
        notion = disabledRemoteMcp "https://mcp.notion.com/mcp";
        phone = disabledLocalMcp "${phoneMcpWrapper}" [ ];
        planetscale = disabledRemoteMcp "https://mcp.pscale.dev/mcp/planetscale";
        planetscale-context-server = extensionMcp false;
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
      diff_view_style = "split";
      disable_ai = false;
      language_models.openai.available_models = [
        {
          name = "gpt-5.5";
          display_name = "gpt-5.5 Extra High";
          max_tokens = 1050000;
          max_output_tokens = 128000;
          reasoning_effort = "xhigh";
          capabilities.chat_completions = false;
        }
      ];
      edit_predictions = {
        provider = "zed";
        mode = "eager";
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
      session.trust_all_worktrees = true;
      show_whitespaces = "all";
      theme = {
        dark = config.theme.displayNameAccented;
        light = "One Light";
        mode = "system";
      };
      ui_font_family = config.fonts.sansSerif.name;
      ui_font_size = 15.0;
      ui_font_weight = 400.0;
      vim_mode = true;
      wrap_guides = [
        80
        100
      ];
    };
    userKeymaps = [
      {
        bindings = {
          "alt-~" = "terminal_panel::ToggleFocus";
        };
      }
      {
        context = "Terminal";
        bindings = {
          "shift-enter" = [
            "terminal::SendText"
            "\u001b\r"
          ];
        };
      }
    ];
  };
}
