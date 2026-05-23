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
    options
    pkgs
    ;
  activationName = "nixcfgTownDock";
  apps = [
    "/System/Applications/Calendar.app"
    "/System/Applications/Messages.app"
    (appPath "onepassword" "1Password.app")
    (appPath "slack" "Slack.app")
    (appPath "zen-twilight" "Twilight.app")
    (appPath "google-chrome" "Google Chrome.app")
    (appPath "town-assistant" "Town Assistant.app")
    (appPath "codex" "Codex.app")
    (appPath "claude" "Claude.app")
    (appPath "opencode" "OpenCode Desktop Dev.app")
    (appPath "zed" "Zed Nightly.app")
    (appPath "code-cursor" "Cursor.app")
    (appPath "vscode-insiders" "Visual Studio Code - Insiders.app")
    (appPath "ghostty" "Ghostty.app")
    (appPath "datagrip" "DataGrip.app")
    (appPath "notion" "Notion.app")
    "/System/Applications/Notes.app"
    (appPath "figma" "Figma.app")
    (appPath "linear" "Linear.app")
    (appPath "spotify" "Spotify.app")
    "/System/Applications/System Settings.app"
  ];
  others = [
    "/Applications"
    "/Applications/Utilities"
    "${homeDirectory}/Downloads"
  ];
  removeOthers = [
    "${homeDirectory}/Applications"
  ];
}
