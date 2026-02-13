{ primaryUser, ... }:
{
  system.defaults.dock = {
    # Keeping this list explicit: app ordering is user-visible, and merging
    # shared/base lists has repeatedly made preserving exact order brittle.
    persistent-apps = [
      { app = "/System/Applications/Calendar.app"; }
      { app = "/System/Applications/Messages.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Slack.app"; }
      { app = "/Applications/1Password.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/ChatGPT.app"; }
      { app = "/Applications/Claude.app"; }
      { app = "/Applications/Twilight.app"; }
      { app = "/Applications/Ghostty.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/OpenCode Dev.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Zed Nightly.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Cursor.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Visual Studio Code - Insiders.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/DataGrip.app"; }
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
