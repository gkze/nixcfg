# Activation admits only packages that can be installed. A gated package
# (`meta.broken` or `passthru.exportReady = false`) must be dropped before
# `resolveApplications` interpolates its output, so the output never enters a
# Darwin root closure.
_:
let
  inherit (import ../lib.nix) assertEq;

  context = import ./eval-context.nix { };
  inherit (context) macApps;

  entry = package: {
    inherit package;
    bundleName = "Example.app";
    cli = { };
    mode = "copy";
    preventDowngrade = false;
    scope = "system";
  };

  installable = outPath: {
    pname = "example";
    inherit outPath;
    passthru.macApp = {
      bundleName = "Example.app";
      bundleRelPath = "Applications/Example.app";
      installMode = "copy";
    };
  };

  resolved =
    applications:
    macApps.resolveApplications {
      homeDirectory = "/Users/example";
      inherit applications;
    };

  names = applications: builtins.attrNames (resolved applications);

  mixed = resolved {
    gated = entry {
      passthru.exportReady = false;
    };
    broken = entry {
      meta.broken = true;
      passthru.exportReady = true;
    };
    ready = entry (installable "/nix/store/ready-app");
  };
in
builtins.deepSeq [
  (assertEq "an ordinary package is routed" [ "ready" ] (names {
    ready = entry (installable "/nix/store/ready-app");
  }))
  (assertEq "an explicit export-ready package is routed" [ "ready" ] (names {
    ready = entry (
      (installable "/nix/store/ready-app")
      // {
        passthru = (installable "/nix/store/ready-app").passthru // {
          exportReady = true;
        };
      }
    );
  }))
  (assertEq "exportReady false is omitted before its output is read" [ "ready" ] (names {
    gated = entry {
      passthru.exportReady = false;
    };
    ready = entry (installable "/nix/store/ready-app");
  }))
  (assertEq "meta.broken is omitted" [ "ready" ] (names {
    broken = entry {
      meta.broken = true;
    };
    ready = entry (installable "/nix/store/ready-app");
  }))
  (assertEq "gated and broken packages are omitted together" [ "ready" ] (builtins.attrNames mixed))
  (assertEq "the resolved routes contain only the installable output" true (
    builtins.match ".*/nix/store/ready-app.*" (builtins.toJSON mixed) != null
  ))
  (assertEq "a withheld output is absent from the resolved routes" true (
    builtins.match ".*gated.*" (builtins.toJSON mixed) == null
    && builtins.match ".*broken.*" (builtins.toJSON mixed) == null
  ))
] true
