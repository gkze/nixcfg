{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (builtins) attrValues;
  macApps = import ../../lib/mac-apps.nix { inherit lib pkgs; };
  cfg = config.nixcfg.macApps;
  managedEntries = attrValues cfg.resolved;
  userEntries = macApps.applicationsForScope "user" cfg.resolved;
  managedBundleNames = map (entry: entry.bundleName) managedEntries;
  cliShimFiles = macApps.cliShimFiles { entries = managedEntries; };
in
{
  options.nixcfg.macApps = {
    applications = macApps.applicationsOption;
    resolved = macApps.resolvedOption;

    activationScope = lib.mkOption {
      type = lib.types.enum [
        "home-manager"
        "nix-darwin"
      ];
      default = "home-manager";
      description = ''
        Which activation reconciles user-scoped application bundles.
        "home-manager" runs this module's own activation step. "nix-darwin"
        means system activation already runs the same entries alongside the
        system scope, so this module must not repeat that work.
      '';
    };
  };

  config = {
    nixcfg.macApps.resolved = macApps.resolveApplications {
      inherit (cfg) applications;
      inherit (config.home) homeDirectory;
    };

    targets.darwin = {
      copyApps.enable = false;
      linkApps.enable = false;
    };

    # Command-line executables bundled inside managed applications are linked
    # into ~/.local/bin. The consumer owns putting that directory on PATH.
    home.file = lib.mkIf (cliShimFiles != { }) cliShimFiles;

    assertions = lib.optionals (managedEntries != [ ]) [
      (macApps.uniqueBundleNamesAssertion managedEntries)
      (macApps.managedAppsNotInPackageListsAssertion {
        entries = managedEntries;
        packageLists = [
          {
            label = "home.packages";
            inherit (config.home) packages;
          }
        ];
      })
    ];

    home.activation.nixcfgRemoveManagedApplicationProfileCopies = lib.mkIf (managedEntries != [ ]) (
      lib.hm.dag.entryAfter [ "installPackages" ] (
        macApps.removeProfileCopiesScript {
          bundleNames = managedBundleNames;
          targetDirectory = config.targets.darwin.copyApps.directory;
        }
      )
    );

    home.activation.nixcfgProfileAppBundleAudit = lib.mkIf (managedEntries != [ ]) (
      lib.hm.dag.entryAfter [ "installPackages" ] (
        macApps.profileBundleLeakAuditScript {
          packagePaths = map toString config.home.packages;
          inherit managedBundleNames;
          label = "home.packages";
        }
      )
    );

    home.activation.nixcfgUserApplications =
      lib.mkIf (userEntries != [ ] && cfg.activationScope == "home-manager")
        (
          lib.hm.dag.entryAfter [ "nixcfgRemoveManagedApplicationProfileCopies" ] (
            macApps.applicationsScript {
              entries = userEntries;
              stateDirectory = "${config.home.homeDirectory}/Applications/.nixcfg-mac-apps";
              stateName = "home-manager-user";
              targetDirectory = "${config.home.homeDirectory}/Applications";
              writable = true;
            }
          )
        );

    # User-scoped apps in ~/Applications are owned either by this activation step
    # or, when nix-darwin runs both scopes concurrently, by system activation.
  };
}
