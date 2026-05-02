{
  pkgs,
  inputs,
  outputs,
  lib,
  installShellFiles,
  makeBinaryWrapper,
  symlinkJoin,
  runCommand,
  ripgrep,
  python3,
  bubblewrap,
  libcap,
  crate2nixSourceOnly ? false,
  ...
}:
let
  slib = outputs.lib;
  version = slib.getFlakeVersion "codex";
  src = "${inputs.codex}/codex-rs";
  pythonForSourcePrep = python3.withPackages (ps: [ ps.tomlkit ]);

  # Reuse the shared codex-v8 overlay source so updates only touch one pin.
  v8Source = slib.sources.codex-v8;
  rustyV8Src = pkgs.codex-v8;
  v8ManifestVersion =
    (builtins.fromTOML (builtins.readFile "${rustyV8Src}/Cargo.toml")).package.version;
  prebuiltV8 =
    if pkgs.stdenv.hostPlatform.isLinux then
      slib.mkRustyV8PrebuiltArtifacts {
        inherit pkgs;
        name = "codex-v8";
        releaseVersion = v8ManifestVersion;
        archiveHash =
          slib.sourceHashForPlatform "codex-v8" "rustyV8ArchiveHash"
            pkgs.stdenv.hostPlatform.system;
        bindingHash =
          slib.sourceHashForPlatform "codex-v8" "rustyV8BindingHash"
            pkgs.stdenv.hostPlatform.system;
      }
    else
      null;
  v8Build = slib.mkRustyV8Build {
    inherit pkgs;
    name = "codex-v8";
    inherit (v8Source) version;
    inherit rustyV8Src;
    clangResourceVersion = "23";
    gnArgsOverrides = {
      # Our Nix-provided rustc + RUSTC_BOOTSTRAP=1 is nightly-capable, but GN
      # can't detect this since we supply rust_sysroot_absolute.
      rustc_nightly_capability = "true";
    };
    # v146.4.0's allocator uses #[linkage = "weak"] for shim symbols, but on
    # Darwin weak symbols in force-loaded static archives are not resolved
    # properly. Remove weak linkage so the symbols are strong externals.
    extraPatchCommands = ''
      ${pkgs.python3}/bin/python3 \
        ${./patch_allocator_weak_linkage.py} \
        "$out/build/rust/allocator/lib.rs"
    '';
    prebuiltArtifacts = prebuiltV8;
  };

  patchedSrc =
    runCommand "codex-${version}-src"
      {
        nativeBuildInputs = [ pythonForSourcePrep ];
      }
      ''
        cp -r ${src} "$out"
        chmod -R u+w "$out"
        if [ -f "${src}/node-version.txt" ]; then
          cp "${src}/node-version.txt" "$out/node-version.txt"
          cp "${src}/node-version.txt" "$out/core/node-version.txt"
          if [ -f "$out/core/src/tools/js_repl/mod.rs" ]; then
            substituteInPlace "$out/core/src/tools/js_repl/mod.rs" \
              --replace-fail '../../../../node-version.txt' '../../../node-version.txt'
          fi
        elif [ -f "$out/core/src/tools/js_repl/mod.rs" ]; then
          echo "codex js_repl source still exists, but ${src}/node-version.txt is missing" >&2
          exit 1
        fi

        ${pythonForSourcePrep}/bin/python3 \
          ${./patch_cargo_lock_version.py} \
          "$out/Cargo.lock" \
          ${lib.escapeShellArg version}
      '';

  cargoNix = import ./Cargo.nix {
    inherit pkgs;
    rootSrc = patchedSrc;
  };
  cargoNixVersion = cargoNix.internal.crates."codex-cli".version;
  cargoNixV8Version = cargoNix.internal.crates.v8.version;
  cargoNixVersionCheck =
    if cargoNixVersion == version then
      true
    else
      throw ''
        packages/codex/Cargo.nix has codex-cli version ${cargoNixVersion},
        expected ${version}; regenerate Cargo.nix
      '';
  cargoNixV8VersionCheck =
    if cargoNixV8Version == v8ManifestVersion then
      true
    else
      throw ''
        packages/codex/Cargo.nix has v8 version ${cargoNixV8Version},
        expected ${v8ManifestVersion}; regenerate Cargo.nix
      '';

  crosstermOverride = attrs: {
    postUnpack = (attrs.postUnpack or "") + ''
      mkdir -p "$sourceRoot/examples/interactive-demo"
      touch "$sourceRoot/examples/interactive-demo/README.md"
    '';
  };

  rmcpOverride =
    attrs:
    assert attrs ? crateName;
    assert attrs ? version;
    {
      CARGO_CRATE_NAME = attrs.crateName;
      CARGO_PKG_VERSION = attrs.version;
    };

  runfilesOverride = attrs: {
    src = "${attrs.src}/rust/runfiles";
  };

  codexLinuxSandboxOverride =
    attrs:
    lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
      nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [ pkgs.pkg-config ];
      buildInputs = (attrs.buildInputs or [ ]) ++ [ libcap ];
      postUnpack = (attrs.postUnpack or "") + ''
        vendor_dir="$(dirname "$sourceRoot")/vendor"
        mkdir -p "$vendor_dir"
        ln -s ${patchedSrc}/vendor/bubblewrap "$vendor_dir/bubblewrap"
      '';
    };

  codexLinuxLowMemoryOverride =
    _attrs:
    lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
      codegenUnits = 16;
    };

  # Prebuilt WebRTC libraries for the webrtc-sys crate. The crate's build.rs
  # tries to download these at build time via scratch::path(), which fails in
  # the Nix sandbox. Setting LK_CUSTOM_WEBRTC skips the download.
  webrtcPrebuilt = pkgs.fetchzip (
    if pkgs.stdenv.hostPlatform.isLinux then
      {
        url = "https://github.com/livekit/rust-sdks/releases/download/webrtc-24f6822-2/webrtc-linux-x64-release.zip";
        hash = "sha256-aR76GGfK2UJheN5nI10e2f8CZPgxMxqlEPxyWc95AQ0=";
      }
    else
      {
        url = "https://github.com/livekit/rust-sdks/releases/download/webrtc-24f6822-2/webrtc-mac-arm64-release.zip";
        hash = "sha256-4IwJM6EzTFgQd2AdX+Hj9NWzmyqXrSioRax2L6GKL1U=";
      }
  );
  webrtcSysOverride = _attrs: {
    LK_CUSTOM_WEBRTC = "${webrtcPrebuilt}";
    # cxx-build creates a symlink to cxx.h that lands outside $lib, triggering
    # the noBrokenSymlinks fixup check. Disable it for this crate.
    dontCheckForBrokenSymlinks = true;
  };

  crateOverrides = pkgs.defaultCrateOverrides // {
    codex-app-server-protocol = codexLinuxLowMemoryOverride;
    crossterm = crosstermOverride;
    codex-linux-sandbox = codexLinuxSandboxOverride;
    rmcp = rmcpOverride;
    runfiles = runfilesOverride;
    v8 = v8Build.mkCrateOverride;
    webrtc-sys = webrtcSysOverride;
  };

  codexDrv = cargoNix.workspaceMembers.codex-cli.build.override {
    inherit crateOverrides;
    runTests = false;
  };
  codexDrvChecked = codexDrv.overrideAttrs (old: {
    doInstallCheck = true;
    installCheckPhase = (old.installCheckPhase or "") + ''
      runHook preInstallCheck

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

      $out/bin/codex --version
      $out/bin/codex --help >/dev/null

      runHook postInstallCheck
    '';
  });
  guardedCodexDrv =
    assert cargoNixVersionCheck;
    assert cargoNixV8VersionCheck;
    codexDrvChecked;
in
if crate2nixSourceOnly then
  patchedSrc
else
  symlinkJoin {
    name = "codex-${version}";
    paths = [ guardedCodexDrv ];
    nativeBuildInputs = [
      installShellFiles
      makeBinaryWrapper
    ];

    postBuild = ''
      installShellCompletion --cmd codex \
        --bash <($out/bin/codex completion bash) \
        --fish <($out/bin/codex completion fish) \
        --zsh <($out/bin/codex completion zsh)

      wrapProgram "$out/bin/codex" --prefix PATH : ${
        lib.makeBinPath ([ ripgrep ] ++ lib.optionals pkgs.stdenv.hostPlatform.isLinux [ bubblewrap ])
      }
    '';

    passthru = {
      inherit
        cargoNix
        crateOverrides
        patchedSrc
        v8Build
        ;
      codexDrv = guardedCodexDrv;
    };

    meta = {
      description = "Lightweight coding agent that runs in your terminal";
      homepage = "https://github.com/openai/codex";
      license = lib.licenses.asl20;
      mainProgram = "codex";
      platforms = [
        "aarch64-darwin"
        "x86_64-linux"
      ];
    };
  }
