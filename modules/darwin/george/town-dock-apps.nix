{ primaryUser, ... }:
{
  system.defaults.dock = {
    # Keeping this list explicit: app ordering is user-visible, and merging
    # shared/base lists has repeatedly made preserving exact order brittle.
    # The remaining Home Manager Apps entries are intentionally left profile-managed;
    # only the known mutable bundles with GC/App Management issues are promoted to
    # /Applications copies.
    persistent-apps = [
      { app = "/System/Applications/Calendar.app"; }
      { app = "/System/Applications/Messages.app"; }
      { app = "/Applications/1Password.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Slack.app"; }
      { app = "/Applications/Twilight.app"; }
      { app = "/Applications/Google Chrome.app"; }
      { app = "/Applications/ChatGPT.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Codex.app"; }
      { app = "/Applications/Claude.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/OpenCode Dev.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Zed Nightly.app"; }
      { app = "/Applications/Cursor.app"; }
      { app = "/Applications/Visual Studio Code - Insiders.app"; }
      { app = "/Applications/Ghostty.app"; }
      { app = "/Applications/DataGrip.app"; }
      { app = "/Users/${primaryUser}/Applications/Home Manager Apps/Notion.app"; }
      { app = "/System/Applications/Notes.app"; }
      { app = "/Applications/Figma.app"; }
      { app = "/Applications/Linear.app"; }
      { app = "/Applications/Spotify.app"; }
      { app = "/System/Applications/System Settings.app"; }
    ];
    persistent-others = [
      "/Applications"
      "/Applications/Utilities"
      "/Users/${primaryUser}/Downloads"
    ];
  };
}
