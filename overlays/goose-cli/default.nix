{
  cargoNixFn ? import ./Cargo.nix,
  cargoNixSha256 ? builtins.hashFile "sha256" ./Cargo.nix,
  crateSourceInfo ? builtins.fromJSON (builtins.readFile ./crate-sources.json),
  final,
  inputs,
  prev,
  slib,
  sources,
  selfSource,
  ...
}:
let
  inherit (selfSource) version;

  upstreamSrc = inputs.goose;
  sourceVersionCheck =
    if inputs.goose.rev == selfSource.commit then
      true
    else
      throw ''
        overlays/goose-cli uses Goose input ${inputs.goose.rev},
        expected ${selfSource.commit} from sources.json
      '';
  gooseLogoSource = builtins.path {
    name = "goose-cli-logos";
    path = "${upstreamSrc}/documentation/static/img";
    filter =
      path: type:
      type == "directory"
      || builtins.elem (builtins.baseNameOf path) [
        "logo_dark.png"
        "logo_light.png"
      ];
  };

  # Goose <= 1.55.0 patches crates.io `v8` to its `vendor/v8` shim over the
  # pinned `v8-goose` fork; 1.56.0 replaced V8 with Boa. patch_source.py
  # validates the rest of the layout and rejects V8 without this patch entry.
  upstreamManifest = builtins.fromTOML (builtins.readFile "${upstreamSrc}/Cargo.toml");
  usesVendoredV8 = upstreamManifest ? patch.crates-io.v8;

  v8Source = sources.goose-v8;
  # The goose-v8 flake input is the evaluator-visible copy of the fork: it is
  # what GooseV8Updater tracks, and its Cargo.toml supplies the crate version
  # without import-from-derivation. The build uses the submodule-complete
  # overlays/goose-v8 fetch instead, which the flake input cannot provide.
  v8ManifestVersion =
    (builtins.fromTOML (builtins.readFile "${inputs.goose-v8}/Cargo.toml")).package.version;
  v8SourceVersionCheck =
    if inputs.goose-v8.rev == v8Source.version then
      true
    else
      throw ''
        overlays/goose-cli uses goose-v8 input ${inputs.goose-v8.rev},
        expected ${v8Source.version} from sources.json
      '';

  prebuiltV8 =
    if prev.stdenv.hostPlatform.isLinux then
      slib.mkRustyV8PrebuiltArtifacts {
        pkgs = prev;
        name = "goose-cli-v8";
        releaseVersion = v8ManifestVersion;
        archiveHash =
          slib.sourceHashForPlatform "goose-v8" "rustyV8ArchiveHash"
            prev.stdenv.hostPlatform.system;
        bindingHash =
          slib.sourceHashForPlatform "goose-v8" "rustyV8BindingHash"
            prev.stdenv.hostPlatform.system;
      }
    else
      null;

  # Single owner of the recursive fork fetch, mirroring codex and codex-v8.
  rustyV8Src = final.goose-v8;

  # librusty_v8 platform policy. mkRustyV8Build returns a GN/Ninja nativeDrv
  # on both Darwin and Linux, and a non-null nativeDrv takes precedence, so
  # both platforms currently build V8 from source and `prebuiltV8` (Linux)
  # is a fallback only. On Darwin the source build is kept because:
  # - the denoland v145.0.0 release archives are built with Chromium's own
  #   rust/clang toolchain, while this build pins rust_sysroot_absolute and
  #   clang_base_path to the Nix toolchain that links the rest of goose-cli;
  # - the shared allocator, whole-archive, and Apple host-build-tools patches
  #   in lib/rusty-v8 only apply to a source build.
  # The fork (jh-block/rusty_v8 dbb64c2) is v145.0.0 plus one build.rs-only
  # commit (MSVC CRT detection), so the upstream aarch64-apple-darwin archive
  # may be ABI-compatible. Switching requires a full aarch64-darwin goose-cli
  # build and link against it; that has not been verified.

  v8Build = slib.mkRustyV8Build {
    pkgs = prev;
    name = "goose-cli-v8";
    inherit (v8Source) version;
    inherit rustyV8Src;
    extraPatches = [ ./rusty-v8-goose-rename.patch ];
    prebuiltArtifacts = prebuiltV8;
  };
  patchedV8Src = v8Build.patchedSrc;
  v8NativeDrv = v8Build.nativeDrv;
  inherit (v8Build) chromiumToolchainBundle;

  cargoNixGooseVersion = cargoNix.internal.crates."goose-cli".version;
  cargoNixHasV8Goose = cargoNix.internal.crates ? "v8-goose";
  cargoNixVersionCheck =
    if cargoNixGooseVersion == version then
      true
    else
      throw ''
        overlays/goose-cli/Cargo.nix has goose-cli version ${cargoNixGooseVersion},
        expected ${version}; regenerate Cargo.nix
      '';
  cargoNixV8Check =
    if !usesVendoredV8 then
      if cargoNixHasV8Goose then
        throw ''
          overlays/goose-cli/Cargo.nix has a v8-goose crate, but Goose ${version}
          does not vendor V8; regenerate Cargo.nix
        ''
      else
        true
    else if !cargoNixHasV8Goose then
      throw ''
        overlays/goose-cli/Cargo.nix has no v8-goose crate, but Goose ${version}
        vendors V8; regenerate Cargo.nix
      ''
    else if cargoNix.internal.crates."v8-goose".version == v8ManifestVersion then
      true
    else
      throw ''
        overlays/goose-cli/Cargo.nix has v8-goose version ${cargoNix.internal.crates."v8-goose".version},
        expected ${v8ManifestVersion}; regenerate Cargo.nix
      '';

  # Keep the fully prepared workspace as an update-time artifact for crate2nix
  # regeneration. Production crate sources are filtered directly from the
  # evaluator-visible flake input below, then patched per crate at build time.
  patchedSrc =
    prev.runCommand "goose-cli-${version}-src"
      {
        nativeBuildInputs = [
          prev.python3
        ];
      }
      (
        ''
          cp -r ${upstreamSrc} $out
          chmod -R u+w $out
        ''
        + prev.lib.optionalString usesVendoredV8 ''

          mkdir -p $out/vendor
          cp -r ${patchedV8Src} $out/vendor/v8-goose-src
          chmod -R u+w $out/vendor/v8-goose-src
        ''
        + ''

          PYTHONPATH=${
            import ../../lib/codemods-pythonpath.nix { inherit (prev) lib; }
          } ${prev.lib.getExe prev.python3} ${./patch_source.py} "$out"

          cp ${./crate-hashes.json} $out/crate-hashes.json
        ''
      );

  crateSource = (import ../../lib/crate2nix-source-slice.nix).sourceFor {
    rootSrc = upstreamSrc;
    source = {
      inherit cargoNixSha256;
      input = "goose";
      narHash = inputs.goose.narHash;
      subdir = ".";
    };
    sourceInfo = crateSourceInfo;
  };

  # Generated by crate2nix and post-processed so the checked-in file can point
  # at a separate patched source tree via rootSrc. See README.md for regen flow.
  cargoNix = cargoNixFn {
    inherit crateSource;
    pkgs = prev;
    rootSrc = upstreamSrc;
  };

  # Native and build inputs needed by the Goose CLI crate.
  gooseCliOverride = attrs: {
    src = prev.runCommand "goose-cli-${attrs.version}-src" { nativeBuildInputs = [ prev.python3 ]; } ''
      cp -R ${attrs.src} "$out"
      chmod -R u+w "$out"

      PYTHONPATH=${
        import ../../lib/codemods-pythonpath.nix { inherit (prev) lib; }
      } ${prev.lib.getExe prev.python3} ${./patch_source.py} \
        --goose-cli-crate \
        --logo-source-dir ${gooseLogoSource} \
        "$out"
    '';

    nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [
      prev.pkg-config
      prev.protobuf
    ];

    buildInputs =
      (attrs.buildInputs or [ ])
      ++ [
        prev.dbus
        prev.openssl
      ]
      ++ prev.lib.optionals prev.stdenv.hostPlatform.isLinux [ prev.libxcb ];

    LIBCLANG_PATH = "${prev.lib.getLib prev.llvmPackages.libclang}/lib";
  };

  # xcap only needs the extra X11 bits on Linux.
  xcapLinuxOverride =
    attrs:
    prev.lib.optionalAttrs prev.stdenv.hostPlatform.isLinux {
      buildInputs = (attrs.buildInputs or [ ]) ++ [ prev.libxcb ];
    };

  # llama-cpp-sys-2 expects Cargo-style env plus native CMake tooling that
  # buildRustCrate/crate2nix do not provide out of the box.
  llamaCppSysOverride = attrs: {
    nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [
      prev.cmake
      prev.pkg-config
      prev.rustPlatform.bindgenHook
    ];

    # buildRustCrate doesn't currently export CARGO_CFG_TARGET_FEATURE even
    # though Cargo build scripts normally see it. llama-cpp-sys uses that env
    # var only to opt into extra x86 GGML toggles, so an empty string is a safe
    # fallback on our darwin/aarch64 builds.
    CARGO_CFG_TARGET_FEATURE = "";
    CMAKE_BUILD_PARALLEL_LEVEL = "1";
    LIBCLANG_PATH = "${prev.lib.getLib prev.llvmPackages.libclang}/lib";
    NIX_CC_WRAPPER_SUPPRESS_TARGET_WARNING = "1";
  };

  # hipstr 0.6.0's serde path assumes serde_bytes is available; patch the
  # transitive crate instead of carrying a Goose-specific workaround.
  hipstrOverride = attrs: {
    patches = (attrs.patches or [ ]) ++ [ ./hipstr-no-serde-bytes.patch ];
  };

  # crate2nix 0.15 drops Cargo's package.rust_version metadata. Restore the
  # authoritative values from the normalized manifests for the two locked
  # bitcoin-internals releases so buildRustCrate exports the same
  # CARGO_PKG_RUST_VERSION as Cargo. Keep this fail-closed: later releases must
  # be reviewed against their own manifest before entering the build graph.
  bitcoinInternalsRustVersionPinPrefix = "bitcoinInternals.";
  bitcoinInternalsRustVersions =
    prev.lib.mapAttrs'
      (
        pinName: rustVersion:
        prev.lib.nameValuePair (prev.lib.removePrefix bitcoinInternalsRustVersionPinPrefix pinName) rustVersion
      )
      (
        prev.lib.filterAttrs (
          pinName: _value: prev.lib.hasPrefix bitcoinInternalsRustVersionPinPrefix pinName
        ) selfSource.pins
      );

  bitcoinInternalsOverride = attrs: {
    "rust-version" =
      bitcoinInternalsRustVersions.${attrs.version}
        or (throw "review bitcoin-internals ${attrs.version} rust-version metadata");
  };

  # rmcp uses env! for Cargo package vars during compilation, but
  # buildRustCrate does not export those automatically.
  rmcpOverride =
    attrs:
    assert attrs ? crateName;
    assert attrs ? version;
    {
      CARGO_CRATE_NAME = attrs.crateName;
      CARGO_PKG_VERSION = attrs.version;
    };

  # V8 crate override: delegates to the shared rusty-v8 builder.
  v8GooseOverride = v8Build.mkCrateOverride;

  # Keep all build-system compatibility shims in one place so version bumps can
  # be audited crate-by-crate.
  crateOverrides =
    prev.defaultCrateOverrides
    // {
      bitcoin-internals = bitcoinInternalsOverride;
      goose-cli = gooseCliOverride;
      hipstr = hipstrOverride;
      llama-cpp-sys-2 = llamaCppSysOverride;
      rmcp = rmcpOverride;
      xcap = xcapLinuxOverride;
    }
    // prev.lib.optionalAttrs usesVendoredV8 {
      v8-goose = v8GooseOverride;
    };

  # Build the Goose CLI workspace member, including its install-check smoke tests.
  gooseCliDrv = cargoNix.workspaceMembers.goose-cli.build.override {
    inherit crateOverrides;
    runTests = false;
  };

  gooseCliDrvChecked = gooseCliDrv.overrideAttrs (old: {
    doInstallCheck = true;
    nativeInstallCheckInputs = (old.nativeInstallCheckInputs or [ ]) ++ [ prev.coreutils ];
    installCheckPhase = ''
      runHook preInstallCheck

      export HOME="$(${prev.coreutils}/bin/mktemp -d)"
      export XDG_CACHE_HOME="$(${prev.coreutils}/bin/mktemp -d)"
      export XDG_CONFIG_HOME="$(${prev.coreutils}/bin/mktemp -d)"
      export XDG_DATA_HOME="$(${prev.coreutils}/bin/mktemp -d)"
      export XDG_STATE_HOME="$(${prev.coreutils}/bin/mktemp -d)"

      $out/bin/goose --version
      $out/bin/goose info
      $out/bin/goose serve --help

      runHook postInstallCheck
    '';
  });
