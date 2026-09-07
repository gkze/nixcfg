# Evaluation is needed to prove callPackage defaults and lazy dependency probes.
{
  pkgs,
  src ? ../..,
}:
let
  inherit (pkgs) lib;
  selfSourceHelper = import (src + "/lib/package-self-source.nix") {
    inherit lib;
    outputs.lib = rec {
      sources.wispr-flow.version = "9.9.9-test";
      sourceEntry = name: sources.${name};
    };
  };
  wisprFlow = selfSourceHelper.injectIntoFunction "wispr-flow" (
    import (src + "/packages/wispr-flow/default.nix")
  );
  appScope = {
    mkDmgApp = throw "source injection must not build an app";
    mkSimpleDarwinApp = args: args.info;
  };
  injectedApp = lib.callPackageWith appScope wisprFlow { };
  overriddenApp = lib.callPackageWith appScope wisprFlow {
    selfSource.version = "explicit-override";
  };

  emdashFunction = import (src + "/packages/emdash/default.nix");
  unusedArguments = lib.genAttrs (builtins.attrNames (builtins.functionArgs emdashFunction)) (
    name: throw "dependency probe forced unrelated ${name}"
  );
  nodejs = pkgs.nodejs_24;
  pnpmPackage = pkgs.pnpm_10;
  dependencyArguments = unusedArguments // {
    inherit lib;
    inherit (pkgs) fetchPnpmDeps stdenv;
    inputs.emdash = builtins.toString ./package-probes/emdash;
    nixcfgElectron.sourceBuildFor = _: {
      # stdenv checks the app's env keys before selecting a passthru dependency;
      # the runtime paths must remain lazy throughout dependency instantiation.
      commonEnv = {
        ELECTRON_DIST = throw "dependency probe forced Electron distribution";
        npm_config_nodedir = throw "dependency probe forced Electron headers";
      };
      runtime = throw "dependency probe forced Electron runtime";
      runtimeVersion = "99.1.2";
      headers = throw "dependency probe forced Electron headers";
      dist = throw "dependency probe forced Electron distribution";
    };
    outputs.lib = {
      getFlakeVersion = _: "candidate";
      sourceHashForPlatform =
        _: _: _:
        lib.fakeHash;
    };
    pkgs = {
      inherit nodejs;
      pnpm = pnpmPackage;
    };
    selfSource = {
      electronVersion = "99.1.2";
      pins = {
        nodejsAttr = "nodejs";
        nodejsVersion = nodejs.version;
        nodeEngine = ">=24";
        pnpmAttr = "pnpm";
        pnpmVersion = pnpmPackage.version;
        pnpmEngine = ">=10";
        packageManager = "pnpm@10.0.0";
      };
    };
  };
  dependencyProbe = emdashFunction dependencyArguments;
  dependencyContext = builtins.getContext dependencyProbe.pnpmDeps.drvPath;
in
assert injectedApp.version == "9.9.9-test";
assert overriddenApp.version == "explicit-override";
assert dependencyProbe.pnpmDeps.outputHash == lib.fakeHash;
# Forcing this path instantiates the real fetchPnpmDeps derivation. Any Electron
# reference in its dependency closure forces one of the exceptions above.
assert builtins.attrNames dependencyContext == [ dependencyProbe.pnpmDeps.drvPath ];
true
