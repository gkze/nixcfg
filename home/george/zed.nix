{
  config,
  pkgs,
  ...
}:
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
        dock = "right";
        inline_assistant_model = {
          model = "claude-opus-4-5-20251101";
          provider = "anthropic";
        };
        model_parameters = [ ];
      };
      buffer_font_family = config.fonts.monospace.name;
      buffer_font_size = 12.0;
      font_family = config.fonts.monospace.name;
      context_servers = {
        browser-tools-context-server = {
          enabled = true;
          remote = false;
          settings = { };
        };
        mcp-server-github = {
          enabled = true;
          settings = { }; # Token injected by activation script
        };
      };
      file_types = {
        "Shell Script" = [ ".envrc" ];
      };
      format_on_save = "on";
      icon_theme = config.theme.displayNameAccented;
      minimap.show = "always";
      outline_panel.dock = "right";
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
