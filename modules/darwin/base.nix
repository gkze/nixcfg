{
  config,
  lib,
  pkgs,
  primaryUser,
  ...
}:
let
  inherit (lib) attrByPath mkOption types;
  cfg = config.darwinDefaults;
  macApps = import ../../lib/mac-apps.nix { inherit lib pkgs; };
  systemMacAppEntries = config.nixcfg.macApps.systemApplications;
  homeMacAppEntries =
    attrByPath [ "home-manager" "users" primaryUser "nixcfg" "macApps" "systemApplications" ] [ ]
      config;
  # In embedded Home Manager setups, let nix-darwin own /Applications and consume
  # the per-user declarations from home-manager.users.<name> when needed.
  activeMacAppEntries = if systemMacAppEntries != [ ] then systemMacAppEntries else homeMacAppEntries;
in
{
  options.nixcfg.macApps.systemApplications = macApps.systemApplicationsOption;

  options.darwinDefaults = {
    keyboard.capsLockToEscape = mkOption {
      type = types.bool;
      default = true;
      description = "Remap Caps Lock to Escape.";
    };
    dock = {
      autohide = mkOption {
        type = types.bool;
        default = true;
        description = "Automatically hide the dock.";
      };
      tilesize = mkOption {
        type = types.int;
        default = 50;
        description = "Dock icon tile size in pixels.";
      };
    };
    user.uid = mkOption {
      type = types.int;
      default = 501;
      description = "Primary user's UID.";
    };
    security = {
      touchIdSudo = mkOption {
        type = types.bool;
        default = true;
        description = "Enable Touch ID for sudo authentication.";
      };
      firewallAllowSigned = mkOption {
        type = types.bool;
        default = true;
        description = "Allow signed applications through the firewall.";
      };
    };
    launchd = {
      maxfiles = mkOption {
        type = types.int;
        default = 1000000;
        description = "Maximum open file descriptors (launchd limit).";
      };
      maxproc = mkOption {
        type = types.int;
        default = 1000000;
        description = "Maximum processes (launchd limit).";
      };
    };
    homebrew = {
      autoUpdate = mkOption {
        type = types.bool;
        default = true;
        description = "Automatically update Homebrew on activation.";
      };
      cleanup = mkOption {
        type = types.enum [
          "none"
          "uninstall"
          "zap"
        ];
        default = "zap";
        description = "Homebrew cleanup strategy on activation.";
      };
    };
  };

  config = {
    assertions =
      lib.optional (systemMacAppEntries != [ ]) (macApps.uniqueBundleNamesAssertion systemMacAppEntries)
      ++ lib.optional (systemMacAppEntries != [ ] && homeMacAppEntries != [ ]) {
        assertion = false;
        message = "Configure macOS system applications in either nixcfg.macApps.systemApplications or home-manager.users.${primaryUser}.nixcfg.macApps.systemApplications, not both.";
      };

    system = {
      keyboard = {
        enableKeyMapping = true;
        remapCapsLockToEscape = cfg.keyboard.capsLockToEscape;
      };
      defaults = {
        NSGlobalDomain = {
          AppleEnableSwipeNavigateWithScrolls = true;
          "com.apple.mouse.tapBehavior" = 1;
          "com.apple.sound.beep.feedback" = 0;
          "com.apple.trackpad.enableSecondaryClick" = true;
          "com.apple.trackpad.scaling" = 2.0;
        };
        SoftwareUpdate.AutomaticallyInstallMacOSUpdates = true;
        dock = {
          inherit (cfg.dock) autohide tilesize;
          autohide-delay = 0.0;
          autohide-time-modifier = 0.0;
          minimize-to-application = true;
          mouse-over-hilite-stack = true;
        };
        finder = {
          AppleShowAllExtensions = true;
          ShowPathbar = true;
        };
        loginwindow.GuestEnabled = false;
        screensaver = {
          askForPassword = true;
          askForPasswordDelay = 0;
        };
        trackpad = {
          Clicking = true;
          TrackpadThreeFingerHorizSwipeGesture = 0;
        };
      };
      inherit primaryUser;
      stateVersion = 6;

      activationScripts.applications.text = lib.mkAfter (
        macApps.systemApplicationsScript {
          entries = activeMacAppEntries;
          stateDirectory = "/Applications/.nixcfg-mac-apps";
          stateName = "darwin-system";
          writable = false;
        }
      );
    };

    users.users.${primaryUser} = {
      inherit (cfg.user) uid;
      shell = pkgs.zsh;
    };

    networking.applicationFirewall.allowSignedApp = cfg.security.firewallAllowSigned;

    security.pam.services.sudo_local.touchIdAuth = cfg.security.touchIdSudo;

    launchd.daemons =
      lib.mapAttrs
        (name: limit: {
          serviceConfig = {
            Label = "limit.${name}";
            RunAtLoad = true;
            ServiceIPC = true;
            ProgramArguments = [
              "launchctl"
              "limit"
              name
              (toString limit)
              (toString limit)
            ];
          };
        })
        {
          inherit (cfg.launchd) maxfiles maxproc;
        };

    homebrew = {
      enable = pkgs.stdenv.isDarwin;
      global.autoUpdate = cfg.homebrew.autoUpdate;
      onActivation = {
        inherit (cfg.homebrew) autoUpdate cleanup;
        upgrade = true;
      };
      taps = builtins.attrNames config.nix-homebrew.taps;
    };
  };
}
