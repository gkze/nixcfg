{
  config,
  pkgs,
  primaryUser,
  ...
}:
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
    inherit primaryUser;
    stateVersion = 6;
  };

  users.users.${primaryUser} = {
    uid = 501;
    shell = pkgs.zsh;
  };

  environment.systemPackages = with pkgs; [
    betterdisplay
    rectangle
  ];

  networking.applicationFirewall.allowSignedApp = true;

  security.pam.services.sudo_local.touchIdAuth = true;

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
    global.autoUpdate = true;
    onActivation = {
      autoUpdate = true;
      cleanup = "zap";
      upgrade = true;
    };
    taps = builtins.attrNames config.nix-homebrew.taps;
  };
}
