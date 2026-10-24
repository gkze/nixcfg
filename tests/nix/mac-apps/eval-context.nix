{
  rsyncPath ? "/usr/bin/rsync",
}:
let
  attrByPath =
    path: default: set:
    let
      result =
        builtins.foldl'
          (
            acc: name:
            if acc.found && builtins.isAttrs acc.value && builtins.hasAttr name acc.value then
              {
                found = true;
                value = builtins.getAttr name acc.value;
              }
            else
              {
                found = false;
                value = default;
              }
          )
          {
            found = true;
            value = set;
          }
          path;
    in
    result.value;

  unique =
    list: builtins.foldl' (acc: item: if builtins.elem item acc then acc else acc ++ [ item ]) [ ] list;

  lib = {
    inherit attrByPath unique;
    inherit (builtins) concatStringsSep;
    escapeShellArg = builtins.toJSON;
    getExe = value: builtins.toString value;
    literalExpression = value: value;
    mapAttrsToList = f: set: builtins.map (name: f name set.${name}) (builtins.attrNames set);
    mkDefault = value: value;
    mkOption = value: value;
    nameValuePair = name: value: { inherit name value; };
    optionalString = cond: value: if cond then value else "";
    types = rec {
      bool = "bool";
      package = "package";
      str = "str";
      enum = values: {
        _type = "enum";
        inherit values;
      };
      attrsOf = value: {
        _type = "attrsOf";
        elemType = value;
      };
      listOf = value: {
        _type = "listOf";
        elemType = value;
      };
      submodule = module: {
        _type = "submodule";
        inherit module;
      };
    };
  };

  pkgs = {
    rsync = rsyncPath;
    # The real writer returns a store path. Returning the text keeps the shim
    # contract observable to tests.
    writeTextFile = args: args.text;
  };
in
{
  inherit lib pkgs;
  macApps = import ../../../lib/mac-apps.nix { inherit lib pkgs; };
}
