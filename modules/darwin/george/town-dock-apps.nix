{
  config,
  lib,
  options,
  pkgs,
  primaryUser ? null,
  username ? null,
  ...
}:
let
  dock = import ./dock-lib.nix { inherit lib; };
  dockContext = dock.mkDockContext {
    inherit
      config
      primaryUser
      username
      ;
  };
  inherit (dockContext) appPath homeDirectory;
in
dock.mkDockModule {
  inherit
    homeDirectory
    options
    pkgs
    ;
  activationName = "nixcfgTownDock";
  # Home Manager preserves unlisted Dock items; retire these managed entries explicitly.
  removeApps = [
    "OpenCode Desktop Dev"
    "Visual Studio Code - Insiders"
    "Figma"
  ];
  apps = [
    "/System/Applications/Calendar.app"
    "/System/Applications/Messages.app"
    (appPath "slack" "Slack.app")
    (appPath "onepassword" "1Password.app")
    (appPath "google-chrome" "Google Chrome.app")
    (appPath "town-assistant" "Town Assistant.app")
    (appPath "zen-twilight" "Twilight.app")
    (appPath "claude" "Claude.app")
    (appPath "codex" "ChatGPT.app")
    (appPath "capy" "Capy.app")
    (appPath "grok-bot" "Grok Bot.app")
    (appPath "code-cursor" "Cursor.app")
    (appPath "zed" "Zed Nightly.app")
    (appPath "linear" "Linear.app")
    (appPath "ghostty" "Ghostty.app")
    (appPath "datagrip" "DataGrip.app")
    (appPath "notion" "Notion.app")
    "/System/Applications/Notes.app"
    (appPath "spotify" "Spotify.app")
    "/System/Applications/System Settings.app"
  ];
}
