{
  config,
  lib,
  osConfig ? null,
  pkgs,
  ...
}:
let
  macApps = import ../../lib/mac-apps.nix { inherit lib pkgs; };
  cfg = config.nixcfg.macApps;
  standaloneActivation = osConfig == null;
in
{
  options.nixcfg.macApps.systemApplications = macApps.systemApplicationsOption;

  config = {
    assertions = lib.optionals (cfg.systemApplications != [ ]) [
      (macApps.uniqueBundleNamesAssertion cfg.systemApplications)
      (macApps.managedAppsNotInPackageListsAssertion {
        entries = cfg.systemApplications;
        packageLists = [
          {
            label = "home.packages";
            inherit (config.home) packages;
          }
        ];
      })
    ];

    home.activation.nixcfgProfileAppBundleAudit = lib.mkIf (cfg.systemApplications != [ ]) (
      lib.hm.dag.entryAfter [ "installPackages" ] (
        macApps.profileBundleLeakAuditScript {
          packagePaths = map toString config.home.packages;
          managedBundleNames = map (entry: entry.bundleName) cfg.systemApplications;
          label = "home.packages";
        }
      )
    );

    home.activation.nixcfgSystemApplications = lib.mkIf standaloneActivation (
      lib.hm.dag.entryAfter [ "installPackages" ] (
        macApps.systemApplicationsScript {
          entries = cfg.systemApplications;
          stateDirectory = "/Applications/.nixcfg-mac-apps";
          stateName = "home-manager";
          writable = true;
        }
      )
    );

    # When Home Manager is embedded inside nix-darwin, let the system activation own
    # /Applications to avoid dueling cleanup state between two separate activations.

    # TEMPORARILY DISABLED: mac-app-util fails to build with SBCL 2.6.0
    # SBCL 2.6.0 broke fare-quasiquote/cl-interpol readtable handling
    # Tracking issue: https://github.com/hraban/mac-app-util/issues/42
    # TODO: Re-enable once upstream is fixed (see issue nixcfg-gl7)
    # imports = [ inputs.mac-app-util.homeManagerModules.default ];
  };
}
