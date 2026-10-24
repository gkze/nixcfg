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
  apps = [
    "/System/Applications/Messages.app"
    (appPath "slack" "Slack.app")
    (appPath "onepassword" "1Password.app")
    (appPath "aside" "Aside.app")
    (appPath "google-chrome" "Google Chrome.app")
    (appPath "claude" "Claude.app")
    (appPath "codex" "ChatGPT.app")
    (appPath "capy-nightly" "Capy Nightly.app")
    (appPath "code-cursor" "Cursor.app")
    (appPath "town-assistant" "Town Assistant.app")
    (appPath "grok-bot" "Grok Bot.app")
    (appPath "zed" "Zed Nightly.app")
    (appPath "linear" "Linear.app")
    (appPath "ghostty" "Ghostty.app")
    (appPath "notion" "Notion.app")
    "/System/Applications/Notes.app"
    (appPath "spotify" "Spotify.app")
    "/System/Applications/System Settings.app"
  ];
}
