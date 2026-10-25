# Render the real login setter without evaluating a host.
let
  lib = {
    inherit (builtins) concatStringsSep;
    escapeShellArg = value: "'${builtins.replaceStrings [ "'" ] [ "'\\''" ] value}'";
    mapAttrsToList = f: attrs: map (name: f name attrs.${name}) (builtins.attrNames attrs);
  };
in
(import ../../darwin/george/build-volume-launchd.nix { inherit lib; })
.launchd.user.agents.build-volume-env.script
