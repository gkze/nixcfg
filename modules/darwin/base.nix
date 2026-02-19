{
  config,
  lib,
  pkgs,
  primaryUser,
  ...
}:
let
  inherit (lib) mkOption types;
  cfg = config.darwinDefaults;
in
{
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
    system = {
      keyboard = {
        enableKeyMapping = true;
        remapCapsLockToEscape = cfg.keyboard.capsLockToEscape;
      };
      defaults = {
        NSGlobalDomain = {
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
        trackpad.Clicking = true;
      };
      inherit primaryUser;
      stateVersion = 6;
    };

    users.users.${primaryUser} = {
      inherit (cfg.user) uid;
      shell = pkgs.zsh;
    };

    environment.systemPackages = with pkgs; [
      betterdisplay
      rectangle
    ];

    networking.applicationFirewall.allowSignedApp = cfg.security.firewallAllowSigned;

    security.pam.services.sudo_local.touchIdAuth = cfg.security.touchIdSudo;

    launchd.daemons = {
      maxfiles.serviceConfig = {
        Label = "limit.maxfiles";
        RunAtLoad = true;
        ServiceIPC = true;
        ProgramArguments = [
          "launchctl"
          "limit"
          "maxfiles"
          (toString cfg.launchd.maxfiles)
          (toString cfg.launchd.maxfiles)
        ];
      };
      maxproc.serviceConfig = {
        Label = "limit.maxproc";
        RunAtLoad = true;
        ServiceIPC = true;
        ProgramArguments = [
          "launchctl"
          "limit"
          "maxproc"
          (toString cfg.launchd.maxproc)
          (toString cfg.launchd.maxproc)
        ];
      };
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
