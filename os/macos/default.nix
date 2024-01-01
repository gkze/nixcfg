{ ... }: {
  # Enable Toudh ID for sudo
  security.pam.enableSudoTouchIdAuth = true;

  # Auto upgrade nix package and the daemon service
  services.nix-daemon.enable = true;

  # System-wide Launch Daemons
  launchd.daemons = {
    # Raise maximum open file limit
    maxfiles.serviceConfig = {
      Label = "limit.maxfiles";
      RunAtLoad = true;
      ServiceIPC = true;
      ProgramArguments = [ "launchctl" "limit" "maxfiles" "1000000" "1000000" ];
    };

    # Raise maximum running process limit
    maxproc.serviceConfig = {
      Label = "limit.maxproc";
      RunAtLoad = true;
      ServiceIPC = true;
      ProgramArguments = [ "launchctl" "limit" "maxproc" "1000000" "1000000" ];
    };
  };

  # mas manages macOS App Store Apps via a CLI
  homebrew.masApps = {
    "AdGuard for Safari" = 1440147259;
    "Apple Configurator" = 1037126344;
    "JSONPeep" = 1458969831;
    "Shazam: Identify Songs" = 897118787;
    "Table Tool" = 1122008420;
    "Twitter" = 1482454543;
    "Xcode" = 497799835;
  };

  # nix-darwin uses different versioning
  # https://daiderd.com/nix-darwin/manual/index.html#opt-system.stateVersion
  system.stateVersion = 4;
}
