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
in
{
  options.nixcfg.macApps = {
    applications = macApps.applicationsOption;
    resolved = macApps.resolvedOption;
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

    home.activation.nixcfgUserApplications = lib.mkIf (userEntries != [ ]) (
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

    # Home Manager owns user-scoped apps in ~/Applications. System-scoped apps are
    # left for nix-darwin system activation so /Applications has one owner.

    # TEMPORARILY DISABLED: mac-app-util fails to build with SBCL 2.6.0
    # SBCL 2.6.0 broke fare-quasiquote/cl-interpol readtable handling
    # Tracking issue: https://github.com/hraban/mac-app-util/issues/42
    # TODO: Re-enable once upstream is fixed (see issue nixcfg-gl7)
    # imports = [ inputs.mac-app-util.homeManagerModules.default ];
  };
}
