{ ... }:
{
  system = {
    # Keyboard settings
    keyboard = {
      # Enable key mapping
      enableKeyMapping = true;
      # Remap Caps Lock to Escape
      # TODO: debug doesn't work
      remapCapsLockToEscape = true;
    };
    # macOS defaults
    defaults = {
      NSGlobalDomain = {
        # Enable tap to click on trackpad
        "com.apple.mouse.tapBehavior" = 1;
        # Disable volume change sound feedback
        "com.apple.sound.beep.feedback" = 0;
        # Trackpad settings
        # Enable secondary click
        "com.apple.trackpad.enableSecondaryClick" = true;
        # Trackpad tracking speed
        "com.apple.trackpad.scaling" = 2.0;
      };
      # Automatically install software updates
      SoftwareUpdate.AutomaticallyInstallMacOSUpdates = true;
      # ALF - Application Layer Firewall
      # Allow any downloaded Application that has been signed to accept incoming
      # requests
      alf.allowdownloadsignedenabled = 0;
      # Dock settings
      dock = {
        # Auto-hide
        autohide = true;
        # Auto-hide delay
        autohide-delay = 0.0;
        # Auto-hide time modifier - sets the speed of the animation when hiding
        # the Dock
        autohide-time-modifier = 0.0;
        # Minimize application windows to their Dock icons instead of a separate
        # Dock section
        minimize-to-application = true;
        # Enable highlight hover effect for the grid view of a stack in the Dock
        mouse-over-hilite-stack = true;
        # Dock item tile size
        tilesize = 50;
      };
      # Finder settings
      finder = {
        # Show all file extensions
        AppleShowAllExtensions = true;
        # Show path bar at the bottom of the Finder window
        ShowPathbar = true;
      };
      # Disable guest user on login screen
      loginwindow.GuestEnabled = false;
      # Screensaver settings
      screensaver = {
        # Require password when waking from screensaver
        askForPassword = true;
        # Grace period for asking for password when screensaver is active
        askForPasswordDelay = 0;
      };
      # Trackpad settings
      trackpad = {
        # Enable tap to click
        Clicking = true;
        # Enable tap to drag
        # Dragging = true;
        # Enable three-finger dragging
        # TrackpadThreeFingerDrag = true;
      };
    };
  };
}
