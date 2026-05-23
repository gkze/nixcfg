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
  activationName = "nixcfgPersonalDock";
  apps = [
    "/System/Applications/Calendar.app"
    "/System/Applications/Messages.app"
    (appPath "slack" "Slack.app")
    (appPath "claude" "Claude.app")
    (appPath "zen-twilight" "Twilight.app")
    (appPath "ghostty" "Ghostty.app")
    (appPath "zed" "Zed Nightly.app")
    (appPath "datagrip" "DataGrip.app")
    "/System/Applications/Notes.app"
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
