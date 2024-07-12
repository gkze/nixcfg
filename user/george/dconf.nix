# Generated via dconf2nix: https://github.com/gvolpe/dconf2nix
{ lib, ... }: with lib.hm.gvariant; {
  dconf.settings = {
    "apps/seahorse/listing" = { keyrings-selected = [ "gnupg://" ]; };

    "apps/seahorse/windows/key-manager" = { height = 476; width = 600; };

    "ca/desrt/dconf-editor" = {
      saved-pathbar-path = "/org/gnome/desktop/wm/keybindings/";
      saved-view = "/org/gnome/desktop/wm/keybindings/";
      window-height = 1931;
      window-is-maximized = false;
      window-width = 1094;
    };

    "desktop/ibus/panel/emoji" = { unicode-hotkey = [ "<Control><Shift>p" ]; };

    "org/gnome/Connections" = { first-run = false; };

    "org/gnome/Console" = {
      last-window-maximised = false;
      last-window-size = mkTuple [ 1720 1156 ];
    };

    "org/gnome/Geary" = {
      compose-as-html = true;
      formatting-toolbar-visible = false;
      images-trusted-domains = [
        "accounts.google.com"
        "yourstudio.com"
        "doordash.com"
        "findyourzo.com"
        "usebasis.co"
      ];
      migrated-config = true;
      window-height = 1663;
      window-maximize = false;
      window-width = 2367;
    };

    "org/gnome/Weather" = {
      locations = [
        (mkVariant [
          (mkUint32 2)
          (mkVariant [
            "San Francisco"
            "KOAK"
            false
            [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ]
            [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ]
          ])
        ])
      ];
      window-height = 650;
      window-maximized = false;
      window-width = 1317;
    };

    "org/gnome/baobab/ui" = {
      is-maximized = false;
      window-size = mkTuple [ 2545 2034 ];
    };

    "org/gnome/calculator" = {
      accuracy = 9;
      angle-units = "degrees";
      base = 10;
      button-mode = "basic";
      number-format = "engineering";
      show-thousands = false;
      show-zeroes = false;
      source-currency = "DZD";
      source-units = "degree";
      target-currency = "DZD";
      target-units = "radian";
      window-maximized = false;
      window-size = mkTuple [ 397 717 ];
      word-size = 64;
    };

    "org/gnome/calendar" = {
      active-view = "week";
      week-view-zoom-level = 0.8506974059416861;
      window-maximized = true;
      window-size = mkTuple [ 1481 988 ];
    };

    "org/gnome/clocks" = {
      world-clocks = [
        {
          location = mkVariant [
            (mkUint32 2)
            (mkVariant [
              "San Francisco"
              "KOAK"
              true
              [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ]
              [ (mkTuple [ 0.659296885757089 (-2.136621860115334) ]) ]
            ])
          ];
        }
        {
          location = mkVariant [
            (mkUint32 2)
            (mkVariant [
              "New York"
              "KNYC"
              true
              [ (mkTuple [ 0.7118034407872564 (-1.2909618758762367) ]) ]
              [ (mkTuple [ 0.7105980465926592 (-1.2916478949920254) ]) ]
            ])
          ];
        }
        {
          location = mkVariant [
            (mkUint32 2)
            (mkVariant [
              "Berlin"
              "EDDT"
              true
              [ (mkTuple [ 0.9174614159494501 0.23241968454167572 ]) ]
              [ (mkTuple [ 0.916588751323453 0.23387411976724018 ]) ]
            ])
          ];
        }
        {
          location = mkVariant [
            (mkUint32 2)
            (mkVariant [
              "Tbilisi"
              "UGTB"
              true
              [ (mkTuple [ 0.727264160713368 0.7846079132187302 ]) ]
              [ (mkTuple [ 0.7280931921080653 0.7816166108670297 ]) ]
            ])
          ];
        }
      ];
    };

    "org/gnome/clocks/state/window" = {
      maximized = false;
      panel-id = "timer";
      size = mkTuple [ 870 690 ];
    };

    "org/gnome/control-center" = {
      last-panel = "system";
      window-state = mkTuple [ 1028 1047 false ];
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
      apps = [
        "gnome-abrt.desktop"
        "gnome-system-log.desktop"
        "nm-connection-editor.desktop"
        "org.gnome.baobab.desktop"
        "org.gnome.Connections.desktop"
        "org.gnome.DejaDup.desktop"
        "org.gnome.Dictionary.desktop"
        "org.gnome.DiskUtility.desktop"
        "org.gnome.Evince.desktop"
        "org.gnome.FileRoller.desktop"
        "org.gnome.fonts.desktop"
        "org.gnome.Loupe.desktop"
        "org.gnome.seahorse.Application.desktop"
        "org.gnome.tweaks.desktop"
        "org.gnome.Usage.desktop"
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
      cursor-theme = "catppuccin-frappe-blue-cursors";
      font-antialiasing = "grayscale";
      font-hinting = "slight";
      gtk-theme = "catppuccin-frappe-blue-standard+rimless";
      icon-theme = "Papirus-Dark";
      monospace-font-name = "Hack Nerd Font Mono 11";
      overlay-scrolling = true;
      show-battery-percentage = true;
      text-scaling-factor = 1.0;
      toolkit-accessibility = false;
    };

    "org/gnome/desktop/notifications" = {
      application-children = [
        "org-gnome-software"
        "gnome-network-panel"
        "org-gnome-calendar"
        "gnome-system-monitor"
        "gnome-power-panel"
        "slack"
        "org-gnome-evolution-alarm-notify"
        "org-gnome-settings"
        "org-gnome-nautilus"
        "brave-browser"
        "brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-default"
        "org-gnome-baobab"
        "org-gnome-evince"
        "org-gnome-geary"
        "dbeaver"
        "alacritty"
        "io-podman-desktop-podmandesktop"
        "1password"
        "org-gimp-gimp"
        "org-gnome-characters"
        "code"
        "brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-profile-2"
        "org-gnome-systemmonitor"
        "brave-fmpnliohjhemenmnlpbfagaolkdacoja-default"
      ];
      show-banners = true;
    };

    "org/gnome/desktop/notifications/application/1password" = {
      application-id = "1password.desktop";
    };

    "org/gnome/desktop/notifications/application/alacritty" = {
      application-id = "Alacritty.desktop";
    };

    "org/gnome/desktop/notifications/application/brave-browser" = {
      application-id = "brave-browser.desktop";
    };

    "org/gnome/desktop/notifications/application/brave-fmpnliohjhemenmnlpbfagaolkdacoja-default" = {
      application-id = "brave-fmpnliohjhemenmnlpbfagaolkdacoja-Default.desktop";
    };

    "org/gnome/desktop/notifications/application/brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-default" = {
      application-id = "brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-Default.desktop";
    };

    "org/gnome/desktop/notifications/application/brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-profile-2" = {
      application-id = "brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-Profile_2.desktop";
    };

    "org/gnome/desktop/notifications/application/code" = {
      application-id = "code.desktop";
    };

    "org/gnome/desktop/notifications/application/dbeaver" = {
      application-id = "dbeaver.desktop";
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

    "org/gnome/desktop/notifications/application/io-podman-desktop-podmandesktop" = {
      application-id = "io.podman_desktop.PodmanDesktop.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gimp-gimp" = {
      application-id = "org.gimp.GIMP.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-baobab" = {
      application-id = "org.gnome.baobab.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-calendar" = {
      application-id = "org.gnome.Calendar.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-characters" = {
      application-id = "org.gnome.Characters.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-evince" = {
      application-id = "org.gnome.Evince.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-evolution-alarm-notify" = {
      application-id = "org.gnome.Evolution-alarm-notify.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-geary" = {
      application-id = "org.gnome.Geary.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-nautilus" = {
      application-id = "org.gnome.Nautilus.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-settings" = {
      application-id = "org.gnome.Settings.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-software" = {
      application-id = "org.gnome.Software.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-systemmonitor" = {
      application-id = "org.gnome.SystemMonitor.desktop";
    };

    "org/gnome/desktop/notifications/application/slack" = {
      application-id = "slack.desktop";
    };

    "org/gnome/desktop/peripherals/mouse" = {
      natural-scroll = true;
    };

    "org/gnome/desktop/peripherals/touchpad" = {
      speed = 0.2549019607843137;
      tap-to-click = true;
      two-finger-scrolling-enabled = true;
    };

    "org/gnome/desktop/privacy" = {
      old-files-age = mkUint32 30;
      recent-files-max-age = -1;
    };

    "org/gnome/desktop/search-providers" = {
      sort-order = [
        "org.gnome.Contacts.desktop"
        "org.gnome.Documents.desktop"
        "org.gnome.Nautilus.desktop"
      ];
    };

    "org/gnome/desktop/session" = {
      idle-delay = mkUint32 300;
    };

    "org/gnome/desktop/wm/keybindings" = {
      move-to-center = [ "<Super><Control><Shift>Home" ];
      toggle-fullscreen = [ "<Super><Shift>F" ];
    };

    "org/gnome/desktop/wm/preferences" = {
      button-layout = "appmenu:minimize,maximize,close";
      titlebar-font = "Cantarell Bold 11";
    };

    "org/gnome/epiphany" = {
      ask-for-default = false;
    };

    "org/gnome/epiphany/state" = {
      is-maximized = false;
      window-size = mkTuple [ 1024 768 ];
    };

    "org/gnome/evince/default" = {
      continuous = false;
      dual-page = false;
      dual-page-odd-left = false;
      enable-spellchecking = true;
      fullscreen = false;
      inverted-colors = false;
      show-sidebar = true;
      sidebar-page = "thumbnails";
      sidebar-size = 150;
      sizing-mode = "fit-width";
      window-ratio = mkTuple [ 1.59640522875817 1.3977272727272727 ];
      zoom = 0.8333333333333333;
    };

    "org/gnome/evolution-data-server" = {
      migrated = true;
    };

    "org/gnome/evolution-data-server/calendar" = {
      notify-window-height = 483;
      notify-window-paned-position = 63;
      notify-window-width = 409;
      notify-window-x = 102;
      notify-window-y = 102;
      reminders-past = [ ];
    };

    "org/gnome/file-roller/dialogs/extract" = {
      height = 800;
      recreate-folders = true;
      skip-newer = false;
      width = 1000;
    };

    "org/gnome/file-roller/file-selector" = {
      show-hidden = false;
      sidebar-size = 300;
      window-size = mkTuple [ (-1) (-1) ];
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
      current-tab = "resources";
      maximized = false;
      network-total-in-bits = false;
      show-dependencies = true;
      show-whose-processes = "user";
      window-height = 1111;
      window-state = mkTuple [ 1145 873 103 103 ];
      window-width = 1361;
    };

    "org/gnome/gnome-system-monitor/disktreenew" = {
      col-0-visible = true;
      col-0-width = 437;
      col-1-visible = true;
      col-1-width = 93;
      col-2-visible = true;
      col-2-width = 62;
      col-3-visible = true;
      col-3-width = 73;
      col-5-visible = true;
      col-5-width = 91;
      col-6-visible = true;
      col-6-width = 0;
      columns-order = [ 0 1 2 3 4 5 6 ];
      sort-col = 1;
      sort-order = 0;
    };

    "org/gnome/gnome-system-monitor/proctree" = {
      col-0-visible = true;
      col-0-width = 449;
      col-1-visible = true;
      col-1-width = 62;
      col-12-visible = true;
      col-12-width = 97;
      col-15-visible = true;
      col-15-width = 86;
      col-22-visible = true;
      col-22-width = 127;
      col-23-visible = true;
      col-23-width = 132;
      col-24-visible = true;
      col-24-width = 94;
      col-25-visible = true;
      col-25-width = 99;
      col-8-visible = true;
      col-8-width = 77;
      columns-order = [
        0
        1
        8
        2
        3
        4
        6
        9
        10
        11
        12
        13
        14
        15
        16
        17
        18
        19
        20
        21
        22
        23
        24
        25
        26
        7
      ];
      sort-col = 0;
      sort-order = 0;
    };

    "org/gnome/maps" = {
      last-viewed-location = [ 37.76005879400782 (-122.43592813383793) ];
      map-type = "MapsStreetSource";
      transportation-type = "pedestrian";
      window-maximized = false;
      window-size = [ 2448 1786 ];
      zoom-level = 17;
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
    };

    "org/gnome/nautilus/window-state" = {
      initial-size = mkTuple [ 1920 1166 ];
      maximized = false;
    };

    "org/gnome/nm-applet/eap/70ba07b5-9bae-4646-87f2-f77b38a4a58d" = {
      ignore-ca-cert = false;
      ignore-phase2-ca-cert = false;
    };

    "org/gnome/nm-applet/eap/9af31e9a-764b-462f-b9a8-2a8aed26c932" = {
      ignore-ca-cert = false;
      ignore-phase2-ca-cert = false;
    };

    "org/gnome/nm-applet/eap/a4961fba-29df-4663-9846-3de9bbc13e0d" = {
      ignore-ca-cert = false;
      ignore-phase2-ca-cert = false;
    };

    "org/gnome/portal/filechooser/brave-browser" = {
      last-folder-path = "/home/george/Pictures/Screenshots";
    };

    "org/gnome/portal/filechooser/dev/skynomads/Seabird" = {
      last-folder-path = "/home/george/.kube";
    };

    "org/gnome/portal/filechooser/gnome-network-panel" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/io/httpie/Httpie" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/slack" = {
      last-folder-path = "/home/george/Pictures/Screenshots";
    };

    "org/gnome/settings-daemon/plugins/color" = {
      night-light-last-coordinates = mkTuple [ 37.78477307139083 (-122.406) ];
    };

    "org/gnome/settings-daemon/plugins/media-keys" = {
      next = [ "Cancel" ];
      play = [ "Messenger" ];
      previous = [ "Go" ];
    };

    "org/gnome/shell" = {
      disable-user-extensions = false;
      disabled-extensions = [
        "light-style@gnome-shell-extensions.gcampax.github.com"
        "native-window-placement@gnome-shell-extensions.gcampax.github.com"
        "window-list@gnome-shell-extensions.gcampax.github.com"
        "workspace-indicator@gnome-shell-extensions.gcampax.github.com"
      ];
      enabled-extensions = [
        "user-theme@gnome-shell-extensions.gcampax.github.com"
        "apps-menu@gnome-shell-extensions.gcampax.github.com"
        "display-brightness-ddcutil@themightydeity.github.com"
        "drive-menu@gnome-shell-extensions.gcampax.github.com"
        "places-menu@gnome-shell-extensions.gcampax.github.com"
        "screenshot-window-sizer@gnome-shell-extensions.gcampax.github.com"
        "user-theme@gnome-shell-extensions.gcampax.github.com"
      ];
      favorite-apps = [
        "obsidian.desktop"
        "brave-browser.desktop"
        "Alacritty.desktop"
        "slack.desktop"
        "org.gnome.Calendar.desktop"
        "org.gnome.Nautilus.desktop"
        "org.gnome.SystemMonitor.desktop"
        "org.gnome.Settings.desktop"
      ];
      last-selected-power-profile = "power-saver";
      welcome-dialog-last-shown-version = "45.5";
    };

    "org/gnome/shell/extensions/display-brightness-ddcutil" = {
      allow-zero-brightness = true;
      button-location = 0;
      ddcutil-binary-path = "/nix/store/n6llfsminarz3wl9cqzinh0xdnx5zdid-ddcutil-2.1.4/bin/ddcutil";
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
      name = "catppuccin-frappe-blue-standard+rimless";
    };

    "org/gnome/shell/weather" = {
      automatic-location = true;
      locations = [
        (mkVariant [
          (mkUint32 2)
          (mkVariant [
            "San Francisco"
            "KOAK"
            false
            [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ]
            [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ]
          ])
        ])
      ];
    };

    "org/gnome/shell/world-clocks" = {
      locations = [
        (mkVariant [
          (mkUint32 2)
          (mkVariant [
            "San Francisco"
            "KOAK"
            true
            [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ]
            [ (mkTuple [ 0.659296885757089 (-2.136621860115334) ]) ]
          ])
        ])
        (mkVariant [
          (mkUint32 2)
          (mkVariant [
            "New York"
            "KNYC"
            true
            [ (mkTuple [ 0.7118034407872564 (-1.2909618758762367) ]) ]
            [ (mkTuple [ 0.7105980465926592 (-1.2916478949920254) ]) ]
          ])
        ])
        (mkVariant [
          (mkUint32 2)
          (mkVariant [
            "Berlin"
            "EDDT"
            true
            [ (mkTuple [ 0.9174614159494501 0.23241968454167572 ]) ]
            [ (mkTuple [ 0.916588751323453 0.23387411976724018 ]) ]
          ])
        ])
        (mkVariant [
          (mkUint32 2)
          (mkVariant [
            "Tbilisi"
            "UGTB"
            true
            [ (mkTuple [ 0.727264160713368 0.7846079132187302 ]) ]
            [ (mkTuple [ 0.7280931921080653 0.7816166108670297 ]) ]
          ])
        ])
      ];
    };

    "org/gnome/software" = {
      check-timestamp = mkInt64 1720802036;
      first-run = false;
      flatpak-purge-timestamp = mkInt64 1720744246;
      install-timestamp = mkInt64 1717698225;
      update-notification-timestamp = mkInt64 1717699403;
    };

    "org/gnome/system/location" = { enabled = true; };

    "org/gnome/tweaks" = { show-extensions-notice = false; };

    "org/gtk/gtk4/settings/file-chooser" = {
      date-format = "regular";
      location-mode = "path-bar";
      show-hidden = false;
      show-size-column = true;
      show-type-column = true;
      sidebar-width = 277;
      sort-column = "name";
      sort-directories-first = true;
      sort-order = "ascending";
      type-format = "category";
      view-type = "list";
      window-size = mkTuple [ 1134 677 ];
    };

    "org/gtk/settings/color-chooser" = {
      custom-colors = [
        (mkTuple [
          0.3176470588235294
          0.33725490196078434
          0.34509803921568627
          1.0
        ])
        (mkTuple [
          0.9333333333333333
          0.11372549019607843
          0.0
          1.0
        ])
        (mkTuple [
          0.0
          0.0
          0.4588235294117647
          1.0
        ])
        (mkTuple [
          0.25882352941176473
          0.8313725490196079
          0.9568627450980393
          1.0
        ])
        (mkTuple [
          0.6705882352941176
          9.411764705882353e-2
          0.3215686274509804
          1.0
        ])
        (mkTuple [
          0.28627450980392155
          0.6588235294117647
          0.20784313725490197
          1.0
        ])
      ];
      selected-color = mkTuple [
        true
        0.8980392156862745
        0.6470588235294118
        3.92156862745098e-2
        1.0
      ];
    };

    "org/gtk/settings/file-chooser" = {
      date-format = "regular";
      location-mode = "path-bar";
      show-hidden = true;
      show-size-column = true;
      show-type-column = true;
      sidebar-width = 165;
      sort-column = "type";
      sort-directories-first = false;
      sort-order = "ascending";
      type-format = "category";
      window-position = mkTuple [ 102 102 ];
      window-size = mkTuple [ 1203 902 ];
    };

    "org/virt-manager/virt-manager/connections" = {
      autoconnect = [ "qemu:///system" ];
      uris = [ "qemu:///system" ];
    };
  };
}
