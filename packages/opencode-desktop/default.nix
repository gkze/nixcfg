{
  pkgs,
  inputs,
  outputs,
  opencode,
  stdenv,
  lib,
  runCommand,
  ...
}:
let
  inherit (builtins)
    fromJSON
    fromTOML
    readFile
    ;

  slib = outputs.lib;
  upstreamDesktop = inputs.opencode.packages.${stdenv.hostPlatform.system}.desktop;
  inherit (upstreamDesktop) pname version;

  cargoManifestVersion =
    (fromTOML (readFile "${inputs.opencode}/packages/desktop/src-tauri/Cargo.toml")).package.version;
  desktopPackageVersion =
    (fromJSON (readFile "${inputs.opencode}/packages/desktop/package.json")).version;
  tauriConfig = fromJSON (readFile "${inputs.opencode}/packages/desktop/src-tauri/tauri.conf.json");
  appName = tauriConfig.productName;
  appBinaryName = tauriConfig.mainBinaryName or "OpenCode";
  appIdentifier = tauriConfig.identifier;
  appIcon =
    let
      iconCandidates = builtins.filter (icon: lib.hasSuffix ".icns" icon) (
        tauriConfig.bundle.icon or [ ]
      );
    in
    if iconCandidates == [ ] then
      throw "packages/opencode-desktop/default.nix expected an .icns bundle icon"
    else
      builtins.head iconCandidates;
  appIconFileName = builtins.baseNameOf appIcon;
  appUrlSchemes = tauriConfig.plugins."deep-link".desktop.schemes or [ ];
  appUrlTypesPlist = lib.optionalString (appUrlSchemes != [ ]) ''
        <key>CFBundleURLTypes</key>
        <array>
          <dict>
            <key>CFBundleURLSchemes</key>
            <array>
    ${
      lib.concatMapStrings (scheme: "          <string>${scheme}</string>\n") appUrlSchemes
    }        </array>
            <key>CFBundleURLName</key>
            <string>${appIdentifier} ${builtins.head appUrlSchemes}</string>
            <key>CFBundleTypeRole</key>
            <string>Editor</string>
          </dict>
        </array>
  '';

  patchedSrc = runCommand "${pname}-${version}-src" { } ''
    cp -r ${upstreamDesktop.src} "$out"
    chmod -R u+w "$out"

    if [ ! -f "$out/.github/TEAM_MEMBERS" ]; then
      mkdir -p "$out/.github"
      if [ -f ${inputs.opencode}/.github/TEAM_MEMBERS ]; then
        cp ${inputs.opencode}/.github/TEAM_MEMBERS "$out/.github/TEAM_MEMBERS"
      else
        touch "$out/.github/TEAM_MEMBERS"
      fi
    fi
  '';

  cargoNix = import ./Cargo.nix {
    inherit pkgs;
    rootSrc = patchedSrc;
    # cargo tauri build enables tauri/custom-protocol for production bundles.
    # Keep that feature here so crate2nix embeds the packaged frontend instead
    # of producing a dev-style desktop binary that can open to a blank window.
    rootFeatures = [
      "default"
      "tauri/custom-protocol"
    ];
  };
  cargoNixVersion = cargoNix.internal.crates."opencode-desktop".version;
  cargoNixVersionCheck =
    if cargoNixVersion == cargoManifestVersion then
      true
    else
      throw ''
        packages/opencode-desktop/Cargo.nix has opencode-desktop version ${cargoNixVersion},
        expected ${cargoManifestVersion}; regenerate Cargo.nix
      '';

  desktopPackageVersionCheck =
    if version == desktopPackageVersion || lib.hasPrefix "${desktopPackageVersion}-" version then
      true
    else
      throw ''
        packages/opencode-desktop/default.nix has desktop version ${version},
        expected ${desktopPackageVersion} or ${desktopPackageVersion}-<suffix>
      '';

  rootCrateOverride = _attrs: {
    inherit pname version;
    src = patchedSrc;
    dontCargoSetupPostUnpack = true;
    cargoVendorDir = ".";
    setSourceRoot = ''
      sourceRoot="$(echo ./*/packages/desktop/src-tauri)"
    '';

    inherit (upstreamDesktop)
      patches
      nativeBuildInputs
      buildInputs
      strictDeps
      meta
      ;

    preConfigure = ''
      repoRoot="$(realpath ../../..)"
      srcTauriRoot="$repoRoot/packages/desktop/src-tauri"

      chmod -R u+w "$repoRoot"
      cp -a ${opencode.node_modules}/{node_modules,packages} "$repoRoot"
      chmod -R u+w "$repoRoot/node_modules" "$repoRoot/packages"
      patchShebangs "$repoRoot/node_modules"
      patchShebangs "$repoRoot/packages/desktop/node_modules"

      install -Dm755 \
        ${opencode}/bin/opencode \
        "$srcTauriRoot/sidecars/opencode-cli-${stdenv.hostPlatform.rust.rustcTarget}"
    '';

    # crate2nix bypasses cargo-tauri's beforeBuildCommand hook, so build the
    # desktop frontend explicitly before compiling the Rust binary.
    preBuild = ''
      repoRoot="$(realpath ../../..)"
      export HOME="$TMPDIR"

      (cd "$repoRoot/packages/desktop" && bun run build)
    '';

    tauriBuildFlags = [
      "--config"
      "tauri.prod.conf.json"
      "--no-sign"
    ];

    # cargo-tauri's install hook is bypassed under crate2nix because the
    # generated derivation provides its own installPhase. Recreate the macOS
    # app bundle from the crate2nix-built binary so Home Manager still sees an
    # .app under $out/Applications.
    postInstall = lib.optionalString stdenv.hostPlatform.isDarwin ''
            appBundle="$out/Applications/${appName}.app"
            appContents="$appBundle/Contents"
            appMacOS="$appContents/MacOS"
            appResources="$appContents/Resources"

            install -d "$appMacOS" "$appResources"
            install -Dm755 "$out/bin/opencode-desktop" "$appMacOS/${appBinaryName}"
            install -Dm755 ${opencode}/bin/opencode "$appMacOS/opencode-cli"
            install -Dm644 \
              "${patchedSrc}/packages/desktop/src-tauri/${appIcon}" \
              "$appResources/${appIconFileName}"

            cat > "$appContents/Info.plist" <<EOF
      <?xml version="1.0" encoding="UTF-8"?>
      <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
      <plist version="1.0">
      <dict>
        <key>CFBundleDevelopmentRegion</key>
        <string>English</string>
        <key>CFBundleDisplayName</key>
        <string>${appName}</string>
        <key>CFBundleExecutable</key>
        <string>${appBinaryName}</string>
        <key>CFBundleIconFile</key>
        <string>${appIconFileName}</string>
        <key>CFBundleIdentifier</key>
        <string>${appIdentifier}</string>
        <key>CFBundleInfoDictionaryVersion</key>
        <string>6.0</string>
        <key>CFBundleName</key>
        <string>${appName}</string>
        <key>CFBundlePackageType</key>
        <string>APPL</string>
        <key>CFBundleShortVersionString</key>
        <string>${desktopPackageVersion}</string>
        <key>CFBundleVersion</key>
        <string>${desktopPackageVersion}</string>
        <key>CSResourcesFileMapped</key>
        <true/>
        <key>LSMinimumSystemVersion</key>
        <string>10.13</string>
      ${appUrlTypesPlist}  <key>LSRequiresCarbon</key>
        <true/>
        <key>NSHighResolutionCapable</key>
        <true/>
      </dict>
      </plist>
      EOF
    '';

    postFixup = lib.optionalString stdenv.hostPlatform.isLinux ''
      if [ -e "$out/bin/OpenCode" ]; then
        mv "$out/bin/OpenCode" "$out/bin/opencode-desktop"
      fi

      if [ -e "$out/share/applications/OpenCode.desktop" ]; then
        sed -i 's|^Exec=OpenCode$|Exec=opencode-desktop|' \
          "$out/share/applications/OpenCode.desktop"
      fi
    '';
  };

  crateOverrides =
    pkgs.defaultCrateOverrides
    // {
      opencode-desktop = rootCrateOverride;
    }
    // slib.mkCrate2nixTauriOverrides { inherit pkgs; };

  opencodeDesktopDrv = cargoNix.workspaceMembers.opencode-desktop.build.override {
    inherit crateOverrides;
    runTests = false;
  };
  guardedOpencodeDesktopDrv =
    assert cargoNixVersionCheck;
    assert desktopPackageVersionCheck;
    opencodeDesktopDrv;
in
guardedOpencodeDesktopDrv.overrideAttrs (old: {
  __intentionallyOverridingVersion = true;
  inherit pname version;
  inherit (upstreamDesktop) meta;
  passthru = (old.passthru or { }) // {
    inherit cargoNix crateOverrides patchedSrc;
    opencodeDesktopDrv = guardedOpencodeDesktopDrv;
  };
})
