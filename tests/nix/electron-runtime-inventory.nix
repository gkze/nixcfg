{
  src ? ../..,
  inventoryJson ? null,
  inventory ? builtins.fromJSON (
    if inventoryJson == null then
      builtins.readFile (src + "/packages/electron-runtimes/sources.json")
    else
      inventoryJson
  ),
  sourceOverrides ? { },
  fakeHashMode ? false,
  targetSystem ? "aarch64-darwin",
  runtimeVersion ? null,
}:
let
  # A focused evaluator harness is necessary here: AST inspection cannot prove
  # that malformed updater metadata is rejected before a runtime is selected.
  final = {
    callPackage =
      _path: _args: version: hashes:
      let
        package = {
          inherit version hashes;
          passthru = {
            dist = null;
            headers = null;
          };
        };
      in
      package
      // {
        overrideAttrs =
          transform:
          let
            overrides = transform package;
          in
          package
          // overrides
          // {
            passthru = package.passthru // (overrides.passthru or { });
          };
      };
    fetchurl = attrs: attrs;
    fetchzip = attrs: attrs;
    stdenv.hostPlatform.system = targetSystem;
    lib = {
      inherit (builtins) concatStringsSep filter;
      escapeShellArg = value: value;
      fakeHash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
      genAttrs =
        names: valueFor:
        builtins.listToAttrs (
          map (name: {
            inherit name;
            value = valueFor name;
          }) names
        );
      mapAttrsToList = valueFor: attrs: builtins.attrValues (builtins.mapAttrs valueFor attrs);
      subtractLists = removed: values: builtins.filter (value: !(builtins.elem value removed)) values;
      unique = builtins.foldl' (
        values: value: if builtins.elem value values then values else values ++ [ value ]
      ) [ ];
      versionOlder = left: right: builtins.compareVersions left right == -1;
    };
  };
  overlay = import (src + "/overlays/_lib/helpers/electron.nix") {
    inherit final;
    inputs.nixpkgs = src;
    slib = {
      inherit fakeHashMode sourceOverrides;
      sources = sourceOverrides;
      sourceEntry =
        name: if name == "electron-runtimes" then inventory else throw "unexpected source ${name}";
    };
  };
  result = {
    inherit (overlay.nixcfgElectron) allVersions hashes;
  };
in
if runtimeVersion == null then
  result
else
  result
  // {
    runtime = overlay.nixcfgElectron.runtimeFor runtimeVersion;
  }
