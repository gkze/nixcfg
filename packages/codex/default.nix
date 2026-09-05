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
  nodeVersionPath = "${src}/node-version.txt";
  jsReplSourcePath = "${src}/core/src/tools/js_repl/mod.rs";
  nodeVersionFile =
    if builtins.pathExists nodeVersionPath then
      builtins.path {
        path = nodeVersionPath;
        name = "codex-node-version.txt";
      }
    else
      null;
  needsCoreNodeVersionPatch = nodeVersionFile != null && builtins.pathExists jsReplSourcePath;
  bubblewrapVendorSrc = builtins.path {
    path = "${src}/vendor/bubblewrap";
    name = "codex-vendor-bubblewrap";
  };

  patchCoreNodeVersion =
    target:
    lib.optionalString needsCoreNodeVersionPatch ''
      cp ${nodeVersionFile} "${target}/node-version.txt"
      substituteInPlace "${target}/src/tools/js_repl/mod.rs" \
        --replace-fail '../../../../node-version.txt' '../../../node-version.txt'
    '';

  # Reuse the shared codex-v8 overlay source so updates only touch one pin.
  v8Source = slib.sources.codex-v8;
  rustyV8Src = pkgs.codex-v8;
  # The updater records the release tag alongside the recursive source hash.
  # Keep evaluation independent of the fetchgit result; update validation and
  # the checked-in Cargo.nix version assertion guard this generated metadata.
  v8ManifestVersion = lib.removePrefix "v" v8Source.version;
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
    gnArgsOverrides = {
      # Our Nix-provided rustc + RUSTC_BOOTSTRAP=1 is nightly-capable, but GN
      # can't detect this since we supply rust_sysroot_absolute.
      rustc_nightly_capability = "true";
    };
    # v146.4.0's allocator uses #[linkage = "weak"] for shim symbols, but on
    # Darwin weak symbols in force-loaded static archives are not resolved
    # properly. Remove weak linkage so the symbols are strong externals.
    extraPatchCommands = ''
      ${pkgs.python3}/bin/python3 ${./patch_allocator_weak_linkage.py} \
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
        ${
          if nodeVersionFile != null then
            ''
              cp ${nodeVersionFile} "$out/node-version.txt"
              cp ${nodeVersionFile} "$out/core/node-version.txt"
              ${patchCoreNodeVersion "$out/core"}
            ''
          else
            lib.optionalString (builtins.pathExists jsReplSourcePath) ''
              echo "codex js_repl source still exists, but ${nodeVersionPath} is missing" >&2
              exit 1
            ''
        }

        ${pythonForSourcePrep}/bin/python3 \
          ${./patch_cargo_lock_version.py} \
          "$out/Cargo.lock" \
          ${lib.escapeShellArg version}
      '';

  crateSource = (import ../../lib/crate2nix-source-slice.nix).sourceFor {
    rootSrc = src;
    source = {
      cargoNixSha256 = builtins.hashFile "sha256" ./Cargo.nix;
      input = "codex";
      narHash = inputs.codex.narHash;
      subdir = "codex-rs";
    };
    sourceInfo = builtins.fromJSON (builtins.readFile ./crate-sources.json);
  };

  cargoNix = import ./Cargo.nix {
    inherit pkgs crateSource;
    # Cargo.nix maps evaluator-visible flake input paths to updater-hashed,
    # content-addressed per-crate sources. Package-only source surgery belongs
    # in crate overrides below; the complete patched workspace remains an
    # update-time artifact.
    rootSrc = src;
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

  codexCoreOverride = attrs: {
    src = runCommand "codex-core-${attrs.version}-src" { } ''
      cp -R ${attrs.src} "$out"
      chmod -R u+w "$out"
      ${patchCoreNodeVersion "$out"}
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
        ln -s ${bubblewrapVendorSrc} "$vendor_dir/bubblewrap"
      '';
    };

  codexLinuxLowMemoryOverride =
    _attrs:
    lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
      codegenUnits = 16;
    };

  # Each platform crate compiles CARGO_MANIFEST_DIR into its path helpers.
  # crate2nix builds the library separately from its source, so the default
  # value points into a transient producer sandbox. Embed the final library
  # output instead and materialize the referenced protoc/include payload there.
  protocBinVendoredPlatformOverride = attrs: {
    preBuild = (attrs.preBuild or "") + ''
      export CARGO_MANIFEST_DIR="$lib/share/${attrs.crateName}"
    '';
    postInstall = (attrs.postInstall or "") + ''
      mkdir -p "$lib/share/${attrs.crateName}"
      cp -R bin include "$lib/share/${attrs.crateName}/"
    '';
  };
  protocBinVendoredPlatformCrates = map (
    dependency: cargoNix.internal.crates.${dependency.packageId}.crateName
  ) cargoNix.internal.crates."protoc-bin-vendored".dependencies;
  protocBinVendoredPlatformOverrides = lib.genAttrs protocBinVendoredPlatformCrates (
    _: protocBinVendoredPlatformOverride
  );

  crateOverrides =
    pkgs.defaultCrateOverrides
    // protocBinVendoredPlatformOverrides
    // {
      codex-app-server-protocol = codexLinuxLowMemoryOverride;
      crossterm = crosstermOverride;
      codex-linux-sandbox = codexLinuxSandboxOverride;
      rmcp = rmcpOverride;
      runfiles = runfilesOverride;
      v8 = v8Build.mkCrateOverride;
    }
    // lib.optionalAttrs needsCoreNodeVersionPatch {
      codex-core = codexCoreOverride;
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
