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
  gpgHome = attrByPath [
    "home-manager"
    "users"
    primaryUser
    "programs"
    "gpg"
    "homedir"
  ] "/Users/${primaryUser}/.local/share/gnupg" config;
  macApps = import ../../lib/mac-apps.nix { inherit lib pkgs; };
  systemMacAppEntries = macApps.applicationsForScope "system" config.nixcfg.macApps.resolved;
  homeMacAppEntries = macApps.applicationsForScope "system" (
    attrByPath [ "home-manager" "users" primaryUser "nixcfg" "macApps" "resolved" ] { } config
  );
  activeMacAppEntries = systemMacAppEntries ++ homeMacAppEntries;
in
{
  options.nixcfg.macApps = {
    applications = macApps.applicationsOption;
    resolved = macApps.resolvedOption;
  };

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
    nixcfg.macApps.resolved = macApps.resolveApplications {
      inherit (config.nixcfg.macApps) applications;
      homeDirectory = "/Users/${primaryUser}";
    };

    assertions = lib.optional (activeMacAppEntries != [ ]) (
      macApps.uniqueBundleNamesAssertion activeMacAppEntries
    );

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
        macApps.applicationsScript {
          entries = activeMacAppEntries;
          stateDirectory = "/Applications/.nixcfg-mac-apps";
          stateName = "darwin-system";
          targetDirectory = "/Applications";
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

    launchd.user.envVariables.GNUPGHOME = gpgHome;

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
