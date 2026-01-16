{ primaryUser, ... }:
{
  system.defaults.dock = {
    persistent-apps = [
      { app = "/System/Applications/Calendar.app"; }
      { app = "/System/Applications/Messages.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Slack.app"; }
      { app = "/Applications/1Password.app"; }
      { app = "/Applications/Twilight.app"; }
      { app = "/Applications/Ghostty.app"; }
      { app = "/Applications/Zed Preview.app"; }
      { app = "/Applications/Cursor.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Visual Studio Code - Insiders.app"; }
      { app = "/Applications/DataGrip.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Notion.app"; }
      { app = "/Applications/Figma.app"; }
      { app = "/Applications/Linear.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Spotify.app"; }
      { app = "/System/Applications/System Settings.app"; }
    ];
    persistent-others = [
      "/Applications"
      "/Applications/Utilities"
      "/Users/${primaryUser}/Downloads"
    ];
  };
}
