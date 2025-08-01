{ primaryUser, ... }:
{
  local.dock = {
    enable = true;
    entries = [
      { path = "/System/Applications/Calendar.app"; }
      { path = "/System/Applications/Messages.app"; }
      { path = "/Users/${primaryUser}/Applications/Home Manager Apps/Slack.app"; }
      { path = "/Users/${primaryUser}/Applications/Home Manager Apps/Arc.app"; }
      { path = "/Applications/Ghostty.app"; }
      { path = "/Applications/Cursor.app"; }
      { path = "/Applications/DataGrip.app"; }
      { path = "/Users/${primaryUser}/Applications/Home Manager Apps/Notion.app"; }
      { path = "/Applications/Figma.app"; }
      { path = "/Applications/Linear.app"; }
      { path = "/Users/${primaryUser}/Applications/Home Manager Apps/Spotify.app"; }
      { path = "/System/Applications/System Settings.app"; }
      {
        path = "/Applications";
        section = "others";
      }
      {
        path = "/Users/george/Downloads";
        section = "others";
      }
    ];
  };
}
