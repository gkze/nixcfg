# Generated via dconf2nix: https://github.com/gvolpe/dconf2nix
{ lib, ... }:

with lib.hm.gvariant;

{
  dconf.settings = {
    "org/gnome/calendar" = {
      active-view = "week";
      week-view-zoom-level = 1.0;
      window-maximized = true;
      window-size = mkTuple [ 1548 1026 ];
    };

    "org/gnome/desktop/app-folders" = {
      folder-children = [ "Utilities" "YaST" "Pardus" ];
    };

    "org/gnome/desktop/app-folders/folders/Pardus" = {
      categories = [ "X-Pardus-Apps" ];
      name = "X-Pardus-Apps.directory";
      translate = true;
    };

    "org/gnome/desktop/app-folders/folders/Utilities" = {
      apps = [ "gnome-abrt.desktop" "gnome-system-log.desktop" "nm-connection-editor.desktop" "org.gnome.baobab.desktop" "org.gnome.Connections.desktop" "org.gnome.DejaDup.desktop" "org.gnome.Dictionary.desktop" "org.gnome.DiskUtility.desktop" "org.gnome.Evince.desktop" "org.gnome.FileRoller.desktop" "org.gnome.fonts.desktop" "org.gnome.Loupe.desktop" "org.gnome.seahorse.Application.desktop" "org.gnome.tweaks.desktop" "org.gnome.Usage.desktop" "vinagre.desktop" ];
      categories = [ "X-GNOME-Utilities" ];
      name = "X-GNOME-Utilities.directory";
      translate = true;
    };

    "org/gnome/desktop/app-folders/folders/YaST" = {
      categories = [ "X-SuSE-YaST" ];
      name = "suse-yast.directory";
      translate = true;
    };

    "org/gnome/desktop/background" = {
      picture-options = "zoom";
      picture-uri = "file:///home/george/.config/background";
      picture-uri-dark = "file:///home/george/.config/background";
    };

    "org/gnome/desktop/input-sources" = {
      show-all-sources = true;
      sources = [ (mkTuple [ "xkb" "us" ]) ];
      xkb-options = [ "caps:swapescape" ];
    };

    "org/gnome/desktop/interface" = {
      clock-format = "12h";
      clock-show-seconds = false;
      clock-show-weekday = true;
      color-scheme = "prefer-dark";
      font-antialiasing = "grayscale";
      font-hinting = "slight";
      gtk-theme = "Catppuccin-Frappe-Standard-Blue-Dark";
      monospace-font-name = "Hack Nerd Font Mono 11";
      overlay-scrolling = true;
      show-battery-percentage = true;
      toolkit-accessibility = false;
    };

    "org/gnome/desktop/notifications" = {
      application-children = [ "org-gnome-software" "gnome-network-panel" "org-gnome-calendar" ];
    };

    "org/gnome/desktop/notifications/application/gnome-network-panel" = {
      application-id = "gnome-network-panel.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-calendar" = {
      application-id = "org.gnome.Calendar.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-software" = {
      application-id = "org.gnome.Software.desktop";
    };

    "org/gnome/desktop/peripherals/touchpad" = {
      tap-to-click = true;
      two-finger-scrolling-enabled = true;
    };

    "org/gnome/desktop/wm/keybindings" = {
      move-to-center = [ "<Super><Control><Shift>Home" ];
      toggle-fullscreen = [ "<Super><Shift>F" ];
    };

    "org/gnome/desktop/wm/preferences" = {
      button-layout = "appmenu:minimize,maximize,close";
      titlebar-font = "Cantarell Bold 11";
    };

    "org/gnome/evolution-data-server" = {
      migrated = true;
    };

    "org/gnome/file-roller/listing" = {
      list-mode = "as-folder";
      name-column-width = 250;
      show-path = false;
      sort-method = "name";
      sort-type = "ascending";
    };

    "org/gnome/file-roller/ui" = {
      sidebar-width = 200;
      window-height = 480;
      window-width = 600;
    };

    "org/gnome/gnome-system-monitor" = {
      maximized = true;
      window-state = mkTuple [ 1920 1168 0 0 ];
    };

    "org/gnome/mutter" = {
      center-new-windows = true;
      dynamic-workspaces = true;
    };

    "org/gnome/nautilus/list-view" = {
      default-column-order = [ "name" "size" "type" "owner" "group" "permissions" "where" "date_modified" "date_modified_with_time" "date_accessed" "date_created" "recency" "detailed_type" ];
      default-visible-columns = [ "date_created" "date_modified" "detailed_type" "group" "name" "owner" "permissions" "size" "type" ];
      use-tree-view = true;
    };

    "org/gnome/nautilus/preferences" = {
      migrated-gtk-settings = true;
    };

    "org/gnome/settings-daemon/plugins/media-keys" = {
      next = [ "Cancel" ];
      play = [ "Messenger" ];
      previous = [ "Go" ];
    };

    "org/gnome/shell" = {
      disable-user-extensions = false;
      disabled-extensions = [ "light-style@gnome-shell-extensions.gcampax.github.com" "native-window-placement@gnome-shell-extensions.gcampax.github.com" "window-list@gnome-shell-extensions.gcampax.github.com" "workspace-indicator@gnome-shell-extensions.gcampax.github.com" ];
      enabled-extensions = [ "apps-menu@gnome-shell-extensions.gcampax.github.com" "display-brightness-ddcutil@themightydeity.github.com" "drive-menu@gnome-shell-extensions.gcampax.github.com" "places-menu@gnome-shell-extensions.gcampax.github.com" "screenshot-window-sizer@gnome-shell-extensions.gcampax.github.com" "user-theme@gnome-shell-extensions.gcampax.github.com" ];
      favorite-apps = [ "Alacritty.desktop" "beekeeper-studio.desktop" "brave-browser.desktop" "obsidian.desktop" "org.gnome.Calendar.desktop" "org.gnome.Nautilus.desktop" "org.gnome.Settings.desktop" "slack.desktop" ];
      last-selected-power-profile = "performance";
      welcome-dialog-last-shown-version = "45.5";
    };

    "org/gnome/shell/extensions/display-brightness-ddcutil" = {
      allow-zero-brightness = true;
      button-location = 0;
      ddcutil-binary-path = "/etc/profiles/per-user/george/bin/ddcutil";
      ddcutil-queue-ms = 130.0;
      ddcutil-sleep-multiplier = 4.0;
      decrease-brightness-shortcut = [ "<Control>MonBrightnessDown" ];
      disable-display-state-check = false;
      hide-system-indicator = false;
      increase-brightness-shortcut = [ "<Control>MonBrightnessUp" ];
      only-all-slider = true;
      position-system-menu = 1.0;
      show-all-slider = true;
      show-display-name = true;
      show-osd = true;
      show-value-label = false;
      step-change-keyboard = 2.0;
      verbose-debugging = true;
    };

    "org/gnome/shell/extensions/user-theme" = {
      name = "Catppuccin-Frappe-Standard-Blue-Dark";
    };

    "org/gnome/shell/world-clocks" = {
      locations = [ ];
    };

    "org/gnome/software" = {
      check-timestamp = mkInt64 1711511289;
      first-run = false;
      install-timestamp = mkInt64 1706937298;
      update-notification-timestamp = mkInt64 1711511178;
    };

    "org/gnome/tweaks" = {
      show-extensions-notice = false;
    };

    "org/virt-manager/virt-manager/connections" = {
      autoconnect = [ "qemu:///system" ];
      uris = [ "qemu:///system" ];
    };

  };
}
