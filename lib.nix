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
    getEnv
    hasAttr
    length
    listToAttrs
    pathExists
    readFile
    split
    ;
  inherit (lib.lists) findFirst optionals;
  inherit (lib.attrsets) optionalAttrs;

  isCI = getEnv "CI" != "";

  maybePath = p: if pathExists p then p else null;
  userMetaPath = u: maybePath "${src}/home/${u}/meta.nix";
  modulesPath = "${src}/modules";
in
rec {
  inherit modulesPath;
  flakeLock = (fromJSON (readFile ./flake.lock)).nodes;
  sourcesPath =
    let
      envPath = getEnv "SOURCES_JSON";
    in
    if envPath != "" then
      if pathExists envPath then envPath else throw "SOURCES_JSON does not exist: ${envPath}"
    else
      ./sources.json;
  sources = fromJSON (readFile sourcesPath);

  # When FAKE_HASHES=1, all sourceHash* functions return lib.fakeHash instead
  # of reading from sources.json.  This lets the update script evaluate the
  # overlay derivations with placeholder hashes to trigger hash-mismatch errors
  # from which the correct hashes are extracted — without duplicating any
  # derivation logic.
  fakeHashMode = getEnv "FAKE_HASHES" == "1";

  sourceEntry =
    name: if hasAttr name sources then sources.${name} else throw "sources.json missing entry: ${name}";

  # Find hash entry matching hashType and optionally platform
  sourceHashEntry =
    name: hashType:
    if fakeHashMode then
      {
        hash = lib.fakeHash;
        inherit hashType;
        gitDep = "fake-dep";
      }
    else
      let
        entry = sourceEntry name;
        hashes = entry.hashes or [ ];
        match = findFirst (hash: hash.hashType == hashType && !(hash ? platform)) null hashes;
      in
      if match == null then throw "sources.json missing ${hashType} for ${name}" else match;

  # Find hash entry matching hashType and specific platform
  sourceHashEntryForPlatform =
    name: hashType: platform:
    if fakeHashMode then
      {
        hash = lib.fakeHash;
        inherit hashType;
        inherit platform;
      }
    else
      let
        entry = sourceEntry name;
        hashes = entry.hashes or [ ];
        match = findFirst (
          hash: hash.hashType == hashType && (hash.platform or null) == platform
        ) null hashes;
      in
      if match == null then
        throw "sources.json missing ${hashType} for ${name} on ${platform}"
      else
        match;

  sourceHash = name: hashType: (sourceHashEntry name hashType).hash;

  # Get hash for a specific platform
  sourceHashForPlatform =
    name: hashType: platform:
    (sourceHashEntryForPlatform name hashType platform).hash;
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
      k = elemAt sysTup (length sysTup - 1);
    in
    assert
      builtins.elem k [
        "darwin"
        "linux"
      ]
      || throw "unsupported kernel '${k}' from system '${system}' (expected darwin or linux)";
    k;
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
  # Shared module args for home-manager configurations
  mkHomeModuleArgs =
    { system, username }:
    {
      inherit
        inputs
        outputs
        src
        system
        username
        ;
      slib = outputs.lib;
    }
    // userMetaIfExists username;

  # External home-manager modules from flake inputs
  homeExternalModules = [
    inputs.nixvim.homeModules.nixvim
    inputs.sops-nix.homeManagerModules.sops
    inputs.stylix.homeModules.stylix
  ];

  # Shared module list for home-manager configurations
  mkHomeModules =
    {
      system,
      username,
      extraModules ? [ ],
    }:
    homeExternalModules
    ++ [
      "${modulesPath}/home/base.nix"
      "${modulesPath}/home/${kernel system}.nix"
      (userConfigPath username)
    ]
    ++ extraModules;

  mkHome =
    {
      modules ? [ ],
      system,
      username,
      ...
    }:
    {
      inherit system;
      extraSpecialArgs = mkHomeModuleArgs { inherit system username; } // {
        pkgs = pkgsFor.${system};
      };
      modules = mkHomeModules {
        inherit system username;
        extraModules = modules;
      };
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
                  imports = mkHomeModules {
                    inherit system;
                    username = user;
                    extraModules = homeModules;
                  };
                };
              }) users
            );
          };
        }
      ];
    };

  # Common base for all Darwin hosts — captures shared boilerplate so each
  # host file only declares its differences.  hostname is injected by
  # flakelight-darwin from the attrset key (i.e. the filename).
  mkDarwinHost =
    {
      user ? "george",
      extraHomeModules ? [ ],
      extraSystemModules ? [ ],
    }:
    mkSystem {
      system = "aarch64-darwin";
      users = [ user ];
      homeModules = extraHomeModules;
      systemModules = [
        "${modulesPath}/darwin/display-management.nix"
        inputs.nix-homebrew.darwinModules.nix-homebrew
        "${modulesPath}/darwin/homebrew.nix"
        { nix-homebrew = { inherit user; }; }
        "${modulesPath}/darwin/${user}/shell.nix"
        "${modulesPath}/darwin/${user}/brew-apps.nix"
        # Linux builder for cross-platform Nix builds on Apple Silicon.
        # nix-rosetta-builder provides aarch64-linux and x86_64-linux builders
        # via Rosetta 2. Requires initial bootstrap with nix.linux-builder.
        # Skipped in CI — the builder VM image requires aarch64-linux to build
        # and GitHub Actions macOS runners lack a Linux builder.
      ]
      ++ optionals (!isCI) [
        inputs.nix-rosetta-builder.darwinModules.default
        { nix-rosetta-builder.onDemand = true; }
      ]
      ++ extraSystemModules;
    };
}
