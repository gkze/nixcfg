# The metadata lookup must work before the filtered store source is created.
{ lib }:
let
  package = import ../../packages/opencode-desktop/default.nix;
  original = ./opencode-desktop-source/original;
  overridden = ./opencode-desktop-source/overridden;
  filteredSource = "/nix/store/00000000000000000000000000000000-unmaterialized-opencode";
  unusedArguments = lib.genAttrs (builtins.attrNames (builtins.functionArgs package)) (
    name: throw "metadata lookup forced unrelated ${name}"
  );
  packageFor =
    system: source: version:
    package (
      unusedArguments
      // {
        inherit lib;
        inputs.opencode = {
          outPath = builtins.toString original;
          packages.${system}.opencode.src = filteredSource;
        };
        nixcfgElectron.sourceBuildFor = _: { runtimeVersion = "99.1.2"; };
        opencode = {
          src = source;
          inherit version;
        };
        outputs.lib = { };
        selfSource = {
          electronVersion = "99.1.2";
          pins.desktopWorkspace = "packages/desktop";
        };
        stdenv = {
          hostPlatform = { inherit system; };
          mkDerivation = args: args;
        };
      }
    );
  checkSystem =
    system:
    let
      defaultPackage = packageFor system filteredSource "1.0.0";
      overriddenPackage = packageFor system (builtins.toString overridden) "2.0.0";
      missingSource = builtins.tryEval (packageFor system "/missing/overridden-source" "1.0.0").version;
    in
    assert defaultPackage.src == filteredSource;
    assert toString defaultPackage.passthru.workspaceMetadataSource == toString original;
    assert builtins.elem "packages/llm" defaultPackage.passthru.desktopWorkspacePaths;
    assert overriddenPackage.src == toString overridden;
    assert overriddenPackage.passthru.workspaceMetadataSource == toString overridden;
    assert !(builtins.elem "packages/llm" overriddenPackage.passthru.desktopWorkspacePaths);
    assert missingSource.success == false;
    true;
in
builtins.all checkSystem [
  "aarch64-darwin"
  "aarch64-linux"
  "x86_64-linux"
]
