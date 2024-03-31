# Generated via dconf2nix: https://github.com/gvolpe/dconf2nix
{ lib, ... }:

with lib.hm.gvariant;

{
  dconf.settings = {
    "org/gnome/Connections" = {
      first-run = false;
    };

    "org/gnome/Weather" = {
      locations = [
        (mkVariant [ (mkUint32 2) (mkVariant [ "San Francisco" "KOAK" true [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ] [ (mkTuple [ 0.659296885757089 (-2.136621860115334) ]) ] ]) ])
      ];
      window-height = 712;
      window-maximized = false;
      window-width = 1145;
    };

    "org/gnome/baobab/ui" = {
      is-maximized = false;
      window-size = mkTuple [ 2545 2034 ];
    };

    "org/gnome/calculator" = {
      accuracy = 9;
      angle-units = "degrees";
      base = 10;
      button-mode = "programming";
      number-format = "engineering";
      show-thousands = false;
      show-zeroes = false;
      source-currency = "DZD";
      source-units = "degree";
      target-currency = "DZD";
      target-units = "radian";
      window-maximized = false;
      window-size = mkTuple [ 1175 717 ];
      word-size = 64;
    };

    "org/gnome/calendar" = {
      active-view = "month";
      week-view-zoom-level = 1.0;
      window-maximized = true;
      window-size = mkTuple [ 360 600 ];
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
      last-panel = "display";
      window-state = mkTuple [ 980 695 false ];
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
      application-children = [ "org-gnome-software" "gnome-network-panel" "org-gnome-calendar" "gnome-system-monitor" "gnome-power-panel" "slack" "org-gnome-evolution-alarm-notify" "org-gnome-settings" "org-gnome-nautilus" "brave-browser" "brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-default" "org-gnome-baobab" ];
      show-banners = true;
    };

    "org/gnome/desktop/notifications/application/brave-browser" = {
      application-id = "brave-browser.desktop";
    };

    "org/gnome/desktop/notifications/application/brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-default" = {
      application-id = "brave-kjgfgldnnfoeklkmfkjfagphfepbbdan-Default.desktop";
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

    "org/gnome/desktop/notifications/application/org-gnome-baobab" = {
      application-id = "org.gnome.baobab.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-calendar" = {
      application-id = "org.gnome.Calendar.desktop";
    };

    "org/gnome/desktop/notifications/application/org-gnome-evolution-alarm-notify" = {
      application-id = "org.gnome.Evolution-alarm-notify.desktop";
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

    "org/gnome/epiphany" = {
      ask-for-default = false;
    };

    "org/gnome/evolution-data-server" = {
      migrated = true;
    };

    "org/gnome/evolution-data-server/calendar" = {
      reminders-past = [ "4f07455384537b2cfce205647ab990f233d98eean3a6d163dcdf20d96ae33d3b93a94f2be4e05cfe7t20240329T100000n1711731000n1711731600n1711735200nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240329T100000rnDTEND;TZID=America/Los_Angeles:20240329T110000rnRRULE:FREQ=WEEKLY;BYDAY=MO,FR;WKST=SUrnDTSTAMP:20240308T181413ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:cjor6rn0v3te499kon6didavua_R20240308T180000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/mns-oimq-wkgrnCREATED:20221104T045344ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/mns-oimq-wkg\\nOr dial: (US) +1 661-772-9059 PIN: rn 652845310#\\nMore phone numbers: https:rn //tel.meet/mns-oimq-wkg?pin=8044670134175&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240308T181413ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:3rnSTATUS:CONFIRMEDrnSUMMARY:Weekly standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63846464155rnRECURRENCE-ID;TZID=America/Los_Angeles:20240329T100000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:3a6d163dcdf20d96ae33d3b93a94f2be4e05cfe7rnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eean4d6fda9d48d640c40efb385645a6cfd3a81c1c37t20240328T130000n1711655400n1711656000n1711656900nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240328T130000rnDTEND;TZID=America/Los_Angeles:20240328T131500rnRRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231226T130000rnEXDATE;TZID=America/Los_Angeles:20231227T130000rnEXDATE;TZID=America/Los_Angeles:20231228T130000rnEXDATE;TZID=America/Los_Angeles:20240102T130000rnEXDATE;TZID=America/Los_Angeles:20240103T130000rnEXDATE;TZID=America/Los_Angeles:20240104T130000rnDTSTAMP:20240304T174854ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T174854ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63845963341rnRECURRENCE-ID;TZID=America/Los_Angeles:20240328T130000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:4d6fda9d48d640c40efb385645a6cfd3a81c1c37rnEND:VALARMrnEND:VEVENTrn" "4f07455384537b2cfce205647ab990f233d98eean4d6fda9d48d640c40efb385645a6cfd3a81c1c37t20240327T130000n1711569000n1711569600n1711570500nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240327T130000rnDTEND;TZID=America/Los_Angeles:20240327T131500rnRRULE:FREQ=WEEKLY;BYDAY=TU,WE,TH;WKST=SUrnEXDATE;TZID=America/Los_Angeles:20231226T130000rnEXDATE;TZID=America/Los_Angeles:20231227T130000rnEXDATE;TZID=America/Los_Angeles:20231228T130000rnEXDATE;TZID=America/Los_Angeles:20240102T130000rnEXDATE;TZID=America/Los_Angeles:20240103T130000rnEXDATE;TZID=America/Los_Angeles:20240104T130000rnDTSTAMP:20240304T174854ZrnORGANIZER;CN=jesse@usebasis.co:mailto:jesse@usebasis.cornUID:vbarq9ftf3o32tuq9bcj250nhr_R20231128T210000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=jesse@usebasis.co;X-NUM-GUESTS=0:mailto:jesse@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=All Team;X-NUM-GUESTS=0:mailto:team@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=room@usebasis.co;X-NUM-GUESTS=0:mailto:room@usebasis.cornATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;rn CN=george@usebasis.co;X-NUM-GUESTS=0:mailto:george@usebasis.cornX-GOOGLE-CONFERENCE:https://meet.google.com/phb-qxft-kfernCREATED:20231018T010056ZrnDESCRIPTION:-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/phb-qxft-kfe\\nOr dial: (US) +1 650-980-4386 PIN: rn 139125139#\\nMore phone numbers: https:rn //tel.meet/phb-qxft-kfe?pin=9703345477266&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240304T174854ZrnX-LIC-ERROR;X-LIC-ERRORTYPE=VALUE-PARSE-ERROR:No value for LOCATION rn property. Removing entire property:rnSEQUENCE:1rnSTATUS:CONFIRMEDrnSUMMARY:Daily standuprnTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63845963341rnRECURRENCE-ID;TZID=America/Los_Angeles:20240327T130000rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:4d6fda9d48d640c40efb385645a6cfd3a81c1c37rnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacn2df89116cae85131a63335b111378592500870c9t20240327T130000n1711569000n1711569600n1711571400nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240327T130000rnDTEND;TZID=America/Los_Angeles:20240327T133000rnDTSTAMP:20240327T130148ZrnORGANIZER;CN=haoweic@google.com:mailto:haoweic@google.comrnUID:g065vg5aq85bto0ktpgj106uh5_R20240327T200000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnX-GOOGLE-CONFERENCE:https://meet.google.com/bdk-dyzk-gnqrnRECURRENCE-ID;TZID=America/Los_Angeles:20240327T130000rnCREATED:20190701T194904ZrnDESCRIPTION:This is a meeting open to the community\\, focused on the PRs rn and Issues in Kubernetes python client. <br><br>Agenda: <a href=\"https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing\" id=\"ow595\" __is_owner=\"true\">https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing</a>\\n\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/bdk-dyzk-gnq\\nOr dial: (US) +1 260-327-1990 PIN: rn 130450#\\nMore phone numbers: https:rn //tel.meet/bdk-dyzk-gnq?pin=3844973226311&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240327T130148ZrnLOCATION:US-SVL-MP6-3-A-Grandslam (8) [GVC\\, Jamboard]rnSEQUENCE:3rnSTATUS:CONFIRMEDrnSUMMARY:[client-python] Public Bug Scrub / Issues & PR TriagernTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63847227708rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:2df89116cae85131a63335b111378592500870c9rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:e319ca1259b8e0a8081455075969a7014a2588a3rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT2HrnX-EVOLUTION-ALARM-UID:6a7e94ae71992512a77736ac06b3cf3d11e14f9drnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacne319ca1259b8e0a8081455075969a7014a2588a3t20240327T130000n1711567800n1711569600n1711571400nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240327T130000rnDTEND;TZID=America/Los_Angeles:20240327T133000rnDTSTAMP:20240327T130148ZrnORGANIZER;CN=haoweic@google.com:mailto:haoweic@google.comrnUID:g065vg5aq85bto0ktpgj106uh5_R20240327T200000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnX-GOOGLE-CONFERENCE:https://meet.google.com/bdk-dyzk-gnqrnRECURRENCE-ID;TZID=America/Los_Angeles:20240327T130000rnCREATED:20190701T194904ZrnDESCRIPTION:This is a meeting open to the community\\, focused on the PRs rn and Issues in Kubernetes python client. <br><br>Agenda: <a href=\"https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing\" id=\"ow595\" __is_owner=\"true\">https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing</a>\\n\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/bdk-dyzk-gnq\\nOr dial: (US) +1 260-327-1990 PIN: rn 130450#\\nMore phone numbers: https:rn //tel.meet/bdk-dyzk-gnq?pin=3844973226311&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240327T130148ZrnLOCATION:US-SVL-MP6-3-A-Grandslam (8) [GVC\\, Jamboard]rnSEQUENCE:3rnSTATUS:CONFIRMEDrnSUMMARY:[client-python] Public Bug Scrub / Issues & PR TriagernTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63847227708rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:2df89116cae85131a63335b111378592500870c9rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:e319ca1259b8e0a8081455075969a7014a2588a3rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT2HrnX-EVOLUTION-ALARM-UID:6a7e94ae71992512a77736ac06b3cf3d11e14f9drnEND:VALARMrnEND:VEVENTrn" "57f8243406da1dc7a69ed4f26b80a86fd7b75aacn6a7e94ae71992512a77736ac06b3cf3d11e14f9dt20240327T130000n1711562400n1711569600n1711571400nBEGIN:VEVENTrnDTSTART;TZID=America/Los_Angeles:20240327T130000rnDTEND;TZID=America/Los_Angeles:20240327T133000rnDTSTAMP:20240327T130148ZrnORGANIZER;CN=haoweic@google.com:mailto:haoweic@google.comrnUID:g065vg5aq85bto0ktpgj106uh5_R20240327T200000@google.comrnATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;rn CN=george.kontridze@gmail.com;X-NUM-GUESTS=0:mailto:rn george.kontridze@gmail.comrnX-GOOGLE-CONFERENCE:https://meet.google.com/bdk-dyzk-gnqrnRECURRENCE-ID;TZID=America/Los_Angeles:20240327T130000rnCREATED:20190701T194904ZrnDESCRIPTION:This is a meeting open to the community\\, focused on the PRs rn and Issues in Kubernetes python client. <br><br>Agenda: <a href=\"https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing\" id=\"ow595\" __is_owner=\"true\">https:rn //docs.google.com/document/d/1OqxDm-PWyL6-LPMfUqkWXb8kpeTtr1wwBlz5IKknmBA/rn edit?usp=sharing</a>\\n\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-\\nJoin with Google Meet: https:rn //meet.google.com/bdk-dyzk-gnq\\nOr dial: (US) +1 260-327-1990 PIN: rn 130450#\\nMore phone numbers: https:rn //tel.meet/bdk-dyzk-gnq?pin=3844973226311&hs=7\\n\\nLearn more about Meet rn at: https://support.google.com/a/users/answer/9282720\\n\\nPlease do not rn edit this section.\\n-::~:~::~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:~:rn ~:~:~:~:~:~:~:~:~:~:~:~:~::~:~::-rnLAST-MODIFIED:20240327T130148ZrnLOCATION:US-SVL-MP6-3-A-Grandslam (8) [GVC\\, Jamboard]rnSEQUENCE:3rnSTATUS:CONFIRMEDrnSUMMARY:[client-python] Public Bug Scrub / Issues & PR TriagernTRANSP:OPAQUErnX-EVOLUTION-CALDAV-ETAG:63847227708rnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT10MrnX-EVOLUTION-ALARM-UID:2df89116cae85131a63335b111378592500870c9rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT30MrnX-EVOLUTION-ALARM-UID:e319ca1259b8e0a8081455075969a7014a2588a3rnEND:VALARMrnBEGIN:VALARMrnACTION:DISPLAYrnDESCRIPTION:This is an event reminderrnTRIGGER:-PT2HrnX-EVOLUTION-ALARM-UID:6a7e94ae71992512a77736ac06b3cf3d11e14f9drnEND:VALARMrnEND:VEVENTrn" ];
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
      window-state = mkTuple [ 1920 1166 0 0 ];
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
      col-0-width = 1105;
    };

    "org/gnome/maps" = {
      last-viewed-location = [ 37.7771 (-122.406) ];
      map-type = "MapsStreetSource";
      transportation-type = "pedestrian";
      window-maximized = false;
      window-size = [ 2267 1488 ];
      zoom-level = 19;
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
      initial-size = mkTuple [ 1790 1082 ];
    };

    "org/gnome/nm-applet/eap/9af31e9a-764b-462f-b9a8-2a8aed26c932" = {
      ignore-ca-cert = false;
      ignore-phase2-ca-cert = false;
    };

    "org/gnome/portal/filechooser/brave-browser" = {
      last-folder-path = "/home/george/Downloads";
    };

    "org/gnome/portal/filechooser/slack" = {
      last-folder-path = "/home/george/Pictures/Screenshots";
    };

    "org/gnome/settings-daemon/plugins/color" = {
      night-light-last-coordinates = mkTuple [ 34.09983120103345 (-118.4117) ];
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
      last-selected-power-profile = "power-saver";
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

    "org/gnome/shell/weather" = {
      automatic-location = true;
      locations = [ (mkVariant [ (mkUint32 2) (mkVariant [ "San Francisco" "KOAK" true [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ] [ (mkTuple [ 0.659296885757089 (-2.136621860115334) ]) ] ]) ]) ];
    };

    "org/gnome/shell/world-clocks" = {
      locations = [ (mkVariant [ (mkUint32 2) (mkVariant [ "San Francisco" "KOAK" true [ (mkTuple [ 0.6583284898216201 (-2.133408063190589) ]) ] [ (mkTuple [ 0.659296885757089 (-2.136621860115334) ]) ] ]) ]) (mkVariant [ (mkUint32 2) (mkVariant [ "New York" "KNYC" true [ (mkTuple [ 0.7118034407872564 (-1.2909618758762367) ]) ] [ (mkTuple [ 0.7105980465926592 (-1.2916478949920254) ]) ] ]) ]) (mkVariant [ (mkUint32 2) (mkVariant [ "Berlin" "EDDT" true [ (mkTuple [ 0.9174614159494501 0.23241968454167572 ]) ] [ (mkTuple [ 0.916588751323453 0.23387411976724018 ]) ] ]) ]) (mkVariant [ (mkUint32 2) (mkVariant [ "Tbilisi" "UGTB" true [ (mkTuple [ 0.727264160713368 0.7846079132187302 ]) ] [ (mkTuple [ 0.7280931921080653 0.7816166108670297 ]) ] ]) ]) ];
    };

    "org/gnome/software" = {
      check-timestamp = mkInt64 1711729929;
      first-run = false;
      flatpak-purge-timestamp = mkInt64 1711673674;
      install-timestamp = mkInt64 1707937338;
      update-notification-timestamp = mkInt64 1711568769;
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
      sort-column = "type";
      sort-directories-first = true;
      sort-order = "ascending";
      type-format = "category";
      view-type = "list";
      window-size = mkTuple [ 1145 1612 ];
    };

    "org/gtk/settings/color-chooser" = {
      custom-colors = [ (mkTuple [ 0.0 0.0 0.4588235294117647 1.0 ]) (mkTuple [ 0.25882352941176473 0.8313725490196079 0.9568627450980393 1.0 ]) (mkTuple [ 0.6705882352941176 9.411764705882353e-2 0.3215686274509804 1.0 ]) (mkTuple [ 0.28627450980392155 0.6588235294117647 0.20784313725490197 1.0 ]) ];
      selected-color = mkTuple [ true 0.0 0.0 0.4588235294117647 1.0 ];
    };

    "org/gtk/settings/file-chooser" = {
      date-format = "regular";
      location-mode = "path-bar";
      show-hidden = false;
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
