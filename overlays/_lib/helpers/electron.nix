{
  inputs,
  final,
  slib,
  ...
}:
let
  mkElectron = final.callPackage (
    inputs.nixpkgs + "/pkgs/development/tools/electron/binary/generic.nix"
  ) { };

  inventory = slib.sourceEntry "electron-runtimes";
  expectedInventoryVersion = "inventory-v1";
  requiredArtifacts = [
    "headers"
    "aarch64-darwin"
    "aarch64-linux"
    "x86_64-darwin"
    "x86_64-linux"
  ];

  parseRecord =
    record:
    let
      key = record.platform or (throw "nixcfgElectron: runtime hash record missing inventory key");
      match = builtins.match "^([0-9]+\\.[0-9]+\\.[0-9]+):(headers|aarch64-darwin|aarch64-linux|x86_64-darwin|x86_64-linux)$" key;
      version =
        if match == null then
          throw "nixcfgElectron: malformed runtime inventory key ${key}"
        else
          builtins.elemAt match 0;
      artifact = builtins.elemAt match 1;
      hashType = record.hashType or (throw "nixcfgElectron: runtime record ${key} missing hashType");
      hash = record.hash or (throw "nixcfgElectron: runtime record ${key} missing hash");
    in
    if hashType != "sha256" then
      throw "nixcfgElectron: runtime record ${key} must use sha256"
    else if builtins.match "^sha256-[A-Za-z0-9+/]+=*$" hash == null then
      throw "nixcfgElectron: runtime record ${key} has a malformed SRI hash"
    else
      { inherit version artifact hash; };

  insertRecord =
    records: record:
    let
      versionHashes = records.${record.version} or { };
    in
    if builtins.hasAttr record.artifact versionHashes then
      throw "nixcfgElectron: duplicate ${record.version}:${record.artifact} runtime record"
    else
      records
      // {
        "${record.version}" = versionHashes // {
          "${record.artifact}" = record.hash;
        };
      };

  validateVersion =
    version: versionHashes:
    let
      present = builtins.attrNames versionHashes;
      missing = final.lib.subtractLists present requiredArtifacts;
      unexpected = final.lib.subtractLists requiredArtifacts present;
    in
    if missing != [ ] || unexpected != [ ] then
      throw "nixcfgElectron: incomplete runtime ${version}; missing ${final.lib.concatStringsSep "," missing}; unexpected ${final.lib.concatStringsSep "," unexpected}"
    else
      versionHashes;

  records =
    if (inventory.version or null) != expectedInventoryVersion then
      throw "nixcfgElectron: unsupported runtime inventory version ${
        toString (inventory.version or null)
      }"
    else if !(builtins.isList (inventory.hashes or null)) then
      throw "nixcfgElectron: runtime inventory hashes must be a list"
    else
      map parseRecord inventory.hashes;
  rawHashes = builtins.foldl' insertRecord { } records;
  validatedHashes =
    if rawHashes == { } then
      throw "nixcfgElectron: runtime inventory must not be empty"
    else
      builtins.mapAttrs validateVersion rawHashes;
  hashes = builtins.deepSeq validatedHashes validatedHashes;
  allVersions = builtins.sort final.lib.versionOlder (builtins.attrNames hashes);
  runtimes = builtins.mapAttrs mkElectron hashes;

  runtimeFor =
    version:
    runtimes.${version} or (throw "nixcfgElectron: missing packaged Electron runtime for ${version}");

  sourceBuildFor =
    version:
    let
      runtime = runtimeFor version;
      exactRuntime =
        if runtime.version == version then
          runtime
        else
          throw "nixcfgElectron: runtime ${version} resolved Electron ${runtime.version}";
    in
    {
      inherit version;
      runtime = exactRuntime;
      runtimeVersion = exactRuntime.version;
      inherit (exactRuntime.passthru) headers;
      inherit (exactRuntime.passthru) dist;
      commonEnv = {
        ELECTRON_SKIP_BINARY_DOWNLOAD = "1";
        npm_config_runtime = "electron";
        npm_config_target = version;
        npm_config_nodedir = toString exactRuntime.passthru.headers;
      };
      copyDist = ''
        electronDistDir="$PWD/electron-dist"
        mkdir -p "$electronDistDir"
        cp -R ${exactRuntime.passthru.dist}/. "$electronDistDir"/
        chmod -R u+w "$electronDistDir"
      '';
      electronBuilderConfigFlags = ''
        -c.electronDist="$electronDistDir" \
        -c.electronVersion=${final.lib.escapeShellArg exactRuntime.version} \
      '';
    };
in
{
  nixcfgElectron = {
    inherit
      allVersions
      hashes
      runtimeFor
      runtimes
      sourceBuildFor
      ;

    versionsForSystem = _system: allVersions;
  };
}
