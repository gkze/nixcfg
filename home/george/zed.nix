{
  config,
  lib,
  pkgs,
  ...
}:
let
  mcpCatalog = import ./mcp-catalog.nix { inherit config lib pkgs; };
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
          effort = "high";
          enable_thinking = true;
          model = "gpt-6-astra";
          provider = "copilot_chat";
        };
        dock = "left";
        expand_edit_card = false;
        expand_terminal_card = false;
        inline_assistant_model = {
          model = "claude-opus-4-5-20251101";
          provider = "anthropic";
        };
        model_parameters = [ ];
        thinking_display = "always_expanded";
        threads_sidebar.position = "left";
        tool_permissions = {
          default = "confirm";
          tools = {
            create_directory.default = "allow";
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
            skill.default = "allow";
            terminal.default = "allow";
          };
        };
      };
      buffer_font_family = config.fonts.monospace.name;
      buffer_font_size = 12.0;
      cli_default_open_behavior = "existing_window";
      collaboration_panel.dock = "left";
      font_family = config.fonts.monospace.name;
      context_servers = mcpCatalog.zed;
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
      lsp.vtsls = {
        enable_lsp_tasks = true;
        settings = {
          javascript.updateImportsOnFileMove.enabled = "always";
          typescript.updateImportsOnFileMove.enabled = "always";
        };
      };
      edit_predictions = {
        provider = "zed";
        mode = "eager";
      };
      file_types = {
        "Shell Script" = [ ".envrc" ];
      };
      format_on_save = "on";
      git_panel = {
        dock = "right";
        tree_view = true;
      };
      icon_theme = {
        mode = "system";
        light = config.theme.appearances.light.displayNameAccented;
        dark = config.theme.appearances.dark.displayNameAccented;
      };
      minimap.show = "always";
      outline_panel.dock = "right";
      project_panel.dock = "left";
      session.trust_all_worktrees = true;
      show_whitespaces = "all";
      theme = {
        dark = config.theme.appearances.dark.displayNameAccented;
        light = config.theme.appearances.light.displayNameAccented;
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