in
{
  goose-cli-crate2nix-src = patchedSrc;
  goose-cli =
    assert sourceVersionCheck;
    assert v8SourceVersionCheck;
    assert cargoNixVersionCheck;
    assert cargoNixV8Check;
    prev.symlinkJoin {
      name = "goose-cli-${version}";
      paths = [ gooseCliDrvChecked ];
      nativeBuildInputs = [ prev.installShellFiles ];

      postBuild = ''
        rm -f $out/bin/generate_manpages

        export HOME="$TMPDIR/home"
        export XDG_CACHE_HOME="$TMPDIR/xdg-cache"
        export XDG_CONFIG_HOME="$TMPDIR/xdg-config"
        export XDG_DATA_HOME="$TMPDIR/xdg-data"
        export XDG_STATE_HOME="$TMPDIR/xdg-state"
        mkdir -p \
          "$HOME" \
          "$XDG_CACHE_HOME" \
          "$XDG_CONFIG_HOME" \
          "$XDG_DATA_HOME" \
          "$XDG_STATE_HOME"

        installShellCompletion --cmd goose \
          --bash <($out/bin/goose completion bash) \
          --fish <($out/bin/goose completion fish) \
          --zsh <($out/bin/goose completion zsh)
      '';

      passthru = {
        inherit
          cargoNix
          crateOverrides
          patchedSrc
          patchedV8Src
          version
          chromiumToolchainBundle
          v8Build
          ;
        gooseCliDrv = gooseCliDrvChecked;
        inherit v8NativeDrv;
      };

      meta = {
        description = "Open-source, extensible AI agent that goes beyond code suggestions - install, execute, edit, and test with any LLM";
        homepage = "https://github.com/aaif-goose/goose";
        license = prev.lib.licenses.asl20;
        mainProgram = "goose";
        platforms = [
          "aarch64-darwin"
          "x86_64-linux"
        ];
      };
    };
}
