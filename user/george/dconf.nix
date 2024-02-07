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
      week-view-zoom-level = 1.0;
      window-maximized = true;
      window-size = mkTuple [ 360 600 ];
    };

    "org/gnome/clocks/state/window" = {
      maximized = false;
      panel-id = "world";
      size = mkTuple [ 870 690 ];
    };

    "org/gnome/control-center" = {
      last-panel = "display";
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
      application-children = [ "gnome-network-panel" "gnome-power-panel" "org-gnome-calendar" "org-gnome-characters" "org-gnome-console" "org-gnome-epiphany" "org-gnome-settings" "signal-desktop-beta" "slack" "org-gnome-tweaks" "org-gnome-evolution-alarm-notify" "gnome-system-monitor" "brave-browser" "org-gnome-software" "brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-default" "signal-desktop" "com-spotify-client" "code" ];
      show-banners = false;
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
      reminders-past = [ "128ff86471c7b2fc9e3ace8e82ac44a25f8d0b9fnfb75150c0eb687cba6ed5c0673cce250430433d5t20240205T120000n1707162600n1707163200n1707166800nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240205T120000rnDTEND;TZID=America/Los_Angeles:20240205T130000rnRRULE:FREQ=WEEKLY;BYDAY=MO,FR;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231124T120000rnEXDATE;TZID=America/Los_Angeles:20231225T120000rnEXDATE;TZID=America/Los_Angeles:20231229T120000rnEXDATE;TZID=America/Los_Angeles:20240101T120000rnEXDATE;TZID=America/Los_Angeles:20240105T120000rnEXDATE;TZID=America/Los_Angeles:20240115T120000rnDTSTAMP:20231127T172749ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:0qv2g51uk9qooa6p1p9m2kjsu9_R20231016T190000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/pow-unuv-btfrnCREATED:20230620T234404ZrnDESCRIPTION:As the team grows\\, it would be nice eat together as a team\\, rn so we're proposing twice-weekly lunches.<br><span><br></span><br><span>If rn you bring lunch\\, start warming it up a little earlier so you're ready at rn 12. If you're eating out\\, we'll cover your DoorDash up to $25 (don't rn forget -- you can schedule your order ahead of time)</span>\\n\\n-::~:~::~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:rn ~::-\\nJoin with Google Meet: https://meet.google.com/pow-unuv-btf\\nOr rn dial: (US) +1 513-760-6079 PIN: 535859198#\\nMore phone numbers: https:rn //tel.meet/pow-unuv-btf?pin=9484659480677&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20231127T172749ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Team LunchrnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63842490603rnRECURRENCE-ID;TZID=America/Los_Angeles:20240205T120000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:fb75150c0eb687cba6ed5c0673cce250430433d5rnEND:VALARMrnEND:VEVENTrn" "128ff86471c7b2fc9e3ace8e82ac44a25f8d0b9fn723d417833b89309f60e9f9eda7861648475b690t20240202T153000n1706916000n1706916600n1706918400nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240202T153000rnDTEND;TZID=America/Los_Angeles:20240202T160000rnRRULE:FREQ=WEEKLY;BYDAY=FRrnDTSTAMP:20240201T171013ZrnORGANIZER;CN=mike@usebasis.co:mailto:mike@usebasis.cornUID:1k0psmgcv9nmq99he87d75j79q@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=mike@usebasis.co;X-NUM-GUESTS=0:mailto:mike@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/gro-nsya-ttkrnCREATED:20240117T011423ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/gro-nsya-ttk\\nOr dial: (US) +1 352-888-6266 PIN: rn 329812051#\\nMore phone numbers: https:rn //tel.meet/gro-nsya-ttk?pin=6722617362667&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240201T171013ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Demos (5 min each)rnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63842490613rnRECURRENCE-ID;TZID=America/Los_Angeles:20240202T153000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:723d417833b89309f60e9f9eda7861648475b690rnEND:VALARMrnEND:VEVENTrn" "128ff86471c7b2fc9e3ace8e82ac44a25f8d0b9fnfb75150c0eb687cba6ed5c0673cce250430433d5t20240202T120000n1706903400n1706904000n1706907600nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240202T120000rnDTEND;TZID=America/Los_Angeles:20240202T130000rnRRULE:FREQ=WEEKLY;BYDAY=MO,FR;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231124T120000rnEXDATE;TZID=America/Los_Angeles:20231225T120000rnEXDATE;TZID=America/Los_Angeles:20231229T120000rnEXDATE;TZID=America/Los_Angeles:20240101T120000rnEXDATE;TZID=America/Los_Angeles:20240105T120000rnEXDATE;TZID=America/Los_Angeles:20240115T120000rnDTSTAMP:20231127T172749ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:0qv2g51uk9qooa6p1p9m2kjsu9_R20231016T190000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/pow-unuv-btfrnCREATED:20230620T234404ZrnDESCRIPTION:As the team grows\\, it would be nice eat together as a team\\, rn so we're proposing twice-weekly lunches.<br><span><br></span><br><span>If rn you bring lunch\\, start warming it up a little earlier so you're ready at rn 12. If you're eating out\\, we'll cover your DoorDash up to $25 (don't rn forget -- you can schedule your order ahead of time)</span>\\n\\n-::~:~::~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:rn ~::-\\nJoin with Google Meet: https://meet.google.com/pow-unuv-btf\\nOr rn dial: (US) +1 513-760-6079 PIN: 535859198#\\nMore phone numbers: https:rn //tel.meet/pow-unuv-btf?pin=9484659480677&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20231127T172749ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Team LunchrnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63842490603rnRECURRENCE-ID;TZID=America/Los_Angeles:20240202T120000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:fb75150c0eb687cba6ed5c0673cce250430433d5rnEND:VALARMrnEND:VEVENTrn" "128ff86471c7b2fc9e3ace8e82ac44a25f8d0b9fn63070b6a1e42b00d3b9a6548ca2b963a93f49e38t20240201T130000n1706820600n1706821200n1706822100nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240201T130000rnDTEND;TZID=America/Los_Angeles:20240201T131500rnRRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231226T130000rnEXDATE;TZID=America/Los_Angeles:20231227T130000rnEXDATE;TZID=America/Los_Angeles:20231228T130000rnEXDATE;TZID=America/Los_Angeles:20240102T130000rnEXDATE;TZID=America/Los_Angeles:20240103T130000rnEXDATE;TZID=America/Los_Angeles:20240104T130000rnDTSTAMP:20231127T190932ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20231127T190932ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63842490603rnRECURRENCE-ID;TZID=America/Los_Angeles:20240201T130000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:63070b6a1e42b00d3b9a6548ca2b963a93f49e38rnEND:VALARMrnEND:VEVENTrn" "aa3c5774c6d5b6635ee14107e95307ffba6cddc0n7645c06fbee471239d29d2afebf61005e51f5705t20240201T133000n1706811600n1706812200n1706814000nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240201T103000rnDTEND;TZID=America/Los_Angeles:20240201T110000rnDTSTAMP:20240201T141329ZrnORGANIZER;CN=xingyang105@gmail.com:mailto:xingyang105@gmail.comrnUID:ec7mm9u2e7d8kujk2grqh5gpr2@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnX-GOOGLE-CONFERENCE:https://meet.google.com/zjq-ekmd-anprnRECURRENCE-ID;TZID=America/New_York:20240201T133000rnCREATED:20221013T184331ZrnDESCRIPTION:COSI standup meeting.<br><br>Meeting notes and agenda:<br><a rn href=\"https:rn //docs.google.com/document/d/1ZgEyyq3kk9BXP0bJL1Ki0JeOLILilGWZ4Vu2U4gs_QY/rn edit\" class=\"pastedDriveLink-0\">https:rn //docs.google.com/document/d/1ZgEyyq3kk9BXP0bJL1Ki0JeOLILilGWZ4Vu2U4gs_QY/rn edit</a><br><br>KEP:\160<a href=\"https:rn //github.com/kubernetes/enhancements/tree/master/keps/sig-storage/1979-objrn ect-storage-support\">https:rn //github.com/kubernetes/enhancements/tree/master/keps/sig-storage/1979-objrn ect-storage-support</a><br><br>Repos:\160<a href=\"https:rn //github.com/kubernetes-sigs?q=object+storage&type=all&language=&sort=\">htrn tps://github.com/kubernetes-sigs?q=object+storage&amp\\;type=all&amp\\;rn language=&amp\\;sort=</a><br><br><p>Zoom Link (regular): <a href=\"https:rn //zoom.us/j/614261834?pwd=Sk1USmtjR2t0MUdjTGVZeVVEV1BPQT09\"><u>https:rn //zoom.us/j/614261834?pwd=Sk1USmtjR2t0MUdjTGVZeVVEV1BPQT09</u></a>\160\160</p>rn <p>Zoom Link (web client): <a href=\"https:rn //zoom.us/wc/join/614261834?pwd=Sk1USmtjR2t0MUdjTGVZeVVEV1BPQT09\"><u>httpsrn ://zoom.us/wc/join/614261834?pwd=Sk1USmtjR2t0MUdjTGVZeVVEV1BPQT09</u></a>rn \160\160</p><p>Meeting requires a passcode. If asked\\, enter 77777</p>\\n\\n-::rn ~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/zjq-ekmd-anp\\n\\nLearn more about Meet at: https:rn //support.google.com/a/users/answer/9282720\\n\\nPlease do not edit this rn section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240201T141329ZrnLOCATION:https:rn //zoom.us/j/614261834?pwd=Sk1USmtjR2t0MUdjTGVZeVVEV1BPQT09rnSEQUENCE:4rnSTATUS:CONFIRMEDrnSUMMARY:COSI Standup MeetingrnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63842480009rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:7645c06fbee471239d29d2afebf61005e51f5705rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:3cd9c656660fa90dfb5e168fc08b73b2f5da24dfrnEND:VALARMrnEND:VEVENTrn" "aa3c5774c6d5b6635ee14107e95307ffba6cddc0n3cd9c656660fa90dfb5e168fc08b73b2f5da24dft20240201T133000n1706810400n1706812200n1706814000nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240201T103000rnDTEND;TZID=America/Los_Angeles:20240201T110000rnDTSTAMP:20240201T141329ZrnORGANIZER;CN=xingyang105@gmail.com:mailto:xingyang105@gmail.comrnUID:ec7mm9u2e7d8kujk2grqh5gpr2@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnX-GOOGLE-CONFERENCE:https://meet.google.com/zjq-ekmd-anprnRECURRENCE-ID;TZID=America/New_York:20240201T133000rnCREATED:20221013T184331ZrnDESCRIPTION:COSI standup meeting.<br><br>Meeting notes and agenda:<br><a rn href=\"https:rn //docs.google.com/document/d/1ZgEyyq3kk9BXP0bJL1Ki0JeOLILilGWZ4Vu2U4gs_QY/rn edit\" class=\"pastedDriveLink-0\">https:rn //docs.google.com/document/d/1ZgEyyq3kk9BXP0bJL1Ki0JeOLILilGWZ4Vu2U4gs_QY/rn edit</a><br><br>KEP:\160<a href=\"https:rn //github.com/kubernetes/enhancements/tree/master/keps/sig-storage/1979-objrn ect-storage-support\">https:rn //github.com/kubernetes/enhancements/tree/master/keps/sig-storage/1979-objrn ect-storage-support</a><br><br>Repos:\160<a href=\"https:rn //github.com/kubernetes-sigs?q=object+storage&type=all&language=&sort=\">htrn tps://github.com/kubernetes-sigs?q=object+storage&amp\\;type=all&amp\\;rn language=&amp\\;sort=</a><br><br><p>Zoom Link (regular): <a href=\"https:rn //zoom.us/j/614261834?pwd=Sk1USmtjR2t0MUdjTGVZeVVEV1BPQT09\"><u>https:rn //zoom.us/j/614261834?pwd=Sk1USmtjR2t0MUdjTGVZeVVEV1BPQT09</u></a>\160\160</p>rn <p>Zoom Link (web client): <a href=\"https:rn //zoom.us/wc/join/614261834?pwd=Sk1USmtjR2t0MUdjTGVZeVVEV1BPQT09\"><u>httpsrn ://zoom.us/wc/join/614261834?pwd=Sk1USmtjR2t0MUdjTGVZeVVEV1BPQT09</u></a>rn \160\160</p><p>Meeting requires a passcode. If asked\\, enter 77777</p>\\n\\n-::rn ~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/zjq-ekmd-anp\\n\\nLearn more about Meet at: https:rn //support.google.com/a/users/answer/9282720\\n\\nPlease do not edit this rn section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240201T141329ZrnLOCATION:https:rn //zoom.us/j/614261834?pwd=Sk1USmtjR2t0MUdjTGVZeVVEV1BPQT09rnSEQUENCE:4rnSTATUS:CONFIRMEDrnSUMMARY:COSI Standup MeetingrnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63842480009rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:7645c06fbee471239d29d2afebf61005e51f5705rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:3cd9c656660fa90dfb5e168fc08b73b2f5da24dfrnEND:VALARMrnEND:VEVENTrn" "128ff86471c7b2fc9e3ace8e82ac44a25f8d0b9fnf6a3eb144960f8da127c6912f94bf0d28f4135fat20240131T130000n1706734200n1706734800n1706735700nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240131T130000rnDTEND;TZID=America/Los_Angeles:20240131T131500rnRRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231226T130000rnEXDATE;TZID=America/Los_Angeles:20231227T130000rnEXDATE;TZID=America/Los_Angeles:20231228T130000rnEXDATE;TZID=America/Los_Angeles:20240102T130000rnEXDATE;TZID=America/Los_Angeles:20240103T130000rnEXDATE;TZID=America/Los_Angeles:20240104T130000rnDTSTAMP:20231127T190932ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20231127T190932ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63841130969rnRECURRENCE-ID;TZID=America/Los_Angeles:20240131T130000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:f6a3eb144960f8da127c6912f94bf0d28f4135farnEND:VALARMrnEND:VEVENTrn" "128ff86471c7b2fc9e3ace8e82ac44a25f8d0b9fn0235a5a5ca5035208bbce2a84a40c7f4aaace4bct20240130T133000n1706649600n1706650200n1706652000nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240130T133000rnDTEND;TZID=America/Los_Angeles:20240130T140000rnDTSTAMP:20240130T211737ZrnORGANIZER;CN=george@usebasis.co:mailto:george@usebasis.cornUID:435t3fvn1vkaaocu18rq3j0nig@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=kyle@usebasis.co;X-NUM-GUESTS=0:mailto:kyle@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=troute@usebasis.co;X-NUM-GUESTS=0:mailto:troute@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=dante@usebasis.co;X-NUM-GUESTS=0:mailto:dante@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=mike@usebasis.co;X-NUM-GUESTS=0:mailto:mike@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/uud-sgkq-yhtrnCREATED:20240130T210827ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/uud-sgkq-yht\\nOr dial: (US) +1 516-268-6866 PIN: rn 992140193#\\nMore phone numbers: https:rn //tel.meet/uud-sgkq-yht?pin=3609283476861&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240130T211737ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Decoupling Syncs from Authorization FlowsrnTRANSP:OPAQUErnATTACH;FILENAME=Decoupling Syncs from Authorization Flows;rn FMTTYPE=application/vnd.google-apps.document:https:rn //drive.google.com/open?id=1PTfQiq5ZN-IQJXBsjLIINEw9EzeQOxXvFNj3g47NO1M&aurn thuser=0rnX-EVOLUTION-CALDAV-ETAG:63842332657rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:0235a5a5ca5035208bbce2a84a40c7f4aaace4bcrnEND:VALARMrnEND:VEVENTrn" "128ff86471c7b2fc9e3ace8e82ac44a25f8d0b9fnf6a3eb144960f8da127c6912f94bf0d28f4135fat20240130T130000n1706647800n1706648400n1706649300nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240130T130000rnDTEND;TZID=America/Los_Angeles:20240130T131500rnRRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231226T130000rnEXDATE;TZID=America/Los_Angeles:20231227T130000rnEXDATE;TZID=America/Los_Angeles:20231228T130000rnEXDATE;TZID=America/Los_Angeles:20240102T130000rnEXDATE;TZID=America/Los_Angeles:20240103T130000rnEXDATE;TZID=America/Los_Angeles:20240104T130000rnDTSTAMP:20231127T190932ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20231127T190932ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63841130969rnRECURRENCE-ID;TZID=America/Los_Angeles:20240130T130000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:f6a3eb144960f8da127c6912f94bf0d28f4135farnEND:VALARMrnEND:VEVENTrn" "aa3c5774c6d5b6635ee14107e95307ffba6cddc0n5e499c08576fbc042bff9a7a6a9f06267f0a3b26t20240129T140000n1706563800n1706565600n1706566500nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240129T140000rnDTEND;TZID=America/Los_Angeles:20240129T141500rnRRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20240101T140000rnDTSTAMP:20240115T180958ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:1o2qdl0ofcvolmnbpp2456ca9b@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/ees-fcix-szbrnCREATED:20231201T193148ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/ees-fcix-szb\\nOr dial: (US) +1 347-861-6310 PIN: rn 790404641#\\nMore phone numbers: https:rn //tel.meet/ees-fcix-szb?pin=9747143920112&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240115T180958ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:George // Jesse 1:1rnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63841212029rnRECURRENCE-ID;TZID=America/Los_Angeles:20240129T140000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:42920e76d2257e7f91bd8d0e5a6b5b4f35e2b30crnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:5e499c08576fbc042bff9a7a6a9f06267f0a3b26rnEND:VALARMrnEND:VEVENTrn" "128ff86471c7b2fc9e3ace8e82ac44a25f8d0b9fne4e69ca86945de2cf1938afbdf003395d0b342f6t20240129T120000n1706557800n1706558400n1706562000nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240129T120000rnDTEND;TZID=America/Los_Angeles:20240129T130000rnDTSTAMP:20240129T173547ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:0qv2g51uk9qooa6p1p9m2kjsu9_R20231016T190000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/pow-unuv-btfrnRECURRENCE-ID;TZID=America/Los_Angeles:20240129T120000rnCREATED:20230620T234404ZrnDESCRIPTION:As the team grows\\, it would be nice eat together as a team\\, rn so we're proposing twice-weekly lunches.<br><span><br></span><br><span>If rn you bring lunch\\, start warming it up a little earlier so you're ready at rn 12. If you're eating out\\, we'll cover your DoorDash up to $25 (don't rn forget -- you can schedule your order ahead of time)</span>\\n\\n-::~:~::~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:rn ~::-\\nJoin with Google Meet: https://meet.google.com/pow-unuv-btf\\nOr rn dial: (US) +1 513-760-6079 PIN: 535859198#\\nMore phone numbers: https:rn //tel.meet/pow-unuv-btf?pin=9484659480677&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240129T173547ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Team LunchrnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63842232947rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:e4e69ca86945de2cf1938afbdf003395d0b342f6rnEND:VALARMrnEND:VEVENTrn" ];
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
      maximized = true;
      network-total-in-bits = false;
      show-dependencies = false;
      show-whose-processes = "user";
      window-state = mkTuple [ 1920 1168 0 0 ];
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
      initial-size = mkTuple [ 1614 984 ];
      maximized = false;
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
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/settings-daemon/plugins/color" = {
      night-light-enabled = false;
      night-light-last-coordinates = mkTuple [ 37.78286910647302 (-122.406) ];
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
      favorite-apps = [ "org.gnome.Nautilus.desktop" "org.gnome.Calendar.desktop" "brave-browser.desktop" "slack.desktop" "obsidian.desktop" "beekeeper-studio.desktop" "Alacritty.desktop" "org.gnome.Settings.desktop" ];
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
      check-timestamp = mkInt64 1707230876;
      first-run = false;
      flatpak-purge-timestamp = mkInt64 1707178891;
      install-timestamp = mkInt64 1706937298;
      security-timestamp = mkInt64 1707230877207489;
      update-notification-timestamp = mkInt64 1706937593;
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
      view-type = "list";
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
      window-position = mkTuple [ 0 32 ];
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
