# Generated via dconf2nix: https://github.com/gvolpe/dconf2nix
{ lib, ... }:

with lib.hm.gvariant;

{
  dconf.settings = {
    "apps/seahorse/listing" = { keyrings-selected = [ "gnupg://" ]; };

    "apps/seahorse/windows/key-manager" = {
      height = 711;
      width = 1264;
    };

    "ca/desrt/dconf-editor" = {
      saved-pathbar-path = "/org/gnome/desktop/wm/keybindings/";
      saved-view = "/org/gnome/desktop/wm/keybindings/";
      window-height = 1814;
      window-is-maximized = false;
      window-width = 1440;
    };

    "org/gnome/Console" = { last-window-size = mkTuple [ 652 481 ]; };

    "org/gnome/Geary" = { migrated-config = true; };

    "org/gnome/baobab/ui" = {
      is-maximized = false;
      window-size = mkTuple [ 960 600 ];
    };

    "org/gnome/calendar" = {
      active-view = "month";
      window-maximized = true;
      window-size = mkTuple [ 768 600 ];
    };

    "org/gnome/control-center" = {
      last-panel = "power";
      window-state = mkTuple [ 1154 990 ];
    };

    "org/gnome/desktop/a11y/applications" = { screen-reader-enabled = false; };

    "org/gnome/desktop/app-folders" = {
      folder-children = [ "Utilities" "YaST" ];
    };

    "org/gnome/desktop/app-folders/folders/Utilities" = {
      apps = [
        "gnome-abrt.desktop"
        "gnome-system-log.desktop"
        "nm-connection-editor.desktop"
        "org.gnome.Connections.desktop"
        "org.gnome.DejaDup.desktop"
        "org.gnome.Dictionary.desktop"
        "org.gnome.DiskUtility.desktop"
        "org.gnome.Evince.desktop"
        "org.gnome.FileRoller.desktop"
        "org.gnome.Usage.desktop"
        "org.gnome.baobab.desktop"
        "org.gnome.eog.desktop"
        "org.gnome.fonts.desktop"
        "org.gnome.seahorse.Application.desktop"
        "org.gnome.tweaks.desktop"
        "vinagre.desktop"
      ];
      categories = [ "X-GNOME-Utilities" ];
      name = "X-GNOME-Utilities.directory";
      translate = true;
    };

    "org/gnome/desktop/app-folders/folders/YaST" = {
      categories = [ "X-SuSE-YaST" ];
      name = "suse-yast.directory";
      translate = true;
    };

    "org/gnome/desktop/calendar" = { show-weekdate = false; };

    "org/gnome/desktop/input-sources" = {
      sources = [ (mkTuple [ "xkb" "us" ]) ];
      xkb-options = [ "caps:swapescape" ];
    };

    "org/gnome/desktop/interface" = {
      clock-format = "12h";
      clock-show-seconds = false;
      clock-show-weekday = true;
      color-scheme = "prefer-dark";
      cursor-size = 24;
      document-font-name = "Cantarell 10";
      font-antialiasing = "rgba";
      font-hinting = "medium";
      font-name = "Cantarell 10";
      monospace-font-name = "Hack Nerd Font Mono 10";
      overlay-scrolling = true;
      text-scaling-factor = 1.0;
      toolkit-accessibility = false;
    };

    "org/gnome/desktop/notifications" = {
      application-children = [
        "gnome-network-panel"
        "gnome-power-panel"
        "org-gnome-calendar"
        "org-gnome-characters"
        "org-gnome-console"
        "org-gnome-epiphany"
        "org-gnome-settings"
        "signal-desktop-beta"
      ];
      show-banners = false;
    };

    "org/gnome/desktop/notifications/application/gnome-network-panel" = {
      application-id = "gnome-network-panel.desktop";
    };

    "org/gnome/desktop/notifications/application/gnome-power-panel" = {
      application-id = "gnome-power-panel.desktop";
    };

    "org/gnome/desktop/notifications/application/gnome-system-monitor" = {
      application-id = "gnome-system-monitor.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-calendar" = {
      application-id = "org.gnome.Calendar.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-characters" = {
      application-id = "org.gnome.Characters.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-console" = {
      application-id = "org.gnome.Console.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-epiphany" = {
      application-id = "org.gnome.Epiphany.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-settings" = {
      application-id = "org.gnome.Settings.desktop";
    };

    "org/gnome/desktop/notifications/application/signal-desktop-beta" = {
      application-id = "signal-desktop-beta.desktop";
    };

    "org/gnome/desktop/peripherals/touchpad" = {
      tap-to-click = true;
      two-finger-scrolling-enabled = true;
    };

    "org/gnome/desktop/search-providers" = {
      sort-order = [
        "org.gnome.Contacts.desktop"
        "org.gnome.Documents.desktop"
        "org.gnome.Nautilus.desktop"
      ];
    };

    "org/gnome/desktop/wm/keybindings" = {
      move-to-center = [ "<Super><Control><Shift>Home" ];
    };

    "org/gnome/desktop/wm/preferences" = {
      titlebar-font = "Cantarell Bold 10";
    };

    "org/gnome/epiphany" = {
      active-clear-data-items = 103;
      ask-for-default = false;
      default-search-engine = "Google";
      search-engine-providers = ''
        [{'url': <'https://www.bing.com/search?q=%s'>, 'bang': <'!b'>, 'name': <'Bing'>}, {'url': <'https://duckduckgo.com/?q=%s&t=epiphany'>, 'bang': <'!ddg'>, 'name': <'DuckDuckGo'>}, {'url': <'https://www.google.com/search?q=%s'>, 'bang': <'!g'>, 'name': <'Google'>}]
      '';
      use-google-search-suggestions = true;
    };

    "org/gnome/epiphany/reader" = { font-style = "sans"; };

    "org/gnome/epiphany/web" = {
      enable-itp = false;
      last-download-directory = "/home/george/Downloads";
      monospace-font = "Hack Nerd Font Mono 12";
      sans-serif-font = "Hack Nerd Font 12";
      serif-font = "Hack Nerd Font 12";
      use-gnome-fonts = false;
    };

    "org/gnome/evolution-data-server" = { migrated = true; };

    "org/gnome/gnome-system-monitor" = {
      current-tab = "resources";
      maximized = true;
      network-total-in-bits = false;
      show-dependencies = false;
      show-whose-processes = "user";
      window-state = mkTuple [ 1920 1168 ];
    };

    "org/gnome/gnome-system-monitor/disktreenew" = {
      col-6-visible = true;
      col-6-width = 0;
    };

    "org/gnome/gnome-system-monitor/proctree" = {
      col-0-visible = true;
      col-0-width = 877;
      columns-order =
        [ 0 1 2 3 4 6 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 ];
      sort-col = 8;
      sort-order = 0;
    };

    "org/gnome/mutter" = {
      center-new-windows = true;
      dynamic-workspaces = true;
    };

    "org/gnome/nautilus/list-view" = {
      default-column-order = [
        "name"
        "size"
        "type"
        "owner"
        "group"
        "permissions"
        "where"
        "date_modified"
        "date_modified_with_time"
        "date_accessed"
        "date_created"
        "recency"
        "detailed_type"
      ];
      default-visible-columns = [
        "date_created"
        "date_modified"
        "detailed_type"
        "group"
        "name"
        "owner"
        "permissions"
        "size"
        "type"
      ];
      use-tree-view = true;
    };

    "org/gnome/nautilus/preferences" = {
      default-folder-viewer = "list-view";
      migrated-gtk-settings = true;
      search-filter-time-type = "last_modified";
      show-create-link = true;
      show-delete-permanently = true;
    };

    "org/gnome/nautilus/window-state" = {
      initial-size = mkTuple [ 1606 1048 ];
    };

    "org/gnome/nm-applet/eap/319ce2fe-1f8f-43a8-ad91-311d187d831a" = {
      ignore-ca-cert = false;
      ignore-phase2-ca-cert = false;
    };

    "org/gnome/nm-applet/eap/8b0aab25-ce46-48b1-a5f3-749fecb74a10" = {
      ignore-ca-cert = false;
      ignore-phase2-ca-cert = false;
    };

    "org/gnome/nm-applet/eap/b9f82885-8457-4ee3-84ba-2c41ab63fa32" = {
      ignore-ca-cert = false;
      ignore-phase2-ca-cert = false;
    };

    "org/gnome/portal/filechooser/brave-browser" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/gnome-network-panel" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/org/gnome/Epiphany" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/org/gnome/Settings" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/settings-daemon/plugins/color" = {
      night-light-enabled = false;
    };

    "org/gnome/shell" = {
      app-picker-layout =
        "[{'org.gnome.Geary.desktop': <{'position': <0>}>, 'org.gnome.Contacts.desktop': <{'position': <1>}>, 'org.gnome.Weather.desktop': <{'position': <2>}>, 'org.gnome.clocks.desktop': <{'position': <3>}>, 'org.gnome.Maps.desktop': <{'position': <4>}>, 'org.gnome.Snapshot.desktop': <{'position': <5>}>, 'dev.vlinkz.NixosConfEditor.desktop': <{'position': <6>}>, 'org.gnome.Totem.desktop': <{'position': <7>}>, 'org.gnome.Calculator.desktop': <{'position': <8>}>, 'com.github.qarmin.czkawka.desktop': <{'position': <9>}>, 'simple-scan.desktop': <{'position': <10>}>, 'element-desktop.desktop': <{'position': <11>}>, 'gnome-system-monitor.desktop': <{'position': <12>}>, 'org.gnome.Extensions.desktop': <{'position': <13>}>, 'Helix.desktop': <{'position': <14>}>, 'Utilities': <{'position': <15>}>, 'org.gnome.Loupe.desktop': <{'position': <16>}>, 'yelp.desktop': <{'position': <17>}>, 'LocalSend.desktop': <{'position': <18>}>, 'org.gnome.Music.desktop': <{'position': <19>}>, 'nvim.desktop': <{'position': <20>}>, 'nixos-manual.desktop': <{'position': <21>}>, 'obsidian.desktop': <{'position': <22>}>, 'brave-lgnggepjiihbfdbedefdhcffnmhcahbm-Default.desktop': <{'position': <23>}>}, {'signal-desktop-beta.desktop': <{'position': <0>}>, 'slack.desktop': <{'position': <1>}>, 'dev.vlinkz.NixSoftwareCenter.desktop': <{'position': <2>}>, 'org.gnome.TextEditor.desktop': <{'position': <3>}>, 'org.gnome.Tour.desktop': <{'position': <4>}>, 'app.drey.Warp.desktop': <{'position': <5>}>, 'xterm.desktop': <{'position': <6>}>}]";
      command-history = [ "r" "replace" "restart" "help" ];
      disabled-extensions = [
        "light-style@gnome-shell-extensions.gcampax.github.com"
        "native-window-placement@gnome-shell-extensions.gcampax.github.com"
        "window-list@gnome-shell-extensions.gcampax.github.com"
        "workspace-indicator@gnome-shell-extensions.gcampax.github.com"
      ];
      enabled-extensions = [
        "apps-menu@gnome-shell-extensions.gcampax.github.com"
        "places-menu@gnome-shell-extensions.gcampax.github.com"
        "drive-menu@gnome-shell-extensions.gcampax.github.com"
        "display-brightness-ddcutil@themightydeity.github.com"
      ];
      favorite-apps = [
        "brave-browser.desktop"
        "org.gnome.Epiphany.desktop"
        "Alacritty.desktop"
        "org.gnome.Calendar.desktop"
        "org.gnome.Nautilus.desktop"
        "org.gnome.Settings.desktop"
      ];
      last-selected-power-profile = "performance";
      welcome-dialog-last-shown-version = "44.2";
    };

    "org/gnome/shell/extensions/display-brightness-ddcutil" = {
      allow-zero-brightness = true;
      button-location = 0;
      ddcutil-binary-path = "/usr/bin/ddcutil";
      ddcutil-queue-ms = 130.0;
      ddcutil-sleep-multiplier = 40.0;
      decrease-brightness-shortcut = [ "<Control>XF86MonBrightnessDown" ];
      disable-display-state-check = false;
      hide-system-indicator = false;
      increase-brightness-shortcut = [ "<Control>XF86MonBrightnessUp" ];
      only-all-slider = false;
      position-system-menu = 1.0;
      show-all-slider = true;
      show-display-name = true;
      show-osd = true;
      show-value-label = true;
      step-change-keyboard = 2.0;
      verbose-debugging = true;
    };

    "org/gnome/shell/weather" = {
      automatic-location = true;
      locations = "@av []";
    };

    "org/gnome/shell/world-clocks" = { locations = "@av []"; };

    "org/gnome/tweaks" = { show-extensions-notice = false; };

    "org/gtk/gtk4/settings/file-chooser" = {
      date-format = "regular";
      location-mode = "path-bar";
      show-hidden = false;
      show-size-column = true;
      show-type-column = true;
      sidebar-width = 140;
      sort-column = "name";
      sort-directories-first = true;
      sort-order = "ascending";
      type-format = "category";
      view-type = "list";
      window-size = mkTuple [ 859 372 ];
    };

    "org/gtk/settings/file-chooser" = {
      clock-format = "12h";
      date-format = "regular";
      location-mode = "path-bar";
      show-hidden = false;
      show-size-column = true;
      show-type-column = true;
      sidebar-width = 157;
      sort-column = "name";
      sort-directories-first = false;
      sort-order = "ascending";
      type-format = "category";
      window-position = mkTuple [ 358 141 ];
      window-size = mkTuple [ 1203 902 ];
    };

  };
}
