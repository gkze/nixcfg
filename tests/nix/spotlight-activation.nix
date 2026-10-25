# Render the real Spotlight activation fragments without evaluating a host.
{ namesToCheck, volumes }:
let
  lib = {
    concatMapStringsSep =
      separator: f: values:
      builtins.concatStringsSep separator (map f values);
    escapeShellArg = value: "'${builtins.replaceStrings [ "'" ] [ "'\\''" ] value}'";
    mkIf = condition: text: if condition then text else "";
    mkMerge = builtins.concatStringsSep "\n";
    mkOption = attrs: attrs;
    types = {
      addCheck =
        elemType: check:
        elemType
        // {
          check = value: elemType.check value && check value;
        };
      listOf = elemType: { inherit elemType; };
      str.check = builtins.isString;
    };
  };
  module = import ../../modules/darwin/base.nix {
    inherit lib;
    config.darwinDefaults.spotlight = {
      indexing = true;
      unindexedVolumes = volumes;
    };
    pkgs = { };
    primaryUser = "test";
  };
  volumeNameType = module.options.darwinDefaults.spotlight.unindexedVolumes.type.elemType;
  argus = import ../../darwin/argus/spotlight.nix { };
in
{
  checks = map volumeNameType.check namesToCheck;
  exclusions = module.config.system.activationScripts.postActivation.text;
  restoration = argus.system.activationScripts.preActivation.text;
}
