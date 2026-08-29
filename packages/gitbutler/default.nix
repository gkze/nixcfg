{
  apple-sdk_15 ? null,
  autoPatchelfHook ? null,
  curl,
  fetchPnpmDeps ? null,
  inputs,
  libayatana-appindicator,
  lib,
  libiconv ? null,
  librsvg,
  nodejs_22,
  outputs,
  pkgs,
  pkg-config,
  pnpmConfigHook,
  pnpm_10,
  python3,
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
  crateCachePolicy = import ./crate-cache-policy.nix { inherit lib; };
  nodejs = nodejs_22;
  pnpm = pnpm_10.override { nodejs-slim = nodejs; };

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

  crate2nixSrc =
    runCommand "${pname}-${version}-crate2nix-src" { nativeBuildInputs = [ python3 ]; }
      ''
        cp -r ${src} "$out"
        chmod -R u+w "$out"

        PYTHONPATH=${
          import ../../lib/codemods-pythonpath.nix { inherit lib; }
        } ${python3}/bin/python3 ${./patch_sources.py} "$out"

        rm -rf "$out/crates/gitbutler-tauri/frontend-dist"
        mkdir -p "$out/crates/gitbutler-tauri/frontend-dist"
        touch "$out/crates/gitbutler-tauri/frontend-dist/index.html"
      '';

  crateSource = (import ../../lib/crate2nix-source-slice.nix).sourceFor {
    rootSrc = src;
    source = {
      cargoNixSha256 = builtins.hashFile "sha256" ./Cargo.nix;
      input = "gitbutler";
      narHash = inputs.gitbutler.narHash;
      subdir = ".";
    };
    sourceInfo = builtins.fromJSON (builtins.readFile ./crate-sources.json);
  };

  cargoNix = import ./Cargo.nix {
    inherit pkgs crateSource;
    rootSrc = src;
  };

  patchedCrateSource =
    {
      crateName,
      includeFrontend ? false,
    }:
    runCommand (crateCachePolicy.patchedSourceName crateName) { nativeBuildInputs = [ python3 ]; } ''
      cp -r ${cargoNix.internal.crates.${crateName}.src} "$out"
      chmod -R u+w "$out"

      PYTHONPATH=${
        import ../../lib/codemods-pythonpath.nix { inherit lib; }
      } ${python3}/bin/python3 ${./patch_sources.py} "$out" ${lib.escapeShellArg crateName}

      ${lib.optionalString (crateName == "gitbutler-tauri") ''
        rm -rf "$out/frontend-dist"
        mkdir -p "$out/frontend-dist"
        ${
          if includeFrontend then
            ''
              cp -R ${frontend}/. "$out/frontend-dist/"
            ''
          else
            ''
              touch "$out/frontend-dist/index.html"
            ''
        }
      ''}
    '';

  butSrc = patchedCrateSource { crateName = "but"; };
  gitbutlerTauriSrc = patchedCrateSource {
    crateName = "gitbutler-tauri";
    includeFrontend = true;
  };

  appendCrateInputs =
    {
      buildInputs ? [ ],
      nativeBuildInputs ? [ ],
    }:
    attrs: {
      buildInputs = (attrs.buildInputs or [ ]) ++ buildInputs;
      nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ nativeBuildInputs;
    };

  cacheMetadataOverride =
    attrs:
    crateCachePolicy.forCrate {
      channel = "release";
      inherit (attrs) crateName;
      inherit version;
    };

  rmcpOverride = attrs: {
    CARGO_CRATE_NAME = attrs.crateName;
    CARGO_PKG_VERSION = attrs.version;
  };

  tauriOverrides = slib.mkCrate2nixTauriOverrides {
    inherit pkgs;
    pluginCrates = slib.tauriPluginEnvCrateNames ++ [
      "tauri-plugin-log"
      "tauri-plugin-trafficlights-positioner"
    ];
  };

  composeCrateOverrides =
    overrides: attrs: lib.foldl' (acc: override: acc // override (attrs // acc)) { } overrides;

  mergeCrateOverrideSets =
    overrideSets: lib.zipAttrsWith (_crateName: composeCrateOverrides) overrideSets;

  cacheMetadataOverrides = lib.genAttrs (lib.unique (
    crateCachePolicy.channelConsumers ++ crateCachePolicy.versionConsumers
  )) (_: cacheMetadataOverride);

  # Keep the existing deployment-target and iconv environment intact while the
  # lower-risk tool and library inputs move back to nixpkgs' scoped defaults.
  darwinPlatformOverrides = lib.optionalAttrs stdenv.hostPlatform.isDarwin (
    lib.genAttrs
      (lib.subtractLists (builtins.attrNames tauriOverrides) (
        builtins.attrNames cargoNix.internal.crates
      ))
      (
        _:
        appendCrateInputs {
          buildInputs = [
            apple-sdk_15
            libiconv
          ];
        }
      )
  );

  linuxProbeOverrides = lib.optionalAttrs stdenv.hostPlatform.isLinux {
    gtk = appendCrateInputs {
      nativeBuildInputs = [ pkg-config ];
      buildInputs = [ pkgs.gtk3 ];
    };
    x11-dl = appendCrateInputs {
      nativeBuildInputs = [ pkg-config ];
      buildInputs = [
        pkgs.libx11
        pkgs.libxcursor
        pkgs.libxi
        pkgs.libxrandr
        pkgs.libxrender
      ];
    };
  };

  nativeCrateOverrides = {
    but = appendCrateInputs { buildInputs = [ curl.out ]; };
    but-installer = appendCrateInputs { buildInputs = [ curl.out ]; };
    libgit2-sys = appendCrateInputs { buildInputs = [ zlib ]; };
    gitbutler-tauri = appendCrateInputs {
      nativeBuildInputs = lib.optionals stdenv.hostPlatform.isLinux [ wrapGAppsHook4 ];
      buildInputs = lib.optionals stdenv.hostPlatform.isLinux [
        libayatana-appindicator
        librsvg
        webkitgtk_4_1
      ];
    };
  };

  specialCrateOverrides = {
    but = _attrs: { src = butSrc; };
    gitbutler-tauri = _attrs: { src = gitbutlerTauriSrc; };
    openssl-sys = _attrs: {
      # crate2nix builds openssl-src separately, so its vendored source path is
      # gone by the time openssl-sys' build script runs.
      OPENSSL_NO_VENDOR = "1";
    };
    rmcp = rmcpOverride;
  };

  crateOverrides = mergeCrateOverrideSets [
    (builtins.removeAttrs pkgs.defaultCrateOverrides [
      "libgit2-sys"
      "libsqlite3-sys"
    ])
    cacheMetadataOverrides
    darwinPlatformOverrides
    linuxProbeOverrides
    nativeCrateOverrides
    tauriOverrides
    specialCrateOverrides
  ];

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

      substitute ${./Info.plist.in} "$app/Contents/Info.plist" \
        --replace-fail '@appName@' ${lib.escapeShellArg appName} \
        --replace-fail '@version@' ${lib.escapeShellArg version}

      cp "$PWD/target/bin/gitbutler-tauri" "$app/Contents/MacOS/${appName}"
      cp "${butDrv}/bin/but" "$out/bin/but"
      cp "${askpassDrv}/bin/gitbutler-git-askpass" "$app/Contents/MacOS/gitbutler-git-askpass"
      cp "${gitbutlerTauriSrc}/icons/release/icon.icns" \
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
  crate2nixSrc
else
  gitbutlerApp.overrideAttrs (_old: {
    inherit pname version;
    name = "${pname}-${version}";
    src = gitbutlerTauriSrc;

    passthru = {
      inherit
        askpassDrv
        butDrv
        cargoNix
        crateOverrides
        crate2nixSrc
        frontend
        gitbutlerTauriSrc
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
