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
  opencodeConfig = attrByPath [
    "home-manager"
    "users"
    primaryUser
    "home"
    "sessionVariables"
    "OPENCODE_CONFIG"
  ] null config;
  macApps = import ../../lib/mac-apps.nix { inherit lib pkgs; };
  systemMacAppEntries = macApps.applicationsForScope "system" config.nixcfg.macApps.resolved;
  homeManagerResolved = attrByPath [
    "home-manager"
    "users"
    primaryUser
    "nixcfg"
    "macApps"
    "resolved"
  ] { } config;
  homeMacAppEntries = macApps.applicationsForScope "system" homeManagerResolved;
  userMacAppEntries = macApps.applicationsForScope "user" homeManagerResolved;
  # Home Manager declares which activation owns the user scope. Reading that
  # option instead of writing it keeps this module free of an evaluation cycle
  # through the embedded home-manager configuration.
  nixDarwinOwnsUserScope =
    attrByPath [
      "home-manager"
      "users"
      primaryUser
      "nixcfg"
      "macApps"
      "activationScope"
    ] "home-manager" config == "nix-darwin";
  activeMacAppEntries = systemMacAppEntries ++ homeMacAppEntries;
  primaryUserHome = "/Users/${primaryUser}";
  # System and user application scopes are declared with different option trees
  # but managed by one activation script, so both run concurrently under their
  # own target directory lock.
  systemMacAppScope = {
    entries = activeMacAppEntries;
    stateDirectory = "/Applications/.nixcfg-mac-apps";
    stateName = "darwin-system";
    targetDirectory = "/Applications";
    writable = false;
  };
  userMacAppScope = {
    entries = userMacAppEntries;
    stateDirectory = "${primaryUserHome}/Applications/.nixcfg-mac-apps";
    stateName = "home-manager-user";
    targetDirectory = "${primaryUserHome}/Applications";
    user = {
      inherit (cfg.user) uid;
      name = primaryUser;
    };
    writable = true;
  };
  # The system scope runs unconditionally: emptying the declaration must still
  # remove the bundles the previous activation owned, which only the helper's
  # manifest can identify. The user scope joins only when Home Manager hands it
  # over, mirroring that module's own empty-set rule.
  macAppScopes = [
    systemMacAppScope
  ]
  ++ lib.optional (nixDarwinOwnsUserScope && userMacAppScope.entries != [ ]) userMacAppScope;
in
{
  options.nixcfg.macApps = {
    applications = macApps.applicationsOption;
    resolved = macApps.resolvedOption;
  };

  options.darwinDefaults = {
    appearance = mkOption {
      type = types.enum [
        "automatic"
        "dark"
      ];
      default = "automatic";
      description = "macOS appearance: switch between Light and Dark automatically, or stay Dark.";
    };
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
    spotlight.indexing = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Leave Spotlight indexing under macOS control. When false, activation
        turns indexing off on every volume. Setting it back to true does not
        turn indexing back on; run `sudo mdutil -a -i on` to do that.
      '';
    };
    spotlight.unindexedVolumes = mkOption {
      type = types.listOf (
        (types.addCheck types.str (
          volume: volume != "." && volume != ".." && builtins.match "[^/]+" volume != null
        ))
        // {
          description = "volume basename (nonempty, without `/`, and not `.` or `..`)";
        }
      );
      default = [ ];
      description = ''
        Volume names under `/Volumes` that stay out of the Spotlight index while
        `spotlight.indexing` is true. Activation disables indexing and search
        for each name that is the root of a mounted volume and skips every other
        path, so a build or scratch volume can stay unindexed without disabling
        Spotlight metadata for App Store tooling. Neither this option nor
        `spotlight.indexing = true` re-enables indexing that was previously
        disabled.
      '';
      example = [ "Build" ];
    };
    zsh.deferCompletionInitToHomeManager = mkOption {
      type = types.bool;
      default = false;
      description = "Let Home Manager initialize zsh and bash completion after configuring fpath.";
    };
    homebrew = {
      autoUpdate = mkOption {
        type = types.bool;
        default = false;
        description = "Allow Homebrew to auto-update during activation and manual commands.";
      };
      upgrade = mkOption {
        type = types.bool;
        default = false;
        description = "Upgrade Homebrew and Mac App Store packages on activation.";
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
          # Always written: nix-darwin never deletes defaults keys, so a stale
          # `true` would otherwise keep auto-switching a host pinned to Dark.
          AppleInterfaceStyle = if cfg.appearance == "dark" then "Dark" else null;
          AppleInterfaceStyleSwitchesAutomatically = cfg.appearance == "automatic";
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
        macApps.concurrentApplicationsScript macAppScopes
      );

      # Runs on every activation because macOS updates can turn indexing back
      # on. A failure only warns, so Spotlight state never blocks a switch.
      # Volume exclusions are scoped instead of global: a host can keep the
      # index that App Store tooling reads while leaving build volumes out.
      activationScripts.postActivation.text = lib.mkMerge [
        (lib.mkIf (!cfg.spotlight.indexing) ''
          echo "disabling Spotlight indexing..." >&2
          /usr/bin/mdutil -a -i off >/dev/null \
            || echo "warning: mdutil could not disable Spotlight indexing" >&2
        '')
        # Do not gate this on mdutil's human-readable status: indexing-only and
        # full disablement differ, and the volume path itself can contain the
        # word "disabled". Reapplying the state is safe and never erases an index.
        (lib.mkIf (cfg.spotlight.indexing && cfg.spotlight.unindexedVolumes != [ ]) (
          lib.concatMapStringsSep "\n" (volume: ''
            volumePath=${lib.escapeShellArg "/Volumes/${volume}"}
            currentMountPoint="$(
              /usr/sbin/diskutil info -plist "$volumePath" 2>/dev/null |
                /usr/bin/plutil -extract MountPoint raw -o - - 2>/dev/null || true
            )"
            if [ "$currentMountPoint" = "$volumePath" ]; then
              echo "disabling Spotlight indexing for $volumePath..." >&2
              /usr/bin/mdutil -i off -d "$volumePath" >/dev/null \
                || echo "warning: mdutil could not disable Spotlight indexing for $volumePath" >&2
            else
              echo "skipping Spotlight exclusion for path that is not a mounted volume: $volumePath" >&2
            fi
          '') cfg.spotlight.unindexedVolumes
        ))
      ];
    };

    environment.shells = [ pkgs.zsh ];

    users.users.${primaryUser} = {
      inherit (cfg.user) uid;
      shell = pkgs.zsh;
    };

    networking.applicationFirewall.allowSignedApp = cfg.security.firewallAllowSigned;

    security.pam.services.sudo_local.touchIdAuth = cfg.security.touchIdSudo;

    programs.zsh = lib.mkIf cfg.zsh.deferCompletionInitToHomeManager {
      enableGlobalCompInit = false;
      enableBashCompletion = false;
    };

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

    launchd.user.envVariables = {
      GNUPGHOME = gpgHome;
    }
    // lib.optionalAttrs (opencodeConfig != null) {
      OPENCODE_CONFIG = opencodeConfig;
    };

    homebrew = {
      enable = pkgs.stdenv.hostPlatform.isDarwin;
      global.autoUpdate = cfg.homebrew.autoUpdate;
      onActivation = {
        inherit (cfg.homebrew) autoUpdate cleanup;
        upgrade = cfg.homebrew.upgrade;
      };
    };
  };
}
