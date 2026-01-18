{ primaryUser, ... }:
{
  system.defaults.dock = {
    persistent-apps = [
      { app = "/System/Applications/Calendar.app"; }
      { app = "/System/Applications/Messages.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Slack.app"; }
      { app = "/Applications/Twilight.app"; }
      { app = "/Applications/Ghostty.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Zed Nightly.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/DataGrip.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Notion.app"; }
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
