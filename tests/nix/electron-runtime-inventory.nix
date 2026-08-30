{
  src ? ../..,
  inventory ? builtins.fromJSON (
    builtins.readFile (src + "/packages/electron-runtimes/sources.json")
  ),
}:
let
  # A focused evaluator harness is necessary here: AST inspection cannot prove
  # that malformed updater metadata is rejected before a runtime is selected.
  final = {
    callPackage = _path: _args: version: hashes: {
      inherit version hashes;
      passthru = {
        dist = null;
        headers = null;
      };
    };
    lib = {
      inherit (builtins) concatStringsSep;
      escapeShellArg = value: value;
      subtractLists = removed: values: builtins.filter (value: !(builtins.elem value removed)) values;
      versionOlder = left: right: builtins.compareVersions left right == -1;
    };
  };
  overlay = import (src + "/overlays/_lib/helpers/electron.nix") {
    inherit final;
    inputs.nixpkgs = src;
    slib.sourceEntry =
      name: if name == "electron-runtimes" then inventory else throw "unexpected source ${name}";
  };
in
{
  inherit (overlay.nixcfgElectron) allVersions hashes;
}
