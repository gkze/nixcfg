{ pkgs, ... }:
{
  system = {
    keyboard = {
      enableKeyMapping = true;
      remapCapsLockToEscape = true;
    };
    defaults = {
      NSGlobalDomain = {
        "com.apple.mouse.tapBehavior" = 1;
        "com.apple.sound.beep.feedback" = 0;
        "com.apple.trackpad.enableSecondaryClick" = true;
        "com.apple.trackpad.scaling" = 2.0;
      };
      SoftwareUpdate.AutomaticallyInstallMacOSUpdates = true;
      alf.allowdownloadsignedenabled = 0;
      dock = {
        autohide = true;
        autohide-delay = 0.0;
        autohide-time-modifier = 0.0;
        minimize-to-application = true;
        mouse-over-hilite-stack = true;
        tilesize = 50;
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
  };

  security.pam.enableSudoTouchIdAuth = true;

  services.nix-daemon.enable = true;

  launchd.daemons = {
    maxfiles.serviceConfig = {
      Label = "limit.maxfiles";
      RunAtLoad = true;
      ServiceIPC = true;
      ProgramArguments = [
        "launchctl"
        "limit"
        "maxfiles"
        "1000000"
        "1000000"
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
        "1000000"
        "1000000"
      ];
    };
  };

  homebrew = {
    enable = pkgs.stdenv.isDarwin;
    onActivation = {
      autoUpdate = true;
      cleanup = "zap";
      upgrade = true;
    };
    masApps = {
      "AdGuard for Safari" = 1440147259;
      "Apple Configurator" = 1037126344;
      "JSONPeep" = 1458969831;
      "Shazam: Identify Songs" = 897118787;
      "Twitter" = 1482454543;
      "Xcode" = 497799835;
    };
  };

  system.stateVersion = 4;
}
