# Generated via dconf2nix: https://github.com/gvolpe/dconf2nix
{ lib, ... }:

with lib.hm.gvariant;

{
  dconf.settings = {
    "apps/seahorse/listing" = {
      keyrings-selected = [ "gnupg://" ];
    };

    "apps/seahorse/windows/key-manager" = {
      height = 476;
      width = 600;
    };

    "ca/desrt/dconf-editor" = {
      saved-pathbar-path = "/org/gnome/desktop/wm/keybindings/";
      saved-view = "/org/gnome/desktop/wm/keybindings/";
      window-height = 1931;
      window-is-maximized = false;
      window-width = 1094;
    };

    "desktop/ibus/panel/emoji" = {
      unicode-hotkey = [ "<Control><Shift>p" ];
    };

    "org/gnome/Connections" = {
      first-run = false;
    };

    "org/gnome/Console" = {
      last-window-maximised = false;
      last-window-size = mkTuple [ 1720 1156 ];
    };

    "org/gnome/Geary" = {
      compose-as-html = true;
      formatting-toolbar-visible = false;
      images-trusted-domains = [ "accounts.google.com" "yourstudio.com" "doordash.com" "findyourzo.com" "usebasis.co" ];
      migrated-config = true;
      window-height = 875;
      window-maximize = false;
      window-width = 1334;
    };

    "org/gnome/Weather" = {
      locations = [ (mkVariant [ (mkUint32 2) (mkVariant [ "San Francisco" "KOAK" false [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ] [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ] ]) ]) ];
      window-height = 586;
      window-maximized = false;
      window-width = 1211;
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
      week-view-zoom-level = 1.0;
      window-maximized = true;
      window-size = mkTuple [ 1519 1012 ];
    };

    "org/gnome/clocks" = {
      world-clocks = [{
        location = mkVariant [ (mkUint32 2) (mkVariant [ "San Francisco" "KOAK" true [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ] [ (mkTuple [ 0.659296885757089 (-2.136621860115334) ]) ] ]) ];
      }
        {
          location = mkVariant [ (mkUint32 2) (mkVariant [ "New York" "KNYC" true [ (mkTuple [ 0.7118034407872564 (-1.2909618758762367) ]) ] [ (mkTuple [ 0.7105980465926592 (-1.2916478949920254) ]) ] ]) ];
        }
        {
          location = mkVariant [ (mkUint32 2) (mkVariant [ "Berlin" "EDDT" true [ (mkTuple [ 0.9174614159494501 0.23241968454167572 ]) ] [ (mkTuple [ 0.916588751323453 0.23387411976724018 ]) ] ]) ];
        }
        {
          location = mkVariant [ (mkUint32 2) (mkVariant [ "Tbilisi" "UGTB" true [ (mkTuple [ 0.727264160713368 0.7846079132187302 ]) ] [ (mkTuple [ 0.7280931921080653 0.7816166108670297 ]) ] ]) ];
        }];
    };

    "org/gnome/clocks/state/window" = {
      maximized = false;
      panel-id = "world";
      size = mkTuple [ 870 690 ];
    };

    "org/gnome/control-center" = {
      last-panel = "background";
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
      cursor-theme = "catppuccin-frappe-mauve-cursors";
      font-antialiasing = "grayscale";
      font-hinting = "slight";
      gtk-theme = "catppuccin-frappe-mauve-standard+normal";
      icon-theme = "Papirus-Dark";
      monospace-font-name = "Hack Nerd Font Mono 11";
      overlay-scrolling = true;
      show-battery-percentage = true;
      text-scaling-factor = 1.0;
      toolkit-accessibility = false;
    };

    "org/gnome/desktop/notifications" = {
      application-children = [ "org-gnome-software" "gnome-network-panel" "org-gnome-calendar" "gnome-system-monitor" "gnome-power-panel" "slack" "org-gnome-evolution-alarm-notify" "org-gnome-settings" "org-gnome-nautilus" "brave-browser" "brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-default" "org-gnome-baobab" "org-gnome-evince" "org-gnome-geary" "dbeaver" "alacritty" "io-podman-desktop-podmandesktop" "1password" "org-gimp-gimp" "org-gnome-characters" "code" "brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-profile-2" ];
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

    "org/gnome/epiphany" = {
      ask-for-default = false;
    };

    "org/gnome/epiphany/state" = {
      is-maximized = false;
      window-size = mkTuple [ 1024 768 ];
    };

    "org/gnome/evince/default" = {
      continuous = false;
      dual-page = true;
      dual-page-odd-left = false;
      enable-spellchecking = true;
      fullscreen = false;
      inverted-colors = false;
      show-sidebar = true;
      sidebar-page = "thumbnails";
      sidebar-size = 246;
      sizing-mode = "free";
      window-ratio = mkTuple [ 5.027777777777778 2.388888888888889 ];
      zoom = 0.8333333333333333;
    };

    "org/gnome/evolution-data-server" = {
      migrated = true;
    };

    "org/gnome/evolution-data-server/calendar" = {
      notify-window-height = 483;
      notify-window-paned-position = 63;
      notify-window-width = 409;
      notify-window-x = 103;
      notify-window-y = 103;
      reminders-past = [ "18cb049f6671c3ed289a9c71e80c0de52da3688end616492ad29601d566d901db330cd514c2ca1a5ft20240624T100000n1719247800n1719248400n1719252000nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240624T100000rnDTEND;TZID=America/Los_Angeles:20240624T110000rnRRULE:FREQ=WEEKLY;BYDAY=MO,FR;WKST=SUrnDTSTAMP:20240617T170120ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:cjor6rn0v3te499kon6didavua_R20240308T180000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/mns-oimq-wkgrnCREATED:20221104T045344ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/mns-oimq-wkg\\nOr dial: (US) +1 661-772-9059 PIN: rn 652845310#\\nMore phone numbers: https:rn //tel.meet/mns-oimq-wkg?pin=8044670134175&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240617T170120ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:3rnSTATUS:CONFIRMEDrnSUMMARY:Weekly standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63854326880rnRECURRENCE-ID;TZID=America/Los_Angeles:20240624T100000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:d616492ad29601d566d901db330cd514c2ca1a5frnEND:VALARMrnEND:VEVENTrn" "18cb049f6671c3ed289a9c71e80c0de52da3688en9cc32c5026ff8d118bc4ac93bce35238da8eb8d2t20240621T120000n1718995800n1718996400n1719000000nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240621T120000rnDTEND;TZID=America/Los_Angeles:20240621T130000rnRRULE:FREQ=WEEKLY;BYDAY=MO,FR;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231124T120000rnEXDATE;TZID=America/Los_Angeles:20231225T120000rnEXDATE;TZID=America/Los_Angeles:20231229T120000rnEXDATE;TZID=America/Los_Angeles:20240101T120000rnEXDATE;TZID=America/Los_Angeles:20240105T120000rnEXDATE;TZID=America/Los_Angeles:20240115T120000rnEXDATE;TZID=America/Los_Angeles:20240219T120000rnEXDATE;TZID=America/Los_Angeles:20240527T120000rnDTSTAMP:20240617T170120ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:0qv2g51uk9qooa6p1p9m2kjsu9_R20231016T190000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/pow-unuv-btfrnCREATED:20230620T234404ZrnDESCRIPTION:As the team grows\\, it would be nice eat together as a team\\, rn so we're proposing twice-weekly lunches.<br><span><br></span><br><span>If rn you bring lunch\\, start warming it up a little earlier so you're ready at rn 12. If you're eating out\\, we'll cover your DoorDash up to $25 (don't rn forget -- you can schedule your order ahead of time)</span>\\n\\n-::~:~::~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:rn ~::-\\nJoin with Google Meet: https://meet.google.com/pow-unuv-btf\\nOr rn dial: (US) +1 513-760-6079 PIN: 535859198#\\nMore phone numbers: https:rn //tel.meet/pow-unuv-btf?pin=9484659480677&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240617T170120ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Team LunchrnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63854326880rnRECURRENCE-ID;TZID=America/Los_Angeles:20240621T120000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:9cc32c5026ff8d118bc4ac93bce35238da8eb8d2rnEND:VALARMrnEND:VEVENTrn" "18cb049f6671c3ed289a9c71e80c0de52da3688end616492ad29601d566d901db330cd514c2ca1a5ft20240621T100000n1718988600n1718989200n1718992800nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240621T100000rnDTEND;TZID=America/Los_Angeles:20240621T110000rnRRULE:FREQ=WEEKLY;BYDAY=MO,FR;WKST=SUrnDTSTAMP:20240617T170120ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:cjor6rn0v3te499kon6didavua_R20240308T180000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/mns-oimq-wkgrnCREATED:20221104T045344ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/mns-oimq-wkg\\nOr dial: (US) +1 661-772-9059 PIN: rn 652845310#\\nMore phone numbers: https:rn //tel.meet/mns-oimq-wkg?pin=8044670134175&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240617T170120ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:3rnSTATUS:CONFIRMEDrnSUMMARY:Weekly standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63854326880rnRECURRENCE-ID;TZID=America/Los_Angeles:20240621T100000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:d616492ad29601d566d901db330cd514c2ca1a5frnEND:VALARMrnEND:VEVENTrn" "bfed93b1acceebaa1a7b695f939477aa2833d1dfn91c47bfcb5cab58f0aa78ef7fba3aff3198d1c26t20240621n1718926200n1718928000n1719187200nBEGIN:VEVENTrnDTSTART;VALUE=DATE:20240621rnDTEND;VALUE=DATE:20240624rnDTSTAMP:20240525T191612ZrnORGANIZER;CN=Unknown Organizer:mailto:rn unknownorganizer@calendar.google.comrnUID:5rgvd564fa74221rpbkkb6ic48@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=unknownorganizer@calendar.google.com;X-NUM-GUESTS=0:mailto:rn unknownorganizer@calendar.google.comrnCLASS:PRIVATErnCREATED:20240525T191612ZrnDESCRIPTION:To see detailed information for automatically created events rn like this one\\, use the official Google Calendar app. https:rn //g.co/calendarrnLAST-MODIFIED:20240525T191612ZrnLOCATION:Sibley | Beautiful Ocean Views & Hot Tub!\\, Sea RanchrnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Stay at Sibley | Beautiful Ocean Views & Hot Tub!rnTRANSP:TRANSPARENTrnX-EVOLUTION-CALDAV-ETAG:63852347772rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:91c47bfcb5cab58f0aa78ef7fba3aff3198d1c26rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT7H10MrnX-EVOLUTION-ALARM-UID:c9f30f10053d446254c267969cca3d669b0dbf23rnEND:VALARMrnEND:VEVENTrn" "bfed93b1acceebaa1a7b695f939477aa2833d1dfn8a6c885449bc52e7480c7c33a9cc98ded9a90577t20240621n1718926200n1718928000n1719187200nBEGIN:VEVENTrnDTSTART;VALUE=DATE:20240621rnDTEND;VALUE=DATE:20240624rnDTSTAMP:20240619T232402ZrnORGANIZER;CN=unknownorganizer@calendar.google.com:mailto:rn unknownorganizer@calendar.google.comrnUID:buf802s2v61vfbe2mmugpmaqq8@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnCLASS:PRIVATErnCREATED:20240614T002457ZrnDESCRIPTION:To see detailed information for automatically created events rn like this one\\, use the official Google Calendar app. https:rn //g.co/calendar\\n\\nThis event was created from an email you received in rn Gmail. https:rn //mail.google.com/mail?extsrc=cal&plid=ACUX6DMGiPsoXtenE4PVOT042So3OzRW0Gfrn jGtI\\nrnLAST-MODIFIED:20240619T232402ZrnLOCATION:Sibley | Beautiful Ocean Views & Hot Tub\\, Sea RanchrnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Stay at Sibley | Beautiful Ocean Views & Hot TubrnTRANSP:TRANSPARENTrnX-EVOLUTION-CALDAV-ETAG:63854522642rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:8a6c885449bc52e7480c7c33a9cc98ded9a90577rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT7H10MrnX-EVOLUTION-ALARM-UID:82c4c26ff914c47c5fce1873b0e08ab18970ee1arnEND:VALARMrnEND:VEVENTrn" "18cb049f6671c3ed289a9c71e80c0de52da3688en373eeed6b9277d2826cb6aedfa4aedb1e378aba8t20240620T130000n1718923800n1718924400n1718926200nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240620T160000rnDTEND;TZID=America/Los_Angeles:20240620T163000rnDTSTAMP:20240620T210958ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernRECURRENCE-ID;TZID=America/Los_Angeles:20240620T130000rnCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240620T210958ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:2rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63854600998rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:373eeed6b9277d2826cb6aedfa4aedb1e378aba8rnEND:VALARMrnEND:VEVENTrn" "18cb049f6671c3ed289a9c71e80c0de52da3688enba261f6812648a1f09f955fe4fd4d0880a163195t20240620T130000n1718913000n1718913600n1718914500nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240620T130000rnDTEND;TZID=America/Los_Angeles:20240620T131500rnRRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231226T130000rnEXDATE;TZID=America/Los_Angeles:20231227T130000rnEXDATE;TZID=America/Los_Angeles:20231228T130000rnEXDATE;TZID=America/Los_Angeles:20240102T130000rnEXDATE;TZID=America/Los_Angeles:20240103T130000rnEXDATE;TZID=America/Los_Angeles:20240104T130000rnEXDATE;TZID=America/Los_Angeles:20240528T130000rnDTSTAMP:20240304T174854ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T174854ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63852606901rnRECURRENCE-ID;TZID=America/Los_Angeles:20240620T130000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:ba261f6812648a1f09f955fe4fd4d0880a163195rnEND:VALARMrnEND:VEVENTrn" "bfed93b1acceebaa1a7b695f939477aa2833d1dfnc9f30f10053d446254c267969cca3d669b0dbf23t20240621n1718902200n1718928000n1719187200nBEGIN:VEVENTrnDTSTART;VALUE=DATE:20240621rnDTEND;VALUE=DATE:20240624rnDTSTAMP:20240525T191612ZrnORGANIZER;CN=Unknown Organizer:mailto:rn unknownorganizer@calendar.google.comrnUID:5rgvd564fa74221rpbkkb6ic48@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=unknownorganizer@calendar.google.com;X-NUM-GUESTS=0:mailto:rn unknownorganizer@calendar.google.comrnCLASS:PRIVATErnCREATED:20240525T191612ZrnDESCRIPTION:To see detailed information for automatically created events rn like this one\\, use the official Google Calendar app. https:rn //g.co/calendarrnLAST-MODIFIED:20240525T191612ZrnLOCATION:Sibley | Beautiful Ocean Views & Hot Tub!\\, Sea RanchrnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Stay at Sibley | Beautiful Ocean Views & Hot Tub!rnTRANSP:TRANSPARENTrnX-EVOLUTION-CALDAV-ETAG:63852347772rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:91c47bfcb5cab58f0aa78ef7fba3aff3198d1c26rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT7H10MrnX-EVOLUTION-ALARM-UID:c9f30f10053d446254c267969cca3d669b0dbf23rnEND:VALARMrnEND:VEVENTrn" "bfed93b1acceebaa1a7b695f939477aa2833d1dfn82c4c26ff914c47c5fce1873b0e08ab18970ee1at20240621n1718902200n1718928000n1719187200nBEGIN:VEVENTrnDTSTART;VALUE=DATE:20240621rnDTEND;VALUE=DATE:20240624rnDTSTAMP:20240619T232402ZrnORGANIZER;CN=unknownorganizer@calendar.google.com:mailto:rn unknownorganizer@calendar.google.comrnUID:buf802s2v61vfbe2mmugpmaqq8@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnCLASS:PRIVATErnCREATED:20240614T002457ZrnDESCRIPTION:To see detailed information for automatically created events rn like this one\\, use the official Google Calendar app. https:rn //g.co/calendar\\n\\nThis event was created from an email you received in rn Gmail. https:rn //mail.google.com/mail?extsrc=cal&plid=ACUX6DMGiPsoXtenE4PVOT042So3OzRW0Gfrn jGtI\\nrnLAST-MODIFIED:20240619T232402ZrnLOCATION:Sibley | Beautiful Ocean Views & Hot Tub\\, Sea RanchrnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Stay at Sibley | Beautiful Ocean Views & Hot TubrnTRANSP:TRANSPARENTrnX-EVOLUTION-CALDAV-ETAG:63854522642rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:8a6c885449bc52e7480c7c33a9cc98ded9a90577rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT7H10MrnX-EVOLUTION-ALARM-UID:82c4c26ff914c47c5fce1873b0e08ab18970ee1arnEND:VALARMrnEND:VEVENTrn" "18cb049f6671c3ed289a9c71e80c0de52da3688enba261f6812648a1f09f955fe4fd4d0880a163195t20240619T130000n1718826600n1718827200n1718828100nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240619T130000rnDTEND;TZID=America/Los_Angeles:20240619T131500rnRRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231226T130000rnEXDATE;TZID=America/Los_Angeles:20231227T130000rnEXDATE;TZID=America/Los_Angeles:20231228T130000rnEXDATE;TZID=America/Los_Angeles:20240102T130000rnEXDATE;TZID=America/Los_Angeles:20240103T130000rnEXDATE;TZID=America/Los_Angeles:20240104T130000rnEXDATE;TZID=America/Los_Angeles:20240528T130000rnDTSTAMP:20240304T174854ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T174854ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63852606901rnRECURRENCE-ID;TZID=America/Los_Angeles:20240619T130000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:ba261f6812648a1f09f955fe4fd4d0880a163195rnEND:VALARMrnEND:VEVENTrn" "bfed93b1acceebaa1a7b695f939477aa2833d1dfn715159f8e59b952d78609987504de8577894da7at20240619T130000n1718826600n1718827200n1718829000nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240619T130000rnDTEND;TZID=America/Los_Angeles:20240619T133000rnDTSTAMP:20240615T171736ZrnORGANIZER;CN=haoweic@google.com:mailto:haoweic@google.comrnUID:g065vg5aq85bto0ktpgj106uh5_R20240605T200000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=DECLINED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnX-GOOGLE-CONFERENCE:https://meet.google.com/bdk-dyzk-gnqrnRECURRENCE-ID;TZID=America/Los_Angeles:20240619T130000rnCREATED:20190701T194904ZrnDESCRIPTION:This is a meeting open to the community\\, focused on the PRs rn and Issues in Kubernetes python client. <br><br>Agenda: <a href=\"https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing\" id=\"ow595\" __is_owner=\"true\">https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing</a>\\n\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/bdk-dyzk-gnq\\nOr dial: (US) +1 260-327-1990 PIN: rn 130450#\\nMore phone numbers: https:rn //tel.meet/bdk-dyzk-gnq?pin=3844973226311&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240615T171736ZrnLOCATION:US-SVL-MP6-3-A-Grandslam (8) [GVC\\, Jamboard]rnSEQUENCE:3rnSTATUS:CONFIRMEDrnSUMMARY:[client-python] Public Bug Scrub / Issues & PR TriagernTRANSP:TRANSPARENTrnX-EVOLUTION-CALDAV-ETAG:63854155056rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:715159f8e59b952d78609987504de8577894da7arnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:75c36a54a58365a8e4bbc2fa02a48098f1b843a3rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT2HrnX-EVOLUTION-ALARM-UID:efb3dfbc48409e226563ce3d86477e44726036b5rnEND:VALARMrnEND:VEVENTrn" "bfed93b1acceebaa1a7b695f939477aa2833d1dfn75c36a54a58365a8e4bbc2fa02a48098f1b843a3t20240619T130000n1718825400n1718827200n1718829000nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240619T130000rnDTEND;TZID=America/Los_Angeles:20240619T133000rnDTSTAMP:20240615T171736ZrnORGANIZER;CN=haoweic@google.com:mailto:haoweic@google.comrnUID:g065vg5aq85bto0ktpgj106uh5_R20240605T200000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=DECLINED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnX-GOOGLE-CONFERENCE:https://meet.google.com/bdk-dyzk-gnqrnRECURRENCE-ID;TZID=America/Los_Angeles:20240619T130000rnCREATED:20190701T194904ZrnDESCRIPTION:This is a meeting open to the community\\, focused on the PRs rn and Issues in Kubernetes python client. <br><br>Agenda: <a href=\"https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing\" id=\"ow595\" __is_owner=\"true\">https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing</a>\\n\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/bdk-dyzk-gnq\\nOr dial: (US) +1 260-327-1990 PIN: rn 130450#\\nMore phone numbers: https:rn //tel.meet/bdk-dyzk-gnq?pin=3844973226311&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240615T171736ZrnLOCATION:US-SVL-MP6-3-A-Grandslam (8) [GVC\\, Jamboard]rnSEQUENCE:3rnSTATUS:CONFIRMEDrnSUMMARY:[client-python] Public Bug Scrub / Issues & PR TriagernTRANSP:TRANSPARENTrnX-EVOLUTION-CALDAV-ETAG:63854155056rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:715159f8e59b952d78609987504de8577894da7arnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:75c36a54a58365a8e4bbc2fa02a48098f1b843a3rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT2HrnX-EVOLUTION-ALARM-UID:efb3dfbc48409e226563ce3d86477e44726036b5rnEND:VALARMrnEND:VEVENTrn" "bfed93b1acceebaa1a7b695f939477aa2833d1dfnefb3dfbc48409e226563ce3d86477e44726036b5t20240619T130000n1718820000n1718827200n1718829000nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240619T130000rnDTEND;TZID=America/Los_Angeles:20240619T133000rnDTSTAMP:20240615T171736ZrnORGANIZER;CN=haoweic@google.com:mailto:haoweic@google.comrnUID:g065vg5aq85bto0ktpgj106uh5_R20240605T200000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=DECLINED;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnX-GOOGLE-CONFERENCE:https://meet.google.com/bdk-dyzk-gnqrnRECURRENCE-ID;TZID=America/Los_Angeles:20240619T130000rnCREATED:20190701T194904ZrnDESCRIPTION:This is a meeting open to the community\\, focused on the PRs rn and Issues in Kubernetes python client. <br><br>Agenda: <a href=\"https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing\" id=\"ow595\" __is_owner=\"true\">https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing</a>\\n\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/bdk-dyzk-gnq\\nOr dial: (US) +1 260-327-1990 PIN: rn 130450#\\nMore phone numbers: https:rn //tel.meet/bdk-dyzk-gnq?pin=3844973226311&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240615T171736ZrnLOCATION:US-SVL-MP6-3-A-Grandslam (8) [GVC\\, Jamboard]rnSEQUENCE:3rnSTATUS:CONFIRMEDrnSUMMARY:[client-python] Public Bug Scrub / Issues & PR TriagernTRANSP:TRANSPARENTrnX-EVOLUTION-CALDAV-ETAG:63854155056rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:715159f8e59b952d78609987504de8577894da7arnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:75c36a54a58365a8e4bbc2fa02a48098f1b843a3rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT2HrnX-EVOLUTION-ALARM-UID:efb3dfbc48409e226563ce3d86477e44726036b5rnEND:VALARMrnEND:VEVENTrn" "18cb049f6671c3ed289a9c71e80c0de52da3688enf33272bf0c49a6264eae047d9b2bdaeb57106033t20240618T140000n1718743800n1718744400n1718745300nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240618T140000rnDTEND;TZID=America/Los_Angeles:20240618T141500rnRRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU;WKST=SUrnDTSTAMP:20240604T183132ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:5vmnq0smv7srd8558pmkd368hp@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/ees-fcix-szbrnCREATED:20231201T193148ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/ees-fcix-szb\\nOr dial: (US) +1 347-861-6310 PIN: rn 790404641#\\nMore phone numbers: https:rn //tel.meet/ees-fcix-szb?pin=9747143920112&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240604T183132ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:George // Jesse 1:1rnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63853401861rnRECURRENCE-ID;TZID=America/Los_Angeles:20240618T140000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:f33272bf0c49a6264eae047d9b2bdaeb57106033rnEND:VALARMrnEND:VEVENTrn" "18cb049f6671c3ed289a9c71e80c0de52da3688en6f8c26b1bbbff96c6bc88190b20f9989980c72f1t20240618T133000n1718742000n1718742600n1718743800nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240618T133000rnDTEND;TZID=America/Los_Angeles:20240618T135000rnRRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU;WKST=SUrnDTSTAMP:20240326T203013ZrnORGANIZER;CN=dante@usebasis.co:mailto:dante@usebasis.cornUID:0b5u7e66pr7tssqouvs41pndk4_R20240326T203000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=dante@usebasis.co;X-NUM-GUESTS=0:mailto:dante@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/zsn-vngp-iamrnCREATED:20240311T164427ZrnDESCRIPTION:Setting up a recurring time for us to chat / walk.\\n\\n-::~:~::rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::rn ~:~::-\\nJoin with Google Meet: https://meet.google.com/zsn-vngp-iam\\nOr rn dial: (US) +1 304-518-2648 PIN: 171779794#\\nMore phone numbers: https:rn //tel.meet/zsn-vngp-iam?pin=1086952216780&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240326T203013ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:George / DanternTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63849600499rnRECURRENCE-ID;TZID=America/Los_Angeles:20240618T133000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:6f8c26b1bbbff96c6bc88190b20f9989980c72f1rnEND:VALARMrnEND:VEVENTrn" "18cb049f6671c3ed289a9c71e80c0de52da3688enba261f6812648a1f09f955fe4fd4d0880a163195t20240618T130000n1718740200n1718740800n1718741700nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240618T130000rnDTEND;TZID=America/Los_Angeles:20240618T131500rnRRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231226T130000rnEXDATE;TZID=America/Los_Angeles:20231227T130000rnEXDATE;TZID=America/Los_Angeles:20231228T130000rnEXDATE;TZID=America/Los_Angeles:20240102T130000rnEXDATE;TZID=America/Los_Angeles:20240103T130000rnEXDATE;TZID=America/Los_Angeles:20240104T130000rnEXDATE;TZID=America/Los_Angeles:20240528T130000rnDTSTAMP:20240304T174854ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T174854ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63852606901rnRECURRENCE-ID;TZID=America/Los_Angeles:20240618T130000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:ba261f6812648a1f09f955fe4fd4d0880a163195rnEND:VALARMrnEND:VEVENTrn" "18cb049f6671c3ed289a9c71e80c0de52da3688en9cc32c5026ff8d118bc4ac93bce35238da8eb8d2t20240617T120000n1718650200n1718650800n1718654400nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240617T120000rnDTEND;TZID=America/Los_Angeles:20240617T130000rnRRULE:FREQ=WEEKLY;BYDAY=MO,FR;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231124T120000rnEXDATE;TZID=America/Los_Angeles:20231225T120000rnEXDATE;TZID=America/Los_Angeles:20231229T120000rnEXDATE;TZID=America/Los_Angeles:20240101T120000rnEXDATE;TZID=America/Los_Angeles:20240105T120000rnEXDATE;TZID=America/Los_Angeles:20240115T120000rnEXDATE;TZID=America/Los_Angeles:20240219T120000rnEXDATE;TZID=America/Los_Angeles:20240527T120000rnDTSTAMP:20240617T170120ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:0qv2g51uk9qooa6p1p9m2kjsu9_R20231016T190000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/pow-unuv-btfrnCREATED:20230620T234404ZrnDESCRIPTION:As the team grows\\, it would be nice eat together as a team\\, rn so we're proposing twice-weekly lunches.<br><span><br></span><br><span>If rn you bring lunch\\, start warming it up a little earlier so you're ready at rn 12. If you're eating out\\, we'll cover your DoorDash up to $25 (don't rn forget -- you can schedule your order ahead of time)</span>\\n\\n-::~:~::~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:rn ~::-\\nJoin with Google Meet: https://meet.google.com/pow-unuv-btf\\nOr rn dial: (US) +1 513-760-6079 PIN: 535859198#\\nMore phone numbers: https:rn //tel.meet/pow-unuv-btf?pin=9484659480677&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240617T170120ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:0rnSTATUS:CONFIRMEDrnSUMMARY:Team LunchrnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63854326880rnRECURRENCE-ID;TZID=America/Los_Angeles:20240617T120000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:9cc32c5026ff8d118bc4ac93bce35238da8eb8d2rnEND:VALARMrnEND:VEVENTrn" "18cb049f6671c3ed289a9c71e80c0de52da3688enee505824c2687c17f5d29d9cb5c6dca036d84760t20240617T100000n1718644800n1718645400n1718649000nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240617T103000rnDTEND;TZID=America/Los_Angeles:20240617T113000rnDTSTAMP:20240617T164503ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:cjor6rn0v3te499kon6didavua_R20240308T180000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/mns-oimq-wkgrnRECURRENCE-ID;TZID=America/Los_Angeles:20240617T100000rnCREATED:20221104T045344ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/mns-oimq-wkg\\nOr dial: (US) +1 661-772-9059 PIN: rn 652845310#\\nMore phone numbers: https:rn //tel.meet/mns-oimq-wkg?pin=8044670134175&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240617T164503ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:4rnSTATUS:CONFIRMEDrnSUMMARY:Weekly standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63854325903rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:ee505824c2687c17f5d29d9cb5c6dca036d84760rnEND:VALARMrnEND:VEVENTrn" "18cb049f6671c3ed289a9c71e80c0de52da3688ena380548cf10b916ea437a6f23d735f27c8666182t20240617T100000n1718643000n1718643600n1718647200nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240617T100000rnDTEND;TZID=America/Los_Angeles:20240617T110000rnRRULE:FREQ=WEEKLY;BYDAY=MO,FR;WKST=SUrnDTSTAMP:20240506T154231ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:cjor6rn0v3te499kon6didavua_R20240308T180000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/mns-oimq-wkgrnCREATED:20221104T045344ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/mns-oimq-wkg\\nOr dial: (US) +1 661-772-9059 PIN: rn 652845310#\\nMore phone numbers: https:rn //tel.meet/mns-oimq-wkg?pin=8044670134175&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240506T154231ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:3rnSTATUS:CONFIRMEDrnSUMMARY:Weekly standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63853722115rnRECURRENCE-ID;TZID=America/Los_Angeles:20240617T100000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:a380548cf10b916ea437a6f23d735f27c8666182rnEND:VALARMrnEND:VEVENTrn" ];
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
      window-height = 1111;
      window-state = mkTuple [ 1145 873 103 103 ];
      window-width = 1315;
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
      col-0-width = 498;
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
      columns-order = [ 0 1 8 2 3 4 6 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 7 ];
      sort-col = 0;
      sort-order = 0;
    };

    "org/gnome/maps" = {
      last-viewed-location = [ 37.76005879400782 (-122.43592813383793) ];
      map-type = "MapsStreetSource";
      transportation-type = "pedestrian";
      window-maximized = true;
      window-size = [ 2267 1488 ];
      zoom-level = 17;
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
    };

    "org/gnome/nautilus/window-state" = {
      initial-size = mkTuple [ 1435 887 ];
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
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/dev/skynomads/Seabird" = {
      last-folder-path = "/home/george/.kube";
    };

    "org/gnome/portal/filechooser/gnome-network-panel" = {
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
      disabled-extensions = [ "light-style@gnome-shell-extensions.gcampax.github.com" "native-window-placement@gnome-shell-extensions.gcampax.github.com" "window-list@gnome-shell-extensions.gcampax.github.com" "workspace-indicator@gnome-shell-extensions.gcampax.github.com" ];
      enabled-extensions = [ "user-theme@gnome-shell-extensions.gcampax.github.com" "apps-menu@gnome-shell-extensions.gcampax.github.com" "display-brightness-ddcutil@themightydeity.github.com" "drive-menu@gnome-shell-extensions.gcampax.github.com" "places-menu@gnome-shell-extensions.gcampax.github.com" "screenshot-window-sizer@gnome-shell-extensions.gcampax.github.com" "user-theme@gnome-shell-extensions.gcampax.github.com" ];
      favorite-apps = [ "beekeeper-studio.desktop" "obsidian.desktop" "brave-browser.desktop" "Alacritty.desktop" "slack.desktop" "org.gnome.Calendar.desktop" "org.gnome.Nautilus.desktop" "org.gnome.Settings.desktop" ];
      last-selected-power-profile = "power-saver";
      welcome-dialog-last-shown-version = "45.5";
    };

    "org/gnome/shell/extensions/display-brightness-ddcutil" = {
      allow-zero-brightness = true;
      button-location = 0;
      ddcutil-binary-path = "/nix/store/bhblhjrykm00k2cc38qdhfgv714ifmbi-ddcutil-2.1.4/bin/ddcutil";
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
      name = "catppuccin-frappe-mauve-standard+normal";
    };

    "org/gnome/shell/weather" = {
      automatic-location = true;
      locations = [ (mkVariant [ (mkUint32 2) (mkVariant [ "San Francisco" "KOAK" false [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ] [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ] ]) ]) ];
    };

    "org/gnome/shell/world-clocks" = {
      locations = [ (mkVariant [ (mkUint32 2) (mkVariant [ "San Francisco" "KOAK" true [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ] [ (mkTuple [ 0.659296885757089 (-2.136621860115334) ]) ] ]) ]) (mkVariant [ (mkUint32 2) (mkVariant [ "New York" "KNYC" true [ (mkTuple [ 0.7118034407872564 (-1.2909618758762367) ]) ] [ (mkTuple [ 0.7105980465926592 (-1.2916478949920254) ]) ] ]) ]) (mkVariant [ (mkUint32 2) (mkVariant [ "Berlin" "EDDT" true [ (mkTuple [ 0.9174614159494501 0.23241968454167572 ]) ] [ (mkTuple [ 0.916588751323453 0.23387411976724018 ]) ] ]) ]) (mkVariant [ (mkUint32 2) (mkVariant [ "Tbilisi" "UGTB" true [ (mkTuple [ 0.727264160713368 0.7846079132187302 ]) ] [ (mkTuple [ 0.7280931921080653 0.7816166108670297 ]) ] ]) ]) ];
    };

    "org/gnome/software" = {
      check-timestamp = mkInt64 1719245711;
      first-run = false;
      flatpak-purge-timestamp = mkInt64 1718938916;
      install-timestamp = mkInt64 1717698225;
      update-notification-timestamp = mkInt64 1717699403;
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
      sidebar-width = 140;
      sort-column = "name";
      sort-directories-first = true;
      sort-order = "ascending";
      type-format = "category";
      view-type = "list";
      window-size = mkTuple [ 1579 1166 ];
    };

    "org/gtk/settings/color-chooser" = {
      custom-colors = [ (mkTuple [ 0.3176470588235294 0.33725490196078434 0.34509803921568627 1.0 ]) (mkTuple [ 0.9333333333333333 0.11372549019607843 0.0 1.0 ]) (mkTuple [ 0.0 0.0 0.4588235294117647 1.0 ]) (mkTuple [ 0.25882352941176473 0.8313725490196079 0.9568627450980393 1.0 ]) (mkTuple [ 0.6705882352941176 9.411764705882353e-2 0.3215686274509804 1.0 ]) (mkTuple [ 0.28627450980392155 0.6588235294117647 0.20784313725490197 1.0 ]) ];
      selected-color = mkTuple [ true 0.8980392156862745 0.6470588235294118 3.92156862745098e-2 1.0 ];
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
      window-position = mkTuple [ 103 103 ];
      window-size = mkTuple [ 1203 902 ];
    };

    "org/virt-manager/virt-manager/connections" = {
      autoconnect = [ "qemu:///system" ];
      uris = [ "qemu:///system" ];
    };

  };
}
