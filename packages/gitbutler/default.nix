{
  apple-sdk_15 ? null,
  autoPatchelfHook ? null,
  cmake,
  curl,
  fetchPnpmDeps ? null,
  inputs,
  libayatana-appindicator,
  lib,
  libiconv ? null,
  libssh2,
  librsvg,
  nodejs_22,
  openssl,
  outputs,
  perl,
  pkgs,
  pkg-config,
  pnpmConfigHook,
  pnpm_10,
  python3,
  rustPlatform,
  runCommand,
  stdenv,
  webkitgtk_4_1,
  wrapGAppsHook4,
  zlib,
  crate2nixSourceOnly ? false,
  ...
}:
let
  pname = "gitbutler";
  appName = "GitButler";
  appBundleName = "${appName}.app";
  slib = outputs.lib;
  version = lib.removePrefix "release/" (slib.getFlakeVersion pname);
  src = inputs.gitbutler;
  nodejs = nodejs_22;
  pnpm = pnpm_10.override { inherit nodejs; };

  pnpmDeps =
    if fetchPnpmDeps != null then
      fetchPnpmDeps {
        inherit
          pname
          version
          src
          pnpm
          ;
        fetcherVersion = 3;
        hash = slib.sourceHash pname "npmDepsHash";
      }
    else
      pnpm.fetchDeps {
        inherit
          pname
          version
          src
          ;
        fetcherVersion = 3;
        hash = slib.sourceHash pname "npmDepsHash";
      };

  frontend = stdenv.mkDerivation {
    pname = "${pname}-frontend";
    inherit version src pnpmDeps;

    nativeBuildInputs = [
      nodejs
      pnpm
      pnpmConfigHook
    ]
    ++ lib.optionals stdenv.hostPlatform.isLinux [ autoPatchelfHook ];

    buildInputs = lib.optionals stdenv.hostPlatform.isLinux [ stdenv.cc.cc.lib ];

    autoPatchelfIgnoreMissingDeps = lib.optionals stdenv.hostPlatform.isLinux [
      "libc.musl-x86_64.so.1"
    ];

    env = {
      CI = "1";
      npm_config_manage_package_manager_versions = "false";
    };

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR/gitbutler-home"
      mkdir -p "$HOME"
      pnpm config set manage-package-manager-versions false
      ${lib.optionalString stdenv.hostPlatform.isLinux ''
        autoPatchelf node_modules
      ''}
      pnpm build:desktop -- --mode production

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall


      mkdir -p "$out"
      cp -R apps/desktop/build/. "$out/"

      runHook postInstall
    '';
  };

  patchedSource =
    {
      includeFrontend,
      nameSuffix,
    }:
    runCommand "${pname}-${version}-${nameSuffix}" { nativeBuildInputs = [ python3 ]; } ''
      cp -r ${src} "$out"
      chmod -R u+w "$out"

      ${python3}/bin/python3 ${./patch_sources.py} "$out"

      rm -rf "$out/crates/gitbutler-tauri/frontend-dist"
      mkdir -p "$out/crates/gitbutler-tauri/frontend-dist"
      ${
        if includeFrontend then
          ''
            cp -R ${frontend}/. "$out/crates/gitbutler-tauri/frontend-dist/"
          ''
        else
          ''
            touch "$out/crates/gitbutler-tauri/frontend-dist/index.html"
          ''
      }
    '';

  crate2nixSrc = patchedSource {
    includeFrontend = false;
    nameSuffix = "crate2nix-src";
  };

  patchedSrc =
    if crate2nixSourceOnly then
      crate2nixSrc
    else
      patchedSource {
        includeFrontend = true;
        nameSuffix = "src";
      };

  cargoNix = import ./Cargo.nix {
    inherit pkgs;
    rootSrc = crate2nixSrc;
  };

  commonNativeBuildInputs = [
    cmake
    perl
    pkg-config
    rustPlatform.bindgenHook
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [ wrapGAppsHook4 ];

  commonBuildInputs = [
    libssh2
    openssl
    zlib
  ]
  ++ lib.optionals stdenv.hostPlatform.isDarwin [
    apple-sdk_15
    libiconv
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [
    libayatana-appindicator
    librsvg
    webkitgtk_4_1
  ];

  commonOverride = attrs: {
    nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ commonNativeBuildInputs;
    buildInputs = (attrs.buildInputs or [ ]) ++ commonBuildInputs;
    CHANNEL = "release";
    VERSION = version;
  };

  opensslSysOverride =
    attrs:
    (commonOverride attrs)
    // {
      # crate2nix builds openssl-src separately, so its vendored source path is
      # gone by the time openssl-sys' build script runs.
      OPENSSL_NO_VENDOR = "1";
    };

  rmcpOverride = attrs: {
    CARGO_CRATE_NAME = attrs.crateName;
    CARGO_PKG_VERSION = attrs.version;
  };

  butInstallerOverride =
    attrs:
    let
      base = commonOverride attrs;
    in
    base
    // {
      buildInputs = base.buildInputs ++ [ curl.out ];
    };

  gitbutlerTauriOverride =
    attrs:
    (commonOverride attrs)
    // {
      src = patchedSrc;
      workspace_member = "crates/gitbutler-tauri";
    };

  tauriOverrides = slib.mkCrate2nixTauriOverrides {
    inherit pkgs;
    pluginCrates = slib.tauriPluginEnvCrateNames ++ [
      "tauri-plugin-log"
      "tauri-plugin-trafficlights-positioner"
    ];
  };

  crateOverrides =
    pkgs.defaultCrateOverrides
    // lib.genAttrs (builtins.attrNames cargoNix.internal.crates) (_: commonOverride)
    // tauriOverrides
    // {
      but = butInstallerOverride;
      but-installer = butInstallerOverride;
      gitbutler-tauri = gitbutlerTauriOverride;
      openssl-sys = opensslSysOverride;
      rmcp = attrs: (commonOverride attrs) // (rmcpOverride attrs);
    };

  askpassDrv = cargoNix.workspaceMembers.gitbutler-git.build.override {
    inherit crateOverrides;
    runTests = false;
  };

  butDrv = cargoNix.workspaceMembers.but.build.override {
    inherit crateOverrides;
    runTests = false;
  };

  gitbutlerDrv = cargoNix.workspaceMembers.gitbutler-tauri.build.override {
    inherit crateOverrides;
    runTests = false;
    features = [
      "default"
      "builtin-but"
      "disable-auto-updates"
      "packaged-but-distribution"
    ];
  };

  darwinAppAttrs = old: {
    installPhase = ''
      runHook preInstall

      app="$out/Applications/${appBundleName}"
      mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" "$out/bin"

      cat >"$app/Contents/Info.plist" <<EOF
      <?xml version="1.0" encoding="UTF-8"?>
      <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
      <plist version="1.0">
      <dict>
        <key>CFBundleDevelopmentRegion</key>
        <string>English</string>
        <key>CFBundleDisplayName</key>
        <string>${appName}</string>
        <key>CFBundleExecutable</key>
        <string>${appName}</string>
        <key>CFBundleIconFile</key>
        <string>${appName}</string>
        <key>CFBundleIdentifier</key>
        <string>com.gitbutler.app</string>
        <key>CFBundleInfoDictionaryVersion</key>
        <string>6.0</string>
        <key>CFBundleName</key>
        <string>${appName}</string>
        <key>CFBundlePackageType</key>
        <string>APPL</string>
        <key>CFBundleShortVersionString</key>
        <string>${version}</string>
        <key>CFBundleVersion</key>
        <string>${version}</string>
        <key>CFBundleURLTypes</key>
        <array>
          <dict>
            <key>CFBundleTypeRole</key>
            <string>Editor</string>
            <key>CFBundleURLSchemes</key>
            <array>
              <string>but</string>
            </array>
          </dict>
        </array>
        <key>LSApplicationCategoryType</key>
        <string>public.app-category.developer-tools</string>
        <key>LSMinimumSystemVersion</key>
        <string>12.0</string>
        <key>NSHighResolutionCapable</key>
        <true/>
      </dict>
      </plist>
      EOF

      cp "$PWD/target/bin/gitbutler-tauri" "$app/Contents/MacOS/${appName}"
      cp "${butDrv}/bin/but" "$out/bin/but"
      cp "${askpassDrv}/bin/gitbutler-git-askpass" "$app/Contents/MacOS/gitbutler-git-askpass"
      cp "${patchedSrc}/crates/gitbutler-tauri/icons/release/icon.icns" \
        "$app/Contents/Resources/${appName}.icns"
      ln -s "$app/Contents/MacOS/${appName}" "$out/bin/${pname}"

      runHook postInstall
    '';

    doInstallCheck = true;
    installCheckPhase = (old.installCheckPhase or "") + ''
      runHook preInstallCheck

      test -x "$out/Applications/${appBundleName}/Contents/MacOS/${appName}"
      test -x "$out/Applications/${appBundleName}/Contents/MacOS/gitbutler-git-askpass"
      test -x "$out/bin/but"
      test -L "$out/bin/${pname}"

      runHook postInstallCheck
    '';
  };

  linuxAppAttrs = old: {
    installPhase = ''
      runHook preInstall

      install -Dm755 "$PWD/target/bin/gitbutler-tauri" "$out/bin/${pname}"
      install -Dm755 "${butDrv}/bin/but" "$out/bin/but"
      install -Dm755 "${askpassDrv}/bin/gitbutler-git-askpass" "$out/bin/gitbutler-git-askpass"

      runHook postInstall
    '';

    doInstallCheck = true;
    installCheckPhase = (old.installCheckPhase or "") + ''
      runHook preInstallCheck

      test -x "$out/bin/${pname}"
      test -x "$out/bin/but"
      test -x "$out/bin/gitbutler-git-askpass"

      runHook postInstallCheck
    '';
  };

  gitbutlerApp = gitbutlerDrv.overrideAttrs (
    old:
    if stdenv.hostPlatform.isDarwin then
      darwinAppAttrs old
    else if stdenv.hostPlatform.isLinux then
      linuxAppAttrs old
    else
      { }
  );
in
if crate2nixSourceOnly then
  patchedSrc
else
  gitbutlerApp.overrideAttrs (_old: {
    inherit pname version;
    name = "${pname}-${version}";
    src = patchedSrc;

    passthru = {
      inherit
        askpassDrv
        butDrv
        cargoNix
        crateOverrides
        crate2nixSrc
        frontend
        patchedSrc
        ;

    }
    // lib.optionalAttrs stdenv.hostPlatform.isDarwin {
      macApp = {
        bundleName = appBundleName;
        bundleRelPath = "Applications/${appBundleName}";
        installMode = "copy";
      };
    };

    meta = with lib; {
      description = "Git client for simultaneous branches on top of Git";
      homepage = "https://github.com/gitbutlerapp/gitbutler";
      license = licenses.fsl11Mit;
      mainProgram = pname;
      platforms = [
        "aarch64-darwin"
        "x86_64-linux"
      ];
    };
  })
