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
    assertions = lib.optional (cfg.systemApplications != [ ]) (
      macApps.uniqueBundleNamesAssertion cfg.systemApplications
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
