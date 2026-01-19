{
  inputs,
  lib,
  outputs,
  pkgsFor,
  src,
  ...
}:
let
  inherit (builtins)
    elemAt
    fromJSON
    hasAttr
    length
    listToAttrs
    pathExists
    readFile
    split
    ;
  inherit (lib.lists) findFirst optionals;
  inherit (lib.attrsets) optionalAttrs;

  maybePath = p: if pathExists p then p else null;
  userMetaPath = u: maybePath "${src}/home/${u}/meta.nix";
  modulesPath = "${src}/modules";
in
rec {
  inherit modulesPath;
  flakeLock = (fromJSON (readFile ./flake.lock)).nodes;
  sources = fromJSON (readFile ./sources.json);

  sourceEntry =
    name: if hasAttr name sources then sources.${name} else throw "sources.json missing entry: ${name}";

  sourceHashEntry =
    name: hashType:
    let
      entry = sourceEntry name;
      hashes = entry.hashes or [ ];
      match = findFirst (hash: hash.hashType == hashType) null hashes;
    in
    if match == null then throw "sources.json missing ${hashType} for ${name}" else match;

  sourceHash = name: hashType: (sourceHashEntry name hashType).hash;
  sourceUrl =
    name: hashType:
    let
      entry = sourceHashEntry name hashType;
    in
    entry.url or (throw "sources.json missing url for ${name}:${hashType}");

  # Convert a Nix value to JSONC format with trailing commas
  toJSONC =
    {
      indent ? 2,
      initialIndent ? 0,
    }:
    value:
    let
      inherit (builtins)
        attrNames
        concatStringsSep
        isBool
        isList
        isString
        map
        replaceStrings
        typeOf
        ;
      inherit (lib) concatMapStringsSep;

      # Generate indentation string
      mkIndent = level: concatStringsSep "" (map (_: " ") (lib.range 1 (level * indent)));

      # Escape special characters in strings
      escapeString =
        s:
        let
          escaped = replaceStrings [ "\\" "\"" "\n" "\r" "\t" ] [ "\\\\" "\\\"" "\\n" "\\r" "\\t" ] s;
        in
        "\"${escaped}\"";

      # Convert value to JSONC recursively
      toJSONCImpl =
        level: v:
        let
          currentIndent = mkIndent level;
          nextIndent = mkIndent (level + 1);
          vType = typeOf v;
        in
        if vType == "null" then
          "null"
        else if isBool v then
          if v then "true" else "false"
        else if vType == "int" || vType == "float" then
          toString v
        else if isString v then
          escapeString v
        else if isList v then
          if v == [ ] then
            "[]"
          else
            "[\n${
              concatMapStringsSep ",\n" (item: "${nextIndent}${toJSONCImpl (level + 1) item}") v
            },\n${currentIndent}]"
        else if vType == "set" then
          let
            keys = attrNames v;
          in
          if keys == [ ] then
            "{}"
          else
            "{\n${
              concatMapStringsSep ",\n" (
                key: "${nextIndent}${escapeString key}: ${toJSONCImpl (level + 1) v.${key}}"
              ) keys
            },\n${currentIndent}}"
        else
          throw "toJSONC: unsupported type '${vType}'";
    in
    toJSONCImpl initialIndent value;
  userMetaIfExists =
    user:
    let
      userMetaFile = userMetaPath user;
    in
    optionalAttrs (userMetaFile != null) { userMeta = import userMetaFile; };
  userConfigPath = u: "${src}/home/${u}/configuration.nix";
  kernel =
    system:
    let
      sysTup = split "-" system;
    in
    elemAt sysTup (length sysTup - 1);
  homeDirBase =
    system:
    {
      darwin = "/Users";
      linux = "/home";
    }
    .${kernel system};
  srcDirBase =
    system:
    {
      darwin = "Development";
      linux = "src";
    }
    .${kernel system};
  ghRaw =
    {
      owner,
      repo,
      rev,
      path,
    }:
    "https://raw.githubusercontent.com/${owner}/${repo}/${rev}/${path}";
  mkHome =
    {
      modules ? [ ],
      system,
      username,
      ...
    }:
    {
      inherit system;
      extraSpecialArgs = {
        inherit
          inputs
          outputs
          src
          system
          username
          ;
        pkgs = pkgsFor.${system};
        slib = outputs.lib;
      }
      // userMetaIfExists username;
      modules = [
        "${modulesPath}/home/base.nix"
        "${modulesPath}/home/${kernel system}.nix"
        (userConfigPath username)
      ]
      ++ modules;
    };
  mkSystem =
    {
      homeModules ? [ ],
      system,
      systemModules ? [ ],
      users ? [ ],
      ...
    }:
    let
      hmEntryPoint =
        {
          darwin = "darwin";
          linux = "nixos";
        }
        .${kernel system};
    in
    {
      inherit system;
      specialArgs = {
        inherit
          inputs
          outputs
          src
          system
          ;
        primaryUser = elemAt users 0;
      }
      // {
        slib = outputs.lib;
      };
      modules = [
        "${modulesPath}/common.nix"
        "${modulesPath}/${kernel system}/base.nix"
      ]
      ++ systemModules
      ++ optionals (length homeModules > 0) [
        inputs.home-manager."${hmEntryPoint}Modules".home-manager
        {
          home-manager = {
            useGlobalPkgs = true;
            useUserPackages = true;
            extraSpecialArgs = {
              inherit
                inputs
                outputs
                src
                system
                ;
              slib = outputs.lib;
              pkgs = pkgsFor.${system};
            };
            users = listToAttrs (
              map (user: {
                name = user;
                value = {
                  _module.args = {
                    username = user;
                  }
                  // userMetaIfExists user;
                  imports = [
                    "${modulesPath}/home/base.nix"
                    "${modulesPath}/home/${kernel system}.nix"
                    "${src}/home/${user}/configuration.nix"
                  ]
                  ++ homeModules;
                };
              }) users
            );
          };
        }
      ];
    };
}
