{
  bun,
  bun2nix,
  bun2nixSource,
  lib,
  libarchive,
  makeWrapper,
  pkgs,
  stdenv,
  stdenvNoCC,
  traycerSource,
  zig_0_15,
}:
let
  invalidBunNixError = ''
    packages/traycer/bun.nix failed to evaluate.

    Regenerate it through packages/traycer/updater.py from the pinned bun.lock.
  '';
  workspacePaths = {
    "clients/desktop" = "desktop";
    "clients/gui-app" = "gui-app";
    "clients/shared" = "shared";
    "clients/traycer-cli" = "traycer-cli";
    protocol = "protocol";
  };
  workspacePackage =
    relativePath:
    let
      workspaceName =
        workspacePaths.${relativePath} or (throw ''
          Traycer bun.nix referenced an unknown workspace: ${relativePath}
        '');
    in
    stdenvNoCC.mkDerivation {
      name = "traycer-bun-workspace-${workspaceName}";
      src = traycerSource;
      dontUnpack = true;
      dontBuild = true;
      # Keep the exact workspace bytes consumed by Bun. In particular, do not
      # run the generic shebang fixup over package sources.
      dontFixup = true;

      installPhase = ''
        runHook preInstall

        mkdir -p "$out"
        cp -R "$src/${relativePath}/." "$out"

        runHook postInstall
      '';

      passthru.nixcfg.workspacePath = relativePath;
      allowSubstitutes = true;
      preferLocalBuild = false;
    };
  copyBunWorkspacePathToStore =
    path:
    let
      generatedRoot = "${toString ./.}/";
      pathString = toString path;
      relativePath = lib.removePrefix generatedRoot pathString;
    in
    assert lib.assertMsg (lib.hasPrefix generatedRoot pathString)
      "Traycer bun.nix referenced a path outside its generated workspace: ${pathString}";
    workspacePackage relativePath;

  bunPackages = builtins.addErrorContext invalidBunNixError (
    (import ./bun.nix) {
      copyPathToStore = copyBunWorkspacePathToStore;
      inherit (pkgs) fetchFromGitHub fetchgit fetchurl;
    }
  );
  bunPackageEntries = lib.mapAttrsToList (name: package: { inherit name package; }) bunPackages;
  # Two hexadecimal digest characters give a deterministic namespace of at
  # most 256 independently substitutable shards.
  bunPackageShards = lib.groupBy (
    entry: builtins.substring 0 2 (builtins.hashString "sha256" entry.name)
  ) bunPackageEntries;

  bunWithFakeNode = stdenvNoCC.mkDerivation {
    name = "traycer-bun-with-fake-node";
    nativeBuildInputs = [ makeWrapper ];
    dontUnpack = true;
    dontBuild = true;

    installPhase = ''
      runHook preInstall

      cp -R "${bun}/." "$out"
      chmod u+w "$out/bin"
      for nodeBinary in node npm bunx; do
        if [ ! -e "$out/bin/$nodeBinary" ]; then
          ln -s "$out/bin/bun" "$out/bin/$nodeBinary"
        fi
      done
      makeWrapper "$out/bin/bunx" "$out/bin/npx"

      runHook postInstall
    '';
  };
  bunCacheEntryCreator = stdenvNoCC.mkDerivation {
    pname = "traycer-bun2nix-cache-entry-creator";
    inherit (bun2nix) version;
    src = bun2nixSource + "/programs/cache-entry-creator";
    nativeBuildInputs = [ zig_0_15.hook ];

    postConfigure = ''
      ln -s ${pkgs.callPackage (bun2nixSource + "/programs/cache-entry-creator/deps.nix") { }} \
        "$ZIG_GLOBAL_CACHE_DIR/p"
    '';
    zigBuildFlags = [ "--release=fast" ];
    doCheck = true;

    meta.mainProgram = "cache_entry_creator";
  };
  extractHost =
    url:
    let
      match = builtins.match "https?://([^/]+).*" url;
    in
    if match != null then builtins.elemAt match 0 else null;
  registryHostFor =
    package:
    let
      packageUrl = package.passthru.url or null;
      host = if packageUrl != null then extractHost packageUrl else null;
    in
    if host != null && host != "registry.npmjs.org" then host else null;
  buildBunShard =
    shard: entries:
    stdenv.mkDerivation {
      name = "traycer-bun-cache-shard-${shard}";
      nativeBuildInputs = [ bunWithFakeNode ];
      phases = [
        "extractPhase"
        "cacheEntryPhase"
      ];
      # This is the patchShebangs = false cache contract. Package trees enter
      # cache entries byte-for-byte, with no patch or fixup phase.
      dontFixup = true;

      extractPhase = ''
        runHook preExtract

        ${lib.concatMapStringsSep "\n" (
          { name, package }:
          ''
            destination="$out/share/bun-packages/${name}"
            mkdir -p "$destination"
            if [ -d "${package}" ]; then
              cp -R "${package}/." "$destination"
            else
              ${lib.getExe' libarchive "bsdtar"} \
                --extract \
                --file "${package}" \
                --strip-components=1 \
                --no-same-owner \
                --no-same-permissions \
                --directory "$destination"
            fi
            chmod -R u+rwX "$destination"
          ''
        ) entries}

        runHook postExtract
      '';

      cacheEntryPhase = ''
        runHook preCacheEntry

        ${lib.concatMapStringsSep "\n" (
          { name, package }:
          let
            registryHost = registryHostFor package;
          in
          ''
            "${lib.getExe bunCacheEntryCreator}" \
              --out "$out/share/bun-cache" \
              --name ${lib.escapeShellArg name} \
              --package "$out/share/bun-packages/${name}" \
              ${lib.optionalString (registryHost != null) ''
                --registry ${lib.escapeShellArg registryHost}
              ''}
          ''
        ) entries}

        runHook postCacheEntry
      '';

      allowSubstitutes = true;
      preferLocalBuild = false;
    };
  shardOutputs = builtins.attrValues (builtins.mapAttrs buildBunShard bunPackageShards);
  shardSizes = builtins.map builtins.length (builtins.attrValues bunPackageShards);
in
pkgs.symlinkJoin {
  name = "traycer-bun-cache";
  paths = shardOutputs;
  allowSubstitutes = true;
  preferLocalBuild = false;
  passthru.nixcfg = {
    packageCount = builtins.length bunPackageEntries;
    shardCount = builtins.length shardSizes;
    maxShardSize = builtins.foldl' lib.max 0 shardSizes;
    inherit shardOutputs;
    patchShebangs = false;
  };
}
