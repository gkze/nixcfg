{
  pkgs,
  inputs,
  lib,
  symlinkJoin,
  makeFontsConf,
  git,
  cmake,
  curl,
  perl,
  pkg-config,
  protobuf,
  xcodebuild,
  fontconfig,
  freetype,
  imagemagick,
  libicns,
  libgit2,
  openssl,
  sqlite,
  zlib,
  zstd,
  apple-sdk_15,
  alsa-lib,
  darwinMinVersionHook,
  envsubst,
  glib,
  libdrm,
  libgbm,
  libglvnd,
  libva,
  libxcomposite,
  libxdamage,
  libxext,
  libxfixes,
  libxkbcommon,
  libxrandr,
  lld,
  libx11,
  libxcb,
  makeWrapper,
  nodejs_22,
  python3,
  vulkan-loader,
  wayland,
  crate2nixSourceOnly ? false,
  ...
}:
let
  pname = "zed-editor-nightly";
  version = "unstable-${inputs.zed.shortRev or (builtins.substring 0 8 inputs.zed.rev)}";
  src = inputs.zed;
  zedManifest = builtins.fromTOML (builtins.readFile "${src}/crates/zed/Cargo.toml");
  appVersion = zedManifest.package.version;
  releaseChannel = "nightly";
  rustToolchainChannel = (builtins.fromTOML (builtins.readFile "${src}/rust-toolchain.toml")).toolchain.channel;
  rustToolchain = pkgs.rust-bin.stable.${rustToolchainChannel}.default;
  zedRustPlatform = pkgs.makeRustPlatform {
    cargo = rustToolchain;
    rustc = rustToolchain;
  };
  zedBuildRustCrate = pkgs.buildRustCrate.override {
    cargo = rustToolchain;
    rustc = rustToolchain;
  };

  copyZedManifestFor = crateName: ''
    if [ -d "$workspaceRoot/crates/${crateName}" ]; then
      cp "$workspaceRoot/crates/zed/Cargo.toml" "$workspaceRoot/crates/${crateName}/zed-Cargo.toml"
    fi
  '';

  patchIfExists = relPath: body: ''
    if [ -f "$workspaceRoot/${relPath}" ]; then
      ${body}
    fi
  '';

  preparedWorkspaceInputs = ''
    cp -r "$workspaceRoot/assets" "$workspaceRoot/crates/assets/workspace-assets"
    cp -r "$workspaceRoot/assets" "$workspaceRoot/crates/settings/workspace-assets"
    cp -r "$workspaceRoot/crates/extension_api/wit" "$workspaceRoot/crates/extension_host/workspace-extension-api-wit"
    cp -r "$workspaceRoot/crates/gpui" "$workspaceRoot/crates/gpui_macos/workspace-gpui"
    cp "$workspaceRoot/crates/git_ui/src/commit_message_prompt.txt" "$workspaceRoot/crates/prompt_store/commit_message_prompt.txt"
    cp "$workspaceRoot/script/uninstall.sh" "$workspaceRoot/crates/cli/uninstall.sh"
    cp "$workspaceRoot/crates/zed/RELEASE_CHANNEL" "$workspaceRoot/crates/release_channel/RELEASE_CHANNEL"

    if [ -d "$workspaceRoot/crates/edit_prediction_cli" ]; then
      if [ -d "$workspaceRoot/crates/grammars/src" ]; then
        cp -r "$workspaceRoot/crates/grammars/src" "$workspaceRoot/crates/edit_prediction_cli/workspace-language-configs-src"
      elif [ -d "$workspaceRoot/crates/languages/src" ]; then
        cp -r "$workspaceRoot/crates/languages/src" "$workspaceRoot/crates/edit_prediction_cli/workspace-language-configs-src"
      fi
    fi

    ${copyZedManifestFor "remote_server"}
    ${copyZedManifestFor "edit_prediction_cli"}
    ${copyZedManifestFor "eval"}
    ${copyZedManifestFor "eval_cli"}
  '';

  preparedWorkspacePatches = ''
    substituteInPlace "$workspaceRoot/crates/release_channel/src/lib.rs" \
      --replace-fail 'include_str!("../../zed/RELEASE_CHANNEL")' 'include_str!("../RELEASE_CHANNEL")'

    substituteInPlace "$workspaceRoot/crates/assets/src/assets.rs" \
      --replace-fail '#[folder = "../../assets"]' '#[folder = "workspace-assets"]' \
      --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};' \
      --replace-fail ".filter_map(|p| {" ".filter_map(|p: std::borrow::Cow<'static, str>| {"

    substituteInPlace "$workspaceRoot/crates/settings/src/settings.rs" \
      --replace-fail '#[folder = "../../assets"]' '#[folder = "workspace-assets"]' \
      --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};'

    substituteInPlace "$workspaceRoot/crates/prompt_store/src/prompt_store.rs" \
      --replace-fail 'include_str!("../../git_ui/src/commit_message_prompt.txt")' 'include_str!("../commit_message_prompt.txt")'

    substituteInPlace "$workspaceRoot/crates/extension_host/build.rs" \
      --replace-fail 'PathBuf::from("../extension_api/wit")' 'PathBuf::from("workspace-extension-api-wit")'

    for path in "$workspaceRoot"/crates/extension_host/src/wasm_host/wit/since_v*.rs; do
      substituteInPlace "$path" \
        --replace-fail 'path: "../extension_api/wit/' 'path: "workspace-extension-api-wit/'
    done

    ${patchIfExists "crates/remote_server/build.rs" ''
      substituteInPlace "$workspaceRoot/crates/remote_server/build.rs" \
        --replace-fail 'include_str!("../zed/Cargo.toml")' 'include_str!("./zed-Cargo.toml")'
    ''}

    ${patchIfExists "crates/edit_prediction_cli/build.rs" ''
      substituteInPlace "$workspaceRoot/crates/edit_prediction_cli/build.rs" \
        --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'
    ''}

    ${patchIfExists "crates/eval/build.rs" ''
      substituteInPlace "$workspaceRoot/crates/eval/build.rs" \
        --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'
    ''}

    ${patchIfExists "crates/eval_cli/build.rs" ''
      substituteInPlace "$workspaceRoot/crates/eval_cli/build.rs" \
        --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")' \
        --replace-fail 'println!("cargo:rerun-if-changed=../zed/Cargo.toml");' 'println!("cargo:rerun-if-changed=./zed-Cargo.toml");'
    ''}

    ${patchIfExists "crates/edit_prediction_cli/src/filter_languages.rs" ''
      if grep -Fq '#[folder = "../grammars/src/"]' "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs"; then
        substituteInPlace "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" \
          --replace-fail '#[folder = "../grammars/src/"]' '#[folder = "workspace-language-configs-src/"]'
      elif grep -Fq '#[folder = "../languages/src/"]' "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs"; then
        substituteInPlace "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" \
          --replace-fail '#[folder = "../languages/src/"]' '#[folder = "workspace-language-configs-src/"]'
      fi

      if grep -Fq 'concat!(env!("CARGO_MANIFEST_DIR"), "/../grammars/src")' "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs"; then
        substituteInPlace "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" \
          --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../grammars/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'
      elif grep -Fq 'concat!(env!("CARGO_MANIFEST_DIR"), "/../languages/src")' "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs"; then
        substituteInPlace "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" \
          --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../languages/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'
      fi
    ''}

    substituteInPlace "$workspaceRoot/crates/cli/src/main.rs" \
      --replace-fail 'include_bytes!("../../../script/uninstall.sh")' 'include_bytes!("../uninstall.sh")'

    substituteInPlace "$workspaceRoot/crates/inspector_ui/build.rs" \
      --replace-fail '    let mut path = std::path::PathBuf::from(&cargo_manifest_dir);' '    println!("cargo:rustc-env=ZED_REPO_DIR={}", cargo_manifest_dir);
        return;

        let mut path = std::path::PathBuf::from(&cargo_manifest_dir);'

    substituteInPlace "$workspaceRoot/crates/gpui_macos/build.rs" \
      --replace-fail '        gpui::GPUI_MANIFEST_DIR.into()' '        PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("workspace-gpui")'
  '';

  patchedSrc = pkgs.stdenvNoCC.mkDerivation {
    pname = "${pname}-src";
    inherit version src;
    patches = [
      ./stable-wasi-sdk-asset-selection.patch
      ./stable-client-telemetry-os-version.patch
    ];

    postPatch = ''
      workspaceRoot="$PWD"
      {
        printf '# ###### THEME LICENSES ######\n\n'
        cat "$workspaceRoot/assets/themes/LICENSES"
        printf '\n# ###### ICON LICENSES ######\n\n'
        cat "$workspaceRoot/assets/icons/LICENSES"
        printf '\n# ###### CODE LICENSES ######\n\n'
        printf 'Generated in Nix packaging; cargo-about step is pending.\n'
      } > "$workspaceRoot/assets/licenses.md"

      printf '${releaseChannel}\n' > "$workspaceRoot/crates/zed/RELEASE_CHANNEL"

      ${preparedWorkspaceInputs}
      ${preparedWorkspacePatches}
    '';

    installPhase = ''
      runHook preInstall

      mkdir -p "$out"
      cp -R . "$out/"

      runHook postInstall
    '';

    dontConfigure = true;
    dontBuild = true;
    dontFixup = true;
    dontPatchShebangs = true;
  };

  cargoNix = import ./Cargo.nix {
    inherit pkgs;
    rootSrc = patchedSrc;
    buildRustCrateForPkgs = _: zedBuildRustCrate;
  };
  cargoNixVersion = cargoNix.internal.crates.zed.version;
  cargoNixVersionCheck =
    if cargoNixVersion == appVersion then
      true
    else
      throw ''
        packages/zed-editor-nightly/Cargo.nix has zed version ${cargoNixVersion},
        expected ${appVersion}; regenerate Cargo.nix
      '';

  livekitLibwebrtc =
    let
      upstreamLivekitLibwebrtc = pkgs.callPackage "${src}/nix/livekit-libwebrtc/package.nix" { };
    in
    if pkgs.stdenv.hostPlatform.isLinux then
      upstreamLivekitLibwebrtc.overrideAttrs (old: {
        gnFlags = builtins.filter (flag: flag != "rtc_use_pipewire=true") (old.gnFlags or [ ]) ++ [
          "rtc_use_pipewire=false"
        ];
        # Keep Linux CI/builder runs stable here; parallel livekit-libwebrtc
        # builds have been flaky enough in practice that serialized ninja is the
        # safer default until the underlying failure mode is better understood.
        ninjaFlags = [ "-j1" ] ++ (old.ninjaFlags or [ ]);
      })
    else
      upstreamLivekitLibwebrtc;
  gpuLib = vulkan-loader;

  commonNativeBuildInputs = [
    cmake
    curl
    perl
    pkg-config
    protobuf
    zedRustPlatform.bindgenHook
  ]
  ++ lib.optionals pkgs.stdenv.hostPlatform.isDarwin [
    # Zed's Darwin binary is large enough that nixpkgs' ld64 can fail to
    # synthesize ARM64 branch thunks.
    lld
    xcodebuild
  ];

  commonBuildInputs = [
    fontconfig
    freetype
    libgit2
    openssl
    sqlite
    zlib
    zstd
  ]
  ++ lib.optionals pkgs.stdenv.hostPlatform.isLinux [
    alsa-lib
    glib
    gpuLib
    libdrm
    libgbm
    libglvnd
    libva
    libxcomposite
    libxdamage
    libxext
    libxfixes
    libxkbcommon
    libxrandr
    libx11
    libxcb
  ]
  ++ lib.optionals pkgs.stdenv.hostPlatform.isDarwin [
    apple-sdk_15
    (darwinMinVersionHook "10.15")
  ];

  commonCrates =
    if pkgs.stdenv.hostPlatform.isLinux then
      # Linux builds compile external -sys crates (for X11/Wayland/GLib/etc.) as
      # standalone crate2nix derivations, so they need the shared pkg-config and
      # system library inputs as well.
      builtins.attrNames cargoNix.internal.crates
    else
      builtins.attrNames cargoNix.workspaceMembers;

  commonOverride = attrs: {
    nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ commonNativeBuildInputs;

    buildInputs = (attrs.buildInputs or [ ]) ++ commonBuildInputs;

    # Keep font discovery pointed at the raw flake input so cross-platform
    # evaluation does not need to realize the Darwin-only patched workspace
    # source derivation just to inspect the bundled fonts.
    FONTCONFIG_FILE = makeFontsConf {
      fontDirectories = [
        "${src}/assets/fonts/lilex"
        "${src}/assets/fonts/ibm-plex-sans"
      ];
    };
    LK_CUSTOM_WEBRTC = livekitLibwebrtc;
    NIX_LDFLAGS = lib.optionalString pkgs.stdenv.hostPlatform.isLinux "-rpath ${
      lib.makeLibraryPath [
        gpuLib
        wayland
        libva
      ]
    }";
    NIX_OUTPATH_USED_AS_RANDOM_SEED = "norebuilds";
    PROTOC = "${protobuf}/bin/protoc";
    RELEASE_VERSION = version;
    ZED_COMMIT_SHA = inputs.zed.rev or "";
    ZED_UPDATE_EXPLANATION = "Zed has been installed using Nix. Auto-updates have thus been disabled.";
    ZSTD_SYS_USE_PKG_CONFIG = true;
    dontPatchELF = pkgs.stdenv.hostPlatform.isLinux;
  };

  gpuiMacosOverride = attrs: {
    nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [ pkgs.rust-cbindgen ];
  };

  webrtcSysOverride = attrs: {
    dontCheckForBrokenSymlinks = true;
    patches = (attrs.patches or [ ]) ++ [ ./webrtc-sys-dynamic-libwebrtc.patch ];
  };

  documentedOverride = attrs: {
    postPatch = (attrs.postPatch or "") + ''
      substituteInPlace src/lib.rs \
        --replace-fail 'concat!("../", std::env!("CARGO_PKG_README"))' '"../README.md"'
    '';
  };

  # tooling/perf exposes both a lib target and an internal binary, but Zed only
  # needs the library via util_macros. Building the perf binary in the crate2nix
  # dependency graph creates an unnecessary out↔lib multi-output reference cycle
  # on Linux builders, so suppress it here.
  perfOverride = _attrs: {
    crateBin = [ ];
  };

  rav1eOverride = _attrs: {
    CARGO_ENCODED_RUSTFLAGS = "";
  };

  rmcpOverride =
    attrs:
    assert attrs ? crateName;
    assert attrs ? version;
    {
      CARGO_CRATE_NAME = attrs.crateName;
      CARGO_PKG_VERSION = attrs.version;
    };

  wasmtimeCApiImplOverride = attrs: {
    nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [ cmake ];
  };

  treeSitterOverride =
    attrs:
    let
      wasmtimeCApiIncludeDirs =
        lib.concatMapStringsSep " " (dep: "${dep.lib}/lib/wasmtime-c-api-impl.out/include")
          (builtins.filter (dep: (dep.crateName or "") == "wasmtime-c-api-impl") (attrs.dependencies or [ ]));
    in
    {
      nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [ python3 ];
      preConfigure = (attrs.preConfigure or "") + ''
        export DEP_WASMTIME_C_API_INCLUDE="${wasmtimeCApiIncludeDirs}"
        if [ -z "$DEP_WASMTIME_C_API_INCLUDE" ]; then
          echo "missing wasmtime-c-api-impl include path for tree-sitter" >&2
          exit 1
        fi
      '';
      postPatch = (attrs.postPatch or "") + ''
        ${lib.getExe python3} \
          ${./patch_tree_sitter_build_rs.py} \
          binding_rust/build.rs
      '';
    };

  zedLinuxInstallPhase = ''
    runHook preInstall

    mkdir -p "$out/bin" "$out/libexec"
    cp "$PWD/target/bin/zed" "$out/libexec/zed-editor"
    cp "${cliDrv}/bin/cli" "$out/bin/zed"
    ln -s "$out/bin/zed" "$out/bin/zeditor"

    install -D "${patchedSrc}/crates/zed/resources/app-icon-nightly@2x.png" \
      "$out/share/icons/hicolor/1024x1024@2x/apps/zed.png"
    install -D "${patchedSrc}/crates/zed/resources/app-icon-nightly.png" \
      "$out/share/icons/hicolor/512x512/apps/zed.png"

    (
      export DO_STARTUP_NOTIFY="true"
      export APP_CLI="zed"
      export APP_ICON="zed"
      export APP_NAME="Zed Nightly"
      export APP_ARGS="%U"
      mkdir -p "$out/share/applications"
      ${lib.getExe envsubst} < "${patchedSrc}/crates/zed/resources/zed.desktop.in" > \
        "$out/share/applications/dev.zed.Zed-Nightly.desktop"
      chmod +x "$out/share/applications/dev.zed.Zed-Nightly.desktop"
    )

    wrapProgram "$out/libexec/zed-editor" --suffix PATH : ${lib.makeBinPath [ nodejs_22 ]}

    runHook postInstall
  '';

  zedOverride = attrs: {
    # crate2nix does not provide Cargo's per-binary compile-time env here, but
    # Zed 1.3.0 now asserts that it matches paths::APP_NAME_LOWERCASE.
    CARGO_BIN_NAME = "zed";
    nativeBuildInputs =
      (attrs.nativeBuildInputs or [ ])
      ++ lib.optionals pkgs.stdenv.hostPlatform.isLinux [
        envsubst
        makeWrapper
      ];
    buildInputs = (attrs.buildInputs or [ ]) ++ lib.optionals pkgs.stdenv.hostPlatform.isDarwin [ git ];
    extraRustcOpts =
      (attrs.extraRustcOpts or [ ])
      ++ lib.optionals pkgs.stdenv.hostPlatform.isDarwin [
        "-C link-arg=-fuse-ld=${lld}/bin/ld64.lld"
      ];
    installPhase =
      if pkgs.stdenv.hostPlatform.isDarwin then
        ''
          runHook preInstall

          ${pkgs.stdenv.shell} ${./install_zed_nightly_app.sh} \
            "$out" \
            "$TMPDIR" \
            ${lib.escapeShellArg appVersion} \
            ${lib.escapeShellArg (toString patchedSrc)} \
            ${lib.escapeShellArg "${imagemagick}/bin/magick"} \
            ${lib.escapeShellArg "${libicns}/bin/png2icns"} \
            ${lib.escapeShellArg "${git}/bin/git"} \
            ${lib.escapeShellArg "${cliDrv}/bin/cli"} \
            "$PWD/target/bin/zed"

          runHook postInstall
        ''
      else
        zedLinuxInstallPhase;
  };

  commonCrateOverrides = lib.genAttrs commonCrates (_: commonOverride);

  crateOverrides =
    pkgs.defaultCrateOverrides
    // commonCrateOverrides
    // {
      documented = documentedOverride;
      "av-scenechange" = _attrs: {
        CARGO_ENCODED_RUSTFLAGS = "";
      };
      gpui_macos = attrs: (commonOverride attrs) // (gpuiMacosOverride attrs);
      perf = attrs: (commonOverride attrs) // (perfOverride attrs);
      rav1e = rav1eOverride;
      rmcp = attrs: (commonOverride attrs) // (rmcpOverride attrs);
      tree-sitter = treeSitterOverride;
      wasmtime-c-api-impl = wasmtimeCApiImplOverride;
      webrtc-sys = attrs: (commonOverride attrs) // (webrtcSysOverride attrs);
      zed = attrs: (commonOverride attrs) // (zedOverride attrs);
    };

  cliDrv = cargoNix.workspaceMembers.cli.build.override {
    inherit crateOverrides;
    runTests = false;
  };

  zedDrv = cargoNix.workspaceMembers.zed.build.override {
    inherit crateOverrides;
    runTests = false;
    features = [
      "default"
      "gpui_platform/runtime_shaders"
    ];
  };
  zedDrvChecked = zedDrv.overrideAttrs (old: {
    doInstallCheck = true;
    installCheckPhase =
      (old.installCheckPhase or "")
      + ''
        runHook preInstallCheck
      ''
      + lib.optionalString pkgs.stdenv.hostPlatform.isDarwin ''
        test -x "$out/Applications/Zed Nightly.app/Contents/MacOS/zed"
        test -L "$out/bin/zed"
        $out/bin/zed --help >/dev/null
      ''
      + lib.optionalString pkgs.stdenv.hostPlatform.isLinux ''
        test -x "$out/libexec/zed-editor"
        test -x "$out/bin/zed"
        test -L "$out/bin/zeditor"
        test -f "$out/share/applications/dev.zed.Zed-Nightly.desktop"
        $out/bin/zed --help >/dev/null
      ''
      + ''
        runHook postInstallCheck
      '';
  });
  guardedZedDrv =
    assert cargoNixVersionCheck;
    zedDrvChecked;
in
if crate2nixSourceOnly then
  patchedSrc
else
  symlinkJoin {
    name = "${pname}-${version}";
    paths = [ guardedZedDrv ];

    passthru = {
      inherit cargoNix crateOverrides patchedSrc;
      zedDrv = guardedZedDrv;
    }
    // lib.optionalAttrs pkgs.stdenv.hostPlatform.isDarwin {
      macApp = {
        bundleName = "Zed Nightly.app";
        bundleRelPath = "Applications/Zed Nightly.app";
        installMode = "copy";
      };
    };

    meta = {
      description = "High-performance, multiplayer code editor from the creators of Atom and Tree-sitter";
      homepage = "https://zed.dev";
      changelog = "https://zed.dev/releases/preview";
      license = lib.licenses.gpl3Only;
      mainProgram = "zed";
      # Keep the exported surface constrained to the repo's currently validated
      # primary Darwin/Linux outputs. The package expression still carries both
      # platform branches so additional architectures can be re-enabled once
      # corresponding builds are proven.
      platforms = [
        "aarch64-darwin"
        "x86_64-linux"
      ];
    };
  }
