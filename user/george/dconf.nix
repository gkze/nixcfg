# Generated via dconf2nix: https://github.com/gvolpe/dconf2nix
{ lib, ... }:

with lib.hm.gvariant;

{
  dconf.settings = {
    "apps/seahorse/listing" = {
      keyrings-selected = [ "gnupg://" ];
    };

    "apps/seahorse/windows/key-manager" = {
      height = 711;
      width = 1264;
    };

    "ca/desrt/dconf-editor" = {
      saved-pathbar-path = "/org/gnome/desktop/wm/keybindings/toggle-fullscreen";
      saved-view = "/org/gnome/desktop/wm/keybindings/";
      window-height = 1961;
      window-is-maximized = false;
      window-width = 1336;
    };

    "desktop/ibus/panel/emoji" = {
      unicode-hotkey = [ ];
    };

    "org/gnome/Console" = {
      font-scale = 0.8;
      last-window-size = mkTuple [ 1440 876 ];
      use-system-font = true;
    };

    "org/gnome/Geary" = {
      migrated-config = true;
      window-height = 1842;
      window-maximize = false;
      window-width = 2650;
    };

    "org/gnome/Snapshot" = {
      capture-mode = "picture";
      is-maximized = false;
      window-height = 640;
      window-width = 800;
    };

    "org/gnome/baobab/ui" = {
      is-maximized = false;
      window-size = mkTuple [ 960 600 ];
    };

    "org/gnome/calculator" = {
      accuracy = 9;
      angle-units = "degrees";
      base = 10;
      button-mode = "programming";
      number-format = "automatic";
      show-thousands = false;
      show-zeroes = false;
      source-currency = "";
      source-units = "degree";
      target-currency = "";
      target-units = "radian";
      window-maximized = false;
      window-size = mkTuple [ 680 666 ];
      word-size = 64;
    };

    "org/gnome/calendar" = {
      active-view = "week";
      week-view-zoom-level = 0.9633152064308809;
      window-maximized = true;
      window-size = mkTuple [ 360 600 ];
    };

    "org/gnome/clocks/state/window" = {
      maximized = false;
      panel-id = "world";
      size = mkTuple [ 870 690 ];
    };

    "org/gnome/control-center" = {
      last-panel = "power";
      window-state = mkTuple [ 1091 945 false ];
    };

    "org/gnome/desktop/a11y/applications" = {
      screen-reader-enabled = false;
    };

    "org/gnome/desktop/a11y/magnifier" = {
      mag-factor = 7.0;
    };

    "org/gnome/desktop/app-folders" = {
      folder-children = [ "Utilities" "YaST" ];
    };

    "org/gnome/desktop/app-folders/folders/Utilities" = {
      apps = [ "gnome-abrt.desktop" "gnome-system-log.desktop" "nm-connection-editor.desktop" "org.gnome.Connections.desktop" "org.gnome.DejaDup.desktop" "org.gnome.Dictionary.desktop" "org.gnome.DiskUtility.desktop" "org.gnome.Evince.desktop" "org.gnome.FileRoller.desktop" "org.gnome.Usage.desktop" "org.gnome.baobab.desktop" "org.gnome.eog.desktop" "org.gnome.fonts.desktop" "org.gnome.seahorse.Application.desktop" "org.gnome.tweaks.desktop" "vinagre.desktop" ];
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

    "org/gnome/desktop/calendar" = {
      show-weekdate = false;
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
      cursor-size = 24;
      document-font-name = "Cantarell 11";
      font-antialiasing = "rgba";
      font-hinting = "medium";
      font-name = "Cantarell 11";
      monospace-font-name = "Hack Nerd Font Mono 11";
      overlay-scrolling = true;
      show-battery-percentage = false;
      text-scaling-factor = 1.0;
      toolkit-accessibility = false;
    };

    "org/gnome/desktop/notifications" = {
      application-children = [ "gnome-network-panel" "gnome-power-panel" "org-gnome-calendar" "org-gnome-characters" "org-gnome-console" "org-gnome-epiphany" "org-gnome-settings" "signal-desktop-beta" "slack" "org-gnome-tweaks" "org-gnome-evolution-alarm-notify" "gnome-system-monitor" "brave-browser" "org-gnome-software" "brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-default" "signal-desktop" "com-spotify-client" "code" "org-gnome-fileroller" "1password" "org-gnome-nautilus" ];
      show-banners = false;
    };

    "org/gnome/desktop/notifications/application/1password" = {
      application-id = "1password.desktop";
    };

    "org/gnome/desktop/notifications/application/brave-browser" = {
      application-id = "brave-browser.desktop";
    };

    "org/gnome/desktop/notifications/application/brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-default" = {
      application-id = "brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-Default.desktop";
    };

    "org/gnome/desktop/notifications/application/code" = {
      application-id = "code.desktop";
    };

    "org/gnome/desktop/notifications/application/com-spotify-client" = {
      application-id = "com.spotify.Client.desktop";
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

    "org/gnome/desktop/notifications/application/org-gnome-evolution-alarm-notify" = {
      application-id = "org.gnome.Evolution-alarm-notify.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-fileroller" = {
      application-id = "org.gnome.FileRoller.desktop";
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

    "org/gnome/desktop/notifications/application/org-gnome-tweaks" = {
      application-id = "org.gnome.tweaks.desktop";
    };

    "org/gnome/desktop/notifications/application/signal-desktop-beta" = {
      application-id = "signal-desktop-beta.desktop";
    };

    "org/gnome/desktop/notifications/application/signal-desktop" = {
      application-id = "signal-desktop.desktop";
    };

    "org/gnome/desktop/notifications/application/slack" = {
      application-id = "slack.desktop";
    };

    "org/gnome/desktop/peripherals/mouse" = {
      accel-profile = "flat";
      natural-scroll = true;
      speed = 0.18939393939393945;
    };

    "org/gnome/desktop/peripherals/touchpad" = {
      tap-to-click = true;
      two-finger-scrolling-enabled = true;
    };

    "org/gnome/desktop/privacy" = {
      old-files-age = mkUint32 30;
      recent-files-max-age = -1;
    };

    "org/gnome/desktop/search-providers" = {
      sort-order = [ "org.gnome.Contacts.desktop" "org.gnome.Documents.desktop" "org.gnome.Nautilus.desktop" ];
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

    "org/gnome/dictionary" = {
      database = "!";
      source-name = "Default";
      strategy = "exact";
    };

    "org/gnome/epiphany" = {
      active-clear-data-items = 103;
      ask-for-default = false;
      default-search-engine = "Google";
      search-engine-providers = "[{'url': <'https://www.bing.com/search?q=%s'>, 'bang': <'!b'>, 'name': <'Bing'>}, {'url': <'https://duckduckgo.com/?q=%s&t=epiphany'>, 'bang': <'!ddg'>, 'name': <'DuckDuckGo'>}, {'url': <'https://www.google.com/search?q=%s'>, 'bang': <'!g'>, 'name': <'Google'>}]n";
      use-google-search-suggestions = true;
    };

    "org/gnome/epiphany/reader" = {
      font-style = "sans";
    };

    "org/gnome/epiphany/web" = {
      enable-itp = false;
      last-download-directory = "/home/george/Downloads";
      monospace-font = "Hack Nerd Font Mono 12";
      sans-serif-font = "Hack Nerd Font 12";
      serif-font = "Hack Nerd Font 12";
      use-gnome-fonts = false;
    };

    "org/gnome/evince" = {
      document-directory = "file:///home/george/Downloads";
    };

    "org/gnome/evince/default" = {
      continuous = true;
      dual-page = false;
      dual-page-odd-left = false;
      enable-spellchecking = true;
      fullscreen = false;
      inverted-colors = false;
      show-sidebar = true;
      sidebar-page = "thumbnails";
      sidebar-size = 132;
      sizing-mode = "automatic";
      window-ratio = mkTuple [ 1.9722222222222223 1.3308080808080809 ];
    };

    "org/gnome/evolution-data-server" = {
      migrated = true;
    };

    "org/gnome/evolution-data-server/calendar" = {
      notify-window-height = 281;
      notify-window-paned-position = 48;
      notify-window-width = 213;
      notify-window-x = 26;
      notify-window-y = 23;
      reminders-past = [ "4f07455384537b2cfce205647ab990f233d98eeanb87fdcada76853323215ef59900c8e84c64f2b9bt20240307T130000n1709844600n1709845200n1709846100nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240307T130000rnDTEND;TZID=America/Los_Angeles:20240307T131500rnRRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231226T130000rnEXDATE;TZID=America/Los_Angeles:20231227T130000rnEXDATE;TZID=America/Los_Angeles:20231228T130000rnEXDATE;TZID=America/Los_Angeles:20240102T130000rnEXDATE;TZID=America/Los_Angeles:20240103T130000rnEXDATE;TZID=America/Los_Angeles:20240104T130000rnDTSTAMP:20240304T174854ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T174854ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63845257734rnRECURRENCE-ID;TZID=America/Los_Angeles:20240307T130000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:b87fdcada76853323215ef59900c8e84c64f2b9brnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eean3b7840830fdd34bab7c25c03021a6a9e96c82f8et20240307T110000n1709837400n1709838000n1709839800nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240307T110000rnDTEND;TZID=America/Los_Angeles:20240307T113000rnRRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=THrnEXDATE;TZID=America/Los_Angeles:20231228T110000rnDTSTAMP:20240208T212949ZrnORGANIZER;CN=mike@usebasis.co:mailto:mike@usebasis.cornUID:7l8ntagq3a0i6dvc5aqumg8mki@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=mike@usebasis.co;X-NUM-GUESTS=0:mailto:mike@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/kwx-yxbk-zrdrnCREATED:20231213T003821ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/kwx-yxbk-zrd\\nOr dial: (US) +1 320-318-7878 PIN: rn 414605953#\\nMore phone numbers: https:rn //tel.meet/kwx-yxbk-zrd?pin=3367942540003&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240208T212949ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:George x MikernTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63843528333rnRECURRENCE-ID;TZID=America/Los_Angeles:20240307T110000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:3b7840830fdd34bab7c25c03021a6a9e96c82f8ernEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eeanb87fdcada76853323215ef59900c8e84c64f2b9bt20240306T130000n1709758200n1709758800n1709759700nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240306T130000rnDTEND;TZID=America/Los_Angeles:20240306T131500rnRRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231226T130000rnEXDATE;TZID=America/Los_Angeles:20231227T130000rnEXDATE;TZID=America/Los_Angeles:20231228T130000rnEXDATE;TZID=America/Los_Angeles:20240102T130000rnEXDATE;TZID=America/Los_Angeles:20240103T130000rnEXDATE;TZID=America/Los_Angeles:20240104T130000rnDTSTAMP:20240304T174854ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T174854ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63845257734rnRECURRENCE-ID;TZID=America/Los_Angeles:20240306T130000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:b87fdcada76853323215ef59900c8e84c64f2b9brnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eeanb87fdcada76853323215ef59900c8e84c64f2b9bt20240305T130000n1709671800n1709672400n1709673300nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240305T130000rnDTEND;TZID=America/Los_Angeles:20240305T131500rnRRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231226T130000rnEXDATE;TZID=America/Los_Angeles:20231227T130000rnEXDATE;TZID=America/Los_Angeles:20231228T130000rnEXDATE;TZID=America/Los_Angeles:20240102T130000rnEXDATE;TZID=America/Los_Angeles:20240103T130000rnEXDATE;TZID=America/Los_Angeles:20240104T130000rnDTSTAMP:20240304T174854ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T174854ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63845257734rnRECURRENCE-ID;TZID=America/Los_Angeles:20240305T130000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:b87fdcada76853323215ef59900c8e84c64f2b9brnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eean16822e0373b4688c61d83249a470063b98d36553t20240304T100000n1709585400n1709586000n1709589600nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240304T130000rnDTEND;TZID=America/Los_Angeles:20240304T140000rnDTSTAMP:20240304T173802ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:cjor6rn0v3te499kon6didavua_R20231204T180000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=mike@usebasis.co;X-NUM-GUESTS=0:mailto:mike@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=dante@usebasis.co;X-NUM-GUESTS=0:mailto:dante@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=kyle@usebasis.co;X-NUM-GUESTS=0:mailto:kyle@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=troute@usebasis.co;X-NUM-GUESTS=0:mailto:troute@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/mns-oimq-wkgrnRECURRENCE-ID;TZID=America/Los_Angeles:20240304T100000rnCREATED:20221104T045344ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/mns-oimq-wkg\\nOr dial: (US) +1 661-772-9059 PIN: rn 652845310#\\nMore phone numbers: https:rn //tel.meet/mns-oimq-wkg?pin=8044670134175&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T173802ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:4rnSTATUS:CONFIRMEDrnSUMMARY:Weekly standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63845257082rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:16822e0373b4688c61d83249a470063b98d36553rnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacn80bc441bdb9621f03e193d4a228c216098a6754et20240304T100000n1709585400n1709586000n1709589600nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240304T130000rnDTEND;TZID=America/Los_Angeles:20240304T140000rnDTSTAMP:20240304T173802ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:cjor6rn0v3te499kon6didavua_R20231204T180000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=mike@usebasis.co;X-NUM-GUESTS=0:mailto:mike@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=dante@usebasis.co;X-NUM-GUESTS=0:mailto:dante@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=kyle@usebasis.co;X-NUM-GUESTS=0:mailto:kyle@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=troute@usebasis.co;X-NUM-GUESTS=0:mailto:troute@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/mns-oimq-wkgrnRECURRENCE-ID;TZID=America/Los_Angeles:20240304T100000rnCREATED:20221104T045344ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/mns-oimq-wkg\\nOr dial: (US) +1 661-772-9059 PIN: rn 652845310#\\nMore phone numbers: https:rn //tel.meet/mns-oimq-wkg?pin=8044670134175&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T173802ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:4rnSTATUS:CONFIRMEDrnSUMMARY:Weekly standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63845257082rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:80bc441bdb9621f03e193d4a228c216098a6754ernEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:3420ca236f1e7327d0ea59d3cbaf215f8aed0031rnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacn3420ca236f1e7327d0ea59d3cbaf215f8aed0031t20240304T100000n1709584200n1709586000n1709589600nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240304T130000rnDTEND;TZID=America/Los_Angeles:20240304T140000rnDTSTAMP:20240304T173802ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:cjor6rn0v3te499kon6didavua_R20231204T180000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=mike@usebasis.co;X-NUM-GUESTS=0:mailto:mike@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=dante@usebasis.co;X-NUM-GUESTS=0:mailto:dante@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=kyle@usebasis.co;X-NUM-GUESTS=0:mailto:kyle@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=troute@usebasis.co;X-NUM-GUESTS=0:mailto:troute@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/mns-oimq-wkgrnRECURRENCE-ID;TZID=America/Los_Angeles:20240304T100000rnCREATED:20221104T045344ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/mns-oimq-wkg\\nOr dial: (US) +1 661-772-9059 PIN: rn 652845310#\\nMore phone numbers: https:rn //tel.meet/mns-oimq-wkg?pin=8044670134175&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T173802ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:4rnSTATUS:CONFIRMEDrnSUMMARY:Weekly standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63845257082rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:80bc441bdb9621f03e193d4a228c216098a6754ernEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:3420ca236f1e7327d0ea59d3cbaf215f8aed0031rnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eeana0dd6b029fe5c173a173ac03f6361c4f8e5a9029t20240304T120000n1709581800n1709582400n1709586000nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240304T120000rnDTEND;TZID=America/Los_Angeles:20240304T130000rnRRULE:FREQ=WEEKLY;BYDAY=MO,FR;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231124T120000rnEXDATE;TZID=America/Los_Angeles:20231225T120000rnEXDATE;TZID=America/Los_Angeles:20231229T120000rnEXDATE;TZID=America/Los_Angeles:20240101T120000rnEXDATE;TZID=America/Los_Angeles:20240105T120000rnEXDATE;TZID=America/Los_Angeles:20240115T120000rnEXDATE;TZID=America/Los_Angeles:20240219T120000rnDTSTAMP:20240304T174837ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:0qv2g51uk9qooa6p1p9m2kjsu9_R20231016T190000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/pow-unuv-btfrnCREATED:20230620T234404ZrnDESCRIPTION:As the team grows\\, it would be nice eat together as a team\\, rn so we're proposing twice-weekly lunches.<br><span><br></span><br><span>If rn you bring lunch\\, start warming it up a little earlier so you're ready at rn 12. If you're eating out\\, we'll cover your DoorDash up to $25 (don't rn forget -- you can schedule your order ahead of time)</span>\\n\\n-::~:~::~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:rn ~::-\\nJoin with Google Meet: https://meet.google.com/pow-unuv-btf\\nOr rn dial: (US) +1 513-760-6079 PIN: 535859198#\\nMore phone numbers: https:rn //tel.meet/pow-unuv-btf?pin=9484659480677&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T174837ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Team LunchrnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63845257717rnRECURRENCE-ID;TZID=America/Los_Angeles:20240304T120000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:a0dd6b029fe5c173a173ac03f6361c4f8e5a9029rnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacn6e28b896c7f8ba8cb42e8b9172aa29d5676b9df4t20240303T120000n1709495100n1709496000n1709499600nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240303T120000rnDTEND;TZID=America/Los_Angeles:20240303T130000rnEXDATE;TZID=America/Los_Angeles:20200403T120000rnEXDATE;TZID=America/Los_Angeles:20201103T120000rnEXDATE;TZID=America/Los_Angeles:20210803T120000rnRRULE:FREQ=MONTHLY;BYMONTHDAY=3rnDTSTAMP:20240204T213148ZrnORGANIZER;CN=George Kontridze:mailto:george.kontridze@gmail.comrnUID:quim3bbfqtqrtkffu943q4t57g@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=George Kontridze;X-NUM-GUESTS=0:mailto:george.kontridze@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=Daniel Popescu;X-NUM-GUESTS=0:mailto:danielpops@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=jacob.bakhoum@gmail.com;X-NUM-GUESTS=0:mailto:jacob.bakhoum@gmail.comrnCREATED:20181221T203422ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for DESCRIPTION rn property. Removing entire property:rnLAST-MODIFIED:20240204T213148ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:2rnSTATUS:CONFIRMEDrnSUMMARY:Sigwinch.Computer LunchrnTRANSP:OPAQUErnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnX-EVOLUTION-CALDAV-ETAG:63843145615rnRECURRENCE-ID;TZID=America/Los_Angeles:20240303T120000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT15MrnX-EVOLUTION-ALARM-UID:6e28b896c7f8ba8cb42e8b9172aa29d5676b9df4rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:cdc02998e7354791ff192817ca4d4772d275cfa1rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT2HrnX-EVOLUTION-ALARM-UID:53f53f9d75bc516abbbf980745678685786175edrnEND:VALARMrnBEGIN:VALARMrnACTION:NONErnTRIGGER;VALUE=DATE-TIME:19760401T005545ZrnX-EVOLUTION-ALARM-UID:cca31adaec129e40e723742027591eb2ff9ea5b7rnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacncdc02998e7354791ff192817ca4d4772d275cfa1t20240303T120000n1709494200n1709496000n1709499600nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240303T120000rnDTEND;TZID=America/Los_Angeles:20240303T130000rnEXDATE;TZID=America/Los_Angeles:20200403T120000rnEXDATE;TZID=America/Los_Angeles:20201103T120000rnEXDATE;TZID=America/Los_Angeles:20210803T120000rnRRULE:FREQ=MONTHLY;BYMONTHDAY=3rnDTSTAMP:20240204T213148ZrnORGANIZER;CN=George Kontridze:mailto:george.kontridze@gmail.comrnUID:quim3bbfqtqrtkffu943q4t57g@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=George Kontridze;X-NUM-GUESTS=0:mailto:george.kontridze@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=Daniel Popescu;X-NUM-GUESTS=0:mailto:danielpops@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=jacob.bakhoum@gmail.com;X-NUM-GUESTS=0:mailto:jacob.bakhoum@gmail.comrnCREATED:20181221T203422ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for DESCRIPTION rn property. Removing entire property:rnLAST-MODIFIED:20240204T213148ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:2rnSTATUS:CONFIRMEDrnSUMMARY:Sigwinch.Computer LunchrnTRANSP:OPAQUErnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnX-EVOLUTION-CALDAV-ETAG:63843145615rnRECURRENCE-ID;TZID=America/Los_Angeles:20240303T120000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT15MrnX-EVOLUTION-ALARM-UID:6e28b896c7f8ba8cb42e8b9172aa29d5676b9df4rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:cdc02998e7354791ff192817ca4d4772d275cfa1rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT2HrnX-EVOLUTION-ALARM-UID:53f53f9d75bc516abbbf980745678685786175edrnEND:VALARMrnBEGIN:VALARMrnACTION:NONErnTRIGGER;VALUE=DATE-TIME:19760401T005545ZrnX-EVOLUTION-ALARM-UID:cca31adaec129e40e723742027591eb2ff9ea5b7rnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacn53f53f9d75bc516abbbf980745678685786175edt20240303T120000n1709488800n1709496000n1709499600nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240303T120000rnDTEND;TZID=America/Los_Angeles:20240303T130000rnEXDATE;TZID=America/Los_Angeles:20200403T120000rnEXDATE;TZID=America/Los_Angeles:20201103T120000rnEXDATE;TZID=America/Los_Angeles:20210803T120000rnRRULE:FREQ=MONTHLY;BYMONTHDAY=3rnDTSTAMP:20240204T213148ZrnORGANIZER;CN=George Kontridze:mailto:george.kontridze@gmail.comrnUID:quim3bbfqtqrtkffu943q4t57g@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=George Kontridze;X-NUM-GUESTS=0:mailto:george.kontridze@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=Daniel Popescu;X-NUM-GUESTS=0:mailto:danielpops@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=jacob.bakhoum@gmail.com;X-NUM-GUESTS=0:mailto:jacob.bakhoum@gmail.comrnCREATED:20181221T203422ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for DESCRIPTION rn property. Removing entire property:rnLAST-MODIFIED:20240204T213148ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:2rnSTATUS:CONFIRMEDrnSUMMARY:Sigwinch.Computer LunchrnTRANSP:OPAQUErnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnX-EVOLUTION-CALDAV-ETAG:63843145615rnRECURRENCE-ID;TZID=America/Los_Angeles:20240303T120000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT15MrnX-EVOLUTION-ALARM-UID:6e28b896c7f8ba8cb42e8b9172aa29d5676b9df4rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:cdc02998e7354791ff192817ca4d4772d275cfa1rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT2HrnX-EVOLUTION-ALARM-UID:53f53f9d75bc516abbbf980745678685786175edrnEND:VALARMrnBEGIN:VALARMrnACTION:NONErnTRIGGER;VALUE=DATE-TIME:19760401T005545ZrnX-EVOLUTION-ALARM-UID:cca31adaec129e40e723742027591eb2ff9ea5b7rnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eeanbc8f6465550b48c2ad0dc57805de6fe53c9b26e1t20240302T120000n1709409000n1709409600n1709420400nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240302T120000rnDTEND;TZID=America/Los_Angeles:20240302T150000rnDTSTAMP:20240211T223415ZrnUID:665pnjv7vvq84tj7vbrqtsgs6u@google.comrnCREATED:20240211T223415ZrnDESCRIPTION:Before baby Kiara arrives in mid-April\\, we would love to rn celebrate with family and friends! Please join us on Saturday\\, March rn 2nd\\, from 12:00-3:00pm for lunch and libations. \\n\\nThe guest list is rn viewable to encourage carpooling. For those driving\\, there is street rn parking. \\n\\nPlease RSVP by Saturday\\, February 3rd. \\n\\nThank you! rn \\n\\nSerena & Patrick\\n\\nhttps:rn //www.paperlesspost.com/go/rNX0p1XFmBzWyRU4ZlnlK/pp_g/ff37a4a55c200d842917rn 84903d24e8e83313dee1rnLAST-MODIFIED:20240211T223415ZrnLOCATION:1033 Clarendon Crescent Oakland CA 94610 United StatesrnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Baby Kiara's ShowerrnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63843374055rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:bc8f6465550b48c2ad0dc57805de6fe53c9b26e1rnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacn8d72d4562b18652523961fc2ab915344217e7f41t20240302T120000n1709409000n1709409600n1709413200nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240302T120000rnDTEND;TZID=America/Los_Angeles:20240302T130000rnDTSTAMP:20240211T223419ZrnORGANIZER;CN=unknownorganizer@calendar.google.com:mailto:rn unknownorganizer@calendar.google.comrnUID:7kukuqrfedlm2f9tacm01q69ko3n7iluqjbi0ssmfaee0qr49hqu9ks2pbk1uggt2urgrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnCLASS:PRIVATErnCREATED:20240211T223419ZrnDESCRIPTION:To see detailed information for automatically created events rn like this one\\, use the official Google Calendar app. https:rn //g.co/calendar\\n\\nThis event was created from an email you received in rn Gmail. https:rn //mail.google.com/mail?extsrc=cal&plid=ACUX6DNouWV8l-7mcG2nki7O5ZIVjIYaYgOrn u_6o\\nrnLAST-MODIFIED:20240211T223419ZrnLOCATION:1033 Clarendon Crescent\\, Oakland\\, CA\\, 94610\\, United StatesrnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Baby Kiara's ShowerrnTRANSP:TRANSPARENTrnX-EVOLUTION-CALDAV-ETAG:63843374059rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:8d72d4562b18652523961fc2ab915344217e7f41rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:8b0f3bc94e84594e7d35fa6617a64607b8f88a6arnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacn8b0f3bc94e84594e7d35fa6617a64607b8f88a6at20240302T120000n1709407800n1709409600n1709413200nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240302T120000rnDTEND;TZID=America/Los_Angeles:20240302T130000rnDTSTAMP:20240211T223419ZrnORGANIZER;CN=unknownorganizer@calendar.google.com:mailto:rn unknownorganizer@calendar.google.comrnUID:7kukuqrfedlm2f9tacm01q69ko3n7iluqjbi0ssmfaee0qr49hqu9ks2pbk1uggt2urgrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnCLASS:PRIVATErnCREATED:20240211T223419ZrnDESCRIPTION:To see detailed information for automatically created events rn like this one\\, use the official Google Calendar app. https:rn //g.co/calendar\\n\\nThis event was created from an email you received in rn Gmail. https:rn //mail.google.com/mail?extsrc=cal&plid=ACUX6DNouWV8l-7mcG2nki7O5ZIVjIYaYgOrn u_6o\\nrnLAST-MODIFIED:20240211T223419ZrnLOCATION:1033 Clarendon Crescent\\, Oakland\\, CA\\, 94610\\, United StatesrnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Baby Kiara's ShowerrnTRANSP:TRANSPARENTrnX-EVOLUTION-CALDAV-ETAG:63843374059rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:8d72d4562b18652523961fc2ab915344217e7f41rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:8b0f3bc94e84594e7d35fa6617a64607b8f88a6arnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacnc02665bdc303a9876994d52ca6c4ffca08ec555bt20240301T183000n1709346000n1709346600n1709350200nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240301T183000rnDTEND;TZID=America/Los_Angeles:20240301T193000rnDTSTAMP:20240229T192117ZrnORGANIZER;CN=unknownorganizer@calendar.google.com:mailto:rn unknownorganizer@calendar.google.comrnUID:7kukuqrfedlm2f9tr9e6u8sl395lnogh2ig2ctmcrde62kk0cbmsuqgee52bfohe2ah0rnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnCLASS:PRIVATErnCREATED:20240223T201955ZrnDESCRIPTION:To see detailed information for automatically created events rn like this one\\, use the official Google Calendar app. https:rn //g.co/calendar\\n\\nThis event was created from an email you received in rn Gmail. https:rn //mail.google.com/mail?extsrc=cal&plid=ACUX6DMv0uOaUAyAwiSQKCF5aXbqNA8h3y0rn f2Bc\\nrnLAST-MODIFIED:20240229T192117ZrnLOCATION:Krua Thai - San Francisco\\, 525 Valencia St\\, San Francisco\\, CA rn 94110rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Reservation at Krua Thai - San FranciscornTRANSP:TRANSPARENTrnX-EVOLUTION-CALDAV-ETAG:63844917677rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:c02665bdc303a9876994d52ca6c4ffca08ec555brnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:44601e24810afd8f8a5848ff145dbd0b3a02e89brnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacn44601e24810afd8f8a5848ff145dbd0b3a02e89bt20240301T183000n1709344800n1709346600n1709350200nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240301T183000rnDTEND;TZID=America/Los_Angeles:20240301T193000rnDTSTAMP:20240229T192117ZrnORGANIZER;CN=unknownorganizer@calendar.google.com:mailto:rn unknownorganizer@calendar.google.comrnUID:7kukuqrfedlm2f9tr9e6u8sl395lnogh2ig2ctmcrde62kk0cbmsuqgee52bfohe2ah0rnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnCLASS:PRIVATErnCREATED:20240223T201955ZrnDESCRIPTION:To see detailed information for automatically created events rn like this one\\, use the official Google Calendar app. https:rn //g.co/calendar\\n\\nThis event was created from an email you received in rn Gmail. https:rn //mail.google.com/mail?extsrc=cal&plid=ACUX6DMv0uOaUAyAwiSQKCF5aXbqNA8h3y0rn f2Bc\\nrnLAST-MODIFIED:20240229T192117ZrnLOCATION:Krua Thai - San Francisco\\, 525 Valencia St\\, San Francisco\\, CA rn 94110rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Reservation at Krua Thai - San FranciscornTRANSP:TRANSPARENTrnX-EVOLUTION-CALDAV-ETAG:63844917677rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:c02665bdc303a9876994d52ca6c4ffca08ec555brnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:44601e24810afd8f8a5848ff145dbd0b3a02e89brnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eean255bcdb3fef5f6d47ba2282311a31b13eef21772t20240301T153000n1709335200n1709335800n1709337600nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240301T153000rnDTEND;TZID=America/Los_Angeles:20240301T160000rnDTSTAMP:20240221T183649ZrnORGANIZER;CN=mike@usebasis.co:mailto:mike@usebasis.cornUID:1k0psmgcv9nmq99he87d75j79q@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=mike@usebasis.co;X-NUM-GUESTS=0:mailto:mike@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/gro-nsya-ttkrnRECURRENCE-ID;TZID=America/Los_Angeles:20240301T153000rnCREATED:20240117T011423ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/gro-nsya-ttk\\nOr dial: (US) +1 352-888-6266 PIN: rn 329812051#\\nMore phone numbers: https:rn //tel.meet/gro-nsya-ttk?pin=6722617362667&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240221T183649ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Demos (5 min each)rnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63844223809rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:255bcdb3fef5f6d47ba2282311a31b13eef21772rnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eean809d7395ef3ead23f607663d1ccaf4f739d7768at20240226T140000n1709328000n1709328600n1709329500nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240301T133000rnDTEND;TZID=America/Los_Angeles:20240301T134500rnDTSTAMP:20240229T173127ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:1o2qdl0ofcvolmnbpp2456ca9b@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/ees-fcix-szbrnRECURRENCE-ID;TZID=America/Los_Angeles:20240226T140000rnCREATED:20231201T193148ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/ees-fcix-szb\\nOr dial: (US) +1 347-861-6310 PIN: rn 790404641#\\nMore phone numbers: https:rn //tel.meet/ees-fcix-szb?pin=9747143920112&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240229T173127ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:2rnSTATUS:CONFIRMEDrnSUMMARY:George // Jesse 1:1rnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63844911087rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:809d7395ef3ead23f607663d1ccaf4f739d7768arnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eeane79bc9df4821ab51ac5bcad2e10854ec7667937ft20240301T120000n1709322600n1709323200n1709326800nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240301T120000rnDTEND;TZID=America/Los_Angeles:20240301T130000rnDTSTAMP:20240201T171003ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:0qv2g51uk9qooa6p1p9m2kjsu9_R20231016T190000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/pow-unuv-btfrnRECURRENCE-ID;TZID=America/Los_Angeles:20240301T120000rnCREATED:20230620T234404ZrnDESCRIPTION:As the team grows\\, it would be nice eat together as a team\\, rn so we're proposing twice-weekly lunches.<br><span><br></span><br><span>If rn you bring lunch\\, start warming it up a little earlier so you're ready at rn 12. If you're eating out\\, we'll cover your DoorDash up to $25 (don't rn forget -- you can schedule your order ahead of time)</span>\\n\\n-::~:~::~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:rn ~::-\\nJoin with Google Meet: https://meet.google.com/pow-unuv-btf\\nOr rn dial: (US) +1 513-760-6079 PIN: 535859198#\\nMore phone numbers: https:rn //tel.meet/pow-unuv-btf?pin=9484659480677&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240201T171003ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Team LunchrnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63844223802rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:e79bc9df4821ab51ac5bcad2e10854ec7667937frnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eeanf729bd41840716311acfa51c34c68af4c68d25b9t20240301T100000n1709315400n1709316000n1709319600nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240301T100000rnDTEND;TZID=America/Los_Angeles:20240301T110000rnDTSTAMP:20240301T180704ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:cjor6rn0v3te499kon6didavua_R20231204T180000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=mike@usebasis.co;X-NUM-GUESTS=0:mailto:mike@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=DECLINED;rn CN=dante@usebasis.co;X-NUM-GUESTS=0;X-RESPONSE-COMMENT=\"I am taking time rn off to enjoy the snow. I&#39\\;ll have my laptop with me if anything rn critical comes up.\":mailto:dante@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=kyle@usebasis.co;X-NUM-GUESTS=0:mailto:kyle@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=troute@usebasis.co;X-NUM-GUESTS=0:mailto:troute@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=DECLINED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/mns-oimq-wkgrnRECURRENCE-ID;TZID=America/Los_Angeles:20240301T100000rnCREATED:20221104T045344ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/mns-oimq-wkg\\nOr dial: (US) +1 661-772-9059 PIN: rn 652845310#\\nMore phone numbers: https:rn //tel.meet/mns-oimq-wkg?pin=8044670134175&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240301T180704ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:3rnSTATUS:CONFIRMEDrnSUMMARY:Weekly standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63844999624rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:f729bd41840716311acfa51c34c68af4c68d25b9rnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eeanfd654106cdf26c8880cda4190b5b8d09f26b0f47t20240229T130000n1709229000n1709229600n1709230500nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240229T100000rnDTEND;TZID=America/Los_Angeles:20240229T101500rnDTSTAMP:20240229T162215ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernRECURRENCE-ID;TZID=America/Los_Angeles:20240229T130000rnCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240229T162215ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:2rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63844906935rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:fd654106cdf26c8880cda4190b5b8d09f26b0f47rnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eean08ed1c9a9db8352560bb2705e10c49b3b0985909t20240228T130000n1709139000n1709139600n1709140500nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240228T090000rnDTEND;TZID=America/Los_Angeles:20240228T091500rnDTSTAMP:20240227T210624ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernRECURRENCE-ID;TZID=America/Los_Angeles:20240228T130000rnCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240227T210624ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:2rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63844751184rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:08ed1c9a9db8352560bb2705e10c49b3b0985909rnEND:VALARMrnEND:VEVENTrn" ];
    };

    "org/gnome/file-roller/dialogs/extract" = {
      recreate-folders = true;
      skip-newer = false;
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
      show-dependencies = false;
      show-whose-processes = "user";
      window-state = mkTuple [ 2510 1661 26 23 ];
    };

    "org/gnome/gnome-system-monitor/disktreenew" = {
      col-6-visible = true;
      col-6-width = 0;
    };

    "org/gnome/gnome-system-monitor/proctree" = {
      col-0-visible = true;
      col-0-width = 877;
      columns-order = [ 0 1 2 3 4 6 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 ];
      sort-col = 8;
      sort-order = 0;
    };

    "org/gnome/maps" = {
      last-viewed-location = [ 37.7780215 (-122.4164555) ];
      map-type = "MapsStreetSource";
      rotation = 0.0;
      transportation-type = "transit";
      window-maximized = true;
      window-size = [ 2980 1977 ];
      zoom-level = 15;
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
      default-folder-viewer = "list-view";
      migrated-gtk-settings = true;
      search-filter-time-type = "last_modified";
      show-create-link = true;
      show-delete-permanently = true;
    };

    "org/gnome/nautilus/window-state" = {
      initial-size = mkTuple [ 1922 1736 ];
      maximized = false;
    };

    "org/gnome/nm-applet/eap/0c74c377-2692-4c9d-99e0-09fcc369a740" = {
      ignore-ca-cert = false;
      ignore-phase2-ca-cert = false;
    };

    "org/gnome/nm-applet/eap/319ce2fe-1f8f-43a8-ad91-311d187d831a" = {
      ignore-ca-cert = false;
      ignore-phase2-ca-cert = false;
    };

    "org/gnome/nm-applet/eap/8b0aab25-ce46-48b1-a5f3-749fecb74a10" = {
      ignore-ca-cert = false;
      ignore-phase2-ca-cert = false;
    };

    "org/gnome/nm-applet/eap/970a91ab-12d7-43b9-ad15-c487979916d2" = {
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

    "org/gnome/portal/filechooser/io/gitlab/adhami3310/Converter" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/io/podman_desktop/PodmanDesktop" = {
      last-folder-path = "/home/george/src/git.usebasis.co/basis/basis/tests/e2e";
    };

    "org/gnome/portal/filechooser/org/gnome/Epiphany" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/org/gnome/Settings" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/org/gnome/Epiphany" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/org/gnome/Settings" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/slack" = {
      last-folder-path = "/home/george/Pictures/Screenshots";
    };

    "org/gnome/settings-daemon/plugins/color" = {
      night-light-enabled = false;
      night-light-last-coordinates = mkTuple [ 34.08880933009937 (-118.4117) ];
    };

    "org/gnome/settings-daemon/plugins/media-keys" = {
      next = [ "Cancel" ];
      play = [ "Messenger" ];
      previous = [ "Go" ];
    };

    "org/gnome/settings-daemon/plugins/power" = {
      sleep-inactive-ac-type = "nothing";
    };

    "org/gnome/shell" = {
      app-picker-layout = "[{'org.gnome.Geary.desktop': <{'position': <0>}>, 'org.gnome.Contacts.desktop': <{'position': <1>}>, 'org.gnome.Weather.desktop': <{'position': <2>}>, 'org.gnome.clocks.desktop': <{'position': <3>}>, 'org.gnome.Maps.desktop': <{'position': <4>}>, 'org.gnome.Snapshot.desktop': <{'position': <5>}>, 'dev.vlinkz.NixosConfEditor.desktop': <{'position': <6>}>, 'org.gnome.Totem.desktop': <{'position': <7>}>, 'org.gnome.Calculator.desktop': <{'position': <8>}>, 'com.github.qarmin.czkawka.desktop': <{'position': <9>}>, 'simple-scan.desktop': <{'position': <10>}>, 'element-desktop.desktop': <{'position': <11>}>, 'gnome-system-monitor.desktop': <{'position': <12>}>, 'org.gnome.Extensions.desktop': <{'position': <13>}>, 'Helix.desktop': <{'position': <14>}>, 'Utilities': <{'position': <15>}>, 'org.gnome.Loupe.desktop': <{'position': <16>}>, 'yelp.desktop': <{'position': <17>}>, 'LocalSend.desktop': <{'position': <18>}>, 'org.gnome.Music.desktop': <{'position': <19>}>, 'nvim.desktop': <{'position': <20>}>, 'nixos-manual.desktop': <{'position': <21>}>, 'obsidian.desktop': <{'position': <22>}>, 'brave-lgnggepjiihbfdbedefdhcffnmhcahbm-Default.desktop': <{'position': <23>}>}, {'signal-desktop-beta.desktop': <{'position': <0>}>, 'slack.desktop': <{'position': <1>}>, 'dev.vlinkz.NixSoftwareCenter.desktop': <{'position': <2>}>, 'org.gnome.TextEditor.desktop': <{'position': <3>}>, 'org.gnome.Tour.desktop': <{'position': <4>}>, 'app.drey.Warp.desktop': <{'position': <5>}>, 'xterm.desktop': <{'position': <6>}>}]";
      command-history = [ "r" "replace" "restart" "help" "open" "obsidian://open?vault=basis.obsidian&file=00-09%20Meta%20%26%20personal%2F01%20Personal%2F01.01%20.plan%2Fgeorge%2FOnboarding%20Notes" "reload" "reload extensions" ];
      disable-user-extensions = false;
      disabled-extensions = [ "native-window-placement@gnome-shell-extensions.gcampax.github.com" "window-list@gnome-shell-extensions.gcampax.github.com" "workspace-indicator@gnome-shell-extensions.gcampax.github.com" "light-style@gnome-shell-extensions.gcampax.github.com" ];
      enabled-extensions = [ "apps-menu@gnome-shell-extensions.gcampax.github.com" "places-menu@gnome-shell-extensions.gcampax.github.com" "drive-menu@gnome-shell-extensions.gcampax.github.com" "screenshot-window-sizer@gnome-shell-extensions.gcampax.github.com" "display-brightness-ddcutil@themightydeity.github.com" ];
      favorite-apps = [ "org.gnome.Nautilus.desktop" "org.gnome.Calendar.desktop" "brave-browser.desktop" "slack.desktop" "Alacritty.desktop" "obsidian.desktop" "beekeeper-studio.desktop" "org.gnome.Settings.desktop" ];
      last-selected-power-profile = "power-saver";
      welcome-dialog-last-shown-version = "44.2";
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

    "org/gnome/shell/weather" = {
      automatic-location = true;
      locations = "@av []";
    };

    "org/gnome/shell/world-clocks" = {
      locations = [ ];
    };

    "org/gnome/software" = {
      check-timestamp = mkInt64 1709826744;
      first-run = false;
      flatpak-purge-timestamp = mkInt64 1709857233;
      install-timestamp = mkInt64 1706937298;
      security-timestamp = mkInt64 1707230877207489;
      update-notification-timestamp = mkInt64 1709475465;
    };

    "org/gnome/system/location" = {
      enabled = true;
    };

    "org/gnome/tweaks" = {
      show-extensions-notice = false;
    };

    "org/gtk/gtk4/settings/file-chooser" = {
      date-format = "regular";
      location-mode = "path-bar";
      show-hidden = false;
      show-size-column = true;
      show-type-column = true;
      sidebar-width = 272;
      sort-column = "modified";
      sort-directories-first = true;
      sort-order = "ascending";
      type-format = "category";
      view-type = "grid";
      window-size = mkTuple [ 1376 922 ];
    };

    "org/gtk/settings/file-chooser" = {
      clock-format = "12h";
      date-format = "regular";
      location-mode = "path-bar";
      show-hidden = false;
      show-size-column = true;
      show-type-column = true;
      sidebar-width = 157;
      sort-column = "type";
      sort-directories-first = false;
      sort-order = "ascending";
      type-format = "category";
      window-position = mkTuple [ 1320 2301 ];
      window-size = mkTuple [ 1203 902 ];
    };

    "org/virt-manager/virt-manager" = {
      manager-window-height = 550;
      manager-window-width = 550;
    };

    "org/virt-manager/virt-manager/connections" = {
      autoconnect = [ "qemu:///system" ];
      uris = [ "qemu:///system" ];
    };

    "org/virt-manager/virt-manager/vmlist-fields" = {
      disk-usage = false;
      network-traffic = false;
    };

  };
}
