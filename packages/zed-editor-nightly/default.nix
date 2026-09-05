{
  pkgs,
  inputs,
  lib,
  bash-dynamic-pipe-heredoc,
  symlinkJoin,
  makeFontsConf,
  git,
  cmake,
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
  crateCachePolicy = import ./crate-cache-policy.nix { inherit lib; };
  zedManifest = builtins.fromTOML (builtins.readFile "${src}/crates/zed/Cargo.toml");
  appVersion = zedManifest.package.version;
  releaseChannel = "nightly";
  rustToolchainChannel =
    (builtins.fromTOML (builtins.readFile "${src}/rust-toolchain.toml")).toolchain.channel;
  rustToolchain = pkgs.rust-bin.stable.${rustToolchainChannel}.default;
  zedRustPlatform = pkgs.makeRustPlatform {
    cargo = rustToolchain;
    rustc = rustToolchain;
  };
  zedBuildRustCrate = pkgs.buildRustCrate.override {
    cargo = rustToolchain;
    rustc = rustToolchain;
  };
  generatedLicenses = destination: ''
    {
      printf '# ###### THEME LICENSES ######\n\n'
      cat ${src}/assets/themes/LICENSES
      printf '\n# ###### ICON LICENSES ######\n\n'
      cat ${src}/assets/icons/LICENSES
      printf '\n# ###### CODE LICENSES ######\n\n'
      printf 'Generated in Nix packaging; cargo-about step is pending.\n'
    } > ${destination}
  '';

  workspaceAssets = ''
    cp -r ${src}/assets "$crateRoot/workspace-assets"
    chmod -R u+w "$crateRoot/workspace-assets"
    ${generatedLicenses ''"$crateRoot/workspace-assets/licenses.md"''}
  '';

  # Zed's asset embedding has existed in both a direct rust-embed form and the
  # newer util::fs_embed! wrapper. Relocate the declared crate-relative source
  # for either representation, and fail explicitly if upstream changes the
  # contract again.
  relocateWorkspaceAssets = sourceFile: legacyPreparation: ''
    if grep -Fq 'crate_relative = "../../assets"' ${sourceFile}; then
      substituteInPlace ${sourceFile} \
        --replace-fail 'crate_relative = "../../assets"' 'crate_relative = "workspace-assets"'
    elif grep -Fq '#[folder = "../../assets"]' ${sourceFile}; then
      substituteInPlace ${sourceFile} \
        --replace-fail '#[folder = "../../assets"]' '#[folder = "workspace-assets"]'
      ${legacyPreparation}
    else
      echo "unsupported Zed asset embedding contract in ${sourceFile}" >&2
      exit 1
    fi
  '';

  # crate2nix filters each workspace member independently. Keep that cache
  # boundary by preparing only the crates whose sources depend on files outside
  # their own directory, or which need a packaging patch. The same map also
  # drives the full prepared workspace used by update-time Cargo.nix generation.
  crateSourcePreparations = {
    assets = workspaceAssets + ''
      ${relocateWorkspaceAssets ''"$crateRoot/src/assets.rs"'' ''
        substituteInPlace "$crateRoot/src/assets.rs" \
          --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};' \
          --replace-fail ".filter_map(|p| {" ".filter_map(|p: std::borrow::Cow<'static, str>| {"
      ''}
    '';

    cli = ''
      cp ${src}/script/uninstall.sh "$crateRoot/uninstall.sh"
      substituteInPlace "$crateRoot/src/main.rs" \
        --replace-fail 'include_bytes!("../../../script/uninstall.sh")' 'include_bytes!("../uninstall.sh")'
    '';

    client = ''
      (cd "$crateRoot" && patch -p3 < ${./stable-client-telemetry-os-version.patch})
    '';

    edit_prediction_cli = ''
      cp ${src}/crates/zed/Cargo.toml "$crateRoot/zed-Cargo.toml"
      if [ -d ${src}/crates/grammars/src ]; then
        cp -r ${src}/crates/grammars/src "$crateRoot/workspace-language-configs-src"
      elif [ -d ${src}/crates/languages/src ]; then
        cp -r ${src}/crates/languages/src "$crateRoot/workspace-language-configs-src"
      fi

      substituteInPlace "$crateRoot/build.rs" \
        --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'

      if grep -Fq '#[folder = "../grammars/src/"]' "$crateRoot/src/filter_languages.rs"; then
        substituteInPlace "$crateRoot/src/filter_languages.rs" \
          --replace-fail '#[folder = "../grammars/src/"]' '#[folder = "workspace-language-configs-src/"]'
      elif grep -Fq '#[folder = "../languages/src/"]' "$crateRoot/src/filter_languages.rs"; then
        substituteInPlace "$crateRoot/src/filter_languages.rs" \
          --replace-fail '#[folder = "../languages/src/"]' '#[folder = "workspace-language-configs-src/"]'
      fi

      if grep -Fq 'concat!(env!("CARGO_MANIFEST_DIR"), "/../grammars/src")' "$crateRoot/src/filter_languages.rs"; then
        substituteInPlace "$crateRoot/src/filter_languages.rs" \
          --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../grammars/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'
      elif grep -Fq 'concat!(env!("CARGO_MANIFEST_DIR"), "/../languages/src")' "$crateRoot/src/filter_languages.rs"; then
        substituteInPlace "$crateRoot/src/filter_languages.rs" \
          --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../languages/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'
      fi
    '';

    eval = ''
      cp ${src}/crates/zed/Cargo.toml "$crateRoot/zed-Cargo.toml"
      substituteInPlace "$crateRoot/build.rs" \
        --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'
    '';

    eval_cli = ''
      cp ${src}/crates/zed/Cargo.toml "$crateRoot/zed-Cargo.toml"
      substituteInPlace "$crateRoot/build.rs" \
        --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")' \
        --replace-fail 'println!("cargo:rerun-if-changed=../zed/Cargo.toml");' 'println!("cargo:rerun-if-changed=./zed-Cargo.toml");'
    '';

    extension = ''
      (cd "$crateRoot" && patch -p3 < ${./stable-wasi-sdk-asset-selection.patch})
    '';

    extension_host = ''
      cp -r ${src}/crates/extension_api/wit "$crateRoot/workspace-extension-api-wit"
      substituteInPlace "$crateRoot/build.rs" \
        --replace-fail 'PathBuf::from("../extension_api/wit")' 'PathBuf::from("workspace-extension-api-wit")'
      for path in "$crateRoot"/src/wasm_host/wit/since_v*.rs; do
        substituteInPlace "$path" \
          --replace-fail 'path: "../extension_api/wit/' 'path: "workspace-extension-api-wit/'
      done
    '';

    gpui_apple = ''
      cp -r ${src}/crates/gpui "$crateRoot/workspace-gpui"
      if grep -Fq 'gpui::GPUI_MANIFEST_DIR.into()' "$crateRoot/build.rs"; then
        substituteInPlace "$crateRoot/build.rs" \
          --replace-fail 'gpui::GPUI_MANIFEST_DIR.into()' \
          'PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("workspace-gpui")'
      elif grep -Fq '.join("../gpui")' "$crateRoot/build.rs"; then
        substituteInPlace "$crateRoot/build.rs" \
          --replace-fail '.join("../gpui")' '.join("workspace-gpui")'
      else
        echo "unsupported Zed gpui_apple source-location contract" >&2
        exit 1
      fi
    '';

    inspector_ui = ''
      # Older revisions had an implicit Cargo build script that walked to the
      # repository root. Newer revisions removed it entirely.
      if [ -f "$crateRoot/build.rs" ]; then
        substituteInPlace "$crateRoot/build.rs" \
          --replace-fail '    let mut path = std::path::PathBuf::from(&cargo_manifest_dir);' '    println!("cargo:rustc-env=ZED_REPO_DIR={}", cargo_manifest_dir);
          return;

          let mut path = std::path::PathBuf::from(&cargo_manifest_dir);'
      fi
    '';

    prompt_store = ''
      cp ${src}/crates/git_ui/src/commit_message_prompt.txt "$crateRoot/commit_message_prompt.txt"
      substituteInPlace "$crateRoot/src/prompt_store.rs" \
        --replace-fail 'include_str!("../../git_ui/src/commit_message_prompt.txt")' 'include_str!("../commit_message_prompt.txt")'
    '';

    release_channel = ''
      printf '${releaseChannel}\n' > "$crateRoot/RELEASE_CHANNEL"
      substituteInPlace "$crateRoot/src/lib.rs" \
        --replace-fail 'include_str!("../../zed/RELEASE_CHANNEL")' 'include_str!("../RELEASE_CHANNEL")'
    '';

    remote_server = ''
      cp ${src}/crates/zed/Cargo.toml "$crateRoot/zed-Cargo.toml"
      substituteInPlace "$crateRoot/build.rs" \
        --replace-fail 'include_str!("../zed/Cargo.toml")' 'include_str!("./zed-Cargo.toml")'
    '';

    settings = workspaceAssets + ''
      ${relocateWorkspaceAssets ''"$crateRoot/src/settings.rs"'' ''
        substituteInPlace "$crateRoot/src/settings.rs" \
          --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};'
      ''}
    '';

    zed = ''
      printf '${releaseChannel}\n' > "$crateRoot/RELEASE_CHANNEL"
    '';
  };

  prepareCrateSource =
    attrs:
    pkgs.stdenvNoCC.mkDerivation {
      name = crateCachePolicy.preparedSourceName attrs.crateName;
      inherit (attrs) src;

      postPatch = ''
        crateRoot="$PWD"
        ${crateSourcePreparations.${attrs.crateName}}
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

  patchedSrc = pkgs.stdenvNoCC.mkDerivation {
    pname = "${pname}-src";
    inherit version src;
    postPatch = ''
      workspaceRoot="$PWD"
      ${generatedLicenses ''"$workspaceRoot/assets/licenses.md"''}
      ${lib.concatMapStringsSep "\n" (crateName: ''
        crateRoot="$workspaceRoot/crates/${crateName}"
        if [ -d "$crateRoot" ]; then
          ${crateSourcePreparations.${crateName}}
        fi
      '') (builtins.attrNames crateSourcePreparations)}
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

  crateSource = (import ../../lib/crate2nix-source-slice.nix).sourceFor {
    rootSrc = src;
    source = {
      cargoNixSha256 = builtins.hashFile "sha256" ./Cargo.nix;
      input = "zed";
      narHash = inputs.zed.narHash;
      subdir = ".";
    };
    sourceInfo = builtins.fromJSON (builtins.readFile ./crate-sources.json);
  };

  cargoNix = import ./Cargo.nix {
    inherit pkgs crateSource;
    # The generated graph must only traverse evaluator-visible sources. Crates
    # which need source surgery replace their own filtered source in
    # scopedOverride below; no build output is inspected during evaluation.
    rootSrc = src;
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

  zedBuildInputs = [
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
  ];

  # These affect Darwin compilation and minimum-version semantics in ways that
  # are not isolated to one audited build script. Keep their existing workspace
  # scope until real source builds prove a narrower boundary safe.
  darwinWorkspaceBuildInputs = lib.optionals pkgs.stdenv.hostPlatform.isDarwin [
    apple-sdk_15
    (darwinMinVersionHook "10.15")
  ];

  darwinWorkspaceCrates = lib.optionals pkgs.stdenv.hostPlatform.isDarwin (
    builtins.attrNames cargoNix.workspaceMembers ++ [ "webrtc-sys" ]
  );

  scopedCrates = lib.unique (
    builtins.attrNames crateSourcePreparations
    ++ crateCachePolicy.bindgenConsumers
    ++ crateCachePolicy.commitShaConsumers
    ++ crateCachePolicy.fontConfigConsumers
    ++ crateCachePolicy.lldConsumers
    ++ crateCachePolicy.livekitWebrtcConsumers
    ++ crateCachePolicy.pkgConfigConsumers
    ++ crateCachePolicy.protocConsumers
    ++ crateCachePolicy.releaseVersionConsumers
    ++ crateCachePolicy.systemLibraryConsumers
    ++ crateCachePolicy.updateExplanationConsumers
    ++ crateCachePolicy.xcodebuildConsumers
    ++ crateCachePolicy.zstdPkgConfigConsumers
    ++ darwinWorkspaceCrates
  );

  scopedOverride =
    attrs:
    let
      inherit (attrs) crateName;
    in
    {
      nativeBuildInputs =
        (attrs.nativeBuildInputs or [ ])
        ++
          lib.optionals
            (pkgs.stdenv.hostPlatform.isDarwin && builtins.elem crateName crateCachePolicy.bindgenConsumers)
            [
              zedRustPlatform.bindgenHook
            ]
        ++
          lib.optionals
            (pkgs.stdenv.hostPlatform.isLinux && builtins.elem crateName crateCachePolicy.pkgConfigConsumers)
            [
              pkg-config
            ]
        ++ lib.optionals (builtins.elem crateName crateCachePolicy.protocConsumers) [
          protobuf
        ]
        ++
          lib.optionals
            (pkgs.stdenv.hostPlatform.isDarwin && builtins.elem crateName crateCachePolicy.lldConsumers)
            [
              # Zed's Darwin binary is large enough that nixpkgs' ld64 can fail to
              # synthesize ARM64 branch thunks.
              lld
            ]
        ++
          lib.optionals
            (pkgs.stdenv.hostPlatform.isDarwin && builtins.elem crateName crateCachePolicy.xcodebuildConsumers)
            [
              xcodebuild
            ];

      buildInputs =
        (attrs.buildInputs or [ ])
        ++ lib.optionals (builtins.elem crateName crateCachePolicy.systemLibraryConsumers) zedBuildInputs
        ++ lib.optionals (builtins.elem crateName darwinWorkspaceCrates) darwinWorkspaceBuildInputs;
    }
    // lib.optionalAttrs (builtins.elem crateName crateCachePolicy.systemLibraryConsumers) {
      NIX_LDFLAGS = lib.optionalString pkgs.stdenv.hostPlatform.isLinux "-rpath ${
        lib.makeLibraryPath [
          gpuLib
          wayland
          libva
        ]
      }";
      NIX_OUTPATH_USED_AS_RANDOM_SEED = "norebuilds";
      dontPatchELF = pkgs.stdenv.hostPlatform.isLinux;
    }
    // crateCachePolicy.forCrate {
      inherit crateName;
      commitSha = inputs.zed.rev or "";
      fontConfig = makeFontsConf {
        fontDirectories = [
          "${src}/assets/fonts/lilex"
          "${src}/assets/fonts/ibm-plex-sans"
        ];
      };
      livekitWebrtc = livekitLibwebrtc;
      protoc = "${protobuf}/bin/protoc";
      releaseVersion = version;
      updateExplanation = "Zed has been installed using Nix. Auto-updates have thus been disabled.";
    }
    // lib.optionalAttrs (builtins.hasAttr attrs.crateName crateSourcePreparations) {
      src = prepareCrateSource attrs;
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
      nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [
        python3
      ];
      preConfigure = (attrs.preConfigure or "") + ''
        export DEP_WASMTIME_C_API_INCLUDE="${wasmtimeCApiIncludeDirs}"
        if [ -z "$DEP_WASMTIME_C_API_INCLUDE" ]; then
          echo "missing wasmtime-c-api-impl include path for tree-sitter" >&2
          exit 1
        fi
        PYTHONPATH=${import ../../lib/codemods-pythonpath.nix { inherit lib; }} ${lib.getExe python3} \
          ${./patch_tree_sitter_build_rs.py} \
          ${lib.escapeShellArg attrs.build}
      '';
    };

  zedLinuxInstallPhase = ''
    runHook preInstall

    mkdir -p "$out/bin" "$out/libexec"
    cp "$PWD/target/bin/zed" "$out/libexec/zed-editor"
    cp "${cliDrv}/bin/cli" "$out/bin/zed"
    ln -s "$out/bin/zed" "$out/bin/zeditor"

    install -D "${src}/crates/zed/resources/app-icon-nightly@2x.png" \
      "$out/share/icons/hicolor/1024x1024@2x/apps/zed.png"
    install -D "${src}/crates/zed/resources/app-icon-nightly.png" \
      "$out/share/icons/hicolor/512x512/apps/zed.png"

    (
      export DO_STARTUP_NOTIFY="true"
      export APP_CLI="zed"
      export APP_ICON="zed"
      export APP_NAME="Zed Nightly"
      export APP_ARGS="%U"
      mkdir -p "$out/share/applications"
      ${lib.getExe envsubst} < "${src}/crates/zed/resources/zed.desktop.in" > \
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

          # The installer embeds plist fragments through command substitutions
          # in a heredoc. Bash 5.3 can deadlock there after XNU reduces pipe
          # capacity, so scope the upstream backport to this one script.
          ${bash-dynamic-pipe-heredoc}/bin/bash ${./install_zed_nightly_app.sh} \
            "$out" \
            "$TMPDIR" \
            ${lib.escapeShellArg appVersion} \
            ${lib.escapeShellArg (toString src)} \
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

  composeCrateOverride =
    base: extension: attrs:
    let
      baseAttrs = base attrs;
    in
    baseAttrs // extension (attrs // baseAttrs);

  scopedThen = extension: composeCrateOverride scopedOverride extension;

  projectCrateOverrides = lib.genAttrs scopedCrates (_: scopedOverride) // {
    documented = documentedOverride;
    "av-scenechange" = _attrs: {
      CARGO_ENCODED_RUSTFLAGS = "";
    };
    perf = scopedThen perfOverride;
    rav1e = rav1eOverride;
    rmcp = scopedThen rmcpOverride;
    tree-sitter = treeSitterOverride;
    wasmtime-c-api-impl = wasmtimeCApiImplOverride;
    webrtc-sys = scopedThen webrtcSysOverride;
    zed = scopedThen zedOverride;
  };

  # Project policy extends rather than replaces nixpkgs's targeted defaults.
  # This keeps native OpenSSL/zstd/etc. inputs on their -sys consumers without
  # reintroducing them across every Zed workspace or registry crate.
  crateOverrides =
    pkgs.defaultCrateOverrides
    // lib.mapAttrs (
      crateName: override:
      composeCrateOverride (pkgs.defaultCrateOverrides.${crateName} or (_attrs: { })) override
    ) projectCrateOverrides;

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
